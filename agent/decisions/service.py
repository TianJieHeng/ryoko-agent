"""Transport-neutral typed node handler for a separately installed model adapter.

This is not a LAYA vendor integration or a deploy command. The serving host owns
TLS/client-certificate validation, IP firewall and finite HTTP worker admission.
The handler additionally checks peer allowlist, payload bounds, immutable pins,
closed distributions and its independent queue. No packet/exception is logged.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import threading
import time

from agent.decisions.contracts import DecisionError, DecisionRequest, DecisionResult, ModelBundle, canonical, digest, require
from agent.decisions.registry import REGISTRY, contract_for
from agent.decisions.state import build_state


class TypedDecisionService:
    def __init__(self, manifest, predictor, *, model_path, calibration_path, resource_snapshot):
        # Read existing local artifacts only, never fetch latest at boot. This
        # verifies bytes, not vendor compatibility, licensing or trained quality.
        with open(model_path, "rb") as model:
            require(hashlib.file_digest(model, "sha256").hexdigest() == manifest.model_digest, "model_artifact_mismatch")
        with open(calibration_path, "rb") as calibration:
            require(hashlib.file_digest(calibration, "sha256").hexdigest() == manifest.calibration_digest, "calibration_artifact_mismatch")
        require(manifest.registry_digest == digest({key: value.contract_digest for key, value in REGISTRY.items()}),
                "registry_digest_mismatch")
        self.manifest, self.predictor = manifest, predictor
        self.bundle = ModelBundle(manifest.model_digest, manifest.calibration_digest, manifest.service_digest)
        self.resource_snapshot = resource_snapshot
        self._slots = threading.BoundedSemaphore(manifest.max_queue)
        self._lock = threading.Lock()
        self._active = 0
        self._started = time.monotonic()

    def handle(self, path, body, *, peer_address, mutually_authenticated):
        require(mutually_authenticated is True and peer_address in self.manifest.allowed_client_addresses,
                "node_auth_required")
        if path == "/v1/health":
            require(body in (b"", None), "invalid_health_request")
            return self.health()
        require(path == "/v1/decide", "unknown_endpoint")
        require(isinstance(body, bytes) and len(body) <= self.manifest.max_request_bytes, "request_too_large")
        require(self._slots.acquire(blocking=False), "node_capacity")
        with self._lock:
            self._active += 1
        try:
            request = self._request(body)
            started = time.monotonic()
            prediction = self.predictor(request)
            require(time.time() < request.deadline, "deadline_exceeded")
            require(isinstance(prediction, dict) and set(prediction) == {"distribution", "selected", "unclear"},
                    "invalid_prediction")
            source = request.to_record()
            result = {key: source[key] for key in ("request_id", "point_id", "contract_version", "contract_digest",
                                                 "question_id", "input_digest", "scope_digest")}
            result.update(**asdict(self.bundle), **prediction, latency_ms=(time.monotonic() - started) * 1000)
            DecisionResult.validate(result, request, self.bundle)
            require(len(canonical(result).encode()) <= self.manifest.max_response_bytes, "response_too_large")
            return result
        except DecisionError:
            raise
        except Exception:
            raise DecisionError("prediction_failed") from None
        finally:
            with self._lock:
                self._active -= 1
            self._slots.release()

    def _request(self, body):
        try:
            raw = json.loads(body)
        except ValueError:
            raise DecisionError("invalid_request") from None
        require(isinstance(raw, dict) and set(raw) == {"point_id", "contract_version", "contract_digest", "question_id",
            "state_packet", "scope_digest", "input_digest", "classification", "live_options", "deadline", "request_id"},
            "invalid_request")
        contract = contract_for(raw["point_id"], raw["contract_version"])
        require(contract.contract_digest == raw["contract_digest"], "contract_digest_mismatch")
        question = contract.question(raw["question_id"])
        packet = build_state(raw["point_id"], raw["state_packet"], scope_digest=raw["scope_digest"],
                             classification=raw["classification"])
        # This release is synthetic/public only. Server has no bool to bypass
        # the missing private data retention/destination authorization gates.
        require(packet.classification in {"synthetic", "public"}, "privacy_not_qualified")
        require(packet.input_digest == raw["input_digest"], "input_digest_mismatch")
        request = DecisionRequest(raw["point_id"], raw["contract_version"], raw["contract_digest"], raw["question_id"],
                                  packet, tuple(raw["live_options"]), raw["deadline"], raw["request_id"])
        require(time.time() < request.deadline <= time.time() + 1.1, "invalid_deadline")
        require(question.dynamic or request.live_options == question.options, "closed_options_changed")
        return request

    def health(self):
        from agent.decisions.health import validate_health
        resources = self.resource_snapshot()
        require(isinstance(resources, dict) and set(resources) == {"memory_used_bytes", "memory_limit_bytes"},
                "invalid_health_resource")
        with self._lock:
            active = self._active
        result = {"schema_version": 1, **asdict(self.bundle), "registry_digest": self.manifest.registry_digest,
            "queue_depth": active, "max_queue": self.manifest.max_queue, **resources,
            "uptime_seconds": time.monotonic() - self._started, "ready": active < self.manifest.max_queue,
            "hardware_verified": False}
        return validate_health(result, self.manifest)
