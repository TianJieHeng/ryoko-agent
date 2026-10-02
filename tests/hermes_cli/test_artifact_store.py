"""Real owned controls, exact approvals, immutable files and project version CAS."""
import base64
import contextvars
import hashlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.artifact_commands import artifact_control_scope, begin_artifact_control, finish_artifact_control
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.project_context import project_access
from agent.result_artifacts import ArtifactConflict, artifact_actor, read_project_artifact
from hermes_cli import projects_db as pdb
from hermes_cli.artifact_store import (
    prepare_markdown, prepare_markdown_edit, prepare_markdown_merge, publish_markdown, read_artifact,
    read_artifact_recovery,
)
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from tools import capability_broker as broker

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def artifact_runtime(tmp_path, monkeypatch):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with pdb.connect_closing() as conn:
        project = pdb.create_project(conn, name="Evidence", owner_principal_id="owner", grants=[
            {"principal_id": "owner", "agent_id": agent, "permissions": ["read", "write", "share"]}
            for agent in ("primary", "specialist")])
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                        "allowed_tools": [], "project_grants": [project]},
            "specialist": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                           "allowed_tools": [], "project_grants": [project]},
        }}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    db = SessionDB(tmp_path / "state.db")
    agents, sessions, contexts = {}, {}, {}
    for name in ("primary", "specialist"):
        from dataclasses import replace
        from agent.agent_identity import parse_agent_identity_config
        context = resolve_agent_context(raw, session_id=name, profile_home=tmp_path)
        if name == "specialist":
            policy = parse_agent_identity_config(raw).agents[name]
            context = replace(context, policy=policy, identity=replace(context.identity,
                agent_id=name, policy_digest=policy.digest))
        contexts[name] = context
        db.create_session(name, source="tui")
        db.claim_session_agent_identity(name, context.identity.to_record())
        agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=name)
        peer = SimpleNamespace(write=lambda _frame: True)
        sessions[name] = {"agent": agent, "profile_home": str(tmp_path), "transport": peer,
                          "session_key": name, "history": [], "history_lock": threading.RLock()}
        agents[name] = agent
    monkeypatch.setattr(server, "_sessions", sessions)

    @contextmanager
    def scope(command="artifact-command", actor="primary"):
        session = sessions[actor]
        token = bind_transport(session["transport"])
        session_token = server._current_runtime_session_record.set(session)
        method_token = server._current_rpc_method.set("runtime.artifact.prepare")
        with agent_runtime_scope(contexts[actor]):
            try:
                run = begin_artifact_control(agents[actor], actor, command, {"operation": "artifact_fixture"})
                with artifact_control_scope(run):
                    yield run
            finally:
                server._current_runtime_session_record.reset(session_token)
                server._current_rpc_method.reset(method_token)
                reset_transport(token)

    yield SimpleNamespace(db=db, home=tmp_path, raw=raw, project=project, contexts=contexts,
                          agents=agents, sessions=sessions, scope=scope)
    db.close()


def approve(proposal):
    preview = broker.recover_approval_preview(proposal.approval_id, broker.project_artifact_action(proposal.scope))
    broker.resolve_approval(preview, proposal.approval_digest, "once")


def publish_fixture(run, project, request, content, **kwargs):
    proposal = prepare_markdown(run, project_id=project, request_id=request, content=content, **kwargs)
    approve(proposal)
    return publish_markdown(run, proposal)


def text(run, project, artifact_id, version):
    return read_project_artifact(run.context, run.db, project, artifact_id, version).decode("utf-8")


