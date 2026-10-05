"""Synthetic-only closed protocol evidence; no service or loaded-model claims."""
from dataclasses import FrozenInstanceError, replace
import copy
import json
from pathlib import Path

import pytest

from agent.decisions.contracts import (
    DecisionError, DecisionRequest, DecisionResult, ModelBundle, StatePacket,
    canonical, digest,
)
from agent.decisions.laya_codec import (
    MAX_BODY_BYTES, BatchUsage, decode_laya_batch, encode_laya_batch,
)
from agent.decisions.laya_prompts import render_dp16_state
from agent.decisions.planner_context import build_planner_context
from agent.decisions.registry import REGISTRY, contract_for
from agent.decisions.state import build_state
from agent.decisions.tool_planner import LiveCatalog

SCOPE = "4" * 64
BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)
MODEL_ALIAS = "laya-rl-agent"


def catalog():
    names = ("list_files", "file_details", "read_clock")
    definitions = [{"type": "function", "function": {"name": name,
        "description": "Inspect authorized " + name, "parameters": {"type": "object", "properties": {}}}}
        for name in names]
    return LiveCatalog.from_authorized(definitions, scope_digest=SCOPE,
        families={"files": names[:2], "clock": names[2:]}, bridge_names=())


def requests(question_ids=("need", "family", "family"), *, stage=1, classification="synthetic"):
    live = catalog()
    family = live.family_alias("files")
    context = build_planner_context("List filenames and check details in the authorized project folder",
        scope_digest=SCOPE, classification=classification)
    prior = {} if stage == 1 else {"need": "needs_tools", "effort": "two_three", "selected_families": [family]}
    shortlist = [live.tool_alias("list_files"), live.tool_alias("file_details")]
    if stage == 3:
        prior["selected_tools"] = shortlist
    state = json.loads(render_dp16_state(context, catalog=live.descriptor_values, stage=stage, prior=prior))
    contract = contract_for("DP16", 2)
    result = []
    family_index = 0
    for index, question_id in enumerate(question_ids):
        values = copy.deepcopy(state)
        if question_id == "family":
            values["selected_family"] = live.family_alias(("files", "clock")[family_index])
            family_index += 1
        if question_id in {"include", "verify"}:
            values.update(selected_family=family, selected_tool=shortlist[index], menu=shortlist)
        if question_id == "verify":
            values["shortlist"] = shortlist
        packet = build_state("DP16", values, scope_digest=SCOPE,
                             classification=classification, contract_version=2)
        result.append(DecisionRequest("DP16", 2, contract.contract_digest, question_id, packet,
            contract.question(question_id).options, 2000000000.0, digest({"index": index, "input": packet.input_digest})))
    return tuple(result)


def response(batch):
    defaults = {"need": "needs_tools", "effort": "two_three", "family": "yes", "include": "yes", "verify": "yes"}
    return {"model": MODEL_ALIAS, "answers": {
        binding.wire_qid: {"type": "choice", "choice": defaults[binding.request.question_id],
            "probabilities": {option: float(option == defaults[binding.request.question_id])
                              for option in binding.request.live_options}, "confidence": 1.0}
        for binding in batch.bindings}, "usage": {"input_tokens": 200, "output_tokens": len(batch.bindings)}}


def decode(data, batch):
    # allow_nan=True deliberately permits malicious fixtures to reach the parser.
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return decode_laya_batch(raw, batch, BUNDLE, latency_ms=12.5)


def packet_change(request, **changes):
    values = json.loads(request.state_packet.state_json)
    values.update(changes)
    return replace(request, state_packet=StatePacket(canonical(values), request.state_packet.scope_digest,
                                                    request.state_packet.classification))


def test_explicit_v2_keeps_every_legacy_default_and_closed_questions():
    assert all(contract_for(point) is contract and contract_for(point, 1) is contract
               for point, contract in REGISTRY.items())
    v2 = contract_for("DP16", 2)
    assert v2.contract_digest != REGISTRY["DP16"].contract_digest
    assert v2.question("include").options == ("yes", "no", "unclear")
    assert all(not question.dynamic for question in v2.questions)
    for point, version in (("DP01", 2), ("DP16", 3), ("DP16", True), ("DP16", 2.0)):
        with pytest.raises(DecisionError, match="unknown_contract_version"):
            contract_for(point, version)


