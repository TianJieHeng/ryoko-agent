"""Explicit human admission for the first offline speech operation in a session.

This uses the existing finite-control journal and budget path. It never admits a
model/tool run, starts capture, allocates worker slots, or renews an old budget.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

from agent.bounded_services import require
from agent.speech_budget import (SpeechBudgetBinding, current_speech_account,
                                 speech_policy, voice_admission_record)
from agent.budget_account import actor_for


def _descriptor(agent, account_id, owner_check):
    from agent.project_context import project_access
    context, db = agent.runtime_context, agent._session_db
    actor = actor_for(context)
    account = db.get_budget_account(account_id, actor)
    mission = db.get_mission(str(agent.session_id), actor, access=project_access(context))
    binding = SpeechBudgetBinding(agent, context, account_id, "admission", "stt", account["deadline"],
        mission["mission_id"] if mission else None, mission.get("deadline") if mission else None, owner_check)
    _, _, policy, _ = binding.account()
    from agent.speech_local import WALL_SECONDS
    maximum = min(math.floor(WALL_SECONDS * 1000), policy.record["request_timeout_ms"],
                  math.floor((binding.effective_deadline - time.time()) * 1000))
    require(maximum >= 1500, "speech_budget_deadline_insufficient")
    # Read the same ancestors the transactional reservation will charge. This
    # is an honest readiness snapshot, not a new counter or a reservation.
    with db._runtime_read() as conn:
        row = db._budget_account_on_conn(conn, account_id, actor)
        ancestors = db._budget_ancestors_on_conn(conn, row)
        db._budget_check_open(ancestors, deadline=binding.effective_deadline)
        for ancestor in ancestors:
            limits, used, held = (json.loads(ancestor[key]) for key in
                                  ("limits_json", "consumed_json", "reserved_json"))
            for key, required in {"attempts": 1, "wall_ms": maximum, "executor_slots": 1}.items():
                require(used[key] + held[key] + required <= limits[key], "budget_exhausted")
    return {"status": "admitted", "budget_account_id": account_id, "root_id": account["root_id"],
            "deadline": binding.effective_deadline, "capture_started": False, "model_dispatched": False,
            "mission_accepted": False, "reservation_created": False,
            "budget": {key: account[key] for key in ("state", "limits", "consumed", "reserved",
                "unknown_usage", "unknown_operations", "debt", "cost_tracking", "invoice_guarantee")}}


def admit_voice_control(agent, ui_session_id, request_id, owner_check):
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == agent.runtime_context, "identity_mismatch")
    require(owner_check(), "speech_owner_changed")
    policy = speech_policy(agent)
    if policy is None:
        return {"status": "not_required", "capture_started": False, "model_dispatched": False,
                "mission_accepted": False, "reservation_created": False}
    account_id = current_speech_account(agent)
    record = voice_admission_record(agent)
    command_id = "speech-admission:" + hashlib.sha256(request_id.encode()).hexdigest()
    if account_id is not None:
        if record is None or record["receipt"]["run_id"] != account_id or record["status"] == "completed":
            return _descriptor(agent, account_id, owner_check)
        # A crash between admission and completion may reopen only the exact
        # original live control. Neither a fresh ID nor a reconnect remints it.
        require(record["receipt"]["command_id"] == command_id, "speech_admission_unfinished")
    run = begin_artifact_control(agent, ui_session_id, command_id,
        {"mode": "runtime.voice.admit", "purpose": "offline_speech"})
    with artifact_control_scope(run):
        require(run.budget is not None and run.budget.policy == policy, "speech_budget_unsupported")
        from hermes_state_runtime import RuntimeStoreError
        try:
            result = _descriptor(agent, run.budget.account_id, owner_check)
        except RuntimeStoreError as exc:
            finish_artifact_control(run, {"status": "blocked", "reason": exc.code}, status="blocked")
            raise
        finish_artifact_control(run, result)
        return result
