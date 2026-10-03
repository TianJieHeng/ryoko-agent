"""Pure decision-specific floors. Facts are supplied by owners, never classifiers.

These adapters cannot dispatch, send, grant, edit, increase a budget or erase
source evidence. A host must apply the resulting *narrower* effect through its
existing authorized consumer. Unintegrated owners remain explicitly pending.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.decisions.contracts import require


@dataclass(frozen=True)
class Effect:
    action: str
    value: object = None
    reason: str = "accepted_within_floor"


@dataclass(frozen=True)
class IngressFacts:
    direct_message: bool
    direct_question: bool
    ambient_opt_in: bool
    incumbent: str = "allow"


def ingress(choice, facts):
    if facts.direct_message or facts.direct_question or not facts.ambient_opt_in:
        return Effect("allow", reason="direct_or_non_opted_in")
    return Effect("skip" if choice in {"other", "ambient"} else "allow")


@dataclass(frozen=True)
class BusyFacts:
    configured_policy: str
    explicit_command: str | None = None
    cancellation_authorized: bool = False
    delivery_status: str = "queued"


def busy(choice, facts):
    require(facts.configured_policy in {"interrupt", "steer", "queue"}, "invalid_busy_policy")
    if facts.explicit_command is not None:
        return Effect("preserve_command", (facts.explicit_command, facts.delivery_status), "command_semantics")
    selected = choice if choice in {"interrupt", "steer", "queue"} else facts.configured_policy
    if selected == "interrupt" and not facts.cancellation_authorized:
        selected = facts.configured_policy if facts.configured_policy != "interrupt" else "queue"
    return Effect(selected, facts.delivery_status)


@dataclass(frozen=True)
class ModelFacts:
    authorized_routes: tuple[str, ...]
    private_egress_routes: tuple[str, ...]
    within_budget_routes: tuple[str, ...]
    capable_routes: tuple[str, ...]
    incumbent: str = "main"


def model_route(choice, facts):
    allowed = (set(facts.authorized_routes) & set(facts.private_egress_routes)
               & set(facts.within_budget_routes) & set(facts.capable_routes))
    selected = choice if choice in allowed else facts.incumbent
    return Effect("route", selected) if selected in allowed else Effect("pause", reason="no_permitted_model_route")


@dataclass(frozen=True)
class CandidateFacts:
    authorized_candidates: tuple[str, ...]
    incumbent: tuple[str, ...]
    catalog_live: bool
    personal_candidates: tuple[str, ...] = ()
    is_primary: bool = False


def candidates(choice, facts):
    allowed = set(facts.authorized_candidates)
    if not facts.is_primary:
        allowed -= set(facts.personal_candidates)
    fallback = tuple(item for item in facts.incumbent if item in allowed)
    if not facts.catalog_live or choice not in allowed:
        return Effect("select", fallback, "authorized_full_fallback")
    return Effect("select", (choice,))


@dataclass(frozen=True)
class RecallFacts:
    is_ryoko_primary: bool
    project_authorized: bool
    personal_authorized: bool
    incumbent: str = "none"


def recall(choice, facts):
    allowed = {"none"}
    if facts.project_authorized:
        allowed.add("project")
    if facts.is_ryoko_primary and facts.personal_authorized:
        allowed.add("personal")
        if facts.project_authorized:
            allowed.add("both")
    selected = choice if choice in allowed else facts.incumbent
    return Effect("recall", selected if selected in allowed else "none")


@dataclass(frozen=True)
class RiskFacts:
    deterministic_verdict: str
    mandatory_confirmation: bool
    current_judge_permitted: bool = False


def risk(choice, facts):
    require(facts.deterministic_verdict in {"allow", "ask", "block"}, "invalid_risk_floor")
    if facts.deterministic_verdict == "block":
        return Effect("block", reason="deterministic_block")
    if facts.mandatory_confirmation or facts.deterministic_verdict == "ask":
        return Effect("ask", reason="mandatory_confirmation")
    if choice == "no":
        return Effect("allow")
    if choice == "yes":
        return Effect("ask", reason="classifier_tightens")
    return Effect("current_judge" if facts.current_judge_permitted else "ask", reason="uncertain_risk")


@dataclass(frozen=True)
class UntrustedFacts:
    source: str
    source_digest: str
    credential_lure: bool = False


def untrusted(choice, facts):
    # The original bytes remain available even under quarantine; classification
    # cannot promote them into instructions or delete receipt/evidence bindings.
    treatment = "quarantine" if facts.credential_lure else "flag" if choice in {"instructions", "mixed"} else "wrap"
    return Effect(treatment, (("source", facts.source), ("source_digest", facts.source_digest),
                              ("trust", "untrusted")))


@dataclass(frozen=True)
class RegexFacts:
    regex_hit: bool
    incumbent_blocked: bool
    allowlisted_descriptive_class: bool = False
    relaxation_approved: bool = False


def directive(choice, facts):
    if not facts.regex_hit:
        return Effect("incumbent", facts.incumbent_blocked, "no_regex_hit_no_classification")
    relaxed = (choice == "describes" and facts.allowlisted_descriptive_class and facts.relaxation_approved)
    return Effect("allow" if relaxed else "block", reason="proven_descriptive_class" if relaxed else "regex_floor")


@dataclass(frozen=True)
class VerifyFacts:
    deterministic_validation_passed: bool
    required_tests_passed: bool
    receipts_present: bool
    artifact_present: bool
    incumbent: str = "pause"


def verify(choice, facts):
    complete = (facts.deterministic_validation_passed and facts.required_tests_passed
                and facts.receipts_present and facts.artifact_present)
    if choice == "verified_done":
        return Effect("done" if complete else "pause", reason="validated" if complete else "missing_completion_evidence")
    routes = {"continue": "continue", "blocked": "blocked", "needs_human": "needs_human"}
    return Effect(routes.get(choice, "pause"))


@dataclass(frozen=True)
class ReviewFacts:
    counter_due: bool
    route_b_ready: bool
    individual_review: bool
    own_scope: str
    budget_available: bool


def review(choice, facts):
    # A review has the owner's bounded scope, even if a classifier suggests a
    # personal preference. It never inherits the primary personal harness.
    due = facts.counter_due or (facts.route_b_ready and choice in {"durable_fact", "preference_correction", "reusable_procedure"})
    return Effect("review" if due and facts.budget_available else "defer", (facts.own_scope, "no_personal_inheritance"))


@dataclass(frozen=True)
class MonitorFacts:
    authorized_observation: bool
    incumbent: str
    needs_reply: bool = False


def monitor(choice, facts):
    if not facts.authorized_observation:
        return Effect("ignore", reason="observation_not_authorized")
    action = choice if choice in {"now", "digest", "ignore"} else facts.incumbent
    require(action in {"now", "digest", "ignore"}, "invalid_monitor_route")
    return Effect(action, (("needs_reply", facts.needs_reply), ("send_reply", False)))


@dataclass(frozen=True)
class OutboundFacts:
    original_text: str
    deterministic_hold: bool
    mode: str
    guard_available: bool
    mandatory_guard: bool = False
    outage_policy: str = "hold"


def outbound(choice, facts):
    require(facts.mode in {"off", "shadow", "advisory", "enforce"}, "invalid_mode")
    require(facts.outage_policy in {"hold", "deterministic_fallback"}, "invalid_outage_policy")
    hold = facts.deterministic_hold
    if facts.mode == "enforce":
        hold |= choice in {"credential", "wrong_recipient", "cross_project"}
        hold |= facts.mandatory_guard and (not facts.guard_available or choice == "unclear") and facts.outage_policy == "hold"
    return Effect("hold" if hold else "current_send_policy", facts.original_text)


@dataclass(frozen=True)
class ProgressFacts:
    deterministic_repeat_stop: bool
    budget_remaining: bool
    bounded_observations: bool
    partial_result: str


def progress(choice, facts):
    if facts.deterministic_repeat_stop or not facts.budget_remaining:
        return Effect("halt", facts.partial_result, "deterministic_repeat_or_budget_floor")
    routes = {"stalled": "nudge", "blocked": "halt", "progress": "continue"}
    return Effect(routes.get(choice, "pause") if facts.bounded_observations else "pause", facts.partial_result)


@dataclass(frozen=True)
class AnchorFacts:
    exact_anchors: tuple[str, ...]
    protected_identifiers: tuple[str, ...]
    approval_bindings: tuple[str, ...]
    source_id: str


def anchors(choice, facts):
    protected = set(facts.exact_anchors) | set(facts.protected_identifiers) | set(facts.approval_bindings)
    if choice in {"decision", "constraint", "approval", "identifier"}:
        protected.add(facts.source_id)
    return Effect("retain", tuple(sorted(protected)))


@dataclass(frozen=True)
class AssigneeFacts:
    live_authorized_roster: tuple[str, ...]
    default_assignee: str | None
    roster_live: bool


def assignee(choice, facts):
    if not facts.roster_live:
        return Effect("human", reason="stale_roster")
    selected = choice if choice in facts.live_authorized_roster else facts.default_assignee
    return (Effect("assign", selected) if selected in facts.live_authorized_roster
            else Effect("human", reason="no_authorized_assignee"))


@dataclass(frozen=True)
class PlannerFacts:
    authorized_tools: tuple[str, ...]
    verified_tools: tuple[str, ...]
    bridge_authorized: bool
    catalog_live: bool
    cache_boundary: bool


def planner(choice, facts):
    if not facts.catalog_live or not facts.cache_boundary or not facts.bridge_authorized:
        return Effect("authorized_full", facts.authorized_tools, "planner_boundary_or_catalog_unready")
    selected = () if choice == "no_tools" else tuple(tool for tool in facts.verified_tools if tool in facts.authorized_tools)
    if choice not in {"no_tools", "needs_tools"} or (choice == "needs_tools" and not selected):
        return Effect("authorized_full", facts.authorized_tools, "planner_unclear")
    return Effect("bundle", selected, "authorized_bridge_always_present")


ADAPTERS = {
    "DP01": (IngressFacts, ingress), "DP02": (BusyFacts, busy), "DP03": (ModelFacts, model_route),
    "DP04": (CandidateFacts, candidates), "DP05": (RecallFacts, recall), "DP06": (RiskFacts, risk),
    "DP07": (UntrustedFacts, untrusted), "DP08": (RegexFacts, directive), "DP09": (VerifyFacts, verify),
    "DP10": (ReviewFacts, review), "DP11": (MonitorFacts, monitor), "DP12": (OutboundFacts, outbound),
    "DP13": (ProgressFacts, progress), "DP14": (AnchorFacts, anchors), "DP15": (AssigneeFacts, assignee),
    "DP16": (PlannerFacts, planner),
}


def apply_floor(point_id, choice, facts):
    require(point_id in ADAPTERS, "unknown_point")
    expected, adapter = ADAPTERS[point_id]
    require(type(facts) is expected, "wrong_point_facts")
    return adapter(choice, facts)
