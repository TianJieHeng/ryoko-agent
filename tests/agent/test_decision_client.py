"""Typed decisions preserve incumbent authority and remain bounded under failure."""
from dataclasses import asdict, replace
import json
import threading
import time

import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, ModelBundle, digest
from agent.decisions.policy import PointPolicy, PointGate
from agent.decisions.registry import REGISTRY, contract_for
from agent.decisions.state import build_state

BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


def packet(point="DP16", **values):
    return build_state(point, values or {"request": "synthetic fixture"}, scope_digest="4" * 64, classification="synthetic")


def response(request, selected="needs_tools"):
    data = request.to_record()
    keys = ("request_id", "point_id", "contract_version", "contract_digest", "question_id", "input_digest", "scope_digest")
    return {**{key: data[key] for key in keys}, **asdict(BUNDLE),
            "distribution": {name: float(name == selected) for name in request.live_options},
            "selected": selected, "unclear": selected == "unclear", "latency_ms": .1}


def client(mode="shadow", transport=None, sink=None, **kwargs):
    return DecisionClient(bundle=BUNDLE, policies={"DP16": PointPolicy(mode, timeout_seconds=.03)},
                          transport=transport or (lambda req, timeout: response(req)), sink=sink, **kwargs)


def call(c, **kwargs):
    return c.decide("DP16", packet(), 1, time.time() + 1, **kwargs)


def test_shadow_preserves_route_records_closed_distribution_and_redacts_state():
    receipts = []
    c = client(sink=receipts.append)
    state = packet(request="secret-seeded-fixture-do-not-log")
    out = c.decide("DP16", state, 1, time.time() + 1)
    assert out.recommendation is None and out.route == "incumbent"
    assert out.receipt_persisted and out.fallback == "shadow_observation"
    assert receipts[0]["distribution"] == {"no_tools": 0, "needs_tools": 1, "defer": 0, "unclear": 0}
    assert "secret-seeded-fixture" not in json.dumps(receipts)
    assert receipts[0]["input_digest"] == state.input_digest
    assert receipts[0]["model_digest"] == BUNDLE.model_digest


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d["distribution"].update(admin=0), "invalid_distribution_options"),
    (lambda d: d["distribution"].update(needs_tools=float("nan")), "invalid_probability"),
    (lambda d: d["distribution"].update(needs_tools=True), "invalid_probability"),
    (lambda d: d["distribution"].update(needs_tools=.5), "invalid_distribution_sum"),
    (lambda d: d.update(selected="no_tools"), "selection_not_argmax"),
    (lambda d: d.update(unclear=True), "invalid_unclear"),
    (lambda d: d.update(model_digest="9" * 64), "bundle_mismatch"),
    (lambda d: d.update(calibration_digest="9" * 64), "bundle_mismatch"),
    (lambda d: d.update(contract_digest="9" * 64), "response_binding_mismatch"),
    (lambda d: d.update(scope_digest="9" * 64), "response_binding_mismatch"),
    (lambda d: d.update(raw_state="secret"), "invalid_response_schema"),
])
def test_invalid_response_fails_to_incumbent_without_raw_output(mutate, reason):
    def transport(req, timeout):
        data = response(req)
        mutate(data)
        return data
    out = call(client(transport=transport))
    assert out.fallback == reason and out.recommendation is None
    assert out.receipt["distribution"] is None and "raw_state" not in out.receipt


def test_off_private_gate_and_per_point_modes_never_transmit():
    seen = []
    c = client(mode="off", transport=lambda *args: seen.append(args))
    assert call(c).fallback == "off" and not seen
    c.policies["DP16"] = PointPolicy("shadow")
    private = build_state("DP16", {"request": "private"}, scope_digest="4" * 64)
    assert c.decide("DP16", private, 1, time.time()+1).fallback == "privacy_not_qualified"
    assert not seen
    assert c.decide("DP06", packet("DP06", tool_name="read_file"), 1, time.time()+1).fallback == "off"


def test_modes_threshold_unclear_and_bound_point_gate():
    assert call(client(mode="advisory")).recommendation == "needs_tools"
    assert call(client(mode="enforce")).fallback == "point_gate_required"
    policy = PointPolicy("enforce", timeout_seconds=.03)
    gate = PointGate("DP16", REGISTRY["DP16"].contract_digest, BUNDLE.model_digest,
        BUNDLE.calibration_digest, policy.policy_digest,
        "5" * 64, time.time() + 60, ("recommend",))
    class Sink:
        durable = True
        def __call__(self, receipt):
            pass
    c = client(mode="enforce", sink=Sink(), gates={"DP16": gate})
    c.policies["DP16"] = replace(policy, gate_digest=gate.gate_digest)
    assert call(c).route == "qualified_recommendation"
    c.bundle = replace(BUNDLE, model_digest="9" * 64)
    assert call(c).fallback == "point_gate_required"
    c = client(mode="advisory", transport=lambda req, timeout: response(req, "unclear"))
    assert call(c).fallback == "unclear"


