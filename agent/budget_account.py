"""Opt-in, durable run-tree budgets at physical request boundaries.

Policy bounds are administrator declarations, not vendor invoice guarantees. The
first certified adapter is one text-only OpenAI SDK chat completion with SDK
retries disabled. Opaque providers, server tools, media and external loops fail
closed until they have an adapter that can enforce a finite request envelope.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import time
import uuid
from typing import Any, Mapping


class BudgetPolicyError(ValueError):
    pass


class BudgetBlocked(InterruptedError):
    """An allocation was refused; this is neither a successful run nor a refund."""


BUDGET_CONFIG_FIELDS = {
    "schema_version": "Required integer 1; unsupported versions fail closed",
    "mode": "tokens (spend untracked) or cost (operator-declared worst-case rate bounds)",
    "limits.tokens": "Positive aggregate input plus output token ceiling",
    "limits.attempts": "Positive aggregate physical provider attempt ceiling; SDK retries disabled",
    "limits.cost_micros": "Positive policy-currency micro-unit ceiling in cost mode; null in tokens mode",
    "limits.wall_ms": "Positive aggregate work duration allocation in milliseconds",
    "limits.provider_slots": "Positive concurrent physical provider request ceiling across descendants",
    "limits.executor_slots": "Positive concurrent certified local tool execution ceiling",
    "deadline_seconds": "Positive seconds from command acceptance; descendants inherit earliest deadline",
    "request_timeout_ms": "Positive per-request cancellation deadline, no larger than aggregate wall_ms",
    "routes[].model": "Exact dispatched provider model ID; no wildcard or model-name inference",
    "routes[].base_url": "Exact credential-free SDK HTTP endpoint; fallback destinations need their own entry",
    "routes[].max_input_tokens": "Positive input ceiling checked against full UTF-8 request bytes plus verified overhead; overlarge requests reject without truncation",
    "routes[].max_output_tokens": "Positive enforced output cap covering all billable output/reasoning for this route",
    "routes[].output_token_parameter": "Verified cap spelling: max_tokens or max_completion_tokens",
    "routes[].input_overhead_tokens": "Positive operator-verified upper bound for provider framing/hidden input overhead",
    "routes[].input_cost_micros_per_million": "Verified worst-case micro-unit rate per million input tokens; null in tokens mode",
    "routes[].output_cost_micros_per_million": "Verified worst-case micro-unit rate per million output tokens; null in tokens mode",
    "routes[].bounds_verified": "Must be true only after the operator verifies envelope, tokenizer/framing, output cap and worst-case pricing contract",
}


_DIMENSIONS = frozenset({"tokens", "attempts", "cost_micros", "wall_ms", "provider_slots", "executor_slots"})
# These built-ins do not start an opaque billable external loop. Identity grants
# still run first; this set is a cost-contract floor, never a capability grant.
_LOCAL_TOOLS = {"todo_list": "tools.todo_tool", "delegate_task": "tools.delegate_tool",
                "execute_code": "tools.code_execution_tool", "memory": "tools.memory_tool",
                "session_search": "tools.session_search_tool",
                "dots_page_read": "tools.dots_tool", "dots_page_propose": "tools.dots_tool",
                "dots_computer_observe": "tools.dots_tool", "dots_computer_propose": "tools.dots_tool"}


def _integer(value, name, *, minimum=1):
    if type(value) is not int or value < minimum or value > (1 << 63) - 1:
        raise BudgetPolicyError(f"{name} must be an integer between {minimum} and 2^63-1")
    return value


@dataclass(frozen=True)
class BudgetPolicy:
    """JSON snapshot is immutable and revalidated on durable restoration."""
    snapshot: str

    @property
    def record(self):
        return json.loads(self.snapshot)

    @property
    def limits(self):
        return self.record["limits"]

    @property
    def deadline_seconds(self):
        return self.record["deadline_seconds"]

    @property
    def cost_tracking(self):
        return "bounded" if self.record["mode"] == "cost" else "untracked"


def parse_budget_policy(config: Mapping) -> BudgetPolicy | None:
    raw = config.get("runtime_budget", {})
    if raw == {}:
        return None
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "mode", "limits", "deadline_seconds", "request_timeout_ms", "routes"}:
        raise BudgetPolicyError("runtime_budget requires the complete versioned policy")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise BudgetPolicyError("unsupported runtime_budget schema_version")
    if raw["mode"] not in {"tokens", "cost"}:
        raise BudgetPolicyError("runtime_budget mode must be tokens or cost")
    limits = raw["limits"]
    if not isinstance(limits, dict) or set(limits) != _DIMENSIONS:
        raise BudgetPolicyError("runtime_budget limits require every resource dimension")
    for key, value in limits.items():
        if key == "cost_micros" and raw["mode"] == "tokens":
            if value is not None:
                raise BudgetPolicyError("tokens mode requires cost_micros: null (untracked cost)")
        else:
            _integer(value, f"runtime_budget.limits.{key}")
    _integer(raw["deadline_seconds"], "runtime_budget.deadline_seconds")
    if raw["deadline_seconds"] > 315360000:
        raise BudgetPolicyError("runtime budget deadline cannot exceed ten years")
    _integer(raw["request_timeout_ms"], "runtime_budget.request_timeout_ms")
    if raw["request_timeout_ms"] > limits["wall_ms"]:
        raise BudgetPolicyError("request timeout exceeds aggregate wall allocation")
    routes = raw["routes"]
    if not isinstance(routes, list) or not routes:
        raise BudgetPolicyError("runtime_budget requires explicit bounded routes")
    seen = set()
    fields = {"model", "base_url", "max_input_tokens", "max_output_tokens", "input_overhead_tokens",
              "input_cost_micros_per_million", "output_cost_micros_per_million", "bounds_verified", "output_token_parameter"}
    for route in routes:
        if not isinstance(route, dict) or set(route) != fields:
            raise BudgetPolicyError("invalid runtime_budget route fields")
        if route["output_token_parameter"] not in {"max_tokens", "max_completion_tokens"}:
            raise BudgetPolicyError("invalid verified output token parameter")
        if route["bounds_verified"] is not True:
            raise BudgetPolicyError("route envelope and worst-case rate bounds must be verified by the operator")
        for key in ("model", "base_url"):
            if not isinstance(route[key], str) or not route[key].strip():
                raise BudgetPolicyError(f"route {key} must be explicit")
        endpoint = route["base_url"].rstrip("/")
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BudgetPolicyError("budget route base_url must be an exact credential-free HTTP endpoint")
        key = (endpoint, route["model"])
        if key in seen:
            raise BudgetPolicyError("duplicate runtime_budget route")
        seen.add(key)
        for name in ("max_input_tokens", "max_output_tokens", "input_overhead_tokens"):
            _integer(route[name], f"route.{name}")
        for name in ("input_cost_micros_per_million", "output_cost_micros_per_million"):
            value = route[name]
            if raw["mode"] == "cost":
                _integer(value, f"route.{name}", minimum=0)
            elif value is not None:
                raise BudgetPolicyError("tokens mode must not imply known cost rates")
    return BudgetPolicy(json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False))


def install_budget_policy(agent, config, *, session_db=None, session_id=None):
    policy = parse_budget_policy(config)
    if session_db is not None and session_id and session_db.get_session(session_id) is not None:
        from agent.agent_identity import parse_agent_identity_config
        identity = parse_agent_identity_config(config)
        if identity is not None:
            actor = {"principal_id": identity.principal_id, "profile_id": identity.profile_id,
                     "agent_id": identity.active_agent_id}
            binding = session_db.get_budget_child_binding(str(session_id), actor)
            if binding is not None:
                inherited = parse_budget_policy({"runtime_budget": binding["parent_account"]["policy_snapshot"]})
                if policy != inherited:
                    raise BudgetPolicyError("persisted budget policy cannot be disabled or replaced on resume")
    if policy is not None and not config.get("agent_identity"):
        raise BudgetPolicyError("runtime_budget requires durable configured agent identity")
    agent._runtime_budget_policy = policy


def current_budget(agent=None):
    from agent.runtime_commands import assert_runtime_dispatch
    run = assert_runtime_dispatch(agent)
    return run.budget if run is not None else None


def budget_enabled(agent):
    return isinstance(getattr(agent, "_runtime_budget_policy", None), BudgetPolicy)


def actor_for(context):
    identity = context.identity
    return {"principal_id": identity.principal_id, "profile_id": identity.profile_id, "agent_id": identity.agent_id}


@dataclass
class BudgetRuntime:
    db: Any
    account_id: str
    root_id: str
    policy: BudgetPolicy
    actor: dict
    holder: str
    generation: int
    deadline: float
    agent: Any
    blocked_reason: str | None = None

    def block(self, reason):
        self.blocked_reason = reason
        raise BudgetBlocked(reason)

    def check(self):
        if self.blocked_reason:
            raise BudgetBlocked(self.blocked_reason)
        if bool(getattr(self.agent, "_interrupt_requested", False)):
            self.block("runtime budget cancelled; upstream usage may be unknown")
        if time.time() >= self.deadline:
            self.block("runtime budget deadline exceeded")

    @property
    def fence(self):
        return {"holder": self.holder, "generation": self.generation}

    def reserve(self, maxima):
        self.check()
        operation_id = uuid.uuid4().hex
        try:
            self.db.reserve_budget(self.account_id, self.actor, operation_id, maxima, **self.fence)
        except ValueError as exc:
            self.block(f"runtime budget allocation refused: {exc}")
        return operation_id

    def dispatched(self, operation_id):
        self.check()
        result = self.db.mark_budget_dispatched(self.account_id, self.actor, operation_id, **self.fence)
        if result.get("dispatch_granted") is not True:
            self.block("runtime budget duplicate operation dispatch refused")

    def settle(self, operation_id, actual, *, unknown=False, slots_released=True):
        result = self.db.settle_budget(self.account_id, self.actor, operation_id, actual,
                                      unknown_usage=unknown, slots_released=slots_released, **self.fence)
        if result["debt"]:
            self.blocked_reason = "reported usage exceeded its verified reservation bound; further dispatch blocked"
        return result

    def status(self):
        account = self.db.get_budget_account(self.account_id, self.actor)
        return {"account_id": self.account_id, "root_id": self.root_id,
                "cost_tracking": self.policy.cost_tracking,
                "unknown_usage": account["unknown_usage"] or bool(account["reserved"]["provider_slots"]),
                "pending_provider_requests": account["reserved"]["provider_slots"], "reserved": account["reserved"],
                "consumed": account["consumed"], "blocked_reason": self.blocked_reason,
                "invoice_guarantee": False}


def admission_deadline(agent):
    policy = getattr(agent, "_runtime_budget_policy", None)
    if policy is None:
        return None
    deadline = time.time() + policy.deadline_seconds
    context = getattr(agent, "runtime_context", None)
    db = getattr(agent, "_session_db", None)
    if context is not None and db is not None:
        binding = db.get_budget_child_binding(str(agent.session_id), actor_for(context))
        if binding is not None:
            deadline = min(deadline, binding["parent_account"]["deadline"])
    return deadline


def create_run_budget(agent, db, context, run_id, holder, generation, command_id):
    policy = getattr(agent, "_runtime_budget_policy", None)
    from agent.admission import AdmissionQueue
    accepted_command = db.read_runtime_command(str(agent.session_id), command_id)
    if accepted_command is None or accepted_command["budget_policy_json"] != (policy.snapshot if policy else None):
        raise BudgetBlocked("budget policy changed after command acceptance; original allocation cannot be replaced")
    found, accepted_policy = AdmissionQueue(db).budget_policy(str(agent.session_id), command_id)
    if found and (policy.snapshot if policy else None) != accepted_policy:
        raise BudgetBlocked("queued budget policy changed after acceptance; resubmit under an explicit new session")
    if policy is None:
        binding = db.get_budget_child_binding(str(agent.session_id), actor_for(context))
        if binding is not None:
            raise BudgetBlocked("persisted budget policy cannot be disabled")
        return None
    from hermes_state_budgets import BudgetStoreError
    actor = actor_for(context)
    try:
        existing = db.get_budget_account(run_id, actor)
    except BudgetStoreError as exc:
        if exc.code != "budget_not_found":
            raise
        existing = None
    if existing is not None:
        account = db.create_budget_account(str(agent.session_id), actor, run_id, policy.limits,
            deadline=existing["deadline"], parent_id=existing["parent_id"], holder=holder,
            generation=generation, policy_snapshot=policy.record)
    else:
        binding = db.get_budget_child_binding(str(agent.session_id), actor)
        parent_id = binding["parent_id"] if binding else None
        from agent.admission import AdmissionQueue
        # Direct submissions carry their acceptance time too; queue waiting never
        # earns another wall/deadline allocation.
        accepted = db.read_runtime_run_accepted_at(str(agent.session_id), run_id)
        if accepted is None:
            raise BudgetBlocked("original budget acceptance time is unavailable; allocation cannot reset")
        deadline = accepted + policy.deadline_seconds
        queued_deadline = AdmissionQueue(db).deadline(str(agent.session_id), command_id)
        if queued_deadline is not None:
            deadline = min(deadline, queued_deadline)
        if context.policy.role == "child" and parent_id is None:
            raise BudgetBlocked("budget-enabled child requires a durable inherited budget")
        if parent_id is not None:
            parent = binding["parent_account"]
            inherited = parse_budget_policy({"runtime_budget": parent["policy_snapshot"]})
            if inherited != policy:
                raise BudgetBlocked("child budget policy differs from its inherited snapshot")
            deadline = min(deadline, parent["deadline"])
        account = db.create_budget_account(str(agent.session_id), actor, run_id, policy.limits,
            deadline=deadline, parent_id=parent_id, holder=holder, generation=generation,
            policy_snapshot=policy.record)
        if parent_id is None:
            # Until BE09 supplies a trusted new-mission boundary, all automatic
            # continuations in this session remain under its first root ceiling.
            db.bind_budget_child(account["account_id"], actor, str(agent.session_id),
                                 holder=holder, generation=generation)
    return BudgetRuntime(db, account["account_id"], account["root_id"], policy, actor,
                         holder, generation, account["deadline"], agent)


def bind_child_budget(parent_agent, child):
    if not budget_enabled(parent_agent):
        if budget_enabled(child):
            raise BudgetBlocked("budget-enabled child has no parent allocation")
        return
    budget = current_budget(parent_agent)
    if budget is None:
        raise BudgetBlocked("child creation requires an admitted parent budget")
    budget.check()
    if getattr(child, "_runtime_budget_policy", None) != budget.policy:
        raise BudgetBlocked("child cannot replace its inherited budget policy")
    budget.db.bind_budget_child(budget.account_id, budget.actor, str(child.session_id), **budget.fence)


def reject_opaque_callback(kind):
    """Opaque hooks cannot add billable work outside the physical adapter.

    Reject, rather than skip, permission middleware: skipping a guard could
    silently relax an existing capability/egress floor.
    """
    from tools.egress_policy import reject_unsupported_route
    from agent.runtime_commands import _RUN
    run = _RUN.get()
    if run is not None:
        if run.budget is not None:
            run.budget.block(f"{kind} callback has no certified finite budget adapter")
        reject_unsupported_route(kind)
        return
    from agent.runtime_context import current_agent_context
    if current_agent_context() is not None:
        from agent.identity_lifecycle import identity_config
        if parse_budget_policy(identity_config()) is not None:
            raise BudgetBlocked(f"{kind} callback requires a certified budget adapter and admitted run")
    reject_unsupported_route(kind)


def _plain_request(kwargs):
    request = dict(kwargs)
    if request.get("stream") or request.get("n", 1) != 1:
        raise BudgetBlocked("budget adapter requires one nonstreaming completion")
    if request.get("extra_body") or request.get("previous_response_id") or request.get("context_management"):
        raise BudgetBlocked("opaque request extensions have no certified budget adapter")
    for message in request.get("messages", []):
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            if not isinstance(content, list) or any(not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str) for part in content):
                raise BudgetBlocked("media inputs have no certified token/cost bound")
    if any(tool.get("type") != "function" for tool in request.get("tools", [])):
        raise BudgetBlocked("server-side tools have no certified budget adapter")
    return request


def _prepare_request(budget, client, kwargs):
    import openai
    if not isinstance(client, (openai.OpenAI, openai.AsyncOpenAI)):
        budget.block("opaque provider client has no certified single-attempt budget adapter")
    request = _plain_request(kwargs)
    endpoint = str(client.base_url).rstrip("/")
    route = next((r for r in budget.policy.record["routes"]
                  if r["base_url"].rstrip("/") == endpoint and r["model"] == request.get("model")), None)
    if route is None:
        budget.block("provider/model route has no configured verified budget bounds")
    cap = route["max_output_tokens"]
    # Both accepted OpenAI wire spellings are capped. Do not change historical
    # prompts or hide an overlarge input by truncating it.
    cap_key = route["output_token_parameter"]
    for key in ("max_tokens", "max_completion_tokens"):
        if key in request:
            _integer(request[key], key)
            cap = min(cap, request[key])
    request.pop("max_tokens", None)
    request.pop("max_completion_tokens", None)
    request[cap_key] = cap
    envelope = {key: value for key, value in request.items() if key != "timeout"}
    try:
        input_bound = len(json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) + route["input_overhead_tokens"]
    except (ValueError, TypeError) as exc:
        budget.block(f"request envelope cannot be bounded: {type(exc).__name__}")
    if input_bound > route["max_input_tokens"]:
        budget.block("request envelope exceeds configured input token bound; prompt was not truncated")
    timeout_ms = min(budget.policy.record["request_timeout_ms"], max(0, int((budget.deadline - time.time()) * 1000)))
    if timeout_ms <= 0:
        budget.block("runtime budget deadline exceeded")
    request["timeout"] = timeout_ms / 1000
    maxima = {"tokens": input_bound + cap, "attempts": 1, "wall_ms": timeout_ms, "provider_slots": 1}
    if budget.policy.cost_tracking == "bounded":
        maxima["cost_micros"] = (input_bound * route["input_cost_micros_per_million"] + cap * route["output_cost_micros_per_million"] + 999_999) // 1_000_000
    else:
        maxima["cost_micros"] = 0
    # Copy is request-local; do not mutate shared clients used by sibling calls.
    bounded = client.with_options(max_retries=0, timeout=timeout_ms / 1000)
    return bounded, request, route, maxima


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _actual(response, route, elapsed_ms, cost_tracking):
    usage = _field(response, "usage")
    inputs, outputs = _field(usage, "prompt_tokens"), _field(usage, "completion_tokens")
    actual = {"attempts": 1, "wall_ms": elapsed_ms}
    if type(inputs) is not int or type(outputs) is not int or inputs < 0 or outputs < 0:
        return actual, True
    actual["tokens"] = inputs + outputs
    if cost_tracking == "bounded":
        # This is policy-priced metered usage, never a vendor invoice assertion.
        actual["cost_micros"] = (inputs * route["input_cost_micros_per_million"] + outputs * route["output_cost_micros_per_million"] + 999_999) // 1_000_000
    return actual, False


@contextmanager
def _request_deadline(budget, operation_id, timeout_ms):
    from agent.periodic_scheduler import schedule
    expires = time.monotonic() + timeout_ms / 1000
    done = False
    def check():
        if done:
            return False
        if time.monotonic() >= expires:
            budget.blocked_reason = "physical request exceeded its reserved wall deadline; remote usage may be unknown"
            run = getattr(budget.agent, "_active_runtime_run", None)
            if run is not None and run.task_scope is not None:
                run.task_scope.request_cancel(budget.blocked_reason)
            return False
    handle = schedule(check, min(0.1, max(0.01, timeout_ms / 1000)))
    try:
        yield
    finally:
        done = True
        handle.cancel()


def invoke_budgeted_completion(client, kwargs, *, agent=None):
    budget = current_budget(agent)
    if budget is None:
        return client.chat.completions.create(**kwargs)
    from agent.attempt_policy import (controller_for, provider_account_ref,
                                      record_attempt, classify_attempt_failure)
    controller = controller_for(budget)
    try:
        bounded, request, route, maxima = _prepare_request(budget, client, kwargs)
    except BudgetBlocked as exc:
        budget.block(str(exc))
    account = provider_account_ref(budget, client)
    controller.check(account)
    operation_id = budget.reserve(maxima)
    started = time.monotonic()
    try:
        attempt = controller.begin(account, operation_id)
        record_attempt(budget, attempt, phase="started")
        budget.dispatched(operation_id)
    except BaseException:
        budget.db.release_budget_reservation(budget.account_id, budget.actor, operation_id, **budget.fence)
        raise
    try:
        with _request_deadline(budget, operation_id, maxima["wall_ms"]):
            response = bounded.chat.completions.create(**request)
    except BaseException as exc:
        failure = classify_attempt_failure(exc)
        actual = {"attempts": 1, "wall_ms": math.ceil((time.monotonic() - started) * 1000)}
        # A completed refusal frees concurrency, but status alone does not prove
        # zero billing on an arbitrary configured compatible endpoint.
        budget.settle(operation_id, actual, unknown=True, slots_released=failure.rejected)
        controller.failed(attempt, failure, error=exc)
        record_attempt(budget, attempt, phase="failed", failure=failure)
        raise
    actual, unknown = _actual(response, route, math.ceil((time.monotonic() - started) * 1000), budget.policy.cost_tracking)
    budget.settle(operation_id, actual, unknown=unknown)
    controller.succeeded(attempt)
    record_attempt(budget, attempt, phase="completed")
    budget.check()
    return response


async def invoke_budgeted_completion_async(client, kwargs):
    budget = current_budget()
    if budget is None:
        return await client.chat.completions.create(**kwargs)
    from agent.attempt_policy import (controller_for, provider_account_ref,
                                      record_attempt, classify_attempt_failure)
    controller = controller_for(budget)
    try:
        bounded, request, route, maxima = _prepare_request(budget, client, kwargs)
    except BudgetBlocked as exc:
        budget.block(str(exc))
    account = provider_account_ref(budget, client)
    controller.check(account)
    operation_id = budget.reserve(maxima)
    started = time.monotonic()
    try:
        attempt = controller.begin(account, operation_id)
        record_attempt(budget, attempt, phase="started")
        budget.dispatched(operation_id)
    except BaseException:
        budget.db.release_budget_reservation(budget.account_id, budget.actor, operation_id, **budget.fence)
        raise
    try:
        with _request_deadline(budget, operation_id, maxima["wall_ms"]):
            response = await bounded.chat.completions.create(**request)
    except BaseException as exc:
        failure = classify_attempt_failure(exc)
        actual = {"attempts": 1, "wall_ms": math.ceil((time.monotonic() - started) * 1000)}
        # A completed refusal frees concurrency, but status alone does not prove
        # zero billing on an arbitrary configured compatible endpoint.
        budget.settle(operation_id, actual, unknown=True, slots_released=failure.rejected)
        controller.failed(attempt, failure, error=exc)
        record_attempt(budget, attempt, phase="failed", failure=failure)
        raise
    actual, unknown = _actual(response, route, math.ceil((time.monotonic() - started) * 1000), budget.policy.cost_tracking)
    budget.settle(operation_id, actual, unknown=unknown)
    controller.succeeded(attempt)
    record_attempt(budget, attempt, phase="completed")
    budget.check()
    return response


@contextmanager
def budget_tool_scope(run, name):
    budget = run.budget
    if budget is None:
        yield
        return
    from tools.registry import registry
    entry = registry.get_entry(name)
    if (name not in _LOCAL_TOOLS or entry is None
            or getattr(entry.handler, "__module__", "") != _LOCAL_TOOLS[name]):
        budget.block(f"tool {name} has no certified bounded execution adapter")
    budget.check()
    # Delegation is orchestration: descendants acquire their own executor/provider
    # slots. Holding a parent executor slot while waiting would deadlock a size-1 pool.
    # The isolated executor reserves at its concrete launch edge, including
    # direct registry/handler entry; a wrapper slot here would double-count.
    # Dots bridges likewise reserve at each physical request; human approval
    # waits hold no executor slot and cannot inflate the finite request bound.
    if name in {"delegate_task", "execute_code", "dots_page_read", "dots_page_propose",
                "dots_computer_observe", "dots_computer_propose"}:
        yield
        return
    maximum = min(budget.policy.record["request_timeout_ms"], max(1, int((budget.deadline - time.time()) * 1000)))
    operation_id = budget.reserve({"executor_slots": 1, "wall_ms": maximum})
    started = time.monotonic()
    try:
        budget.dispatched(operation_id)
    except BaseException:
        budget.db.release_budget_reservation(budget.account_id, budget.actor, operation_id, **budget.fence)
        raise
    try:
        yield
    finally:
        budget.settle(operation_id, {"wall_ms": math.ceil((time.monotonic() - started) * 1000)})
    budget.check()
