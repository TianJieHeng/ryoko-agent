"""Synthetic native batches, shared admission, deadline and receipt isolation."""
from dataclasses import asdict, replace
import json
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from agent.decisions.batching import ReceiptWorkers, SharedBatchAdmission
from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, ModelBundle, StatePacket, canonical
from agent.decisions.laya_prompts import render_dp16_state
from agent.decisions.planner_context import build_planner_context
from agent.decisions.policy import PointPolicy
from agent.decisions.planner_v2 import family_summary_catalog
from agent.decisions.state import build_state
from agent.decisions.tool_planner import BRIDGES, LiveCatalog

SCOPE = "a" * 64
BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


def packets():
    schema = {"type": "function", "function": {"name": "read", "description": "Read project files",
              "parameters": {"type": "object", "properties": {}}}}
    catalog = LiveCatalog.from_authorized([schema], scope_digest=SCOPE,
                                         families={"files": ["read"]}, bridge_names=BRIDGES)
    context = build_planner_context("Inspect project files", scope_digest=SCOPE, classification="synthetic")
    shared = json.loads(render_dp16_state(context, catalog=family_summary_catalog(catalog), stage=1))
    targets = ({}, {}, {"selected_family": catalog.family_alias("files")})
    return tuple(build_state("DP16", {**shared, **target}, scope_digest=SCOPE,
                             classification="synthetic", contract_version=2) for target in targets)


def response(request):
    selected = {"need": "needs_tools", "effort": "one", "family": "yes"}[request.question_id]
    source = request.to_record()
    keys = ("request_id", "point_id", "contract_version", "contract_digest", "question_id", "input_digest", "scope_digest")
    return {**{key: source[key] for key in keys}, **asdict(BUNDLE),
            "distribution": {choice: float(choice == selected) for choice in request.live_options},
            "selected": selected, "unclear": False, "latency_ms": .1}


class Transport:
    protocol = "laya_systemone"

    def __init__(self, callback=None, *, key=None):
        self.admission_key = key or "synthetic:" + uuid.uuid4().hex
        self.callback, self.calls = callback, []

    def decide_many(self, requests, timeout):
        self.calls.append((requests, timeout))
        if self.callback is not None:
            return self.callback(requests, timeout)
        return SimpleNamespace(records=tuple(response(request) for request in requests),
                               usage=SimpleNamespace(input_tokens=100, output_tokens=3))


def client(transport, sink, *, timeout=.2, **kwargs):
    return DecisionClient(bundle=BUNDLE, transport=transport, sink=sink,
        policies={"DP16": PointPolicy("shadow", timeout_seconds=timeout)}, **kwargs)


def call(instance, *, states=None, deadline=None, fence=None):
    return instance.decide_many("DP16", packets() if states is None else states, 2,
        time.time() + 1 if deadline is None else deadline, question_ids=("need", "effort", "family"), fence=fence)


def test_one_native_call_has_atomic_bound_records_receipts_and_single_usage():
    transport, receipts = Transport(), []
    out = call(client(transport, receipts.append))
    assert out.fallback is None and len(transport.calls) == 1
    assert out.metrics.batch_count == 1 and out.metrics.question_count == 3
    assert out.metrics.input_tokens == 100 and out.metrics.output_tokens == 3
    assert len(receipts) == 3 and len({row["request_id"] for row in receipts}) == 3
    assert all(row.receipt_persisted and row.fallback == "shadow_observation" for row in out.outcomes)
    assert all("state_packet" not in row and "usage" not in row for row in receipts)
    with pytest.raises((AttributeError, TypeError)):
        out.metrics.batch_count = 99


def test_all_private_admissions_precede_encoder_and_transport(monkeypatch):
    transport, receipts = Transport(), []
    states = list(packets())
    states[-1] = replace(states[-1], classification="private")
    def forbidden(*args, **kwargs):
        pytest.fail("private batch must not be encoded")
    monkeypatch.setattr("agent.decisions.laya_codec.encode_laya_batch", forbidden)
    out = call(client(transport, receipts.append), states=states)
    assert out.fallback == "privacy_not_qualified" and not transport.calls
    assert len(receipts) == 3 and all(row["distribution"] is None for row in receipts)


def test_one_bad_answer_rejects_all_results_and_arbitrary_exception_text():
    def malformed(requests, timeout):
        records = [response(request) for request in requests]
        records[-1]["scope_digest"] = "b" * 64
        return SimpleNamespace(records=records, usage=SimpleNamespace(input_tokens=10, output_tokens=3))
    receipts = []
    out = call(client(Transport(malformed), receipts.append))
    assert out.fallback == "response_binding_mismatch"
    assert all(item.receipt["distribution"] is None and item.recommendation is None for item in out.outcomes)
    def unsafe_error(*args):
        raise DecisionError("private exception contents must never escape")
    out = call(client(Transport(unsafe_error), receipts.append))
    assert out.fallback == "invalid_response_schema" and "private exception" not in json.dumps(receipts)


