"""Owned specialist RPCs drive real admission and real AIAgent child execution."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from tests.agent.test_specialist_control import specialist_runtime  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def specialist_rpc(specialist_runtime, monkeypatch):
    from tui_gateway import prompt_admission, server
    rt = specialist_runtime
    peer = SimpleNamespace(write=lambda frame: True)
    session = {"agent": rt.agent, "profile_home": str(rt.home), "transport": peer,
               "session_key": rt.agent.session_id, "history": [], "history_lock": threading.RLock()}
    monkeypatch.setattr(server, "_sessions", {"live": session})
    launched = []

    def worker(_rid, sid, owner, text, *, image_paths, runtime_command_receipt):
        # Only the gateway's thread scheduling is synchronous in this fixture;
        # command admission, claim, lease, child, SDK and persistence are real.
        assert owner is session and text == rt.preview()["selection"]["objective"]
        launched.append(runtime_command_receipt["command_id"])
        try:
            rt.execute(runtime_command_receipt)
        finally:
            owner["running"] = False
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", worker)

    def call(method, *, via=None, session_id="live", **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "rpc", "method": method, "params": {
            "schema_version": 1, "session_id": session_id, **params}}, transport=via or peer)

    yield SimpleNamespace(rt=rt, peer=peer, session=session, server=server, call=call, launched=launched)
    if prompt_admission._HANDLE is not None:
        prompt_admission._HANDLE.cancel(wait=2)
        prompt_admission._HANDLE = None
    prompt_admission._STARTED = False
    prompt_admission._STOPPING = False
    server._release_active_session_slot(session)


def prepare(rpc):
    result = rpc.call("runtime.specialist.preview", project_id=rpc.rt.project, specialist_id="researcher",
                      objective="Summarize the configured method")
    assert "result" in result, result
    return result["result"]


def handoff(preview, key="handoff"):
    return {"command_id": key, "idempotency_key": key, "expected_revision": preview["runtime_revision"],
            "selection": preview["selection"], "preview_sha256": preview["preview_sha256"]}


def test_rpc_selection_launch_replay_snapshot_and_reconnect_use_one_command(specialist_rpc):
    from tui_gateway.contracts.runtime_v1 import MissionSnapshot, RuntimeEventsSinceResult
    from tui_gateway.contracts.specialists import SpecialistCatalog, SpecialistPreview, SpecialistStatus
    rpc = specialist_rpc
    catalog = rpc.call("runtime.specialist.catalog", project_id=rpc.rt.project)
    SpecialistCatalog.model_validate(catalog["result"])
    preview = prepare(rpc)
    SpecialistPreview.model_validate(preview)
    answer = rpc.call("runtime.specialist.handoff", **handoff(preview))
    assert "result" in answer, answer
    assert answer["result"]["status"] == "accepted"
    assert rpc.launched == ["handoff"] and [name for name, _ in rpc.rt.calls] == ["researcher"]
    state = rpc.call("runtime.specialist.status", command_id="handoff")["result"]
    SpecialistStatus.model_validate(state)
    assert state["completion"]["schema_valid"] and state["outcome"] == "completed"
    snap = rpc.call("runtime.snapshot")["result"]
    MissionSnapshot.model_validate(snap)
    assert snap["state"]["last_operation"] == "submit"
    replay = rpc.call("runtime.events.since")["result"]
    RuntimeEventsSinceResult.model_validate(replay)
    assert any(event["type"] == "tool.completed" for event in replay["events"])
    assert "bounded specialist answer" not in json.dumps(replay)
    assert "specialist_handoff" not in json.dumps(replay)
    old_peer = rpc.peer
    rpc.peer = SimpleNamespace(write=lambda frame: True)
    rpc.session["transport"] = rpc.peer
    assert rpc.call("runtime.specialist.status", via=old_peer, command_id="handoff")["error"]["code"] == 4001
    assert rpc.call("runtime.specialist.handoff", via=rpc.peer, **handoff(preview))["result"] == answer["result"]
    assert rpc.launched == ["handoff"] and len(rpc.rt.calls) == 1


def test_generic_runtime_input_cannot_forge_specialist_payload_or_grants(specialist_rpc):
    rpc = specialist_rpc
    preview = prepare(rpc)
    payload = rpc.rt.envelope(preview)["payload"]
    answer = rpc.call("runtime.command", command_id="forged", idempotency_key="forged", expected_revision=None,
                      operation="submit", payload=payload)
    assert answer["error"]["code"] == 4000
    request = handoff(preview)
    request["selection"] = {**request["selection"], "agent_id": "primary", "grants": {"allowed_tools": ["terminal"]}}
    assert rpc.call("runtime.specialist.handoff", **request)["error"]["code"] == 4000
    assert not rpc.launched and not rpc.rt.calls
    assert rpc.call("runtime.specialist.status", command_id="missing")["error"]["data"]["code"] == "specialist_command_not_found"


def test_queued_cancel_and_disconnect_do_not_create_a_replacement_child(specialist_rpc):
    from tui_gateway import prompt_admission
    rpc = specialist_rpc
    preview = prepare(rpc)
    rpc.session["running"] = True
    accepted = rpc.call("runtime.specialist.handoff", **handoff(preview))["result"]
    assert accepted["status"] == "accepted" and not rpc.launched
    assert rpc.call("runtime.specialist.status", command_id="handoff")["result"]["outcome"] == "pending"
    cancelled = rpc.call("runtime.command", command_id="cancel", idempotency_key="cancel", expected_revision=None,
                         operation="cancel", payload={"reason": "Cancel queued specialist"})
    assert "result" in cancelled, cancelled
    rpc.session["running"] = False
    rpc.session["transport"] = rpc.server._detached_ws_transport
    prompt_admission.pump(rpc.server)
    rpc.session["transport"] = rpc.peer
    prompt_admission.pump(rpc.server)
    assert rpc.call("runtime.specialist.handoff", **handoff(preview))["result"] == accepted
    assert rpc.call("runtime.specialist.status", command_id="handoff")["result"]["outcome"] == "cancelled"
    assert not rpc.launched and not rpc.rt.calls


def test_catalog_is_read_only_and_two_profile_scope_cannot_cross(specialist_rpc, monkeypatch, tmp_path):
    from agent.agent_identity import resolve_agent_context
    from hermes_state import SessionDB
    rpc = specialist_rpc
    before_env = dict(os.environ)
    before = rpc.rt.db.read_runtime_snapshot(rpc.rt.agent.session_id)["revision"]
    good = rpc.call("runtime.specialist.catalog", project_id=rpc.rt.project)
    other_home = tmp_path / "second-profile"
    other_home.mkdir()
    raw = json.loads(json.dumps(rpc.rt.raw))
    raw["agent_identity"]["profile_id"] = "second"
    raw["agent_identity"]["agents"]["primary"]["project_grants"] = []
    (other_home / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="parent", profile_home=other_home)
    db = SessionDB(other_home / "state.db")
    db.create_session("parent", source="tui")
    db.claim_session_agent_identity("parent", context.identity.to_record())
    other_peer = SimpleNamespace(write=lambda frame: True)
    other = SimpleNamespace(runtime_context=context, _session_db=db, session_id="parent",
                            _runtime_budget_policy=rpc.rt.agent._runtime_budget_policy)
    rpc.server._sessions["second"] = {"agent": other, "profile_home": str(other_home), "transport": other_peer,
                                      "history_lock": threading.RLock(), "history": [], "session_key": "parent"}
    try:
        denied = rpc.call("runtime.specialist.catalog", session_id="second", via=other_peer, project_id=rpc.rt.project)
        assert denied["error"]["data"]["code"] == "project_not_granted"
        assert rpc.call("runtime.specialist.catalog", project_id=rpc.rt.project) == good
        assert rpc.rt.db.read_runtime_snapshot(rpc.rt.agent.session_id)["revision"] == before
        assert not rpc.rt.calls and dict(os.environ) == before_env
    finally:
        db.close()
