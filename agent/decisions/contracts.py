"""Closed, versioned decision wire contracts independent of model providers."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re


class DecisionError(ValueError):
    """Only fixed error codes cross logging/receipt boundaries."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise DecisionError(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise DecisionError("invalid_json") from None


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha256(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_digest")
    return value


def number(value, low, high, code="invalid_number"):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, code)
    return value


def label(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value) is not None, "invalid_option")
    return value


def options(value):
    require(isinstance(value, (list, tuple)) and 2 <= len(value) <= 64, "invalid_options")
    for item in value:
        label(item)
    require(len(set(value)) == len(value) and "unclear" in value, "invalid_options")
    return tuple(value)


@dataclass(frozen=True)
class Question:
    question_id: str
    text: str
    options: tuple[str, ...]
    dynamic: bool = False

    def __post_init__(self):
        label(self.question_id)
        options(self.options)
        require(isinstance(self.text, str) and 0 < len(self.text) < 1000, "invalid_question")


@dataclass(frozen=True)
class DecisionContract:
    point_id: str
    version: int
    owner: str
    family: str
    questions: tuple[Question, ...]
    fallback: str
    state_fields: tuple[str, ...]
    fixture_set: str
    thresholds: tuple[tuple[str, float], ...] = (("select", .95),)
    consumer: str = "catalog_only"

    def question(self, question_id=None):
        question_id = question_id or self.questions[0].question_id
        for item in self.questions:
            if item.question_id == question_id:
                return item
        raise DecisionError("unknown_question")

    @property
    def contract_digest(self):
        from dataclasses import asdict
        return digest(asdict(self))


@dataclass(frozen=True)
class StatePacket:
    """JSON bytes are frozen at construction; arbitrary objects never cross the wire."""
    state_json: str = field(repr=False)
    scope_digest: str
    classification: str = "private"

    def __post_init__(self):
        sha256(self.scope_digest)
        require(self.classification in {"private", "synthetic", "public"}, "invalid_data_class")
        require(isinstance(self.state_json, str) and len(self.state_json.encode()) <= 16384, "state_too_large")
        try:
            state = json.loads(self.state_json)
        except (ValueError, TypeError):
            raise DecisionError("invalid_state") from None
        require(isinstance(state, dict) and canonical(state) == self.state_json, "invalid_state")

    @property
    def input_digest(self):
        return digest({"state": json.loads(self.state_json), "scope": self.scope_digest,
                       "classification": self.classification})


@dataclass(frozen=True)
class DecisionRequest:
    point_id: str
    contract_version: int
    contract_digest: str
    question_id: str
    state_packet: StatePacket
    live_options: tuple[str, ...]
    deadline: float
    request_id: str

    def __post_init__(self):
        sha256(self.contract_digest)
        sha256(self.request_id)
        options(self.live_options)
        number(self.deadline, 0, 253402300799, "invalid_deadline")

    def to_record(self):
        return {"point_id": self.point_id, "contract_version": self.contract_version,
            "contract_digest": self.contract_digest, "question_id": self.question_id,
            "state_packet": json.loads(self.state_packet.state_json),
            "scope_digest": self.state_packet.scope_digest, "input_digest": self.state_packet.input_digest,
            "classification": self.state_packet.classification, "live_options": list(self.live_options),
            "deadline": self.deadline, "request_id": self.request_id}


@dataclass(frozen=True)
class ModelBundle:
    model_digest: str
    calibration_digest: str
    service_digest: str

    def __post_init__(self):
        for value in (self.model_digest, self.calibration_digest, self.service_digest):
            sha256(value)


@dataclass(frozen=True)
class DecisionResult:
    distribution: tuple[tuple[str, float], ...]
    selected: str | None
    unclear: bool
    model_digest: str
    calibration_digest: str
    latency_ms: float

    @classmethod
    def validate(cls, raw, request, bundle):
        require(isinstance(raw, dict) and set(raw) == {"request_id", "point_id", "contract_version",
            "contract_digest", "question_id", "input_digest", "scope_digest", "distribution", "selected",
            "unclear", "model_digest", "calibration_digest", "service_digest", "latency_ms"}, "invalid_response_schema")
        expected = request.to_record()
        for key in ("request_id", "point_id", "contract_version", "contract_digest", "question_id", "input_digest", "scope_digest"):
            require(type(raw[key]) is type(expected[key]) and raw[key] == expected[key], "response_binding_mismatch")
        for key in ("model_digest", "calibration_digest", "service_digest"):
            require(raw[key] == getattr(bundle, key), "bundle_mismatch")
        dist = raw["distribution"]
        require(isinstance(dist, dict) and set(dist) == set(request.live_options), "invalid_distribution_options")
        for value in dist.values():
            number(value, 0, 1, "invalid_probability")
        require(abs(sum(dist.values()) - 1) <= 1e-6, "invalid_distribution_sum")
        selected = raw["selected"]
        require(type(raw["unclear"]) is bool and (selected is None or selected in request.live_options), "invalid_selection")
        require(raw["unclear"] == (selected is None or selected == "unclear"), "invalid_unclear")
        if selected is not None:
            require(dist[selected] == max(dist.values()), "selection_not_argmax")
        number(raw["latency_ms"], 0, 3600000, "invalid_latency")
        return cls(tuple((key, dist[key]) for key in request.live_options), selected, raw["unclear"],
                   bundle.model_digest, bundle.calibration_digest, raw["latency_ms"])
