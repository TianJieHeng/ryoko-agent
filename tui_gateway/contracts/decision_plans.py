"""Closed metadata for point release explanations and cache-safe planner replay."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool

from .base import Result
from .decisions import Digest, Option, Probability

PointId = Annotated[str, Field(pattern=r"^DP(0[1-9]|1[0-6])$")]
Mode = Literal["off", "shadow", "advisory", "enforce"]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class DecisionToolPlan(Result):
    need: Literal["no_tools", "needs_tools", "defer"]
    effort_bucket: Literal["one", "two_three", "four_plus", "defer"]
    families: Annotated[list[Option], Field(max_length=16)]
    verified_tool_ids: Annotated[list[Option], Field(max_length=16)]
    live_catalog_version: Digest
    bundle_id: Digest
    reopen_policy: Literal["authorized_search_describe_call"]
    scope_digest: Digest
    mode: Mode
    fallback: Option | None
    decision_receipt_ids: Annotated[list[Digest], Field(max_length=50)]
    elapsed_ms: Nonnegative


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