def test_shared_wire_state_retains_exact_targets_immutable_requests_and_once_only_usage():
    original = requests()
    batch = encode_laya_batch(original, model_alias=MODEL_ALIAS)
    payload = json.loads(batch.body)
    state = json.loads(payload["state"])
    assert payload["model"] == MODEL_ALIAS
    assert set(payload["questions"]) == set(batch.wire_ids) == set(state["bindings"])
    assert all("state" not in question for question in payload["questions"].values())
    for binding in batch.bindings:
        assert state["bindings"][binding.wire_qid] == json.loads(binding.target_json)
    assert len({binding.request.state_packet.input_digest for binding in batch.bindings}) == len(original)
    with pytest.raises(FrozenInstanceError):
        batch.body = b"replacement"
    assert isinstance(batch.bindings, tuple) and all(isinstance(item.live_options, tuple) for item in batch.requests)
    data = response(batch)
    data["answers"] = dict(reversed(list(data["answers"].items())))
    result = decode(data, batch)
    assert result.usage == BatchUsage(200, len(original))
    assert result.usage.total_tokens == 200 + len(original)
    for request, record, typed in zip(original, result.records, result.results):
        assert record["request_id"] == request.request_id
        assert record["input_digest"] == request.state_packet.input_digest
        assert "usage" not in record and record["latency_ms"] == 12.5
        assert DecisionResult.validate(record, request, BUNDLE) == typed
        assert record["model_digest"] == BUNDLE.model_digest != data["model"]


def test_build_plan_synthetic_answers_become_valid_typed_results():
    fixture = json.loads((Path(__file__).parents[2] / "evals/decisions/laya_api_fixtures.json").read_text())
    batch = encode_laya_batch(requests(("need", "family")), model_alias=fixture["response_example"]["model"])
    raw = copy.deepcopy(fixture["response_example"])
    raw["answers"] = {binding.wire_qid: raw["answers"]["s1_need" if binding.request.question_id == "need"
                                                    else "s1_family_01"] for binding in batch.bindings}
    result = decode(raw, batch)
    assert [answer.selected for answer in result.results] == ["needs_tools", "yes"]
    assert result.usage.input_tokens == fixture["response_example"]["usage"]["input_tokens"]
    assert not fixture["deployed_api_verified"] and fixture["evidence_kind"] == "synthetic"


@pytest.mark.parametrize("stage,question", [(2, "include"), (3, "verify")])
def test_multiple_tools_in_same_family_keep_independent_typed_bindings(stage, question):
    batch = encode_laya_batch(requests((question, question), stage=stage))
    assert len(set(batch.wire_ids)) == 2
    targets = [json.loads(binding.target_json) for binding in batch.bindings]
    assert targets[0]["selected_family"] == targets[1]["selected_family"]
    assert targets[0]["selected_tool"] != targets[1]["selected_tool"]
    result = decode(response(batch), batch)
    assert len(result.records) == 2
    assert result.records[0]["input_digest"] != result.records[1]["input_digest"]


def test_new_exchange_nonce_rejects_stale_response_with_identical_question_shapes():
    bound = requests()
    old, current = encode_laya_batch(bound), encode_laya_batch(bound)
    assert not set(old.wire_ids) & set(current.wire_ids)
    with pytest.raises(DecisionError, match="response_binding_mismatch"):
        decode(response(old), current)


