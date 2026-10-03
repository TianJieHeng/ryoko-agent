"""Deterministic day/week capacity proposals over supplied immutable availability.

The order is the human's requested order. Nothing creates obligations, invites,
or calendar events, and a busy source snapshot is never called live availability.
"""
from datetime import datetime

from hermes_state_commitment_sources import calendar_preview, identifier, require, zone, zoned_timestamp


def plan_agenda(source, *, timezone, participants, windows, work, buffer_minutes,
                daily_capacity_minutes, now):
    require(isinstance(windows, list) and 1 <= len(windows) <= 7,
            "invalid_agenda", "Supply one to seven explicit daily work windows")
    require(isinstance(work, list) and len(work) <= 100,
            "invalid_agenda", "At most 100 explicitly ordered work blocks are supported")
    require(type(buffer_minutes) is int and 0 <= buffer_minutes <= 120
            and type(daily_capacity_minutes) is int and 1 <= daily_capacity_minutes <= 720,
            "invalid_agenda", "Specify bounded buffers and daily flexible-work capacity")
    tz = zone(timezone)
    days, seen = [], set()
    for window in windows:
        require(isinstance(window, dict) and set(window) == {"start_at", "end_at"},
                "invalid_agenda", "Each daily window needs exact start and end timestamps")
        start, end = (zoned_timestamp(window[key], timezone) for key in ("start_at", "end_at"))
        date = datetime.fromtimestamp(start, tz).date()
        require(start < end and end - start <= 24 * 3600
                and datetime.fromtimestamp(end - 0.001, tz).date() == date
                and date not in seen and (not days or start >= days[-1]["end"]),
                "invalid_agenda", "Daily windows must be ordered, distinct and within one local day")
        seen.add(date)
        days.append({"date": date.isoformat(), "start": start, "end": end, "used": 0, "allocated": []})
    availability = calendar_preview(source, timezone=timezone, participants=participants,
        start_at=windows[0]["start_at"], end_at=windows[-1]["end_at"], duration_minutes=5, now=now)
    busy = [(zoned_timestamp(row["start_at"], timezone), zoned_timestamp(row["end_at"], timezone))
            for row in source["busy"]]
    tasks, ids = [], set()
    for item in work:
        require(isinstance(item, dict) and set(item) == {"item_id", "duration_minutes"},
                "invalid_agenda", "Work blocks need exact IDs and duration estimates")
        identifier(item["item_id"])
        minutes = item["duration_minutes"]
        require(item["item_id"] not in ids and type(minutes) is int and 1 <= minutes <= 720,
                "invalid_agenda", "Work IDs must be unique and durations bounded whole minutes")
        ids.add(item["item_id"])
        tasks.append(item)
    def iso(stamp):
        return datetime.fromtimestamp(stamp, tz).isoformat()
    overflow, buffer = [], buffer_minutes * 60
    for item in tasks:
        duration, placed = item["duration_minutes"] * 60, False
        for day in days:
            if day["used"] + item["duration_minutes"] > daily_capacity_minutes:
                continue
            occupied = sorted(busy + [(row["start"], row["end"]) for row in day["allocated"]])
            cursor = max(now, day["start"])
            for start, end in occupied:
                if end + buffer <= cursor:
                    continue
                if cursor + duration <= start - buffer:
                    break
                if cursor < end + buffer and start - buffer < cursor + duration:
                    cursor = end + buffer
            if cursor + duration <= day["end"]:
                day["allocated"].append({**item, "start": cursor, "end": cursor + duration})
                day["used"] += item["duration_minutes"]
                placed = True
                break
        if not placed:
            overflow.append({**item, "reason": "no_contiguous_capacity_after_fixed_work_and_buffers"})
    return {"timezone": timezone, "view": "day" if len(days) == 1 else "week",
        "days": [{"date": day["date"], "window": {"start_at": iso(day["start"]), "end_at": iso(day["end"])},
            "fixed": [{"start_at": iso(max(a, day["start"])), "end_at": iso(min(b, day["end"]))}
                      for a, b in busy if a < day["end"] and b > day["start"]],
            "flexible": [{"item_id": row["item_id"], "duration_minutes": row["duration_minutes"],
                          "start_at": iso(row["start"]), "end_at": iso(row["end"])} for row in day["allocated"]],
            "planned_minutes": day["used"], "capacity_minutes": daily_capacity_minutes} for day in days],
        "overflow": overflow, "buffer_minutes": buffer_minutes, "order": "explicit_input_order",
        "planning_at": iso(now), "captured_at": availability["captured_at"], "stale": availability["stale"],
        "availability_status": "supplied_snapshot_only", "live_availability_verified": False,
        "calendar_changed": False, "invitation_sent": False, "obligations_created": False}
