"""BE18 finite local journeys through owned commands and durable phase boundaries.

No live model, harness, remote delivery, frontend or Dots acceptance is implied.
Phase-specific crash/lease/transport suites remain separate mandatory receipts.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tui_gateway.test_artifact_rpc import (  # noqa: F401
    artifacts, denied, download, publish, result,
)
from tests.tui_gateway.test_memory_rpc import memory, write  # noqa: F401
from tests.tui_gateway.test_runtime_rpc import (  # noqa: F401
    _install_result_worker, envelope, runtime,
)
from tests.tui_gateway import test_schedules_rpc as schedules
from tests.tui_gateway import test_workflows_rpc as workflows

pytestmark = pytest.mark.platforms("linux")


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def reopen(rpc, label="a"):
    from hermes_state import SessionDB
    agent = rpc.agents[label]
    path = agent._session_db.db_path
    agent._session_db.close()
    agent._session_db = SessionDB(path)


def document_journey(rpc, project, *, label="a", prefix="brief"):
    """One exact document becomes a revision and an explicit reusable template."""
    content = "# Decision\nUse blue.\n# Evidence\nProject source only.\n"
    first = publish(rpc, {"project_id": project, "command_id": prefix + "-write",
        "request_id": prefix + "-write", "content": content}, label=label)
    revised = publish(rpc, {"project_id": project, "command_id": prefix + "-edit",
        "request_id": prefix + "-edit", "artifact_id": first["artifact_id"],
        "parent_version": first["version"], "edits": [{"anchor": "Decision",
            "expected_sha256": sha("# Decision\nUse blue.\n"),
            "replacement": "# Decision\nUse teal.\n"}]}, mode="edit.", label=label)
    assert download(rpc, project, first["artifact_id"], label=label) == content.replace("blue", "teal").encode()
    assert download(rpc, project, first["artifact_id"], first["version"], label=label) == content.encode()
    ref = {key: revised[key] for key in ("artifact_id", "version")}
    template = result(rpc.call("runtime.template.create", label, project_id=project,
        template_id=prefix + "-template", baseline_ref=ref, structure=["Decision", "Evidence"],
        style={"tone": "plain"}, assets=[], slots=[], exclusions=["Use teal."]))["template"]
    assert template["baseline_ref"] == ref
    return first, revised, template


def test_slice_a_primary_missing_harness_exact_artifact_resume_and_revocation(artifacts):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.memory_router import initialize_routed_memory

    first, second = artifacts.project(), artifacts.project()
    for project, purpose in ((first, "The approved color is teal"), (second, "The approved color is red")):
        result(artifacts.call("runtime.project.update", project_id=project["id"],
            expected_revision=project["revision"], changes={"purpose": purpose}))
    agent = artifacts.agents["a"]
    agent.disabled_toolsets = []
    agent._emit_startup_warning = lambda _message: None
    with agent_runtime_scope(agent.runtime_context):
        initialize_routed_memory(agent, json.loads((artifacts.homes["a"] / "config.yaml").read_text()))
    health = result(artifacts.call("runtime.memory.status"))
    assert health["health"]["status"] == "unconfigured"
    assert health["capabilities"]["backend"] == "personal_mcp"
    denied(artifacts.call("runtime.memory.records.list"), "memory_operation_unsupported")
    original, revised, template = document_journey(artifacts, first["id"])
    ref = {key: revised[key] for key in ("artifact_id", "version")}
    current = result(artifacts.call("runtime.project.get", project_id=first["id"]))["project"]
    result(artifacts.call("runtime.project.update", project_id=first["id"],
        expected_revision=current["revision"], changes={"canonical_artifact_refs": [ref]}))
    before = result(artifacts.call("runtime.resume.get", project_id=first["id"]))
    reopen(artifacts)
    after = result(artifacts.call("runtime.resume.get", project_id=first["id"]))
    assert after["artifacts"] == before["artifacts"]
    assert after["artifacts"][0]["version"] == revised["version"]
    assert "approved color is red" not in json.dumps(after)
    assert result(artifacts.call("runtime.template.get", template_id=template["template_id"]))["template"] == template
    assert download(artifacts, first["id"], original["artifact_id"], original["version"])
    pending = {"project_id": first["id"], "command_id": "held", "request_id": "held", "content": "must never publish"}
    approval = result(artifacts.call("runtime.artifact.prepare", **pending))
    denied(artifacts.call("runtime.artifact.publish", **pending,
        approval_id=approval["approval_id"], approval_digest="0" * 64), "approval_mismatch")
    current = result(artifacts.call("runtime.project.get", project_id=first["id"]))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=first["id"],
        expected_revision=current["revision"], grants=[]))
    denied(artifacts.call("runtime.artifact.publish", **pending,
        approval_id=approval["approval_id"], approval_digest=approval["approval_digest"]), "project_grant_revoked")
    denied(artifacts.call("runtime.artifact.get", project_id=first["id"], artifact_id=revised["artifact_id"]))
    cancelled = result(artifacts.call("runtime.artifact.cancel", command_id="held"))
    assert cancelled["status"] == "cancelled"
    assert cancelled == result(artifacts.call("runtime.artifact.cancel", command_id="held"))
    assert not (artifacts.homes["a"] / "individual-memory").exists()


def test_slice_a_specialist_builtin_memory_conflicting_projects_and_artifacts(memory):
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state import SessionDB

    a, b = memory.projects["home"], memory.projects["home-alternate"]
    write(memory, record_id="project-a", content="Choose teal", scope="project:" + a)
    write(memory, record_id="project-b", content="Choose red", scope="project:" + b)
    agent = memory.agents["a"]
    for project, expected in ((a, "Choose teal"), (b, "Choose red"), (a, "Choose teal")):
        result(memory.call("runtime.memory.scope.set", project_id=project))
        with agent_runtime_scope(agent.runtime_context):
            fresh = agent._memory_manager.fresh_context("color")
        assert [row["content"] for row in fresh["records"]] == [expected]
    original, revised, template = document_journey(memory, a, prefix="specialist")
    # An explicit artifact grant does not grant a sibling the specialist's memory.
    sibling = memory.agents["b"]
    sibling._session_db.close()
    sibling._session_db = SessionDB(agent._session_db.db_path)
    sibling._session_db.create_session(sibling.session_id, source="tui")
    sibling._session_db.claim_session_agent_identity(sibling.session_id, sibling.runtime_context.identity.to_record())
    assert download(memory, a, revised["artifact_id"], label="b")
    assert result(memory.call("runtime.memory.records.list", "b"))["records"] == []
    denied(memory.call("runtime.artifact.get", project_id=b, artifact_id=revised["artifact_id"]))
    current = result(memory.call("runtime.project.get", project_id=a))["project"]
    result(memory.call("runtime.project.update", project_id=a, expected_revision=current["revision"],
        changes={"canonical_artifact_refs": [{key: revised[key] for key in ("artifact_id", "version") }]}))
    reopen(memory)
    assert download(memory, a, original["artifact_id"], original["version"])
    assert result(memory.call("runtime.template.get", template_id=template["template_id"]))["template"] == template
    assert result(memory.call("runtime.resume.get", project_id=a))["artifacts"][0]["version"] == revised["version"]
    memory.initialize("a")
    assert result(memory.call("runtime.memory.record.get", record_id="project-a"))["record"]["content"] == "Choose teal"
    assert result(memory.call("runtime.memory.status"))["capabilities"]["backend"] == "builtin"


def test_slice_b_source_backed_two_output_mission_reopen_and_accept(artifacts):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.source_manifest import EvidenceRange, SourceRequest
    from hermes_cli.domain_research import resolve_sources

    project = artifacts.project()["id"]
    content = "# Source\nThe supported limit is ten.\n"
    source = publish(artifacts, {"project_id": project, "command_id": "source", "request_id": "source", "content": content})
    quote = b"The supported limit is ten."
    start = content.encode().index(quote)
    request = SourceRequest(source["artifact_id"], "project_artifact", project, source["version"], source["sha256"],
        evidence_ranges=(EvidenceRange(start, start + len(quote), sha(quote), quote.decode()),))
    agent = artifacts.agents["a"]
    with agent_runtime_scope(agent.runtime_context):
        evidence = resolve_sources(agent.runtime_context, agent._session_db, (request,))
    assert evidence.to_record()["complete"]
    assert evidence.sources[0].content_bytes == content.encode()
    outputs = []
    for name in ("brief", "checklist"):
        outputs.append(publish(artifacts, {"project_id": project, "command_id": name, "request_id": name,
            "content": "# Summary\nThe supported limit is ten.\n# Source\n" + evidence.to_bytes().decode(),
            "derived_from": [{"artifact_id": source["artifact_id"], "version": source["version"]}]}))
    refs = [{"artifact_id": row["artifact_id"], "version": row["version"], "digest": row["sha256"]} for row in outputs]
    mission = result(artifacts.call("runtime.mission.create", mission_id="two-output", contract={
        "outcome": "Two source-backed local summaries", "project_id": project, "policy": "reviewed",
        "risk": "low", "uncertainty": "low", "max_turns": 2,
        "deliverables": [{"deliverable_id": name, "artifact_ref": ref} for name, ref in zip(("brief", "checklist"), refs)],
        "acceptance": [{"criterion_id": "sections", "kind": "markdown_sections", "artifact_refs": refs,
            "parameters": {"required_sections": ["Summary", "Source"]}},
            {"criterion_id": "consistent", "kind": "linked_consistency", "artifact_refs": refs,
             "parameters": {"tokens": ["The supported limit is ten."]}}]}))["mission"]
    denied(artifacts.call("runtime.mission.accept", expected_revision=mission["revision"]), "mission_verification_required")
    checked = result(artifacts.call("runtime.mission.verify", expected_revision=mission["revision"]))
    assert all(row["result"] == "pass" for row in checked["receipts"])
    assert checked["dispatch_performed"] is False
    reopen(artifacts)
    accepted = result(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]))["mission"]
    assert accepted["state"] == "completed" and accepted["max_turns"] == 2
    denied(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]), "revision_conflict")
    for row in outputs:
        assert b"supported limit is ten" in download(artifacts, project, row["artifact_id"])
    assert evidence.to_record()["validator_manifest"]["claim_verification"] == "not_performed"


def test_slice_c_parameterized_workflow_monitor_restart_noise_change_cancel(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    draft = workflows.create_workflow(artifacts, project)
    evaluated = workflows.evaluate(artifacts, draft, workflows.baselines(artifacts, project))
    approved = workflows.decision(artifacts, evaluated["workflow"])["workflow"]
    mission = workflows.ready_mission(artifacts, project)
    request = workflows.run_request(approved, mission, "Emi", "workflow-run")
    prepared = result(artifacts.call("runtime.workflow.run.prepare", **request))
    assert result(artifacts.call("runtime.workflow.run.prepare", **request)) == prepared
    approvals = [{key: row[key] for key in ("approval_id", "approval_digest")} for row in prepared["proposals"]]
    published = result(artifacts.call("runtime.workflow.run.publish", **request, approvals=approvals))
    output = published["outputs"][0]
    assert download(artifacts, project, output["artifact_id"]) == b"# Greeting\nHello Emi\n"
    schedule = schedules.create(artifacts, schedules.definition(project, output), command="monitor-create")
    schedule = schedules.update(artifacts, schedule, "active", command="monitor-start")
    now = schedule["next_due"]
    assert schedules.tick(artifacts, monkeypatch, now) == 1
    reopen(artifacts)
    assert schedules.tick(artifacts, monkeypatch, now) == 0
    cosmetic = schedules.source(artifacts, project, "# Greeting\nHello   Emi\n\n", prior=output, command="noise")
    assert schedules.tick(artifacts, monkeypatch, now + 60) == 1
    assert schedules.get(artifacts, schedule)["intents"] == []
    schedules.source(artifacts, project, "# Greeting\nHello Faye\n", prior=cosmetic, command="change")
    assert schedules.tick(artifacts, monkeypatch, now + 120) == 1
    changed = schedules.get(artifacts, schedule)
    assert len(changed["intents"]) == 1 and changed["intents"][0]["state"] == "recorded"
    schedules.update(artifacts, changed, "paused", command="monitor-stop")
    assert schedules.tick(artifacts, monkeypatch, now + 180) == 0
    mission = result(artifacts.call("runtime.mission.get"))["mission"]
    pending = workflows.run_request(approved, mission, "Cancelled", "cancelled-workflow")
    result(artifacts.call("runtime.workflow.run.prepare", **pending))
    result(artifacts.call("runtime.artifact.cancel", command_id="cancelled-workflow"))
    history = json.loads(result(artifacts.call("runtime.workflow.runs", project_id=project,
        workflow_id=approved["workflow_id"], version=approved["version"]))["runs_json"])
    assert {row["control_status"] for row in history} == {"completed", "cancelled"}
    assert all(row["pin"]["sha256"] == approved["sha256"] for row in history)


def test_slice_b_owned_publication_process_death_after_acceptance_never_replays(artifacts, monkeypatch):
    """A real killed writer leaves accepted bytes distinct from committed output."""
    project = artifacts.project()["id"]
    request = {"project_id": project, "command_id": "crash", "request_id": "crash",
               "content": "# Retained\nAccepted before the process stopped.\n"}
    prepared = result(artifacts.call("runtime.artifact.prepare", **request))
    child = r'''
import json, os, sys, threading
from pathlib import Path
from types import SimpleNamespace
from agent.agent_identity import resolve_agent_context
from agent import effect_reconciler
from hermes_state import SessionDB
from tui_gateway import server
home, sid = Path(sys.argv[1]), sys.argv[2]
context = resolve_agent_context(json.loads((home / "config.yaml").read_text()), session_id=sid, profile_home=home)
db = SessionDB(home / "state.db")
agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=sid)
peer = SimpleNamespace(write=lambda frame: True)
server._sessions = {"live-a": {"agent": agent, "transport": peer, "profile_home": str(home),
    "session_key": sid, "history": [], "history_lock": threading.RLock()}}
original = effect_reconciler._publish_bytes
def accepted_then_dead(*args):
    original(*args)
    os._exit(77)
effect_reconciler._publish_bytes = accepted_then_dead
response = server.dispatch({"jsonrpc": "2.0", "id": "crash", "method": "runtime.artifact.publish",
    "params": {"schema_version": 1, "session_id": "live-a", **json.load(sys.stdin)}}, transport=peer)
raise RuntimeError(response)
'''
    command = {**request, **{key: prepared[key] for key in ("approval_id", "approval_digest")}}
    process = subprocess.run([sys.executable, "-c", child, str(artifacts.homes["a"]), artifacts.agents["a"].session_id],
        input=json.dumps(command), text=True, capture_output=True, timeout=30,
        cwd=Path(__file__).resolve().parents[2])
    assert process.returncode == 77, process.stderr
    reopen(artifacts)
    agent = artifacts.agents["a"]
    from agent.result_artifacts import artifact_actor
    effect = agent._session_db.list_effects(agent.session_id, artifact_actor(agent.runtime_context))[0]
    assert effect["state"] == "dispatched"
    path = artifacts.homes["a"] / effect["input_ref"]["locator"]
    before = path.stat().st_ino, path.stat().st_mtime_ns
    assert path.read_text() == request["content"]
    denied(artifacts.call("runtime.artifact.get", project_id=project,
        artifact_id=prepared["artifact_id"], version=prepared["version"]))
    denied(artifacts.call("runtime.artifact.publish", **command), "effect_reconciliation_required")
    result(artifacts.call("runtime.artifact.cancel", command_id="crash"))
    def no_replay(*_args):
        raise AssertionError("Recovery must inspect accepted bytes without mutating them")
    monkeypatch.setattr("agent.effect_reconciler._publish_bytes", no_replay)
    recovered = result(artifacts.call("runtime.effect.reconcile", effect_id=effect["effect_id"]))
    assert recovered["inspection_only"] and not recovered["dispatch_performed"]
    assert recovered["effect"]["state"] == "confirmed"
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == before
    assert result(artifacts.call("runtime.artifact.status", command_id="crash"))["status"] == "cancelled"
    denied(artifacts.call("runtime.artifact.get", project_id=project,
        artifact_id=prepared["artifact_id"], version=prepared["version"]))


def test_slice_b_delivery_failure_reopen_exact_command_and_partial_ack(runtime, monkeypatch):
    """Real finalizer and outbox retain output while only notification is retried."""
    _install_result_worker(runtime, monkeypatch)
    command = envelope("finite-result")
    receipt = result(runtime.call("runtime.command", **command))
    output = result(runtime.call("runtime.result.get", command_id=command["command_id"]))
    assert output["publication_state"] == "committed"
    runtime.peers["a"].write = lambda _frame: False
    failed = result(runtime.call("runtime.delivery.retry", delivery_id=output["delivery_id"]))
    assert failed["state"] == "outcome_unknown" and failed["result_available"]
    reopen(runtime)
    assert result(runtime.call("runtime.command", **command)) == receipt
    assert runtime.dispatched == [command["command_id"]]
    assert result(runtime.call("runtime.result.get", command_id=command["command_id"])) == output
    frames = []
    runtime.peers["a"].write = lambda frame: frames.append(frame) or True
    sent = result(runtime.call("runtime.delivery.retry", delivery_id=output["delivery_id"]))
    assert sent["state"] == "awaiting_ack"
    payload = frames[0]["params"]["payload"]
    acknowledgment = {key: payload[key] for key in ("delivery_id", "attempt_token", "sha256")}
    partial = result(runtime.call("runtime.delivery.ack", **acknowledgment, text_received=True))
    assert partial["state"] == "partial" and partial["components"]["artifact"] == "not_sent"
    delivered = result(runtime.call("runtime.delivery.ack", **acknowledgment, artifact_received=True))
    assert delivered["state"] == "delivered"
    assert result(runtime.call("runtime.delivery.retry", delivery_id=output["delivery_id"])) == delivered
    assert len(frames) == 1 and runtime.dispatched == [command["command_id"]]