def test_real_proposal_reopens_exact_approval_and_publishes_complete_plain_text(artifact_runtime):
    runtime = artifact_runtime
    content = "# Source\nαβ data\n<script>never execute</script>\n"
    with runtime.scope() as run:
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="source", content=content)
        repeat = prepare_markdown(run, project_id=runtime.project, request_id="source", content=content)
        assert repeat.public_record() == proposal.public_record()
        assert "locator" not in json.dumps(proposal.public_record())
        assert not (runtime.home / "runtime-artifacts").exists()
        with pytest.raises(broker.CapabilityDenied, match="approved"):
            publish_markdown(run, proposal)
        approve(proposal)
        record = publish_markdown(run, proposal)
        assert record["disposition"] == "canonical"
        assert publish_markdown(run, repeat) == record
        version = runtime.db.read_artifact_version(record["artifact_id"], record["version"],
            artifact_actor(run.context), access=project_access(run.context))
        assert version["metadata"]["provenance"]["kind"] == "source"
        chunks, offset = [], 0
        while True:
            part = read_artifact(run.context, runtime.db, runtime.project, record["artifact_id"], offset=offset, limit=7)
            assert part["preview_mode"] == "plain_text"
            chunks.append(base64.b64decode(part["data_base64"]))
            if part["eof"]:
                break
            offset = part["next_offset"]
        recovered = b"".join(chunks)
        assert recovered == content.encode()
        assert hashlib.sha256(recovered).hexdigest() == record["sha256"]
        assert len(runtime.db.list_effects(run.session_id, artifact_actor(run.context))) == 1


