"""Offline, closed DP16 v2 adapter for the documented LAYA choice subset.

The fixtures are synthetic, not captured deployment evidence. Returned aliases do
not attest a loaded checkpoint: configured release digests are local bindings.
No HTTP, credential lookup, private-data bypass, or model-provider fallback lives
here. A bound batch belongs to exactly one exchange, including its unique qids.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import re
import uuid

from agent.decisions.contracts import (
    DecisionError, DecisionRequest, DecisionResult, ModelBundle, StatePacket, canonical,
    digest, number, require, sha256,
)
from agent.decisions.registry import contract_for

MAX_STATE_BYTES = 12 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_QUESTIONS = 64
MAX_JSON_DEPTH = 12
RENDERER_VERSION = "dp16-systemone-v1"
_TARGET_FIELDS = frozenset({"selected_family", "selected_tool", "menu", "shortlist"})
_BASE_FIELDS = frozenset({"renderer_version", "context", "catalog", "stage", "bindings", "prior"})
_STAGES = {"need": 1, "effort": 1, "family": 1, "include": 2, "verify": 3}
_ALIAS = {"family": re.compile(r"f_[0-9a-f]{24}"), "tool": re.compile(r"t_[0-9a-f]{24}")}


@dataclass(frozen=True)
class BatchUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self):
        for value in (self.input_tokens, self.output_tokens):
            require(type(value) is int and 0 <= value <= 2**31 - 1, "invalid_usage")

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens

    def to_record(self):
        return asdict(self)


@dataclass(frozen=True)
class WireBinding:
    wire_qid: str
    request: DecisionRequest = field(repr=False)
    target_json: str = field(repr=False)


@dataclass(frozen=True)
class LayaBatch:
    """Immutable request correlation retained only for one physical exchange."""
    bindings: tuple[WireBinding, ...] = field(repr=False)
    body: bytes = field(repr=False)
    state_json: str = field(repr=False)
    model_alias: str | None
    stage: int
    batch_id: str

    @property
    def requests(self):
        return tuple(binding.request for binding in self.bindings)

    @property
    def wire_ids(self):
        return tuple(binding.wire_qid for binding in self.bindings)

    @property
    def batch_digest(self):
        return digest({"body_sha256": hashlib.sha256(self.body).hexdigest(),
                       "request_ids": [request.request_id for request in self.requests]})


@dataclass(frozen=True)
class LayaBatchResult:
    # One batch usage record: never multiply tokens by the answer count.
    records: tuple[dict, ...] = field(repr=False)
    results: tuple[DecisionResult, ...]
    usage: BatchUsage


def _alias(value, kind):
    require(type(value) is str and _ALIAS[kind].fullmatch(value) is not None, "invalid_catalog_alias")
    return value


def _model_alias(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) is not None,
            "invalid_model_alias")
    return value


def _load_json(raw):
    """Bound nesting before the recursive decoder, and reject duplicate keys."""
    require(type(raw) is bytes, "invalid_response_bytes")
    require(len(raw) <= MAX_BODY_BYTES, "response_too_large")
    try:
        source = raw.decode("utf-8")
        depth, quoted, escaped = 0, False, False
        for char in source:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                require(depth <= MAX_JSON_DEPTH, "response_too_deep")
            elif char in "]}":
                depth -= 1

        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "duplicate_json_key")
                result[key] = value
            return result

        def invalid_constant(_):
            raise DecisionError("invalid_json")

        return json.loads(source, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    except DecisionError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
        raise DecisionError("invalid_json") from None


def _catalog_index(catalog, scope, stage):
    if type(catalog) is dict and "projection" in catalog:
        from agent.decisions.laya_prompts import _summary_catalog_aliases
        _summary_catalog_aliases(catalog, stage)
        require(catalog["scope_digest"] == scope, "catalog_scope_mismatch")
        return {_alias(family["id"], "family"): family for family in catalog["families"]}, {}

    require(type(catalog) is dict and set(catalog) == {
        "version", "scope_digest", "policy_digest", "tool_view_revision", "families", "tools", "bridges"},
        "invalid_catalog")
    for key in ("version", "scope_digest", "policy_digest", "tool_view_revision"):
        sha256(catalog[key])
    require(catalog["scope_digest"] == scope, "catalog_scope_mismatch")
    families, tools = catalog["families"], catalog["tools"]
    require(type(families) is list and len(families) <= 16 and type(tools) is list, "catalog_bound")
    family_index, tool_index = {}, {}
    for family in families:
        require(type(family) is dict and set(family) == {"id", "description", "tools"}, "invalid_catalog")
        alias = _alias(family["id"], "family")
        require(alias not in family_index and type(family["tools"]) is list
                and type(family["description"]) is str and bool(family["description"]), "invalid_catalog")
        for tool in family["tools"]:
            _alias(tool, "tool")
        require(len(family["tools"]) == len(set(family["tools"])), "invalid_catalog")
        family_index[alias] = family
    for tool in tools:
        require(type(tool) is dict and set(tool) == {
            "id", "family", "description", "source_digest", "schema_digest", "effect_summary", "input_hints"},
            "invalid_catalog")
        alias = _alias(tool["id"], "tool")
        family = _alias(tool["family"], "family")
        require(alias not in tool_index and family in family_index
                and alias in family_index[family]["tools"], "invalid_catalog")
        require(type(tool["description"]) is str and bool(tool["description"])
                and type(tool["effect_summary"]) is str
                and tool["effect_summary"] in {"unknown", "read_only", "may_write"}
                and type(tool["input_hints"]) is list and len(tool["input_hints"]) <= 8
                and all(type(hint) is str for hint in tool["input_hints"]), "invalid_catalog")
        sha256(tool["source_digest"])
        sha256(tool["schema_digest"])
        tool_index[alias] = tool
    require({tool for family in families for tool in family["tools"]} == set(tool_index), "invalid_catalog")
    # A descriptor cannot masquerade as membership in a second family.
    require(sum(len(family["tools"]) for family in families) == len(tool_index), "invalid_catalog")
    require(type(catalog["bridges"]) is list
            and all(type(bridge) is str for bridge in catalog["bridges"]), "invalid_catalog")
    require(len(set(catalog["bridges"])) == len(catalog["bridges"]), "invalid_catalog")
    return family_index, tool_index


def _target_binding(question_id, state, families, tools):
    target = {key: state[key] for key in _TARGET_FIELDS if key in state}
    required = {"family": {"selected_family"}, "include": {"selected_family", "selected_tool"},
                "verify": {"selected_family", "selected_tool", "shortlist"}}.get(question_id, set())
    allowed = required | ({"menu"} if required else set())
    require(required <= set(target) <= allowed, "invalid_question_binding")
    if not required:
        return target
    family = _alias(target["selected_family"], "family")
    require(family in families, "unknown_family_alias")
    if "member_count" in families[family]:
        require(question_id == "family" and set(target) == {"selected_family"}, "invalid_question_binding")
        return target
    if "selected_tool" in target:
        tool = _alias(target["selected_tool"], "tool")
        require(tool in tools and tools[tool]["family"] == family, "unknown_tool_alias")
    for key in ("menu", "shortlist"):
        if key not in target:
            continue
        values = target[key]
        bound = 12 if key == "shortlist" else 32
        require(type(values) is list and 0 < len(values) <= bound, "invalid_question_binding")
        for value in values:
            _alias(value, "tool")
        require(len(set(values)) == len(values) and set(values) <= set(tools), "unknown_tool_alias")
        if key == "menu":
            require(set(values) <= set(families[family]["tools"]), "invalid_question_binding")
        if "selected_tool" in target:
            require(target["selected_tool"] in values, "invalid_question_binding")
    return target



def _validate_prior(common, bindings, families, tools):
    stage, prior = common["stage"], common["prior"]
    required = {1: set(), 2: {"need", "effort", "selected_families"},
                3: {"need", "effort", "selected_families", "selected_tools"}}[stage]
    require(type(prior) is dict and set(prior) == required, "invalid_stage_prior")
    if stage == 1:
        return
    require(prior["need"] == "needs_tools" and type(prior["effort"]) is str
            and prior["effort"] in {"one", "two_three", "four_plus"}, "invalid_stage_prior")
    selected_families = prior["selected_families"]
    require(type(selected_families) is list and 0 < len(selected_families) <= 16, "invalid_stage_prior")
    for alias in selected_families:
        _alias(alias, "family")
    require(len(set(selected_families)) == len(selected_families)
            and set(selected_families) <= set(families), "invalid_stage_prior")
    targets = [json.loads(binding.target_json) for binding in bindings]
    require(all(target["selected_family"] in selected_families for target in targets), "invalid_stage_prior")
    if stage == 3:
        selected_tools = prior["selected_tools"]
        require(type(selected_tools) is list and 0 < len(selected_tools) <= 12, "invalid_stage_prior")
        for alias in selected_tools:
            _alias(alias, "tool")
        require(len(set(selected_tools)) == len(selected_tools) and set(selected_tools) <= set(tools),
                "invalid_stage_prior")
        require(all(tools[alias]["family"] in selected_families for alias in selected_tools), "invalid_stage_prior")
        require(all(sum(tools[alias]["family"] == family for alias in selected_tools) <= 4
                    for family in selected_families), "shortlist_family_bound")
        require(all(target["shortlist"] == selected_tools for target in targets), "batch_shortlist_mismatch")

def encode_laya_batch(requests, *, model_alias=None):
    """Encode one independent stage; the caller owns admission and deadlines."""
    require(type(requests) is tuple and 1 <= len(requests) <= MAX_QUESTIONS, "invalid_batch_size")
    require(all(type(request) is DecisionRequest and type(request.state_packet) is StatePacket
                for request in requests), "invalid_batch_request")
    # Refuse before reading/rendering private state; there is intentionally no
    # boolean/object escape hatch. Future private support needs qualification.
    require(all(request.state_packet.classification in {"synthetic", "public"} for request in requests),
            "privacy_not_qualified")
    if model_alias is not None:
        _model_alias(model_alias)
    contract = contract_for("DP16", 2)
    require(all(request.point_id == "DP16" and type(request.contract_version) is int
                and request.contract_version == 2 and request.contract_digest == contract.contract_digest
                for request in requests), "batch_contract_mismatch")
    require(len({request.request_id for request in requests}) == len(requests), "duplicate_batch_request")
    requests = tuple(replace(request, live_options=tuple(request.live_options)) for request in requests)
    first = requests[0]
    identity = (first.state_packet.scope_digest, first.state_packet.classification, first.deadline)
    require(all((request.state_packet.scope_digest, request.state_packet.classification, request.deadline) == identity
                for request in requests), "batch_scope_mismatch")
    from agent.decisions.laya_prompts import render_dp16_question, render_dp16_state
    shared, bindings, questions, seen = None, [], {}, set()
    batch_id = uuid.uuid4().hex
    for index, request in enumerate(requests):
        question = contract.question(request.question_id)
        require(request.live_options == question.options, "closed_options_changed")
        state = json.loads(request.state_packet.state_json)
        require(set(state) <= _BASE_FIELDS | _TARGET_FIELDS and _BASE_FIELDS <= set(state), "invalid_state_fields")
        require(len(request.state_packet.state_json.encode("utf-8")) <= MAX_STATE_BYTES, "state_too_large")
        require(state["renderer_version"] == RENDERER_VERSION and type(state["stage"]) is int
                and state["stage"] == _STAGES[question.question_id], "batch_stage_mismatch")
        require(state["bindings"] == {}, "invalid_question_binding")
        common = {key: state[key] for key in _BASE_FIELDS}
        common_json = canonical(common)
        if shared is None:
            shared = common_json
            families, tools = _catalog_index(state["catalog"], identity[0], state["stage"])
        require(common_json == shared, "batch_state_mismatch")
        target = _target_binding(question.question_id, state, families, tools)
        signature = (question.question_id, target.get("selected_family"), target.get("selected_tool"))
        require(signature not in seen, "duplicate_batch_question")
        seen.add(signature)
        wire_qid = f"s{state['stage']}_{question.question_id}_{index + 1:02d}_{batch_id}"
        rendered = render_dp16_question(question.question_id, binding=target)
        require(set(rendered) == {"type", "instructions", "criteria"}
                and rendered["type"] == "choice" and set(rendered["criteria"]) == set(request.live_options),
                "invalid_question_renderer")
        questions[wire_qid] = rendered
        bindings.append(WireBinding(wire_qid, request, canonical(target)))
    common = json.loads(shared)
    require(len(requests) <= {1: 18, 2: 32, 3: 12}[common["stage"]], "batch_stage_bound")
    _validate_prior(common, bindings, families, tools)
    if common["catalog"].get("projection") == "family_summaries_v1":
        # The planner may not omit a family or independently call only the most
        # convenient initial question. All summaries share one causal decision.
        require(seen == {("need", None, None), ("effort", None, None)} |
                {("family", alias, None) for alias in families}, "incomplete_family_stage")
    wire_bindings = {binding.wire_qid: json.loads(binding.target_json) for binding in bindings}
    state_json = render_dp16_state(common["context"], catalog=common["catalog"], stage=common["stage"],
                                   bindings=wire_bindings, prior=common["prior"])
    require(type(state_json) is str and len(state_json.encode("utf-8")) <= MAX_STATE_BYTES, "state_too_large")
    payload = {"state": state_json, "questions": questions}
    if model_alias is not None:
        payload["model"] = model_alias
    body = canonical(payload).encode("utf-8")
    require(len(body) <= MAX_BODY_BYTES, "request_too_large")
    return LayaBatch(tuple(bindings), body, state_json, model_alias, common["stage"], batch_id)


def decode_laya_batch(raw, batch, bundle, *, latency_ms):
    """Validate a conservative envelope and reconstruct locally bound records."""
    require(type(batch) is LayaBatch and type(bundle) is ModelBundle, "invalid_batch_binding")
    number(latency_ms, 0, 3600000, "invalid_latency")
    data = _load_json(raw)
    require(type(data) is dict and set(data) == {"model", "answers", "usage"}, "invalid_response_schema")
    alias = _model_alias(data["model"])
    require(batch.model_alias is None or alias == batch.model_alias, "model_alias_mismatch")
    answers, usage = data["answers"], data["usage"]
    require(type(answers) is dict and set(answers) == set(batch.wire_ids), "response_binding_mismatch")
    require(type(usage) is dict and set(usage) == {"input_tokens", "output_tokens"}, "invalid_usage")
    batch_usage = BatchUsage(**usage)
    records, results = [], []
    for binding in batch.bindings:
        request, answer = binding.request, answers[binding.wire_qid]
        require(type(answer) is dict and set(answer) == {"type", "choice", "probabilities", "confidence"}
                and answer["type"] == "choice", "invalid_answer_schema")
        distribution = answer["probabilities"]
        require(type(distribution) is dict and set(distribution) == set(request.live_options),
                "invalid_distribution_options")
        for probability in distribution.values():
            number(probability, 0, 1, "invalid_probability")
        require(abs(sum(distribution.values()) - 1) <= 1e-6, "invalid_distribution_sum")
        selected = answer["choice"]
        require(type(selected) is str and selected in request.live_options, "invalid_selection")
        maximum = max(distribution.values())
        require(distribution[selected] == maximum, "selection_not_argmax")
        number(answer["confidence"], 0, 1, "invalid_confidence")
        require(abs(answer["confidence"] - maximum) <= 1e-6, "confidence_mismatch")
        if sum(probability == maximum for probability in distribution.values()) != 1:
            selected = None
        source = request.to_record()
        record = {key: source[key] for key in ("request_id", "point_id", "contract_version", "contract_digest",
                                              "question_id", "input_digest", "scope_digest")}
        record.update(**asdict(bundle), distribution=distribution, selected=selected,
                      unclear=selected in (None, "unclear"), latency_ms=latency_ms)
        results.append(DecisionResult.validate(record, request, bundle))
        records.append(record)
    return LayaBatchResult(tuple(records), tuple(results), batch_usage)