@pytest.mark.parametrize("mutate,reason", [
    (lambda row: row.update(type="score"), "invalid_answer_schema"),
    (lambda row: row.update(type="noul"), "invalid_answer_schema"),
    (lambda row: row.update(extra="secret-payload"), "invalid_answer_schema"),
    (lambda row: row.update(choice="admin_tool"), "invalid_selection"),
    (lambda row: row.update(choice=None), "invalid_selection"),
    (lambda row: row.update(choice="no_tools"), "selection_not_argmax"),
    (lambda row: row.update(confidence=.5), "confidence_mismatch"),
    (lambda row: row.update(confidence=True), "invalid_confidence"),
    (lambda row: row["probabilities"].update(unknown_tool=0), "invalid_distribution_options"),
    (lambda row: row["probabilities"].pop("unclear"), "invalid_distribution_options"),
    (lambda row: row["probabilities"].update(needs_tools=True), "invalid_probability"),
    (lambda row: row["probabilities"].update(needs_tools=-.1), "invalid_probability"),
    (lambda row: row["probabilities"].update(needs_tools=1.1), "invalid_probability"),
    (lambda row: row["probabilities"].update(needs_tools=10**300), "invalid_probability"),
    (lambda row: row["probabilities"].update(needs_tools=.5), "invalid_distribution_sum"),
    (lambda row: row["probabilities"].update(needs_tools=float("nan")), "invalid_json"),
    (lambda row: row["probabilities"].update(needs_tools=float("inf")), "invalid_json"),
])
def test_malformed_answer_is_closed_and_never_leaks_vendor_payload(mutate, reason):
    batch = encode_laya_batch(requests(("need",)))
    data = response(batch)
    mutate(data["answers"][batch.wire_ids[0]])
    with pytest.raises(DecisionError) as caught:
        decode(data, batch)
    assert str(caught.value) == reason
    assert "secret-payload" not in str(caught.value)


@pytest.mark.parametrize("change,reason", [
    (lambda data: data.update(extra="secret-payload"), "invalid_response_schema"),
    (lambda data: data.pop("usage"), "invalid_response_schema"),
    (lambda data: data.update(model="different-model"), "model_alias_mismatch"),
    (lambda data: data.update(model={"artifact": "secret-payload"}), "invalid_model_alias"),
    (lambda data: data["answers"].pop(next(iter(data["answers"]))), "response_binding_mismatch"),
    (lambda data: data["answers"].update(extra={}), "response_binding_mismatch"),
    (lambda data: data["usage"].update(input_tokens=True), "invalid_usage"),
    (lambda data: data["usage"].update(output_tokens=-1), "invalid_usage"),
    (lambda data: data["usage"].update(input_tokens=2**31), "invalid_usage"),
    (lambda data: data["usage"].update(total_tokens=999), "invalid_usage"),
])
def test_strict_envelope_question_set_model_alias_and_usage(change, reason):
    batch = encode_laya_batch(requests(), model_alias=MODEL_ALIAS)
    data = response(batch)
    change(data)
    with pytest.raises(DecisionError) as caught:
        decode(data, batch)
    assert str(caught.value) == reason


def test_exact_tie_abstains_without_altering_distribution_and_explicit_unclear_survives():
    batch = encode_laya_batch(requests(("need",)))
    data = response(batch)
    answer = data["answers"][batch.wire_ids[0]]
    answer.update(choice="needs_tools", confidence=.5,
                  probabilities={"needs_tools": .5, "no_tools": .5, "defer": 0, "unclear": 0})
    result = decode(data, batch).results[0]
    assert result.selected is None and result.unclear
    assert dict(result.distribution) == answer["probabilities"]
    answer.update(choice="unclear", confidence=1,
                  probabilities={"needs_tools": 0, "no_tools": 0, "defer": 0, "unclear": 1})
    result = decode(data, batch).results[0]
    assert result.selected == "unclear" and result.unclear


def test_probability_and_confidence_tolerance_do_not_renormalize():
    batch = encode_laya_batch(requests(("need",)))
    data = response(batch)
    row = data["answers"][batch.wire_ids[0]]
    row["probabilities"]["no_tools"] = .0000005
    row["confidence"] = .9999995
    result = decode(data, batch).results[0]
    assert sum(dict(result.distribution).values()) != 1
    row["probabilities"]["no_tools"] = .000002
    with pytest.raises(DecisionError, match="invalid_distribution_sum"):
        decode(data, batch)


@pytest.mark.parametrize("raw,reason", [
    (b'{"model":"secret-one","model":"secret-two"}', "duplicate_json_key"),
    (b'{"answers":{"duplicate":{},"duplicate":{}}}', "duplicate_json_key"),
    (b'{"x":{"p":1,"p":0}}', "duplicate_json_key"),
    (b'[' * 1000 + b']' * 1000, "response_too_deep"),
    (b' ' * (MAX_BODY_BYTES + 1), "response_too_large"),
    (b'\xffsecret-invalid-utf8', "invalid_json"),
    (b'{"secret-error-payload":', "invalid_json"),
    (b'{"x":1e9999}', "invalid_response_schema"),
    (b'{} trailing-secret', "invalid_json"),
    ("{}", "invalid_response_bytes"),
])
def test_duplicate_deep_oversized_and_invalid_json_fail_with_fixed_codes(raw, reason):
    batch = encode_laya_batch(requests(("need",)))
    with pytest.raises(DecisionError) as caught:
        decode_laya_batch(raw, batch, BUNDLE, latency_ms=1)
    assert str(caught.value) == reason


