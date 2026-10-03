"""Canonical owner authority, restart idempotency and bounded committed transcript contracts."""
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def config_for(label):
    return {"agent_identity": {"schema_version": 1, "principal_id": f"owner-{label}",
        "profile_id": f"profile-{label}", "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
        "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server
    from tui_gateway.transport import StdioTransport
    import io

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes, stores = {}, {}
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        (home / "config.yaml").write_text(json.dumps(config_for(label)), encoding="utf-8")
        homes[label], stores[label] = home, SessionDB(home / "state.db")
    peer = StdioTransport(lambda: io.StringIO(), threading.Lock())
    monkeypatch.setattr(server, "_stdio_transport", peer)
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)

    def call(method, label="a", via=None, **params):
        monkeypatch.setenv("HERMES_HOME", str(homes[label]))
        monkeypatch.setattr(server, "_hermes_home", homes[label])
        monkeypatch.setattr(server, "_db", stores[label])
        return server.dispatch({"jsonrpc": "2.0", "id": "test", "method": "runtime.conversation." + method,
            "params": {"schema_version": 1, **params}}, transport=via or peer)

    yield SimpleNamespace(call=call, homes=homes, stores=stores, peer=peer, server=server)
    for db in stores.values():
        db.close()


def test_owner_is_configured_transport_scoped_and_create_survives_reopen(runtime):
    from hermes_state import SessionDB

    before = dict(os.environ)
    created = runtime.call("create", idempotency_key="create-1", title="First")["result"]
    sid = created["conversation"]["conversation_id"]
    runtime.stores["a"].close()
    runtime.stores["a"] = SessionDB(runtime.homes["a"] / "state.db")
    receipt = runtime.call("operation.get", idempotency_key="create-1")["result"]
    assert receipt["found"] and receipt["operation"] == "create" and receipt["conversation"] == created["conversation"]
    missing = runtime.call("operation.get", idempotency_key="never-dispatched")["result"]
    assert not missing["found"] and missing["conversation"] is None
    assert not runtime.call("operation.get", "b", idempotency_key="create-1")["result"]["found"]
    again = runtime.call("create", idempotency_key="create-1", title="First")["result"]
    assert again["conversation"] == created["conversation"] and not again["created"]
    assert runtime.call("create", idempotency_key="create-1", title="Changed")["error"]["data"]["code"] == "idempotency_conflict"
    for label in ("a", "b", "a"):
        caps = runtime.call("capabilities", label)["result"]
        assert caps["identity"]["principal_id"] == f"owner-{label}"
        assert "profile_home" not in json.dumps(caps)
    assert runtime.call("history", "b", conversation_id=sid)["error"]["data"]["code"] == "session_not_found"
    assert not runtime.call("list", "b")["result"]["conversations"]
    assert runtime.call("history", conversation_id=sid)["result"]["messages"] == []
    assert runtime.call("create", idempotency_key="evil", principal_id="owner-b")["error"]["code"] == 4000
    assert runtime.call("list", profile="b")["error"]["code"] == 4000
    stranger = SimpleNamespace(write=lambda frame: True)
    assert runtime.call("list", via=stranger)["error"]["data"]["code"] == "runtime_transport_unsupported"
    # A new principal in the same profile DB cannot see or rebind the prior principal's rows.
    (runtime.homes["a"] / "config.yaml").write_text(json.dumps(config_for("changed")), encoding="utf-8")
    assert runtime.call("list")["result"]["conversations"] == []
    assert runtime.call("bind", conversation_id=sid)["error"]["data"]["code"] == "session_not_found"
    assert {k: v for k, v in os.environ.items() if k != "HERMES_HOME"} == {k: v for k, v in before.items() if k != "HERMES_HOME"}


def test_mutations_are_owner_scoped_idempotent_and_revision_checked(runtime):
    first = runtime.call("create", idempotency_key="new", title="Find me")["result"]["conversation"]
    sid = first["conversation_id"]
    rename = {"conversation_id": sid, "idempotency_key": "rename", "expected_revision": 1, "title": "Named"}
    result = runtime.call("rename", **rename)["result"]
    assert result["conversation"]["revision"] == 2
    assert runtime.call("rename", **rename)["result"] == result
    assert runtime.call("rename", **{**rename, "title": "Other"})["error"]["data"]["code"] == "idempotency_conflict"
    assert runtime.call("rename", **{**rename, "idempotency_key": "stale"})["error"]["data"]["code"] == "revision_conflict"
    assert runtime.call("list", query="named")["result"]["conversations"][0]["conversation_id"] == sid
    archived = runtime.call("archive", conversation_id=sid, idempotency_key="archive", expected_revision=2, archived=True)["result"]
    assert archived["conversation"]["archived"] and archived["conversation"]["revision"] == 3
    assert not runtime.call("list")["result"]["conversations"]
    assert runtime.call("list", archived=True)["result"]["conversations"][0]["conversation_id"] == sid
    # Replaying the original rename receipt cannot undo a later archive or title mutation.
    assert runtime.call("rename", **rename)["result"] == result
    assert runtime.call("operation.get", idempotency_key="rename")["result"]["conversation"] == result["conversation"]
    assert runtime.call("operation.get", idempotency_key="archive")["result"]["conversation"] == archived["conversation"]
    assert runtime.call("list", archived=True)["result"]["conversations"][0]["revision"] == 3
    runtime.call("create", idempotency_key="second", title="Two")
    runtime.call("create", idempotency_key="third", title="Three")
    page = runtime.call("list", limit=1)["result"]
    page2 = runtime.call("list", limit=1, cursor=page["next_cursor"])["result"]
    assert page["conversations"][0]["conversation_id"] != page2["conversations"][0]["conversation_id"]
    assert runtime.call("list", archived=True, cursor=page["next_cursor"])["error"]["data"]["code"] == "invalid_cursor"


def test_history_chunks_preserve_ids_watermark_and_safe_text_through_compression(runtime):
    sid = runtime.call("create", idempotency_key="history")["result"]["conversation"]["conversation_id"]
    db = runtime.stores["a"]
    text = "Hello 🦊\n" * 6000 + "\x00after-nul"
    user = {"role": "user", "content": text, "message_uid": "human-stable"}
    assistant = {"role": "assistant", "content": "Answer", "message_uid": "assistant-stable"}
    db.append_messages_batch(sid, [user, assistant,
        {"role": "system", "content": "private system"},
        {"role": "tool", "content": "private tool"},
        {"role": "user", "content": "private summary", "_compressed_summary": True},
        {"role": "assistant", "content": "private hidden", "display_kind": "hidden"}])
    first = runtime.call("history", conversation_id=sid, limit=1)["result"]
    assert first["messages"][0]["message_id"] == "human-stable" and first["has_more"]
    binding = db.get_session_model_config_value(sid, "agent_identity")
    db.publish_compression_child(parent_session_id=sid, child_session_id="compressed-tip", source="web",
        messages=[user, assistant], model_config={"agent_identity": binding}, require_compression_lease=False)
    db.append_message("compressed-tip", "assistant", "Later")
    chunks = first["messages"]
    cursor = first["next_cursor"]
    for _ in range(30):
        page = runtime.call("export", conversation_id=sid, limit=2, cursor=cursor)["result"]
        assert page["snapshot_max_row_id"] == first["snapshot_max_row_id"]
        assert page["lineage"] == [sid, "compressed-tip"]
        assert sum(len(item["text"].encode()) for item in page["messages"]) <= 262144
        chunks.extend(page["messages"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    else:
        pytest.fail("history pagination failed to make progress")
    assert ''.join(row["text"] for row in chunks if row["message_id"] == "human-stable") == text.replace('\x00', '')
    assert [row["text"] for row in chunks if row["role"] == "assistant"] == ["Answer"]
    assert "private" not in json.dumps(chunks)
    assert all(row["committed"] and row["command_id"] is None for row in chunks)
    new = runtime.call("history", conversation_id=sid)["result"]
    assert {row["message_id"] for row in new["messages"]} >= {"human-stable", "assistant-stable"}
    assert any(row["text"] == "Later" for row in new["messages"])
    # No authority inheritance to an unrelated branch, even if its parent compressed.
    db.create_session("branch", source="web", parent_session_id=sid, model_config={"_branched_from": sid})
    db.append_message("branch", "user", "private branch")
    assert "private branch" not in json.dumps(runtime.call("export", conversation_id=sid))


def test_structured_content_projects_text_only_and_bind_is_honest(runtime):
    from agent.agent_identity import resolve_agent_context

    sid = runtime.call("create", idempotency_key="bound")["result"]["conversation"]["conversation_id"]
    db = runtime.stores["a"]
    db.append_message(sid, "user", [{"type": "text", "text": "Safe text"},
        {"type": "image_url", "image_url": {"url": "private-image-url"}},
        {"type": "thinking", "text": "private reasoning"}])
    page = runtime.call("history", conversation_id=sid)["result"]
    assert page["messages"][0]["text"] == "Safe text"
    assert page["messages"][0]["non_text_omitted"]
    binding = runtime.call("bind", conversation_id=sid)["result"]
    assert binding["readiness"] == "building"
    assert runtime.call("bind", conversation_id=sid)["result"]["session_id"] == binding["session_id"]
    session = runtime.server._sessions[binding["session_id"]]
    context = resolve_agent_context(config_for("a"), session_id=sid, profile_home=runtime.homes["a"])
    session["agent"] = SimpleNamespace(runtime_context=context, _session_db=db, session_id=sid)
    assert runtime.call("bind", conversation_id=sid)["result"]["readiness"] == "ready"
    session["agent_error"] = "private provider credential failure"
    failed = runtime.call("bind", conversation_id=sid)["result"]
    assert failed["readiness"] == "failed" and failed["failure_code"] == "agent_build_failed"
    assert "private provider" not in json.dumps(failed)
    session["transport"] = SimpleNamespace(write=lambda frame: True)
    assert runtime.call("bind", conversation_id=sid)["error"]["data"]["code"] == "identity_mismatch"
