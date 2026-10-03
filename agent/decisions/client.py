"""Bounded typed inference with independent deadlines, capacity and circuit state.

No model-provider retry code is imported. A wedged transport holds one bounded
slot until it exits; we never spawn an unbounded replacement fleet of threads.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from contextvars import copy_context
from dataclasses import dataclass
import json
import threading
import time
import uuid

from agent.decisions.contracts import DecisionError, DecisionRequest, DecisionResult, StatePacket, digest, require, options
from agent.decisions.policy import PointPolicy
from agent.decisions.receipts import receipt_for
from agent.decisions.registry import contract_for, REGISTRY


@dataclass(frozen=True)
class DecisionOutcome:
    recommendation: str | None
    route: str
    fallback: str | None
    receipt: dict | None
    receipt_persisted: bool = False


class DecisionClient:
    def __init__(self, *, bundle, transport=None, policies=None, gates=None, sink=None,
                 max_inflight=4, failure_limit=3, cooldown_seconds=30):
        require(type(max_inflight) is int and 1 <= max_inflight <= 16, "invalid_capacity")
        require(type(failure_limit) is int and 1 <= failure_limit <= 10, "invalid_circuit")
        from agent.decisions.contracts import number
        number(cooldown_seconds, .01, 300, "invalid_circuit")
        self.bundle, self.transport, self.sink = bundle, transport, sink
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
        policy = self.policy(point_id)
        if policy.mode == "off":
            return DecisionOutcome(None, "incumbent", "off", None)
        require(isinstance(state_packet, StatePacket), "invalid_state")
        require(set(json.loads(state_packet.state_json)) <= set(contract.state_fields), "invalid_state_fields")
        question = contract.question(question_id)
        menu = options(live_options if live_options is not None else question.options)
        require(question.dynamic or menu == question.options, "closed_options_changed")
        if question.dynamic:
            require(live_options is not None, "live_menu_required")
            state_menu = json.loads(state_packet.state_json).get("menu", [])
            require(isinstance(state_menu, list) and set(menu) <= set(state_menu) | {"none", "unclear"}, "live_menu_mismatch")
        if point_id == "DP16" and question.question_id in {"tool", "verify"}:
            state = json.loads(state_packet.state_json)
            require(bool(state.get("selected_family")), "family_stage_required")
            if question.question_id == "verify":
                require(state.get("selected_tool") in state.get("menu", []), "tool_stage_required")
        request = DecisionRequest(point_id, contract_version, contract.contract_digest, question.question_id,
            state_packet, menu, deadline, digest({"input": state_packet.input_digest, "nonce": uuid.uuid4().hex}))
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

    def _admit(self, request, contract, policy):
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