def test_targeted_edits_locks_stale_branch_and_selected_delta_merge(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        initial = "# Locked\nKeep exactly.\n# Alpha\nA1\n# Beta\nB1\n"
        first = publish_fixture(run, runtime.project, "create", initial, locked_sections=["Locked"])
        aid, base = first["artifact_id"], first["version"]
        with pytest.raises(ArtifactConflict, match="locked"):
            prepare_markdown(run, project_id=runtime.project, request_id="bad-lock", artifact_id=aid,
                parent_version=base, content=initial.replace("Keep exactly.", "changed"))
        alpha = prepare_markdown_edit(run, project_id=runtime.project, artifact_id=aid, parent_version=base,
            request_id="alpha", edits=[{"anchor": "Alpha", "expected_sha256": hashlib.sha256(b"# Alpha\nA1\n").hexdigest(),
                                       "replacement": "# Alpha\nA2\n"}])
        beta = prepare_markdown_edit(run, project_id=runtime.project, artifact_id=aid, parent_version=base,
            request_id="beta", edits=[{"anchor": "Beta", "expected_sha256": hashlib.sha256(b"# Beta\nB1\n").hexdigest(),
                                      "replacement": "# Beta\nB2\n"}])
        approve(alpha); approve(beta)
        second, branch = publish_markdown(run, alpha), publish_markdown(run, beta)
        assert second["disposition"] == "canonical" and branch["disposition"] == "branch"
        assert branch["head_version"] == second["version"]
        assert text(run, runtime.project, aid, base) == initial
        assert text(run, runtime.project, aid, second["version"]) == initial.replace("A1", "A2")
        merged = prepare_markdown_merge(run, project_id=runtime.project, artifact_id=aid,
            branch_version=branch["version"], current_head_version=second["version"],
            request_id="merge-beta", approved_anchors=["Beta"])
        approve(merged)
        final = publish_markdown(run, merged)
        assert text(run, runtime.project, aid, final["version"]) == initial.replace("A1", "A2").replace("B1", "B2")
        assert text(run, runtime.project, aid, branch["version"]) == initial.replace("B1", "B2")


def test_cross_agent_grants_are_live_without_impersonating_owner(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope("producer") as run:
        output = publish_fixture(run, runtime.project, "create", "# Shared\nAllowed\n")
        finish_artifact_control(run, output)
    with runtime.scope("reader", actor="specialist") as run:
        assert text(run, runtime.project, output["artifact_id"], output["version"]) == "# Shared\nAllowed\n"
        assert run.context.identity.agent_id == "specialist"
        with pdb.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=? AND agent_id=?", (runtime.project, "specialist"))
            conn.commit()
        with pytest.raises(PermissionError, match="grant"):
            text(run, runtime.project, output["artifact_id"], output["version"])


def test_changed_approval_cancelled_control_and_derivative_invalidation(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        first = publish_fixture(run, runtime.project, "create", "# Source\nVersion 1\n")
        derived = publish_fixture(run, runtime.project, "derivative", "# Summary\nDerived\n",
            derived_from=[{"artifact_id": first["artifact_id"], "version": first["version"]}])
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="revision", artifact_id=first["artifact_id"],
            parent_version=first["version"], content="# Source\nVersion 2\n")
        approve(proposal)
        with pytest.raises(ValueError, match="changed"):
            prepare_markdown(run, project_id=runtime.project, request_id="revision", artifact_id=first["artifact_id"],
                parent_version=first["version"], content="# Source\nUnapproved\n", approval_id=proposal.approval_id)
        publish_markdown(run, proposal)
        row = runtime.db.read_artifact_version(derived["artifact_id"], derived["version"],
            artifact_actor(run.context), access=project_access(run.context))
        assert row["derived_validity"] == "stale"
        assert text(run, runtime.project, derived["artifact_id"], derived["version"]) == "# Summary\nDerived\n"
        waiting = prepare_markdown(run, project_id=runtime.project, request_id="cancelled", content="# Stop\n")
        approve(waiting)
        run.agent._interrupt_requested = True
        with pytest.raises(ValueError, match="interrupted"):
            publish_markdown(run, waiting)
        assert len(runtime.db.list_effects(run.session_id, artifact_actor(run.context))) == 3


def test_concurrent_same_request_dispatches_one_effect_and_one_head_commit(artifact_runtime, monkeypatch):
    from agent import effect_reconciler
    runtime = artifact_runtime
    entered, release = threading.Event(), threading.Event()
    actual, calls = effect_reconciler._publish_bytes, []
    def blocked(*args):
        calls.append(True)
        entered.set()
        assert release.wait(timeout=10)
        return actual(*args)
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", blocked)
    with runtime.scope() as run:
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="race", content="# One\n")
        approve(proposal)
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(contextvars.copy_context().run, publish_markdown, run, proposal)
            assert entered.wait(timeout=10)
            try:
                with pytest.raises(broker.CapabilityDenied, match="reconciliation"):
                    publish_markdown(run, proposal)
            finally:
                release.set()
            result = first.result(timeout=10)
        assert publish_markdown(run, proposal) == result
        rows = runtime.db.list_artifact_versions(result["artifact_id"], artifact_actor(run.context),
                                                access=project_access(run.context))
        assert len(rows) == 1 and rows[0]["head_revision"] == 1
        assert calls == [True]
        assert len(runtime.db.list_effects(run.session_id, artifact_actor(run.context))) == 1


def test_failed_catalog_commit_recovers_accepted_bytes_without_second_mutation(artifact_runtime, monkeypatch):
    from agent import effect_reconciler
    runtime = artifact_runtime
    actual_write, writes = effect_reconciler._publish_bytes, []
    def counted(*args):
        writes.append(True)
        return actual_write(*args)
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", counted)
    with runtime.scope() as run:
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="recover", content="# Retain\nBytes\n")
        approve(proposal)
        with monkeypatch.context() as patch:
            def lost(*args, **kwargs):
                raise OSError("catalog receipt missing")
            patch.setattr(runtime.db, "register_artifact_version", lost)
            with pytest.raises(OSError):
                publish_markdown(run, proposal)
        assert runtime.db.list_effects(run.session_id, artifact_actor(run.context))[0]["state"] == "confirmed"
        resumed = prepare_markdown(run, project_id=runtime.project, request_id="recover", content="# Retain\nBytes\n",
                                   approval_id=proposal.approval_id)
        assert resumed.public_record() == proposal.public_record()
        result = publish_markdown(run, resumed)
        assert writes == [True]
        assert text(run, runtime.project, result["artifact_id"], result["version"]) == "# Retain\nBytes\n"


def test_changed_proposal_expired_approval_and_revoked_grant_never_publish(artifact_runtime, monkeypatch):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="exact", content="# Approved\n")
        approve(proposal)
        with pytest.raises(broker.CapabilityDenied, match="effect dispatch"):
            broker.issue_capability(broker.project_artifact_action(proposal.scope),
                approval=broker.recover_approval_preview(proposal.approval_id, broker.project_artifact_action(proposal.scope)))
        with pytest.raises(ArtifactConflict, match="request identity"):
            publish_markdown(run, replace(proposal, request_id="changed"))
        with pytest.raises(ArtifactConflict, match="digest"):
            publish_markdown(run, replace(proposal, approval_digest="0" * 64))
        with pytest.raises(ArtifactConflict, match="bytes"):
            publish_markdown(run, replace(proposal, content_bytes=b"wrong bytes"))
        assert not (runtime.home / "runtime-artifacts").exists()
        with monkeypatch.context() as patch:
            patch.setattr(broker, "time", SimpleNamespace(time=lambda: proposal.expires_at + 1))
            with pytest.raises(broker.CapabilityDenied, match="expired"):
                publish_markdown(run, proposal)
        with pdb.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=?", (runtime.project,))
            conn.commit()
        with pytest.raises(PermissionError, match="grant"):
            publish_markdown(run, proposal)
        assert not (runtime.home / "runtime-artifacts").exists()


