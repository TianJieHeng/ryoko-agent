"""Scheduled commands exercise the existing gateway pump, AIAgent and outbox."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.agent.test_specialist_control import specialist_runtime  # noqa: F401
from tests.tui_gateway.test_artifact_rpc import result
from tests.tui_gateway.test_schedules_rpc import decoded

pytestmark = pytest.mark.platforms("linux")


def test_scheduler_uses_existing_agent_loop_and_delivery_failure_cannot_rerun(specialist_runtime, monkeypatch):
    from agent.runtime_commands import bind_submitted_command
    from tui_gateway import prompt_admission, server
    rt = specialist_runtime
    agent = rt.agent
    agent._disable_streaming = True
    monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: agent.client)
    monkeypatch.setattr(agent, "_close_request_openai_client", lambda *args, **kwargs: None)
    peer = SimpleNamespace(write=lambda _frame: False)
    session = {"agent": agent, "profile_home": str(rt.home), "transport": peer, "session_key": agent.session_id,
               "history": [], "history_lock": threading.RLock()}
    monkeypatch.setattr(server, "_sessions", {"schedule-live": session})
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    launches = []
    def worker(_rid, sid, owner, text, *, image_paths, runtime_command_receipt):
        launches.append(runtime_command_receipt["command_id"])
        with bind_submitted_command(agent, runtime_command_receipt):
            answer = agent.run_conversation(text)
        assert answer["completed"] and answer["runtime_result"]
        owner["running"] = False
        return True
    monkeypatch.setattr(server, "_run_prompt_submit", worker)
    def rpc(method, **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "schedule-execution", "method": method, "params": {
            "schema_version": 1, "session_id": "schedule-live", **params}}, transport=peer)
    now = time.time()
    definition = {"schema_version": 1, "schedule_id": "real-loop", "version": 1, "project_id": rt.project,
        "timezone": "Etc/UTC", "trigger": {"kind": "at", "at": now + 60},
        "policy": {"missed_run": "run_once", "grace_seconds": 120, "overlap": "queue"},
        "budget": {"max_checks": 2, "max_bytes": 10000, "deadline_seconds": 300}, "expires_at": now + 3600,
        "kind": "command", "specification": {"prompt": "Summarize the project privately", "session_id": agent.session_id,
            "authority_description": "Private summary only"}}
    try:
        paused = decoded(rpc("runtime.schedule.create", command_id="create-schedule", definition_json=json.dumps(definition)))
        active = decoded(rpc("runtime.schedule.update", project_id=rt.project, schedule_id=definition["schedule_id"],
            expected_revision=paused["revision"], state="active", command_id="activate-schedule"))
        params = dict(project_id=rt.project, schedule_id=definition["schedule_id"], expected_revision=active["revision"], command_id="run-once")
        occurrence = decoded(rpc("runtime.schedule.run_now", **params))
        assert rt.calls == [] and launches == []
        # Detached UI does not cancel the accepted queue reference.
        session["transport"] = server._detached_ws_transport
        prompt_admission.pump(server)
        assert rt.calls == []
        session["transport"] = peer
        prompt_admission.pump(server)
        assert launches == [occurrence["command_id"]]
        assert [actor for actor, _ in rt.calls] == ["primary"]
        assert agent._cached_system_prompt == "Unchanged parent prefix"
        completed = rt.db.read_runtime_command(agent.session_id, occurrence["command_id"])
        assert completed["status"] == "completed"
        assert decoded(rpc("runtime.schedule.run_now", **params))["command_receipt"] == occurrence["command_receipt"]
        # Unacknowledged delivery uses the outbox, not schedule admission.
        delivery_id = completed["result"]["runtime_result"]["delivery_id"]
        for _ in range(2):
            result(rpc("runtime.delivery.retry", delivery_id=delivery_id))
            prompt_admission.pump(server)
        assert launches == [occurrence["command_id"]] and len(rt.calls) == 1
        status = result(rpc("runtime.delivery.status", delivery_id=delivery_id))
        assert status["state"] != "delivered"
    finally:
        server._release_active_session_slot(session)
