from datetime import datetime

import pytest

from hermes_state_agenda import plan_agenda
from hermes_state_runtime import RuntimeStoreError


def source():
    return {"schema_version": 1, "timezone": "Etc/UTC", "captured_at": "2026-10-03T08:00:00Z",
        "participants": ["owner"], "window": {"start_at": "2026-10-03T08:00:00Z", "end_at": "2026-10-05T18:00:00Z"},
        "busy": [{"start_at": "2026-10-03T10:00:00Z", "end_at": "2026-10-03T11:00:00Z"}]}


def test_ordered_work_respects_fixed_buffer_capacity_and_returns_overflow():
    result = plan_agenda(source(), timezone="Etc/UTC", participants=["owner"],
        windows=[{"start_at": "2026-10-03T09:00:00Z", "end_at": "2026-10-03T13:00:00Z"},
                 {"start_at": "2026-10-04T09:00:00Z", "end_at": "2026-10-04T13:00:00Z"}],
        work=[{"item_id": "first", "duration_minutes": 60}, {"item_id": "second", "duration_minutes": 120},
              {"item_id": "overflow", "duration_minutes": 120}], buffer_minutes=15, daily_capacity_minutes=120,
        now=datetime.fromisoformat("2026-10-03T09:10:00+00:00").timestamp())
    assert result["days"][0]["flexible"][0]["start_at"] == "2026-10-03T11:15:00+00:00"
    assert result["days"][1]["flexible"][0]["item_id"] == "second"
    assert result["overflow"][0]["item_id"] == "overflow"
    assert result["days"][0]["fixed"][0]["end_at"] == "2026-10-03T11:00:00+00:00"
    assert not result["calendar_changed"] and not result["obligations_created"]
    assert not result["live_availability_verified"] and result["stale"]


def test_timezone_dst_identity_and_duplicate_requests_fail_closed():
    data = source()
    args = dict(timezone="Etc/UTC", participants=["owner"], windows=[data["window"]], work=[],
                buffer_minutes=0, daily_capacity_minutes=120, now=0)
    with pytest.raises(RuntimeStoreError):
        plan_agenda(data, **args)  # Not a daily window.
    args["windows"] = [{"start_at": "2026-10-03T09:00:00+01:00", "end_at": "2026-10-03T13:00:00Z"}]
    with pytest.raises(RuntimeStoreError) as error:
        plan_agenda(data, **args)
    assert error.value.code == "commitment_timezone_mismatch"
    data.update(timezone="America/New_York", captured_at="2026-11-01T00:00:00-04:00",
        window={"start_at": "2026-11-01T00:00:00-04:00", "end_at": "2026-11-01T04:00:00-05:00"}, busy=[])
    args.update(timezone=data["timezone"], windows=[data["window"]],
        work=[{"item_id": "fold", "duration_minutes": 120}],
        now=datetime.fromisoformat(data["captured_at"]).timestamp())
    result = plan_agenda(data, **args)
    block = result["days"][0]["flexible"][0]
    assert block["start_at"].endswith("-04:00") and block["end_at"].endswith("-05:00")
    args["work"] *= 2
    with pytest.raises(RuntimeStoreError):
        plan_agenda(data, **args)
