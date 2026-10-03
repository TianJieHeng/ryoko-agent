"""Per-point release/rollback and floor composition above the BE15 typed client.

The production config only admits off/shadow/advisory observers. A host can
install an enforcing policy only via the evidence-bound release workflow;
creating a PointRule or correcting a label never grants execution authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import threading
import time

from agent.decisions.contracts import digest, label, number, require, sha256
from agent.decisions.point_adapters import Effect, apply_floor
from agent.decisions.policy import MODES, PointGate, PointPolicy
from agent.decisions.registry import REGISTRY, contract_for
from agent.decisions.release_gates import inspect_release

# These are effect categories, not tools or permissions. No widening category
# exists (grant, send-reply, auto-edit, increase-budget, erase-evidence, etc.).
ALLOWED_EFFECTS = {
    "DP01": ("allow", "skip"), "DP02": ("interrupt", "steer", "queue", "preserve_command"),
    "DP03": ("route", "pause"), "DP04": ("select",), "DP05": ("recall",),
    "DP06": ("allow", "ask", "block", "current_judge"), "DP07": ("wrap", "flag", "quarantine"),
    "DP08": ("incumbent", "allow", "block"), "DP09": ("done", "continue", "blocked", "needs_human", "pause"),
    "DP10": ("review", "defer"), "DP11": ("now", "digest", "ignore"),
    "DP12": ("hold", "current_send_policy"), "DP13": ("continue", "nudge", "halt", "pause"),
    "DP14": ("retain",), "DP15": ("assign", "human"), "DP16": ("bundle", "authorized_full"),
}
# Accurate production inventory: a floor adapter is not a live qualified owner.
OWNER_STATUS = {
    point: ("shadow_observer" if point in {"DP06", "DP07", "DP10", "DP11", "DP16"}
            else "adapter_only_owner_integration_pending") for point in REGISTRY
}
ROLLBACK_REASONS = frozenset({"operator", "drift", "false_allow", "missed_direct_request",
                              "stale_menu", "tool_recovery_failed", "budget_violation", "latency_regression"})


@dataclass(frozen=True)
class PointRule:
    point_id: str
    mode: str = "off"
    thresholds_by_class: tuple[tuple[str, float], ...] = (("default", .95),)
    timeout_seconds: float = .15
    allowed_effects: tuple[str, ...] = ()
    rollout_scope: tuple[str, ...] = ()

    def __post_init__(self):
        contract_for(self.point_id)
        require(self.mode in MODES, "invalid_mode")
        require(type(self.thresholds_by_class) is tuple and bool(self.thresholds_by_class), "invalid_thresholds")
        thresholds = dict(self.thresholds_by_class)
        require(len(thresholds) == len(self.thresholds_by_class) and "default" in thresholds, "invalid_thresholds")
        for key, value in thresholds.items():
            label(key)
            number(value, .5, 1, "invalid_threshold")
        number(self.timeout_seconds, .001, 1, "invalid_timeout")
        require(type(self.allowed_effects) is tuple and set(self.allowed_effects) <= set(ALLOWED_EFFECTS[self.point_id]),
                "unsupported_point_effect")
        require(type(self.rollout_scope) is tuple and len(set(self.rollout_scope)) == len(self.rollout_scope), "invalid_rollout_scope")
        for scope in self.rollout_scope:
            sha256(scope)

    @property
    def policy_digest(self):
        return digest(asdict(self))

    @property
    def fallback(self):
        return contract_for(self.point_id).fallback

    def threshold(self, choice):
        return dict(self.thresholds_by_class).get(choice, dict(self.thresholds_by_class)["default"])


@dataclass(frozen=True)
class PointResolution:
    point_id: str
    mode: str
    effective: Effect
    proposed: Effect | None
    reason: str
    policy_digest: str
    gate_digest: str | None
    correction: bool = False
    scope_digest: str | None = None

    def to_record(self):
        # Values can contain retained private source text or identifiers. The
        # journal stores only action names and bindings, never those values.
        return {"point_id": self.point_id, "mode": self.mode,
                "effective_action": self.effective.action,
                "proposed_action": self.proposed.action if self.proposed else None,
                "reason": self.reason, "policy_digest": self.policy_digest,
                "gate_digest": self.gate_digest, "correction": self.correction, "scope_digest": self.scope_digest}


@dataclass(frozen=True)
class _Release:
    rule: PointRule
    bundle: object
    evidence: object
    approval: object
    gate: PointGate
    scope_digest: str
    consumer_ready: bool


class PointPolicyBook:
    """Trusted-host release state. Each transition is journaled before it applies.

    Authorization is delegated to the owner-provided operator verifier; there is
    no approval flag, public config deserializer, or classifier-driven promotion.
    The journal must durably retain transitions. Restart defaults to off until
    the host revalidates the complete matched bundle/evidence/approval tuple.
    """
    def __init__(self, *, journal, authorize_operator):
        require(callable(journal) and getattr(journal, "durable", False), "durable_receipt_required")
        require(callable(authorize_operator), "operator_verifier_required")
        self._journal, self._authorize_operator = journal, authorize_operator
        self._rules, self._releases, self._history = {}, {}, {}
        self._lock = threading.RLock()

    def rule(self, point_id):
        contract_for(point_id)
        with self._lock:
            return self._rules.get(point_id, PointRule(point_id))

    def configure_observer(self, rule):
        require(isinstance(rule, PointRule) and rule.mode != "enforce", "point_gate_required")
        with self._lock:
            require(self.rule(rule.point_id).mode != "enforce", "use_recorded_rollback")
            self._journal({"kind": "point_policy", "operation": "observer", **asdict(rule), "policy_digest": rule.policy_digest})
            self._rules[rule.point_id] = rule

    def promote(self, rule, *, bundle, evidence, approval, scope_digest, consumer_ready=False, now=None, _operation="promote", _reason=None):
        now = time.time() if now is None else now
        require(rule.mode == "enforce" and scope_digest in rule.rollout_scope and bool(rule.allowed_effects), "invalid_release_scope")
        verdict = inspect_release(evidence, approval, point_id=rule.point_id, bundle=bundle,
            policy_digest=rule.policy_digest, scope_digest=scope_digest, now=now, consumer_ready=consumer_ready)
        require(verdict["qualified"], "release_not_qualified")
        require(self._authorize_operator(approval), "operator_not_authorized")
        client_policy = PointPolicy("enforce", rule.threshold("default"), rule.timeout_seconds,
                                    effect_thresholds=tuple((key, value) for key, value in rule.thresholds_by_class if key != "default"))
        gate = PointGate(rule.point_id, evidence.contract_digest, bundle.model_digest, bundle.calibration_digest,
            client_policy.policy_digest,
            evidence.evidence_digest, min(evidence.expires_at, approval.expires_at), ("recommend",))
        release = _Release(rule, bundle, evidence, approval, gate, scope_digest, consumer_ready)
        with self._lock:
            self._journal({"kind": "point_policy", "operation": _operation, "point_id": rule.point_id,
                "policy_digest": rule.policy_digest, "gate_digest": gate.gate_digest,
                "evidence_digest": evidence.evidence_digest, "approval_digest": approval.approval_digest,
                "scope_digest": scope_digest, "bundle": asdict(bundle), "recorded_at": now,
                **({"reason": _reason} if _reason else {})})
            self._rules[rule.point_id] = rule
            self._releases[rule.point_id] = release
            self._history[(rule.point_id, gate.gate_digest)] = release
        return gate

    def inspect(self, point_id, *, scope_digest, now=None):
        with self._lock:
            rule = self.rule(point_id)
            release = self._releases.get(point_id)
            reasons = []
            if release is None:
                reasons = ["candidate_evidence_missing", "operator_approval_required"]
            else:
                reasons = inspect_release(release.evidence, release.approval, point_id=point_id,
                    bundle=release.bundle, policy_digest=rule.policy_digest, scope_digest=scope_digest,
                    now=time.time() if now is None else now, consumer_ready=release.consumer_ready)["reasons"]
                if not self._authorize_operator(release.approval):
                    reasons.append("operator_authorization_revoked")
            return {"point_id": point_id, "mode": rule.mode, "policy_digest": rule.policy_digest,
                    "fallback": rule.fallback, "owner_status": OWNER_STATUS[point_id],
                    "qualified": not reasons and rule.mode == "enforce", "reasons": reasons,
                    "gate_digest": release.gate.gate_digest if release else None}

    def client_settings(self, point_id, *, scope_digest, now=None):
        with self._lock:
            rule = self.rule(point_id)
            threshold = rule.threshold("default")
            effects = tuple((key, value) for key, value in rule.thresholds_by_class if key != "default")
            if rule.mode != "enforce":
                return PointPolicy(rule.mode, threshold, rule.timeout_seconds, effect_thresholds=effects), None, None
            status = self.inspect(point_id, scope_digest=scope_digest, now=now)
            if not status["qualified"]:
                return PointPolicy("off", threshold, rule.timeout_seconds, effect_thresholds=effects), None, None
            release = self._releases[point_id]
            return PointPolicy("enforce", threshold, rule.timeout_seconds, release.gate.gate_digest, effects), release.gate, release.bundle

    def rollback(self, point_id, *, reason, mode="shadow", prior_gate_digest=None, now=None):
        require(reason in ROLLBACK_REASONS, "invalid_rollback_reason")
        require(mode in {"off", "shadow"}, "invalid_rollback_mode")
        with self._lock:
            current = self.rule(point_id)
            prior = self._history.get((point_id, prior_gate_digest)) if prior_gate_digest else None
            require(prior_gate_digest is None or prior is not None, "unknown_evaluated_bundle")
            # Restoring a prior release always reruns expiry, provenance and
            # operator authority checks, and restores all bundle pins together.
            if prior is not None:
                return self.promote(prior.rule, bundle=prior.bundle, evidence=prior.evidence,
                    approval=prior.approval, scope_digest=prior.scope_digest,
                    consumer_ready=prior.consumer_ready, now=now, _operation="rollback", _reason=reason)
            rule = replace(current, mode=mode)
            self._journal({"kind": "point_policy", "operation": "rollback", "point_id": point_id,
                "mode": mode, "reason": reason, "previous_policy_digest": current.policy_digest,
                "policy_digest": rule.policy_digest})
            self._rules[point_id] = rule
            self._releases.pop(point_id, None)
            return rule

    def resolve(self, point_id, *, outcome, facts, incumbent, scope_digest, correction=None, now=None):
        """Compose a closed recommendation with floors; never invoke its effect."""
        with self._lock:
            rule = self.rule(point_id)
            status = self.inspect(point_id, scope_digest=scope_digest, now=now)
            release = self._releases.get(point_id)
            if rule.mode == "enforce" and (release is None or release.scope_digest != scope_digest
                                           or scope_digest not in rule.rollout_scope):
                return PointResolution(point_id, rule.mode, incumbent, None, "outside_rollout_scope",
                                       rule.policy_digest, None, correction is not None, scope_digest)
            receipt = outcome.receipt if outcome is not None else None
            choice = correction if correction is not None else (receipt or {}).get("selected")
            proposed = None
            reason = "off" if rule.mode == "off" else "incumbent_fallback"
            if rule.mode != "off" and choice is not None and receipt is not None:
                valid = (receipt.get("point_id") == point_id and receipt.get("scope_digest") == scope_digest
                         and receipt.get("contract_digest") == contract_for(point_id).contract_digest
                         and choice in receipt.get("live_options", []))
                require(valid, "correction_or_receipt_binding_mismatch")
                probability = (receipt.get("distribution") or {}).get(choice, 0)
                if choice != "unclear" and (correction is not None or probability >= rule.threshold(choice)):
                    # DP06's intent question is inverse to the danger questions.
                    normalized = {"yes": "no", "no": "yes"}.get(choice, choice) if point_id == "DP06" and receipt.get("question_id") == "intent" else choice
                    proposed = apply_floor(point_id, normalized, facts)
                    reason = "shadow_observation" if rule.mode == "shadow" else "advisory_only"
            effective = incumbent
            if rule.mode == "enforce":
                # Classifier failures still use the point-specific deterministic
                # fallback (ask, wrap, pause, retain, authorized default, etc.).
                # In particular, an active mandatory outbound guard cannot send
                # simply because a node/receipt is unavailable or expired.
                if point_id == "DP12":
                    require(facts.mode == rule.mode, "point_mode_mismatch")
                effective = apply_floor(point_id, "unclear", facts)
                if not status["qualified"]:
                    reason = status["reasons"][0]
                elif outcome is None or not outcome.receipt_persisted or outcome.route != "qualified_recommendation":
                    reason = "qualified_durable_decision_required"
                elif proposed is not None and proposed.action in rule.allowed_effects:
                    release = self._releases[point_id]
                    expected = {"model_digest": release.bundle.model_digest,
                                "calibration_digest": release.bundle.calibration_digest,
                                "service_digest": release.bundle.service_digest,
                                "point_gate_digest": release.gate.gate_digest}
                    if all(receipt.get(key) == value for key, value in expected.items()):
                        effective, reason = proposed, proposed.reason
                    else:
                        reason = "decision_bundle_mismatch"
            return PointResolution(point_id, rule.mode, effective, proposed, reason,
                                   rule.policy_digest, status["gate_digest"], correction is not None, scope_digest)


def production_status():
    """No empirical experiment or owner qualification is fabricated by tests."""
    return {point: {"point_id": point, "default_mode": "off", "owner_status": OWNER_STATUS[point],
            "production_enforcement": False, "fallback": contract.fallback,
            "pending": ["real_candidate", "point_calibration", "frozen_holdout", "live_shadow",
                        "safety_latency_budget_evidence", "operator_approval", "qualified_owner_consumer"]}
            for point, contract in REGISTRY.items()}
