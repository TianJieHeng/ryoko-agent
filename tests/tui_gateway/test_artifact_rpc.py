"""BE07 public RPCs exercise real projects, approvals, immutable bytes and leases."""
import base64
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from hermes_state import SessionDB
    from tui_gateway import server

    homes, agents, peers, sessions, configs = {}, {}, {}, {}, {}
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        config = {"agent_identity": {"schema_version": 1, "principal_id": "owner",
            "profile_id": "profile-" + label, "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
            "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                  "project_grants": []}}}}
        (home / "config.yaml").write_text(json.dumps(config))
        context = resolve_agent_context(config, session_id="stored-session", profile_home=home)
        db = SessionDB(home / "state.db")
        db.create_session(context.identity.session_id, source="tui")
        db.claim_session_agent_identity(context.identity.session_id, context.identity.to_record())
        # Artifact RPCs cannot rely on a provider route, credentials or client.
        agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=context.identity.session_id)
        peer = SimpleNamespace(write=lambda _frame: True)
        sessions["live-" + label] = {"agent": agent, "profile_home": str(home), "transport": peer,
            "session_key": agent.session_id, "history": [], "history_lock": threading.RLock()}
        homes[label], agents[label], peers[label], configs[label] = home, agent, peer, config
    monkeypatch.setenv("HERMES_HOME", str(homes["a"]))
    monkeypatch.setattr(server, "_sessions", sessions)

    def call(method, label="a", via=None, **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "artifact-test", "method": method,
            "params": {"session_id": "live-" + label, "schema_version": 1, **params}},
            transport=via if via is not None else peers[label])

    def allow(project_id, label="a"):
        # Explicit administrator policy reload in the fixture. The project RPC
        # itself must never mutate the immutable agent ceiling.
        config = configs[label]
        config["agent_identity"]["agents"]["ryoko"]["project_grants"].append(project_id)
        (homes[label] / "config.yaml").write_text(json.dumps(config))
        agent = agents[label]
        agent.session_id = "configured-" + str(len(config["agent_identity"]["agents"]["ryoko"]["project_grants"]))
        agent.runtime_context = resolve_agent_context(config, session_id=agent.session_id, profile_home=homes[label])
        agent._session_db.create_session(agent.session_id, source="tui")
        sessions["live-" + label]["session_key"] = agent.session_id
        agent._session_db.claim_session_agent_identity(agent.session_id, agent.runtime_context.identity.to_record())

    def project(label="a"):
        record = result(call("runtime.project.create", label, name="Research", grants=grants()))["project"]
        assert record["id"] not in agents[label].runtime_context.policy.project_grants
        allow(record["id"], label)
        return record

    yield SimpleNamespace(call=call, allow=allow, project=project, homes=homes, agents=agents, peers=peers)
    for agent in agents.values():
        agent._session_db.close()


def grants():
    return [{"principal_id": "owner", "agent_id": "ryoko", "permissions": ["read", "write", "share"]}]


def result(response):
    assert response is not None and "error" not in response, response
    return response["result"]


def denied(response, code=None):
    assert "error" in response, response
    if code is not None:
        assert response["error"].get("data", {}).get("code") == code, response
    return response["error"]


