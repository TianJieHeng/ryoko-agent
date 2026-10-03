"""Redacted, owner-only decision receipts for FE12 replay consumers."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .base import Result

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Option = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class DecisionReceipt(Result):
    schema_version: Literal[1]
    receipt_id: Digest
    point_id: Annotated[str, Field(pattern=r"^DP(0[1-9]|1[0-6])$")]
    contract_version: Annotated[StrictInt, Field(ge=1)]
    contract_digest: Digest
    question_id: Option
    request_id: Digest
    input_digest: Digest
    scope_digest: Digest
    classification: Literal["private", "public", "synthetic"]
    model_digest: Digest
    calibration_digest: Digest
    service_digest: Digest
    mode: Literal["off", "shadow", "advisory", "enforce"]
    thresholds: dict[Option, Probability]
    point_gate_digest: Digest | None
    live_options: Annotated[list[Option], Field(min_length=2, max_length=64)]
    distribution: dict[Option, Probability] | None
    selected: Option | None
    unclear: StrictBool
    actual_route: Literal["incumbent", "advisory", "qualified_recommendation"]
    fallback: Literal["off", "privacy_not_qualified", "private_transport_unqualified",
        "private_destination_authorization_required", "point_gate_required", "durable_receipt_required",
        "transport_unconfigured", "deadline_exceeded", "node_capacity", "node_unavailable", "node_http_error",
        "circuit_open", "invalid_response_schema", "response_binding_mismatch", "bundle_mismatch",
        "invalid_distribution_options", "invalid_probability", "invalid_distribution_sum", "invalid_selection",
        "invalid_unclear", "selection_not_argmax", "invalid_latency", "unclear", "below_threshold",
        "shadow_observation", "receipt_unavailable"] | None
    incumbent: Option
    latency_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    node_latency_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    recorded_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    outcome: None
    raw_state_retained: Literal[False]


class DecisionOutcomeLabel(Result):
    receipt_id: Digest
    label: Option
    outcome: Literal["correct", "incorrect", "unresolved", "recovered"]
    source_digest: Digest
