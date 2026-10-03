"""A copied agent's frozen schedule uses the existing gateway and AIAgent loop."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.agent.test_specialist_control import specialist_runtime  # noqa: F401
from tests.tui_gateway.test_artifact_rpc import result
from tests.tui_gateway.test_schedules_rpc import decoded
from tests.tui_gateway.test_managed_schedule_bindings import off_session

pytestmark = pytest.mark.platforms("linux")


@pytest.mark.parametrize("revoked", [None, "regrant", "archive"])
def test_copied_specialist_executes_queued_schedule_with_frozen_namespace(specialist_runtime, monkeypatch, revoked):
    from agent.agent_configuration import configured_agent_selection
    from agent.individual_memory_scope import IndividualMemoryScope
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import bind_submitted_command
    from cron.durable_runtime import tick_durable_schedules
    from run_agent import AIAgent
    from tui_gateway import prompt_admission, server
    rt = specialist_runtime
    peer = SimpleNamespace(write=lambda _frame: True)
    sessions = {"owner": {"agent": rt.agent, "profile_home": str(rt.home), "transport": peer, "session_key": rt.agent.session_id,
        "history": [], "history_lock": threading.RLock()}}
    monkeypatch.setattr(server, "_sessions", sessions)
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    def rpc(method, sid="owner", **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "copied-schedule", "method": method, "params": {
            "schema_version": 1, "session_id": sid, **params}}, transport=peer)
    template = result(rpc("runtime.agent.get", agent_id="researcher"))["agent"]
    copied = result(rpc("runtime.agent.create", copy_from_agent_id="researcher",
        config={**template["config"], "instructions": "Frozen copied instructions"}))["agent"]
    project = result(rpc("runtime.project.get", project_id=rt.project))["project"]
    result(rpc("runtime.project.grants.set", project_id=rt.project, expected_revision=project["revision"],
        grants=project["grants"] + [{"principal_id": "owner", "agent_id": copied["agent_id"], "permissions": ["read", "write"]}]))
    with off_session(rt.home), configured_agent_selection(copied["agent_id"]):
        agent = AIAgent(model=rt.agent.model, provider="openai", api_key="fixture-provider-key", base_url=str(rt.agent.client.base_url),
            session_id="copied-agent", session_db=rt.db, quiet_mode=True, skip_context_files=True, skip_memory=False,
            max_iterations=2, enabled_toolsets=["todo", "memory"])
    agent._cached_system_prompt = "Frozen copied prefix"
    agent._disable_streaming = True
    agent.compression_enabled = False
    agent.save_trajectories = False
    monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: agent.client)
    monkeypatch.setattr(agent, "_close_request_openai_client", lambda *args, **kwargs: None)
    sessions["copy"] = {"agent": agent, "profile_home": str(rt.home), "transport": peer, "session_key": agent.session_id,
        "history": [], "history_lock": threading.RLock()}
    launches = []
    def worker(_rid, _sid, session, text, *, image_paths, runtime_command_receipt):
        launches.append(runtime_command_receipt["command_id"])
        if revoked:
            from tools.capability_broker import CapabilityDenied
            with pytest.raises(CapabilityDenied, match="narrowed or archived"), bind_submitted_command(agent, runtime_command_receipt):
                agent.run_conversation(text)
        else:
            with bind_submitted_command(agent, runtime_command_receipt):
                answer = agent.run_conversation(text)
            assert answer["completed"] and answer["runtime_result"]
        session["running"] = False
        return True
    monkeypatch.setattr(server, "_run_prompt_submit", worker)
    now = time.time()
    definition = {"schema_version": 1, "schedule_id": "copied-loop", "version": 1, "project_id": rt.project,
        "timezone": "Etc/UTC", "trigger": {"kind": "at", "at": now + 60},
        "policy": {"missed_run": "run_once", "grace_seconds": 120, "overlap": "queue"},
        "budget": {"max_checks": 1, "max_bytes": 10000, "deadline_seconds": 300}, "expires_at": now + 3600,
        "kind": "command", "specification": {"prompt": "Summarize privately", "session_id": agent.session_id,
            "authority_description": "Private summary"}}
    try:
        paused = decoded(rpc("runtime.schedule.create", "copy", command_id="create", definition_json=json.dumps(definition)))
        row = decoded(rpc("runtime.schedule.update", "copy", project_id=rt.project, schedule_id=definition["schedule_id"],
            command_id="activate", expected_revision=paused["revision"], state="active"))
        original_context = agent.runtime_context
        with agent_runtime_scope(original_context):
            namespace = IndividualMemoryScope.from_context(original_context).namespace_id
        result(rpc("runtime.agent.update", agent_id=copied["agent_id"], expected_revision=copied["revision"],
            config={**copied["config"], "instructions": "Next session only"}))
        monkeypatch.setattr(time, "time", lambda: row["next_due"])
        with off_session(rt.home):
            assert tick_durable_schedules() == 1
        assert launches == [] and rt.calls == []
        if revoked:
            desired = result(rpc("runtime.agent.get", agent_id=copied["agent_id"]))["agent"]
            if revoked == "archive":
                result(rpc("runtime.agent.archive", agent_id=copied["agent_id"], expected_revision=desired["revision"]))
            else:
                narrowed = result(rpc("runtime.agent.update", agent_id=copied["agent_id"], expected_revision=desired["revision"],
                    config={**desired["config"], "memory_allowed": False}))["agent"]
                result(rpc("runtime.agent.update", agent_id=copied["agent_id"], expected_revision=narrowed["revision"], config=desired["config"]))
        prompt_admission.pump(server)
        assert len(launches) == 1 and [actor for actor, _ in rt.calls] == ([] if revoked else [copied["agent_id"]])
        assert agent.runtime_context == original_context and agent._cached_system_prompt == "Frozen copied prefix"
        with agent_runtime_scope(original_context):
            assert IndividualMemoryScope.from_context(original_context).namespace_id == namespace == copied["builtin_memory_namespace"]
        prompt_admission.pump(server)
        with off_session(rt.home):
            assert tick_durable_schedules() == 0
        assert len(rt.calls) == (0 if revoked else 1)
    finally:
        for session in sessions.values():
            server._release_active_session_slot(session)
        agent.close()