def publish(rpc, params, *, mode="", label="a"):
    prefix = "runtime.artifact." + mode
    preview = result(rpc.call(prefix + "prepare", label, **params))
    approved = result(rpc.call(prefix + "publish", label, **params,
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"]))
    assert approved["sha256"] == preview["sha256"] and approved["size"] == preview["size"]
    return approved


def download(rpc, project_id, artifact_id, version=None, *, label="a"):
    data, offset = bytearray(), 0
    while True:
        chunk = result(rpc.call("runtime.artifact.get", label, project_id=project_id,
                               artifact_id=artifact_id, version=version, offset=offset, limit=17))
        assert chunk["preview_mode"] == "plain_text" and chunk["offset"] == offset
        data.extend(base64.b64decode(chunk["data_base64"], validate=True))
        assert chunk["next_offset"] == len(data)
        if chunk["eof"]:
            assert hashlib.sha256(data).hexdigest() == chunk["sha256"]
            assert len(data) == chunk["size"]
            return bytes(data)
        offset = chunk["next_offset"]


def test_owned_project_create_claim_grants_and_metadata_cas(artifacts):
    from hermes_cli import projects_db

    created = result(artifacts.call("runtime.project.create", name="Fresh"))["project"]
    assert created["grants"] == [] and created["owner_principal_id"] == "owner"
    assert artifacts.agents["a"].runtime_context.policy.project_grants == frozenset()
    changed = result(artifacts.call("runtime.project.grants.set", project_id=created["id"],
        expected_revision=created["revision"], grants=grants()))["project"]
    assert changed["revision"] > created["revision"]
    denied(artifacts.call("runtime.project.update", project_id=created["id"],
        expected_revision=created["revision"], changes={"purpose": "stale"}), "project_revision_conflict")
    updated = result(artifacts.call("runtime.project.update", project_id=created["id"],
        expected_revision=changed["revision"], changes={"purpose": "Verified sources"}))["project"]
    assert updated["purpose"] == "Verified sources"
    assert artifacts.agents["a"].runtime_context.policy.project_grants == frozenset()
    with projects_db.connect_closing(artifacts.homes["a"] / "projects.db") as conn:
        legacy = projects_db.create_project(conn, name="Legacy")
        original = projects_db.project_record(conn, legacy)
    claimed = result(artifacts.call("runtime.project.claim", project_id=legacy,
        expected_revision=original["revision"]))["project"]
    assert claimed["owner_principal_id"] == "owner" and claimed["grants"] == []
    denied(artifacts.call("runtime.project.claim", project_id=legacy,
        expected_revision=claimed["revision"]), "project_owner_required")
    assert {row["id"] for row in result(artifacts.call("runtime.project.list"))["projects"]} == {legacy, created["id"]}


def test_two_stage_approval_complete_utf8_download_and_terminal_replay(artifacts):
    project = artifacts.project()
    content = "# Café\n\n" + "Exact UTF-8 🦊 <script>alert(1)</script>\n" * 30
    params = {"project_id": project["id"], "command_id": "write-1", "request_id": "request-1", "content": content}
    preview = result(artifacts.call("runtime.artifact.prepare", **params))
    assert preview == result(artifacts.call("runtime.artifact.prepare", **params))
    assert preview["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert preview["size"] == len(content.encode())
    denied(artifacts.call("runtime.artifact.get", project_id=project["id"], artifact_id=preview["artifact_id"], version=preview["version"]))
    denied(artifacts.call("runtime.artifact.publish", **params, approval_id=preview["approval_id"], approval_digest="0" * 64), "approval_mismatch")
    done = result(artifacts.call("runtime.artifact.publish", **params,
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"]))
    assert done["disposition"] == "canonical" and done["approval_status"] == "approved"
    assert download(artifacts, project["id"], done["artifact_id"]) == content.encode()
    status = result(artifacts.call("runtime.artifact.status", command_id="write-1"))
    assert status["status"] == "completed" and status["result"] == done and not status["owner_live"]
    denied(artifacts.call("runtime.artifact.publish", **params,
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"]), "artifact_control_finished")
    assert not hasattr(artifacts.agents["a"], "client")


def test_changed_publication_payload_conflicts_and_cancel_is_terminal(artifacts):
    project = artifacts.project()
    params = {"project_id": project["id"], "command_id": "write-1", "request_id": "request-1", "content": "# First\n"}
    preview = result(artifacts.call("runtime.artifact.prepare", **params))
    denied(artifacts.call("runtime.artifact.publish", **{**params, "content": "# Changed\n"},
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"]), "idempotency_conflict")
    cancelled = result(artifacts.call("runtime.artifact.cancel", command_id="write-1"))
    assert cancelled["status"] == "cancelled" and cancelled["result"]["effects_undone"] is False
    assert result(artifacts.call("runtime.artifact.cancel", command_id="write-1")) == cancelled
    denied(artifacts.call("runtime.artifact.publish", **params,
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"]), "artifact_control_finished")


def test_exact_transport_profile_and_live_revocation_boundaries(artifacts):
    first, second = artifacts.project("a"), artifacts.project("b")
    for label, project in (("a", first), ("b", second), ("a", first)):
        assert result(artifacts.call("runtime.project.get", label, project_id=project["id"]))["project"]["id"] == project["id"]
        foreign = "b" if label == "a" else "a"
        assert denied(artifacts.call("runtime.project.get", label, via=artifacts.peers[foreign], project_id=project["id"]))["code"] == 4001
    denied(artifacts.call("runtime.project.get", "b", project_id=first["id"]), "project_owner_required")
    params = {"project_id": first["id"], "command_id": "write-1", "request_id": "request-1", "content": "private"}
    preview = result(artifacts.call("runtime.artifact.prepare", **params))
    revoked = result(artifacts.call("runtime.project.grants.set", project_id=first["id"],
        expected_revision=first["revision"], grants=[]))["project"]
    assert revoked["grants"] == []
    denied(artifacts.call("runtime.artifact.publish", **params, approval_id=preview["approval_id"],
        approval_digest=preview["approval_digest"]), "project_grant_revoked")
    assert result(artifacts.call("runtime.artifact.status", command_id="write-1"))["status"] == "claimed"


@pytest.mark.parametrize("extra", [{"principal_id": "forged"}, {"profile": "b"}, {"content": 123}, {"command_id": ""}])
def test_malformed_or_forged_prepare_dto_fails_closed(artifacts, extra):
    project = artifacts.project()
    params = {"project_id": project["id"], "command_id": "bad", "request_id": "request", "content": "text", **extra}
    assert denied(artifacts.call("runtime.artifact.prepare", **params))["code"] == 4000


def test_project_grants_alone_do_not_expand_immutable_agent_policy(artifacts):
    project = result(artifacts.call("runtime.project.create", name="Unconfigured", grants=grants()))["project"]
    denied(artifacts.call("runtime.artifact.prepare", project_id=project["id"], command_id="denied",
        request_id="request", content="text"), "project_not_granted")


def test_targeted_edits_branch_and_explicit_three_way_merge(artifacts):
    project = artifacts.project()
    pid = project["id"]
    base = "# One\nOriginal one\n# Two\nOriginal two\n"
    first = publish(artifacts, {"project_id": pid, "command_id": "base", "request_id": "base", "content": base})
    aid, v1 = first["artifact_id"], first["version"]
    edited = publish(artifacts, {"project_id": pid, "artifact_id": aid, "parent_version": v1,
        "command_id": "edit", "request_id": "edit", "edits": [{"anchor": "One",
        "expected_sha256": hashlib.sha256(b"# One\nOriginal one\n").hexdigest(),
        "replacement": "# One\nNew one\n"}]}, mode="edit.")
    assert download(artifacts, pid, aid) == b"# One\nNew one\n# Two\nOriginal two\n"
    branch = publish(artifacts, {"project_id": pid, "artifact_id": aid, "parent_version": v1,
        "expected_head_version": v1, "command_id": "branch", "request_id": "branch",
        "content": "# One\nOriginal one\n# Two\nNew two\n"})
    assert branch["disposition"] == "branch" and branch["head_version"] == edited["version"]
    merged = publish(artifacts, {"project_id": pid, "artifact_id": aid, "branch_version": branch["version"],
        "current_head_version": edited["version"], "command_id": "merge", "request_id": "merge",
        "approved_anchors": ["Two"]}, mode="merge.")
    assert merged["disposition"] == "canonical"
    assert download(artifacts, pid, aid) == b"# One\nNew one\n# Two\nNew two\n"
    assert download(artifacts, pid, aid, v1) == base.encode()
