"""Bounded typed inference with independent deadlines, capacity and circuit state.

No model-provider retry code is imported. A wedged transport holds one bounded
slot until it exits; we never spawn an unbounded replacement fleet of threads.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from contextvars import copy_context
from dataclasses import dataclass, replace
import json
import threading
import time
import uuid

from agent.decisions.contracts import DecisionError, DecisionRequest, DecisionResult, StatePacket, digest, require, options
from agent.decisions.policy import PointPolicy
from agent.decisions.receipts import receipt_for
from agent.decisions.registry import contract_for, REGISTRY
from agent.decisions.batching import PlannerMetrics, SHARED_BATCH_ADMISSION, SHARED_RECEIPT_WORKERS


@dataclass(frozen=True)
class DecisionOutcome:
    recommendation: str | None
    route: str
    fallback: str | None
    receipt: dict | None
    receipt_persisted: bool = False


@dataclass(frozen=True)
class DecisionBatchOutcome:
    outcomes: tuple[DecisionOutcome, ...]
    metrics: PlannerMetrics
    fallback: str | None = None


_BATCH_REASONS = frozenset({
    "off", "privacy_not_qualified", "private_transport_unqualified",
    "private_destination_authorization_required", "point_gate_required", "durable_receipt_required",
    "transport_unconfigured", "deadline_exceeded", "node_capacity", "node_unavailable", "node_http_error",
    "circuit_open", "invalid_response_schema", "response_binding_mismatch", "bundle_mismatch",
    "invalid_distribution_options", "invalid_probability", "invalid_distribution_sum", "invalid_selection",
    "invalid_unclear", "selection_not_argmax", "invalid_latency", "unclear", "below_threshold",
    "shadow_observation", "receipt_unavailable", "remote_completion_unknown", "native_batch_required",
    "batch_admission_key_required", "invalid_batch_usage", "planner_fence_failed",
})


class DecisionClient:
    def __init__(self, *, bundle, transport=None, policies=None, gates=None, sink=None,
                 max_inflight=4, failure_limit=3, cooldown_seconds=30, protocol=None, admission=None, receipt_workers=None):
        require(type(max_inflight) is int and 1 <= max_inflight <= 16, "invalid_capacity")
        require(type(failure_limit) is int and 1 <= failure_limit <= 10, "invalid_circuit")
        from agent.decisions.contracts import number
        number(cooldown_seconds, .01, 300, "invalid_circuit")
        self.bundle, self.transport, self.sink = bundle, transport, sink
        self.protocol = protocol or getattr(transport, "protocol", "typed_v1")
        require(self.protocol in {"typed_v1", "laya_systemone"}, "unsupported_decision_protocol")
        self._batch_admission = admission if admission is not None else SHARED_BATCH_ADMISSION
        self._receipt_workers = receipt_workers if receipt_workers is not None else SHARED_RECEIPT_WORKERS
        self.policies, self.gates = dict(policies or {}), dict(gates or {})
        require(set(self.policies) <= set(REGISTRY) and set(self.gates) <= set(REGISTRY), "unknown_point")
        require(all(isinstance(value, PointPolicy) for value in self.policies.values()), "invalid_policy")
        self._capacity = threading.BoundedSemaphore(max_inflight)
        self._lock = threading.Lock()
        self._circuits = {}
        self._failure_limit, self._cooldown = failure_limit, cooldown_seconds

    def policy(self, point_id):
        return self.policies.get(point_id, PointPolicy())

    def decide(self, point_id, state_packet, contract_version, deadline, *, question_id=None, live_options=None):
        contract = contract_for(point_id, contract_version)
        require(contract.version == 1, "native_batch_required")
        policy = self.policy(point_id)
        if policy.mode == "off":
            return DecisionOutcome(None, "incumbent", "off", None)
        request = self._request(point_id, state_packet, contract, deadline,
                                question_id=question_id, live_options=live_options)
        started = time.monotonic()
        result, reason = None, None
        try:
            self._admit(request, contract, policy)
            timeout = min(policy.timeout_seconds, deadline - time.time())
            require(timeout >= .001, "deadline_exceeded")
            raw = self._call(request, timeout)
            require(time.monotonic() - started <= timeout, "deadline_exceeded")
            result = DecisionResult.validate(raw, request, self.bundle)
            with self._lock:
                self._circuits.pop(point_id, None)
        except DecisionError as exc:
            reason = exc.code
            if reason in {"node_unavailable", "node_http_error", "deadline_exceeded", "bundle_mismatch",
                          "invalid_response_schema", "response_binding_mismatch"} or reason.startswith("invalid_distribution"):
                self._failed(point_id)
        recommendation, route = None, "incumbent"
        if result is not None:
            confidence = dict(result.distribution).get(result.selected, 0)
            if result.unclear:
                reason = "unclear"
            elif confidence < policy.threshold_for(result.selected):
                reason = "below_threshold"
            elif policy.mode == "shadow":
                reason = "shadow_observation"
            else:
                recommendation, route = result.selected, "advisory" if policy.mode == "advisory" else "qualified_recommendation"
        receipt = receipt_for(request, result, contract, self.bundle, policy, route=route, reason=reason,
                              elapsed_ms=(time.monotonic() - started) * 1000)
        persisted = False
        if self.sink is not None:
            try:
                remaining = min(.05, deadline - time.time())
                require(remaining >= .001, "receipt_deadline")
                self._bounded(lambda: self.sink(receipt), remaining)
                persisted = True
            except Exception:
                # Optional observers cannot abort a turn or leak sink exception payloads.
                if policy.mode == "enforce":
                    recommendation, route, reason = None, "incumbent", "receipt_unavailable"
                    receipt = receipt_for(request, result, contract, self.bundle, policy,
                        route=route, reason=reason, elapsed_ms=(time.monotonic() - started) * 1000)
        return DecisionOutcome(recommendation, route, reason, receipt, persisted)

    @property
    def native_batches(self):
        return self.protocol == "laya_systemone"

    def _request(self, point_id, state_packet, contract, deadline, *, question_id=None, live_options=None):
        require(isinstance(state_packet, StatePacket), "invalid_state")
        require(set(json.loads(state_packet.state_json)) <= set(contract.state_fields), "invalid_state_fields")
        question = contract.question(question_id)
        menu = options(live_options if live_options is not None else question.options)
        require(question.dynamic or menu == question.options, "closed_options_changed")
        if question.dynamic:
            require(live_options is not None, "live_menu_required")
            state_menu = json.loads(state_packet.state_json).get("menu", [])
            require(isinstance(state_menu, list) and set(menu) <= set(state_menu) | {"none", "unclear"}, "live_menu_mismatch")
        if point_id == "DP16" and contract.version == 1 and question.question_id in {"tool", "verify"}:
            state = json.loads(state_packet.state_json)
            require(bool(state.get("selected_family")), "family_stage_required")
            if question.question_id == "verify":
                require(state.get("selected_tool") in state.get("menu", []), "tool_stage_required")
        return DecisionRequest(point_id, contract.version, contract.contract_digest, question.question_id,
            state_packet, menu, deadline, digest({"input": state_packet.input_digest, "nonce": uuid.uuid4().hex}))

    @staticmethod
    def _fence(fence):
        if fence is not None:
            try:
                fence()
            except Exception:
                raise DecisionError("planner_fence_failed") from None

    def decide_many(self, point_id, state_packets, contract_version, deadline, *, question_ids,
                    live_options=None, fence=None):
        """One native stage, with atomic validation and complete receipt admission.

        Every request is constructed and admitted before the first transport
        call. No result is usable until every answer validates and the entire
        stage's receipt worker finishes inside the caller's original deadline.
        """
        require(type(state_packets) in (list, tuple) and 1 <= len(state_packets) <= 64,
                "invalid_batch_size")
        require(type(question_ids) in (list, tuple) and len(question_ids) == len(state_packets),
                "invalid_batch_questions")
        contract = contract_for(point_id, contract_version)
        require(point_id == "DP16" and contract_version == 2, "native_batch_required")
        policy = self.policy(point_id)
        if policy.mode == "off":
            return DecisionBatchOutcome(tuple(DecisionOutcome(None, "incumbent", "off", None)
                                              for _ in state_packets), PlannerMetrics(), "off")
        menus = (None,) * len(state_packets) if live_options is None else live_options
        require(type(menus) in (list, tuple) and len(menus) == len(state_packets), "invalid_batch_questions")
        requests = tuple(self._request(point_id, packet, contract, deadline, question_id=question,
                         live_options=menu) for packet, question, menu in zip(state_packets, question_ids, menus))
        return self._execute_batch(requests, contract, policy, deadline, fence)

    def _execute_batch(self, requests, contract, policy, deadline, fence):
        started = time.monotonic()
        reason, results, raw = None, (), None
        dispatched, remote_unknown = False, False
        inference_ms, admission_ms = 0.0, 0.0
        input_tokens = output_tokens = 0
        key = getattr(self.transport, "admission_key", None)
        try:
            require(self.native_batches and callable(getattr(self.transport, "decide_many", None)),
                    "native_batch_required")
            self._fence(fence)
            for request in requests:
                self._admit(request, contract, policy, check_circuit=False)
            require(len({request.state_packet.scope_digest for request in requests}) == 1,
                    "response_binding_mismatch")
            for request in requests:
                state = json.loads(request.state_packet.state_json)
                require(state.get("stage") != 1 or state.get("catalog", {}).get("projection") ==
                        "family_summaries_v1", "invalid_response_schema")
            # All admissions, especially private-data gates, precede encoding.
            from agent.decisions.laya_codec import encode_laya_batch
            encode_laya_batch(requests)
            timeout = min(policy.timeout_seconds, deadline - time.time())
            require(timeout >= .001, "deadline_exceeded")
            admission_ms = (time.monotonic() - started) * 1000
            inference_started = time.monotonic()
            future = self._batch_admission.submit(key, lambda: self.transport.decide_many(requests, timeout), timeout=timeout)
            dispatched = True
            try:
                raw = future.result(timeout=timeout)
            except TimeoutError:
                remote_unknown = True
                raise DecisionError("deadline_exceeded") from None
            finally:
                inference_ms = (time.monotonic() - inference_started) * 1000
            require(inference_ms <= timeout * 1000 and time.time() < deadline, "deadline_exceeded")
            self._fence(fence)
            records = getattr(raw, "records", None)
            require(type(records) in (tuple, list) and len(records) == len(requests), "invalid_response_schema")
            # Reject the complete batch if even one bound typed record is bad.
            results = tuple(DecisionResult.validate(record, request, self.bundle)
                            for record, request in zip(records, requests))
            usage = getattr(raw, "usage", None)
            input_tokens, output_tokens = getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
            require(all(type(value) is int and 0 <= value <= 1_000_000_000
                        for value in (input_tokens, output_tokens)), "invalid_batch_usage")
        except DecisionError as exc:
            reason = exc.code if exc.code in _BATCH_REASONS else "invalid_response_schema"
            results = ()
            input_tokens = output_tokens = 0
            remote_unknown |= dispatched and (reason in {"remote_completion_unknown", "deadline_exceeded"}
                               or getattr(self.transport, "remote_completion_unknown", False) is True)
        except Exception:
            reason, results = "invalid_response_schema", ()
            input_tokens = output_tokens = 0
        if dispatched:
            self._batch_admission.settle(key, failed=reason is not None and reason != "planner_fence_failed",
                failure_limit=self._failure_limit, cooldown_seconds=self._cooldown, remote_unknown=remote_unknown)
        remote_unknown |= isinstance(key, str) and self._batch_admission.remote_unknown(key)
        outcomes = tuple(self._batch_outcome(request, result, contract, policy, reason, started)
                         for request, result in zip(requests, results or (None,) * len(requests)))
        receipt_started = time.monotonic()
        persisted = False
        try:
            self._receipt_workers.persist(self.sink, tuple(out.receipt for out in outcomes),
                                          deadline=deadline, fence=fence)
            persisted = True
        except DecisionError:
            if reason is None:
                reason = "receipt_unavailable"
        if persisted:
            outcomes = tuple(replace(out, receipt_persisted=True) for out in outcomes)
        else:
            outcomes = tuple(replace(out, recommendation=None, route="incumbent",
                             fallback=reason or "receipt_unavailable") for out in outcomes)
        metrics = PlannerMetrics(int(dispatched), len(requests) if dispatched else 0,
            inference_ms, (time.monotonic() - receipt_started) * 1000, admission_ms,
            input_tokens, output_tokens, remote_unknown)
        return DecisionBatchOutcome(outcomes, metrics, reason)

    def _batch_outcome(self, request, result, contract, policy, reason, started):
        recommendation, route = None, "incumbent"
        if result is not None:
            confidence = dict(result.distribution).get(result.selected, 0)
            if result.unclear:
                reason = "unclear"
            elif confidence < policy.threshold_for(result.selected):
                reason = "below_threshold"
            elif policy.mode == "shadow":
                reason = "shadow_observation"
            else:
                recommendation = result.selected
                route = "advisory" if policy.mode == "advisory" else "qualified_recommendation"
        receipt = receipt_for(request, result, contract, self.bundle, policy, route=route, reason=reason,
                              elapsed_ms=(time.monotonic() - started) * 1000)
        return DecisionOutcome(recommendation, route, reason, receipt)

    def _admit(self, request, contract, policy, *, check_circuit=True):
        if policy.mode == "enforce":
            gate = self.gates.get(request.point_id)
            require(gate is not None and gate.permits(contract, self.bundle, policy, time.time()), "point_gate_required")
            require(self.sink is not None and getattr(self.sink, "durable", False), "durable_receipt_required")
        if request.state_packet.classification == "private":
            # BE14 explicitly does not certify live-store/retention/key custody yet.
            # This cannot be overridden by a config boolean or by a model result.
            from agent.operations_privacy import privacy_qualification
            require(privacy_qualification()["sensitive_ingestion_certified"], "privacy_not_qualified")
            from agent.decisions.transport import LanTransport
            require(isinstance(self.transport, LanTransport), "private_transport_unqualified")
            require(False, "private_destination_authorization_required")
        require(self.transport is not None, "transport_unconfigured")
        if check_circuit:
            with self._lock:
                _, until = self._circuits.get(request.point_id, (0, 0))
            require(until <= time.monotonic(), "circuit_open")

    def _failed(self, point_id):
        with self._lock:
            count, _ = self._circuits.get(point_id, (0, 0))
            count += 1
            self._circuits[point_id] = (count, time.monotonic() + self._cooldown if count >= self._failure_limit else 0)

    def _call(self, request, timeout):
        return self._bounded(lambda: self.transport(request, timeout), timeout)

    def _bounded(self, callback, timeout):
        require(self._capacity.acquire(blocking=False), "node_capacity")
        future = Future()
        def work():
            try:
                future.set_result(callback())
            except Exception:
                # Do not retain exception text, credentials or packet fragments.
                future.set_exception(DecisionError("node_unavailable"))
            finally:
                self._capacity.release()
        thread = threading.Thread(target=copy_context().run, args=(work,), daemon=True, name="typed-decision")
        try:
            thread.start()
        except RuntimeError:
            self._capacity.release()
            raise DecisionError("node_capacity") from None
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise DecisionError("deadline_exceeded") from None
