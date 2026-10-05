"""Closed metadata for point release explanations and cache-safe planner replay."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .base import Result
from .decisions import Digest, Option, Probability

PointId = Annotated[str, Field(pattern=r"^DP(0[1-9]|1[0-6])$")]
Mode = Literal["off", "shadow", "advisory", "enforce"]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


CatalogIdentifier = Annotated[str, Field(min_length=1, max_length=512)]


class DecisionPlannerMetrics(Result):
    batch_count: Annotated[StrictInt, Field(ge=0, le=3)] = 0
    question_count: Annotated[StrictInt, Field(ge=0, le=62)] = 0
    inference_ms: Nonnegative = 0
    receipt_ms: Nonnegative = 0
    admission_ms: Nonnegative = 0
    input_tokens: Annotated[StrictInt, Field(ge=0)] = 0
    output_tokens: Annotated[StrictInt, Field(ge=0)] = 0
    remote_unknown: StrictBool = False


class DecisionToolPlan(Result):
    need: Literal["no_tools", "needs_tools", "defer"]
    effort_bucket: Literal["one", "two_three", "four_plus", "defer"]
    families: Annotated[list[CatalogIdentifier], Field(max_length=16)]
    verified_tool_ids: Annotated[list[CatalogIdentifier], Field(max_length=16)]
    live_catalog_version: Digest
    bundle_id: Digest
    reopen_policy: Literal["authorized_search_describe_call"]
    scope_digest: Digest
    mode: Mode
    fallback: Option | None
    decision_receipt_ids: Annotated[list[Digest], Field(max_length=62)]
    elapsed_ms: Nonnegative
    protocol_version: Literal[1, 2] = 1
    metrics: DecisionPlannerMetrics = Field(default_factory=DecisionPlannerMetrics)

    @model_validator(mode="after")
    def versioned_bounds(self):
        import re
        import unicodedata
        identifiers = [*self.families, *self.verified_tool_ids]
        if any(any(unicodedata.category(char).startswith("C") for char in value) for value in identifiers):
            raise ValueError("invalid_catalog_identifier")
        if len(set(self.verified_tool_ids)) != len(self.verified_tool_ids) or len(set(self.families)) != len(self.families):
            raise ValueError("duplicate_plan_identifier")
        if len(set(self.decision_receipt_ids)) != len(self.decision_receipt_ids):
            raise ValueError("duplicate_plan_receipt")
        if self.protocol_version == 1:
            if len(self.decision_receipt_ids) > 50 or any(re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value) is None for value in identifiers):
                raise ValueError("invalid_v1_plan_bounds")
        elif len(self.verified_tool_ids) > 12:
            raise ValueError("invalid_v2_plan_bounds")
        return self


class DecisionPlannerMiss(Result):
    kind: Literal["planner_miss"]
    scope_digest: Digest
    bundle_id: Digest | None
    catalog_version: Digest
    previous_catalog_version: Digest
    tool_digest: Digest
    recovered: StrictBool
    reason: Literal["authorized_reopen", "not_authorized_or_unavailable"]
    prefix_digest: Digest
    observation_only: StrictBool = False


class DecisionReleaseBundle(Result):
    model_digest: Digest
    calibration_digest: Digest
    service_digest: Digest


class DecisionPolicyRecord(Result):
    kind: Literal["point_policy"]
    operation: Literal["observer", "promote", "rollback"]
    point_id: PointId
    contract_version: Literal[1, 2] | None = None
    binding_digests: dict[Option, Digest] | None = None
    policy_digest: Digest
    scope_digest: Digest
    mode: Mode | None = None
    thresholds_by_class: list[tuple[Option, Probability]] | None = None
    timeout_seconds: Annotated[float, Field(ge=.001, le=1, allow_inf_nan=False)] | None = None
    allowed_effects: list[Option] | None = None
    rollout_scope: list[Digest] | None = None
    gate_digest: Digest | None = None
    evidence_digest: Digest | None = None
    approval_digest: Digest | None = None
    previous_policy_digest: Digest | None = None
    bundle: DecisionReleaseBundle | None = None
    recorded_at: Nonnegative | None = None
    reason: Literal["operator", "drift", "false_allow", "missed_direct_request", "stale_menu",
                    "tool_recovery_failed", "budget_violation", "latency_regression"] | None = None