def test_request_validation_finishes_before_transport_is_invoked():
    transport = Transport()
    states = list(packets())
    value = json.loads(states[-1].state_json)
    value["selected_family"] = "f_" + "0" * 24
    states[-1] = StatePacket(canonical(value), SCOPE, "synthetic")
    out = call(client(transport, lambda row: None), states=states)
    assert out.fallback == "invalid_response_schema" and not transport.calls


def test_hung_batch_quarantines_physical_destination_across_recreated_clients():
    release, entered = threading.Event(), threading.Event()
    key, receipts = "synthetic:" + uuid.uuid4().hex, []
    def hang(requests, timeout):
        entered.set()
        release.wait(2)
        return SimpleNamespace(records=tuple(response(request) for request in requests),
                               usage=SimpleNamespace(input_tokens=1, output_tokens=3))
    original = Transport(hang, key=key)
    try:
        first = call(client(original, receipts.append, timeout=.02))
        assert entered.is_set() and first.fallback == "deadline_exceeded"
        assert first.metrics.remote_unknown and all(out.receipt_persisted for out in first.outcomes)
        for _ in range(5):
            replacement = Transport(key=key)
            later = call(client(replacement, receipts.append))
            assert later.fallback == "remote_completion_unknown" and not replacement.calls
        assert len(original.calls) == 1
    finally:
        release.set()
    # Closing/exiting the local worker still cannot prove remote cancellation.
    replacement = Transport(key=key)
    assert call(client(replacement, receipts.append)).fallback == "remote_completion_unknown"
    assert not replacement.calls


def test_transport_reports_unknown_completion_even_when_worker_exits():
    key = "synthetic:" + uuid.uuid4().hex
    def unknown(*args):
        raise DecisionError("remote_completion_unknown")
    first = call(client(Transport(unknown, key=key), lambda row: None))
    replacement = Transport(key=key)
    assert first.metrics.remote_unknown
    assert call(client(replacement, lambda row: None)).fallback == "remote_completion_unknown"
    assert not replacement.calls


def test_circuit_counts_once_per_batch_and_survives_new_client_instances():
    key, shared, calls = "fixture-circuit", SharedBatchAdmission(), []
    def fail(*args):
        calls.append(1)
        raise DecisionError("node_http_error")
    for expected in ("node_http_error", "node_http_error", "circuit_open"):
        result = call(client(Transport(fail, key=key), lambda row: None,
                             admission=shared, failure_limit=2))
        assert result.fallback == expected
    assert len(calls) == 2  # Six question failures are two physical failures.


def test_receipt_batch_is_bounded_by_same_deadline_and_abandons_remaining_writes():
    entered, release, writes = threading.Event(), threading.Event(), []
    def blocked(row):
        writes.append(row)
        entered.set()
        release.wait(2)
    transport = Transport()
    try:
        start = time.monotonic()
        result = call(client(transport, blocked), deadline=time.time() + .04)
        assert entered.is_set() and result.fallback == "receipt_unavailable"
        assert all(not out.receipt_persisted for out in result.outcomes)
        assert time.monotonic() - start < .4 and len(writes) == 1
    finally:
        release.set()


def test_separate_bounded_receipt_pool_rejects_without_replacement_growth():
    workers, release = ReceiptWorkers(1), threading.Event()
    entered = threading.Event()
    def block(row):
        entered.set()
        release.wait(2)
    try:
        first = call(client(Transport(), block, receipt_workers=workers), deadline=time.time() + .03)
        assert entered.is_set() and first.fallback == "receipt_unavailable"
        second_transport = Transport()
        second = call(client(second_transport, lambda row: None, receipt_workers=workers))
        assert len(second_transport.calls) == 1 and second.fallback == "receipt_unavailable"
    finally:
        release.set()


def test_expired_deadline_missing_capability_and_fence_never_dispatch():
    transport = Transport()
    assert call(client(transport, lambda row: None), deadline=time.time()-1).fallback == "deadline_exceeded"
    def superseded():
        raise RuntimeError("private generation details")
    assert call(client(transport, lambda row: None), fence=superseded).fallback == "planner_fence_failed"
    assert not transport.calls
    transport.protocol = "typed_v1"
    assert call(client(transport, lambda row: None)).fallback == "native_batch_required"


def test_inflight_occupancy_without_timeout_and_registry_cardinality_are_bounded():
    admission = SharedBatchAdmission(max_destinations=1)
    release, started = threading.Event(), threading.Event()
    def hang():
        started.set()
        release.wait(2)
    try:
        admission.submit("one", hang)
        assert started.wait(.5)
        with pytest.raises(DecisionError, match="node_capacity"):
            admission.submit("one", lambda: None)
        with pytest.raises(DecisionError, match="node_capacity"):
            admission.submit("two", lambda: None)
    finally:
        release.set()