def test_strings_containing_braces_do_not_trigger_depth_parser():
    batch = encode_laya_batch(requests(("need",)))
    data = response(batch)
    data["model"] = "[" * 100
    with pytest.raises(DecisionError, match="invalid_model_alias"):
        decode(data, batch)


@pytest.mark.parametrize("mutate,reason", [
    (lambda row: replace(row, contract_version=1), "batch_contract_mismatch"),
    (lambda row: replace(row, contract_digest="9" * 64), "batch_contract_mismatch"),
    (lambda row: replace(row, deadline=row.deadline + 1), "batch_scope_mismatch"),
    (lambda row: replace(row, state_packet=replace(row.state_packet, scope_digest="8" * 64)), "batch_scope_mismatch"),
    (lambda row: replace(row, state_packet=replace(row.state_packet, classification="public")), "batch_scope_mismatch"),
    (lambda row: replace(row, live_options=("admin", "unclear")), "closed_options_changed"),
    (lambda row: packet_change(row, stage=2), "batch_stage_mismatch"),
    (lambda row: packet_change(row, stage=True), "batch_stage_mismatch"),
    (lambda row: packet_change(row, renderer_version="unknown"), "batch_stage_mismatch"),
    (lambda row: packet_change(row, prior={"need": "no_tools"}), "batch_state_mismatch"),
    (lambda row: packet_change(row, selected_family="f_" + "9" * 24), "unknown_family_alias"),
    (lambda row: packet_change(row, selected_family="unclear"), "invalid_catalog_alias"),
    (lambda row: packet_change(row, bindings={"arbitrary": {}}), "invalid_question_binding"),
    (lambda row: packet_change(row, system_prompt="secret-system"), "invalid_state_fields"),
])
def test_batch_cannot_mix_local_contract_scope_stage_or_target(mutate, reason):
    original = requests(("need", "family"))
    with pytest.raises(DecisionError) as caught:
        encode_laya_batch((original[0], mutate(original[1])))
    assert str(caught.value) == reason


def test_private_fails_before_render_and_cannot_be_bypassed_by_extra_arguments(monkeypatch):
    private = requests(("need",), classification="private")
    def must_not_render(*args, **kwargs):
        raise AssertionError("Private rendering occurred")
    monkeypatch.setattr("agent.decisions.laya_prompts.render_dp16_question", must_not_render)
    with pytest.raises(DecisionError, match="privacy_not_qualified"):
        encode_laya_batch(private)
    with pytest.raises(TypeError):
        encode_laya_batch(private, allow_private=True)


def test_duplicate_requests_semantic_targets_and_question_count_are_rejected():
    original = requests(("need",))
    for malformed in ((), original * 65, list(original)):
        with pytest.raises(DecisionError, match="invalid_batch_size"):
            encode_laya_batch(malformed)
    with pytest.raises(DecisionError, match="duplicate_batch_request"):
        encode_laya_batch(original * 2)
    with pytest.raises(DecisionError, match="duplicate_batch_question"):
        encode_laya_batch(original + (replace(original[0], request_id="9" * 64),))


def test_stage_prior_unknown_tool_and_inconsistent_shortlists_are_rejected():
    original = requests(("include", "include"), stage=2)
    with pytest.raises(DecisionError, match="unknown_tool_alias"):
        encode_laya_batch((packet_change(original[0], selected_tool="t_" + "9" * 24),))
    with pytest.raises(DecisionError, match="invalid_stage_prior"):
        encode_laya_batch((packet_change(original[0], prior={}),))
    verify = requests(("verify", "verify"), stage=3)
    state = json.loads(verify[1].state_packet.state_json)
    with pytest.raises(DecisionError, match="batch_shortlist_mismatch"):
        encode_laya_batch((verify[0], packet_change(verify[1], shortlist=list(reversed(state["shortlist"])))))


