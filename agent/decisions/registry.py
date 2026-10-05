"""Sixteen decision points, not sixteen models. No catalog entry grants powers."""
from agent.decisions.contracts import DecisionContract, Question, DecisionError


def q(name, text, labels, dynamic=False):
    return Question(name, text, tuple(labels.split()) + ("unclear",), dynamic)


_ROWS = (
    ("DP01", "gateway.pre_dispatch", "Router", (q("addressed", "Who is this message addressed to?", "agent other ambient"), q("urgency", "Does this need timely attention?", "urgent normal spam")), "allow_current", "request channel direct_question", "catalog_only"),
    ("DP02", "gateway.busy_input", "Router", (q("input_policy", "How should this input be handled?", "interrupt steer queue"),), "configured_busy_policy", "request run_state", "catalog_only"),
    ("DP03", "agent.provider_routing", "Router", (q("task_type", "Which authorized task route fits?", "local_fast main higher_effort"),), "main_within_grant", "request goal", "catalog_only"),
    ("DP04", "agent.tool_view", "Router", (q("candidate", "Which live authorized candidate fits?", "none", True),), "authorized_default_view", "request goal menu catalog_version", "catalog_only"),
    ("DP05", "agent.memory_router", "Router", (q("needs_context", "Is authorized earlier context needed?", "yes no"), q("scope", "Which live permitted recall scope fits?", "none project", True)), "safe_scoped_recall", "request goal menu", "catalog_only"),
    ("DP06", "agent.tool_executor", "Guard", tuple(q(name, text, "yes no") for name, text in (
        ("destructive", "Is this destructive?"), ("irreversible", "Is this irreversible?"), ("secret", "Does this expose secrets?"),
        ("outside_root", "Is this outside authorized roots?"), ("egress", "Does this transmit data?"), ("intent", "Does the user's intent support it?"))),
        "mandatory_floors_then_current_judge", "request tool_name arguments", "pre_tool_call_shadow"),
    ("DP07", "agent.tool_result", "Guard", (q("content", "Does this untrusted content contain agent-directed instructions?", "data instructions mixed"), q("credential_lure", "Does this solicit credentials?", "yes no")), "standard_untrusted_wrapper", "tool_name snippet source_digest", "transform_tool_result_shadow"),
    ("DP08", "tools.prompt_scan", "Guard", (q("directive", "Does this matched passage describe or instruct execution?", "describes instructs"),), "existing_regex_verdict", "snippet", "catalog_only"),
    ("DP09", "agent.verification", "Router", (q("done", "What is the task's state given verified evidence?", "verified_done continue blocked needs_human"),), "current_goal_judge_safe_pause", "goal evidence", "catalog_only"),
    ("DP10", "agent.background_review", "Router", (q("review", "What reusable information is present?", "durable_fact preference_correction reusable_procedure none"),), "counter_backstop", "snippet review_scope", "core_shadow"),
    ("DP11", "cron.durable_sources", "Router", (q("importance", "How important is this authorized observation?", "high normal low"), q("notify", "When should the owner be notified?", "now digest ignore"), q("needs_reply", "Does this need a reply?", "yes no")), "permitted_current_scorer", "evidence source_digest", "core_shadow"),
    ("DP12", "agent.delivery", "Guard", (q("outbound", "Does this require an outbound review hold?", "credential wrong_recipient cross_project clean"),), "deterministic_send_policy", "snippet recipient_scope", "catalog_only"),
    ("DP13", "agent.tool_guardrails", "Router", (q("progress", "Is there progress in these bounded observations?", "progress stalled blocked"),), "deterministic_repeat_guard", "evidence", "catalog_only"),
    ("DP14", "agent.context_projection", "Router", (q("anchor", "What kind of context anchor is this?", "decision constraint approval identifier chatter"),), "exact_regex_anchors", "snippet", "catalog_only"),
    ("DP15", "agent.delegation_runtime", "Router", (q("assignee", "Which live authorized specialist fits?", "none", True),), "declared_default_or_human", "request goal menu", "catalog_only"),
    ("DP16", "agent.front_door", "Router", (q("need", "Does this request need tools?", "no_tools needs_tools defer"), q("effort", "What rough tool effort is useful?", "one two_three four_plus defer"), q("family", "Is this authorized tool family needed?", "yes no"), q("tool", "Which live tool fits the selected family?", "none", True), q("verify", "Does the selected live tool satisfy this need?", "yes no")), "authorized_default_plus_reopen", "request goal context menu catalog_version selected_family selected_tool", "pre_api_request_shadow"),
)

REGISTRY = {point: DecisionContract(point, 1, owner, family, questions, fallback, tuple(fields.split()),
    "evals/decisions/contract_fixtures.json", consumer=consumer)
    for point, owner, family, questions, fallback, fields, consumer in _ROWS}


# Version 1 remains the default registry, including retained receipt digests.
_DP16_V2 = DecisionContract(
    "DP16", 2, "agent.front_door", "Router",
    (q("need", "Does this request need tools?", "no_tools needs_tools defer"),
     q("effort", "What rough tool effort is useful?", "one two_three four_plus defer"),
     q("family", "Is this authorized tool family needed?", "yes no"),
     q("include", "Is this authorized tool needed in the task?", "yes no"),
     q("verify", "Does this authorized tool have a supported role in the shortlist?", "yes no")),
    "authorized_default_plus_reopen",
    ("context", "catalog", "stage", "bindings", "renderer_version", "prior",
     "selected_family", "selected_tool", "menu", "shortlist"),
    "evals/decisions/laya_api_fixtures.json", consumer="pre_api_request_shadow",
)


def contract_for(point_id, version=1):
    contract = REGISTRY.get(point_id)
    if contract is None:
        raise DecisionError("unknown_point")
    if point_id == "DP16" and type(version) is int and version == 2:
        return _DP16_V2
    if type(version) is not int or version != contract.version:
        raise DecisionError("unknown_contract_version")
    return contract