@pytest.mark.parametrize("crash_point", ["before_file", "after_file", "after_receipt"])
def test_actual_process_crash_retains_honest_read_only_project_recovery(artifact_runtime, crash_point):
    from agent.effect_reconciler import reconcile_effect
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="crash", content="# Retained\nOriginal\n")
        approve(proposal)
        script = r'''
import json, os, sys, threading
from pathlib import Path
from types import SimpleNamespace
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.artifact_commands import begin_artifact_control, artifact_control_scope
from hermes_cli.artifact_store import prepare_markdown, publish_markdown
from hermes_state import SessionDB
from agent import effect_reconciler
from tui_gateway import server
from tui_gateway.transport import bind_transport
home, project, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
context = resolve_agent_context(json.loads((home / "config.yaml").read_text()), session_id="primary", profile_home=home)
db = SessionDB(home / "state.db")
agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id="primary")
peer = SimpleNamespace(write=lambda _frame: True)
session = {"agent":agent,"profile_home":str(home),"transport":peer,"session_key":"primary","history":[],"history_lock":threading.RLock()}
server._sessions = {"primary":session}
server._current_runtime_session_record.set(session)
server._current_rpc_method.set("runtime.artifact.publish")
bind_transport(peer)
with agent_runtime_scope(context):
    run = begin_artifact_control(agent,"primary","artifact-command",{"operation":"artifact_fixture"})
    with artifact_control_scope(run):
        proposal = prepare_markdown(run,project_id=project,request_id="crash",content="# Retained\nOriginal\n")
        original = effect_reconciler._publish_bytes
        def interrupted(*args):
            if point == "after_file": original(*args)
            os._exit(77)
        if point == "after_receipt":
            db.register_artifact_version = lambda *args, **kwargs: os._exit(77)
        else:
            effect_reconciler._publish_bytes = interrupted
        publish_markdown(run,proposal)
'''
        process = subprocess.run([sys.executable, "-c", script, str(runtime.home), runtime.project, crash_point],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        assert process.returncode == 77, process.stderr
        actor = artifact_actor(run.context)
        effect = runtime.db.list_effects(run.session_id, actor)[0]
        if crash_point != "after_receipt":
            assert effect["state"] == "dispatched"
            effect = reconcile_effect(runtime.db, effect["effect_id"], context=run.context,
                holder=run.holder, generation=run.generation, deadline_at=time.time() + 30)
        if crash_point == "before_file":
            assert effect["state"] == "reconciliation_required"
            with pytest.raises(ArtifactConflict, match="confirmed"):
                read_artifact_recovery(run.context, runtime.db, runtime.project, effect["effect_id"])
            return
        assert effect["state"] == "confirmed"
        runtime.db.release_session_turn_lease(run.session_id, run.holder, generation=run.generation)
        before = runtime.db.read_runtime_snapshot(run.session_id)["revision"]
        result = read_artifact_recovery(run.context, runtime.db, runtime.project, effect["effect_id"])
        assert result["publication_state"] == "published_uncommitted"
        assert base64.b64decode(result["data_base64"]) == b"# Retained\nOriginal\n"
        assert runtime.db.read_runtime_snapshot(run.session_id)["revision"] == before
        assert runtime.db.read_runtime_command(run.session_id, run.command_id)["status"] == "claimed"
        with pytest.raises(RuntimeStoreError) as absent:
            runtime.db.get_artifact_head(result["artifact_id"], actor, access=project_access(run.context))
        assert absent.value.code == "artifact_not_found"
