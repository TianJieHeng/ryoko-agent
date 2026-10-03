"""Human-created bounded same-client monitor notification policy and receipts."""
from typing import Annotated
from pydantic import Field
from .base import Payload
from .registry import method, event
from .schedules import ScheduleGetParams, ScheduleRecordResult
from .runtime_v1 import RuntimeIdentifier
from pydantic import StrictInt


class MonitorListParams(ScheduleGetParams):
    cursor_json: Annotated[str, Field(min_length=2, max_length=256)] | None = None


class MonitorControlParams(ScheduleGetParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class MonitorPolicyParams(MonitorControlParams):
    policy_json: Annotated[str, Field(min_length=2, max_length=4096)]


class MonitorSnoozeParams(MonitorControlParams):
    until_at: float | None


class MonitorDismissParams(MonitorControlParams):
    intent_id: RuntimeIdentifier


class MonitorAvailablePayload(Payload):
    delivery_id: RuntimeIdentifier
    attempt_token: RuntimeIdentifier
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    notification_json: Annotated[str, Field(min_length=2, max_length=131072)]


_SPECS = {
    "runtime.monitor.policy.set": (MonitorPolicyParams, "Authorize bounded exact-session local monitor delivery"),
    "runtime.monitor.snooze": (MonitorSnoozeParams, "Suppress notification delivery until explicit expiry without stopping checks"),
    "runtime.monitor.dismiss": (MonitorDismissParams, "Persist dismissal of one immutable meaningful-change notice"),
    "runtime.monitor.notifications": (MonitorListParams, "Read exact-session retained notices, pending holds and delivery truth"),
}
for name, (params, doc) in _SPECS.items():
    method(name, params=params, result=ScheduleRecordResult, doc=doc)
event("runtime.monitor.available", MonitorAvailablePayload)
