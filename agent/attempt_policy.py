"""Finite physical attempts for the certified BE03 adapter.

The budget writer remains the only spending authority. This policy records its
reservation ID, never invents another allowance, and never retries an ambiguous
acceptance. Legacy/unmetered adapters do not acquire a certification by import.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from agent.retry_utils import jittered_backoff, parse_retry_after_seconds

_SCOPE_KEY = secrets.token_bytes(32)
_CONTROLLER_LOCK = threading.Lock()


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=lambda obj: (type(obj).__name__, id(obj)))
    return hmac.new(_SCOPE_KEY, raw.encode(), hashlib.sha256).hexdigest()


def authority_scope(agent=None) -> str:
    """Opaque process-local key; neither secrets nor their names are diagnostic data."""
    from agent.runtime_context import AgentContext, current_agent_context
    from agent.secret_scope import current_secret_scope, current_secret_scope_home
    context = current_agent_context()
    owner = getattr(agent, "runtime_context", None)
    if isinstance(owner, AgentContext) and context != owner:
        raise PermissionError("provider client requires its owning identity scope")
    identity = None if context is None else (
        context.identity.principal_id, context.identity.profile_id, context.identity.agent_id,
        context.identity.profile_home_digest, context.config_digest, context.policy.digest,
    )
    return _digest((identity, current_secret_scope_home(), current_secret_scope()))


def client_scope_key(agent, options) -> tuple:
    """Immutable client identity including the actual proxy and async loop owner."""
    from agent.process_bootstrap import _get_proxy_for_base_url
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    endpoint = str(options.get("base_url", ""))
    return (authority_scope(agent), _digest(options), _digest(_get_proxy_for_base_url(endpoint)), loop)


def auxiliary_cache_scope(endpoint: str):
    from hermes_cli.config import load_config_readonly, get_compatible_custom_providers
    config = load_config_readonly()
    # Endpoint may still be implicit at lookup time. Include all configured route
    # TLS/header options so a change cannot reuse a client built with old trust.
    return client_scope_key(None, {"base_url": endpoint,
                                  "custom_routes": get_compatible_custom_providers(config),
                                  "model": config.get("model", {})})


def strict_failover_refusal(agent) -> str | None:
    """BE01 grants credentials, not recipient/privacy or protocol-transfer authority.

    BE05 has not admitted purpose/recipient grants yet. Even a same-endpoint model
    change needs a certified opaque-state/tool continuation and budget route proof.
    Refuse before credential resolution rather than treating config as permission.
    """
    from agent.runtime_context import AgentContext, current_agent_context
    if isinstance(getattr(agent, "runtime_context", None), AgentContext) or current_agent_context() is not None:
        return "automatic_failover_requires_recipient_purpose_and_continuation_grant"
    return None


class FailureClass(str, Enum):
    AUTH = "auth_failure"
    QUOTA = "quota_exhausted"
    THROTTLE = "throttled"
    OVERLOAD = "overloaded"
    CONTEXT = "context_overflow"
    UNSUPPORTED = "unsupported_capability"
    AMBIGUOUS = "ambiguous_transport"
    REJECTED = "request_rejected"


@dataclass(frozen=True)
class Failure:
    reason: FailureClass
    remote_acceptance: str
    retry_after: float | None = None

    @property
    def rejected(self):
        return self.remote_acceptance == "rejected"


def classify_attempt_failure(error) -> Failure:
    from agent.error_classifier import classify_api_error
    classified = classify_api_error(error)
    reason = classified.reason.value
    groups = {
        "auth": FailureClass.AUTH, "auth_permanent": FailureClass.AUTH,
        "billing": FailureClass.QUOTA,
        "rate_limit": FailureClass.THROTTLE, "upstream_rate_limit": FailureClass.THROTTLE,
        "overloaded": FailureClass.OVERLOAD,
        "context_overflow": FailureClass.CONTEXT, "payload_too_large": FailureClass.CONTEXT,
        "model_not_found": FailureClass.UNSUPPORTED, "model_entitlement": FailureClass.UNSUPPORTED,
        "multimodal_tool_content_unsupported": FailureClass.UNSUPPORTED,
        "reasoning_mandatory": FailureClass.UNSUPPORTED,
        "format_error": FailureClass.REJECTED, "content_policy_blocked": FailureClass.REJECTED,
        "thinking_signature": FailureClass.UNSUPPORTED,
        "invalid_encrypted_content": FailureClass.UNSUPPORTED,
        "provider_policy_blocked": FailureClass.REJECTED,
    }
    kind = groups.get(reason, FailureClass.AMBIGUOUS)
    status = getattr(error, "status_code", None)
    # A completed refusal is different from a timeout or a server error after
    # possible acceptance. Never use text like "timeout" as evidence of rejection.
    rejected = type(status) is int and 400 <= status < 500 and status != 408
    headers = getattr(getattr(error, "response", None), "headers", None)
    retry_after = parse_retry_after_seconds(headers)
    return Failure(kind, "rejected" if rejected else "unknown", retry_after)


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    reason: str
    provider_account_ref: str
    reservation_id: str
    remote_acceptance: str
    logical_request_id: str

    def to_record(self):
        return asdict(self)


@dataclass(frozen=True)
class RetryDecision:
    action: str
    reason: str
    delay_seconds: float = 0.0


class AccountCircuits:
    """Bounded process-local, identity/secret/account scoped cooldowns.

    Persistent attempt/cost bounds remain in BE03. Restarting this acceleration
    cache does not restore a reservation or authorize replay of an uncertain run.
    """
    def __init__(self, *, capacity=4096, clock=time.monotonic):
        self.capacity, self.clock = capacity, clock
        self._lock = threading.Lock()
        self._until = {}

    def remaining(self, account):
        with self._lock:
            now = self.clock()
            self._until = {k: v for k, v in self._until.items() if v > now}
            return max(0.0, self._until.get(account, now) - now)

    def trip(self, account, delay):
        with self._lock:
            now = self.clock()
            self._until = {k: v for k, v in self._until.items() if v > now}
            if account not in self._until and len(self._until) >= self.capacity:
                # Do not evict a live circuit and accidentally reopen its account.
                raise RuntimeError("provider circuit capacity reached")
            self._until[account] = max(self._until.get(account, now), now + delay)


_CIRCUITS = AccountCircuits()


class AttemptController:
    MAX_ATTEMPTS = 3
    MAX_DELAY = 30.0

    def __init__(self, budget, *, circuits=None, clock=time.monotonic):
        self.budget = budget
        self.circuits = circuits or _CIRCUITS
        self.clock = clock
        self._lock = threading.RLock()
        self._counts = {}
        self._failures = {}
        self._not_before = {}
        self._aux_sequence = 0
        self.last_decision = None
        self.last_error = None

    def logical_id(self):
        main = getattr(self.budget.agent, "_current_api_request_id", None)
        return str(main) if main else f"auxiliary:{self._aux_sequence}"

    def check(self, account):
        self.budget.check()
        logical = self.logical_id()
        with self._lock:
            failure = self._failures.get(logical)
            if failure is not None and failure.action == "stop":
                self.budget.block(f"provider attempt policy: {failure.reason}")
            if self._counts.get(logical, 0) >= self.MAX_ATTEMPTS:
                self.budget.block("provider physical attempt ceiling reached")
            wait = max(self.circuits.remaining(account), self._not_before.get(logical, 0) - self.clock())
            if wait > 0:
                self.budget.block("provider account circuit is cooling down")
        return logical

    def begin(self, account, reservation):
        with self._lock:
            logical = self.check(account)
            self._counts[logical] = self._counts.get(logical, 0) + 1
            prior = self._failures.get(logical)
            return Attempt(reservation, prior.reason if prior else "initial", account, reservation, "unknown", logical)

    def failed(self, attempt, failure, *, error=None):
        with self._lock:
            count = self._counts[attempt.logical_request_id]
            reason = failure.reason.value
            delay = 0.0
            retryable = failure.rejected and failure.reason in {FailureClass.THROTTLE, FailureClass.OVERLOAD}
            if retryable and count < self.MAX_ATTEMPTS:
                # Retry-After is a floor. If it does not fit, stop instead of
                # clipping it and hammering the provider earlier than permitted.
                delay = max(failure.retry_after or 0, min(self.MAX_DELAY, jittered_backoff(count, base_delay=0.5, max_delay=15)))
                remaining = self.budget.deadline - time.time()
                retryable = delay <= self.MAX_DELAY and delay < remaining
            else:
                retryable = False
            decision = RetryDecision("retry" if retryable else "stop", reason, delay if retryable else 0)
            if failure.reason in {FailureClass.AUTH, FailureClass.QUOTA}:
                self.circuits.trip(attempt.provider_account_ref, max(60.0, failure.retry_after or 60))
            elif failure.reason in {FailureClass.THROTTLE, FailureClass.OVERLOAD}:
                cooldown = max(failure.retry_after or 0, delay if retryable else 1.0)
                self.circuits.trip(attempt.provider_account_ref, cooldown)
            self._not_before[attempt.logical_request_id] = self.clock() + decision.delay_seconds
            self._failures[attempt.logical_request_id] = decision
            self.last_decision = decision
            self.last_error = error
            return decision

    def succeeded(self, attempt):
        with self._lock:
            self.last_decision = None
            self.last_error = None
            if attempt.logical_request_id.startswith("auxiliary:"):
                self._aux_sequence += 1
            # Main logical IDs are monotonic; prune completed records. Failed
            # requests remain fenced until this run ends.
            self._counts.pop(attempt.logical_request_id, None)
            self._failures.pop(attempt.logical_request_id, None)
            self._not_before.pop(attempt.logical_request_id, None)


def controller_for(budget):
    with _CONTROLLER_LOCK:
        controller = getattr(budget, "_attempt_controller", None)
        if controller is None:
            controller = budget._attempt_controller = AttemptController(budget)
        return controller


def provider_account_ref(budget, client):
    from agent.runtime_context import current_agent_context
    from agent.secret_scope import current_secret_scope_home
    authority_scope(budget.agent)  # validate the owning identity before looking up any account
    context = current_agent_context()
    # Identities using the same granted account share its cooldown, but clients
    # remain separately keyed by their full immutable authority above.
    owner = (context.identity.principal_id, context.identity.profile_id,
             context.identity.profile_home_digest) if context else current_secret_scope_home()
    return _digest((owner, str(client.base_url).rstrip("/"), getattr(client, "api_key", None)))


def record_attempt(budget, attempt, *, phase, failure=None):
    from agent.runtime_commands import assert_runtime_dispatch, _record_operation_event, _RUN
    run = assert_runtime_dispatch(budget.agent) if phase == "started" else _RUN.get()
    final = replace(attempt, remote_acceptance=(failure.remote_acceptance if failure else "accepted")) if phase != "started" else attempt
    if failure:
        final = replace(final, reason=failure.reason.value)
    # Physical records share the budget reservation correlation; the surrounding
    # logical model operation remains a separate journal entry.
    _record_operation_event(run, f"model.{phase}", {
        "command_id": run.command_id, "kind": "model", "phase": phase,
        "physical_attempt": final.to_record(),
    }, run_id=run.run_id, operation_id=attempt.reservation_id)


def bounded_error_result(agent, error, *, messages, api_call_count):
    """Early strict gate: never run legacy refresh/failover/compression ladders."""
    run = getattr(agent, "_active_runtime_run", None)
    budget = getattr(run, "budget", None)
    controller = controller_for(budget) if budget is not None else None
    decision = controller.last_decision if controller is not None and controller.last_error is error else None
    # Pre-dispatch failures have no prior physical outcome to retry.
    from agent.budget_account import BudgetBlocked
    if isinstance(error, BudgetBlocked) or decision is None or decision.action != "retry":
        reason = decision.reason if decision else "provider_dispatch_blocked"
        uncertain = bool(budget is not None and budget.status()["pending_provider_requests"])
        return {"final_response": "The provider request stopped: " + reason.replace("_", " ") + ".",
                "messages": messages, "api_calls": api_call_count, "completed": False, "failed": True,
                "failure_reason": reason, "failure_retryable": False,
                "outcome_uncertain": uncertain}
    return decision