def test_frozen_v2_state_and_final_wire_bytes_have_independent_bounds():
    original = requests(("need",))[0]
    values = json.loads(original.state_packet.state_json)
    values["context"]["request"] = "界" * 2000
    with pytest.raises(DecisionError, match="state_too_large"):
        build_state("DP16", values, scope_digest=SCOPE, classification="synthetic", contract_version=2)
    values["context"]["request"] = "x" * 4096
    packet = build_state("DP16", values, scope_digest=SCOPE, classification="synthetic", contract_version=2)
    assert packet.state_json != original.state_packet.state_json
    values["context"]["request"] = "mutated"
    assert "mutated" not in packet.state_json


def test_invalid_measured_latency_never_uses_remote_latency_or_claims_identity():
    batch = encode_laya_batch(requests(("need",)))
    data = json.dumps(response(batch)).encode()
    for latency in (True, -1, float("nan"), float("inf"), 10**500):
        with pytest.raises(DecisionError, match="invalid_latency"):
            decode_laya_batch(data, batch, BUNDLE, latency_ms=latency)


def _many_candidate_requests(count):
    names = [f"tool_{index}" for index in range(count)]
    live = LiveCatalog.from_authorized([{"type": "function", "function": {
        "name": name, "description": "Inspect records", "parameters": {"type": "object", "properties": {}}}}
        for name in names], scope_digest=SCOPE, families={"files": names}, bridge_names=())
    family = live.family_alias("files")
    context = build_planner_context("Inspect authorized records", scope_digest=SCOPE, classification="synthetic")
    base = json.loads(render_dp16_state(context, catalog=live.descriptor_values, stage=2,
        prior={"need": "needs_tools", "effort": "four_plus", "selected_families": [family]}))
    contract = contract_for("DP16", 2)
    return tuple(DecisionRequest("DP16", 2, contract.contract_digest, "include",
        build_state("DP16", {**base, "selected_family": family, "selected_tool": live.tool_alias(name)},
                    scope_digest=SCOPE, classification="synthetic", contract_version=2),
        contract.question("include").options, 2000000000.0, digest(index)) for index, name in enumerate(names))


def test_full_encoding_may_exceed_byte_limits_before_nominal_candidate_cap():
    # A valid individual packet and candidate count cannot bypass the final
    # shared-state or body bound. Reject the whole batch rather than clip tools.
    for count, code in ((22, "request_too_large"), (24, "laya_state_bytes_exceeded")):
        rows = _many_candidate_requests(count)
        assert len(rows) <= 32
        assert all(len(row.state_packet.state_json.encode()) <= 12 * 1024 for row in rows)
        with pytest.raises(DecisionError, match=code):
            encode_laya_batch(rows)


def test_large_finite_exponent_nonfinite_exponent_and_duplicate_escaped_key():
    batch = encode_laya_batch(requests(("need",)))
    raw = json.dumps(response(batch)).encode()
    for literal in (b"1e9999", b"1e300"):
        malicious = raw.replace(b'"needs_tools": 1.0', b'"needs_tools": ' + literal)
        with pytest.raises(DecisionError, match="invalid_probability"):
            decode_laya_batch(malicious, batch, BUNDLE, latency_ms=1)
    with pytest.raises(DecisionError, match="duplicate_json_key"):
        decode_laya_batch(b'{"model":"one","\\u006dodel":"two"}', batch, BUNDLE, latency_ms=1)


def test_direct_state_packet_does_not_bypass_v2_state_and_json_depth_bounds():
    original = requests(("need",))[0]
    values = json.loads(original.state_packet.state_json)
    values["context"]["request"] = "x" * 12000
    packet = StatePacket(canonical(values), SCOPE, "synthetic")
    with pytest.raises(DecisionError, match="state_too_large"):
        encode_laya_batch((replace(original, state_packet=packet),))
    deep = {"nested": None}
    for _ in range(20):
        deep = {"nested": deep}
    values["context"]["request"] = deep
    with pytest.raises(DecisionError, match="state_too_deep"):
        build_state("DP16", values, scope_digest=SCOPE, classification="synthetic", contract_version=2)