def test_hung_transport_keeps_one_capacity_slot_and_never_retries():
    started, release = threading.Event(), threading.Event()
    calls = []
    def hang(req, timeout):
        calls.append(req)
        started.set()
        release.wait(10)
        return response(req)
    c = client(transport=hang, max_inflight=1)
    began = time.monotonic()
    try:
        assert call(c).fallback == "deadline_exceeded"
        assert started.is_set()
        assert call(c).fallback == "node_capacity"
        assert len(calls) == 1 and time.monotonic() - began < 2
    finally:
        release.set()


def test_circuit_cooldown_and_sink_failure_are_independent_of_main_model():
    calls = []
    def unavailable(*args):
        calls.append(1)
        raise OSError("secret-error-payload")
    c = client(transport=unavailable, failure_limit=1)
    assert call(c).fallback == "node_unavailable"
    assert call(c).fallback == "circuit_open" and len(calls) == 1
    def broken_sink(receipt):
        raise OSError("another-secret")
    out = call(client(sink=broken_sink))
    assert out.route == "incumbent" and not out.receipt_persisted
    assert "secret" not in json.dumps(out.receipt)


def test_registry_closed_versions_dynamic_live_menu_and_state_freezing():
    assert {entry.family for entry in REGISTRY.values()} == {"Router", "Guard"}
    assert all("unclear" in question.options for entry in REGISTRY.values() for question in entry.questions)
    for name, value in REGISTRY.items():
        assert contract_for(name, value.version).contract_digest == value.contract_digest
    with pytest.raises(DecisionError, match="unknown_contract"):
        contract_for("DP16", 2)
    with pytest.raises(DecisionError, match="closed_options"):
        call(client(), live_options=("grant_admin", "unclear"))
    with pytest.raises(DecisionError, match="live_menu"):
        call(client(), question_id="tool")
    with pytest.raises(DecisionError, match="invalid_state_fields"):
        packet(transcript="must not accept whole history")
    values = {"request": "hello", "menu": ["read_file"]}
    state = build_state("DP16", values, scope_digest="4" * 64, classification="synthetic")
    values["menu"].append("unauthorized")
    assert "unauthorized" not in state.state_json


def test_deadline_expired_never_calls_transport():
    calls = []
    c = client(transport=lambda *args: calls.append(args))
    out = c.decide("DP16", packet(), 1, time.time()-1)
    assert out.fallback == "deadline_exceeded" and not calls


def test_each_registry_question_has_executable_synthetic_protocol_fallback():
    from pathlib import Path
    fixtures = json.loads((Path(__file__).parents[2] / "evals/decisions/contract_fixtures.json").read_text())
    covered = set()
    for fixture in fixtures["rows"]:
        point = fixture["point_id"]
        contract = REGISTRY[point]
        c = DecisionClient(bundle=BUNDLE, policies={point: PointPolicy("shadow")},
            transport=lambda request, timeout: response(request, "unclear"))
        state = build_state(point, fixture["state"], scope_digest="4"*64, classification="synthetic")
        outcome = c.decide(point, state, contract.version, time.time()+1,
                           question_id=fixture["question_id"], live_options=fixture["live_options"])
        assert outcome.fallback == fixture["expected_fallback"] and outcome.route == "incumbent"
        covered.add((point, fixture["question_id"]))
    assert covered == {(point, question.question_id) for point, contract in REGISTRY.items() for question in contract.questions}


def test_per_effect_thresholds_are_applied_recorded_and_gate_bound():
    def prediction(request, timeout):
        raw = response(request)
        raw["distribution"].update(needs_tools=.96, no_tools=.04)
        return raw
    c = client(mode="advisory", transport=prediction)
    assert call(c).recommendation == "needs_tools"
    old = c.policies["DP16"]
    c.policies["DP16"] = replace(old, effect_thresholds=(("needs_tools", .99),))
    result = call(c)
    assert result.fallback == "below_threshold" and result.recommendation is None
    assert result.receipt["thresholds"]["needs_tools"] == .99
    assert old.policy_digest != c.policies["DP16"].policy_digest
