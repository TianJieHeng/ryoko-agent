"""Real owned loop/RPC receipts, local SDK transport, exact memory and prefix checks."""
import json
import threading
from types import SimpleNamespace

import pytest
from agent.identity_lifecycle import agent_runtime_scope
from tests.tui_gateway.test_memory_rpc import result, denied
from tests.agent import test_runtime_commands as runtime_fixture

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def influenced(tmp_path, monkeypatch):
    raw = runtime_fixture._config()
    primary = raw["agent_identity"]["agents"]["primary"]
    raw["agent_identity"]["agents"]["specialist"] = {**primary, "role": "specialist", "memory_backend": "builtin"}
    raw["agent_identity"]["agents"]["b"] = {**primary, "role": "specialist", "memory_backend": "builtin"}
    raw["agent_identity"]["active_agent_id"] = "specialist"
    from hermes_cli import projects_db
    with projects_db.connect_closing(tmp_path / "projects.db") as conn:
        projects = [projects_db.create_project(conn, name="Project " + name, owner_principal_id="owner",
                    grants=[{"principal_id": "owner", "agent_id": "specialist", "permissions": ["read", "write"]}])
                    for name in ("one", "two")]
    raw["agent_identity"]["agents"]["specialist"]["project_grants"] = projects
    monkeypatch.setattr(runtime_fixture, "_config", lambda: raw)
    generator = runtime_fixture.real_agent.__wrapped__(tmp_path, monkeypatch)
    agent = next(generator)
    from agent.memory_router import initialize_routed_memory
    with agent_runtime_scope(agent.runtime_context):
        initialize_routed_memory(agent, raw, skip_memory=False)
    from tui_gateway import server
    peer = SimpleNamespace(write=lambda frame: True)
    monkeypatch.setattr(server, "_sessions", {"live": {"agent": agent, "profile_home": str(tmp_path),
        "transport": peer, "session_key": agent.session_id, "history": [], "history_lock": threading.RLock()}})

    def call(method, **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "test", "method": method,
            "params": {"schema_version": 1, "session_id": "live", **params}}, transport=peer)

    def turn(text="Answer briefly"):
        outcome = agent.run_conversation(text)
        assert outcome["completed"], outcome
        listing = result(call("runtime.memory.output.list"))
        run_id = listing["outputs"][0]["run_id"]
        return result(call("runtime.memory.output.get", run_id=run_id))

    yield SimpleNamespace(agent=agent, call=call, turn=turn, raw=raw, home=tmp_path, projects=projects)
    try:
        next(generator)
    except StopIteration:
        pass


def remember(fixture, **kwargs):
    return result(fixture.call("runtime.memory.record.write", record_id="style", content="Use short sentences",
                               kind="preference", **kwargs))["outcome"]["record"]


def control(fixture, output, record, control_id="ignore", action="ignore", scope="response", **kwargs):
    return fixture.call("runtime.memory.output.control", control_id=control_id, run_id=output["run_id"],
        context_sha256=output["context_sha256"], record_id=record["record_id"], expected_version=record["version"],
        namespace_id=record["namespace_id"], action=action, scope=scope, **kwargs)


def test_exact_supplied_refs_response_ignore_expiration_and_byte_stable_prefix(influenced):
    record = remember(influenced)
    frozen = influenced.agent._cached_system_prompt
    output = influenced.turn()
    assert output["references"][0]["version"] == record["version"]
    assert output["references"][0]["namespace_id"] == record["namespace_id"]
    assert not output["causal_explanation"] and not output["historical_context_enumerated"]
    receipt = result(control(influenced, output, record))
    assert receipt["status"] == "queued_next_turn" and not receipt["current_output_changed"]
    assert receipt["acknowledged_version"] is None
    next_output = influenced.turn()
    assert next_output["controls"][0]["action"] == "ignore"
    assert next_output["references"] == []
    applied = result(influenced.call("runtime.memory.output.control.get", control_id="ignore"))
    assert applied["status"] == "context_supplied" and applied["applied_run_id"] == next_output["run_id"]
    third = influenced.turn()
    assert third["controls"][0]["action"] == "expire"
    assert result(influenced.call("runtime.memory.output.control.get", control_id="ignore"))["status"] == "expired"
    assert result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]["version"] == 1
    assert influenced.agent._cached_system_prompt == frozen


def test_durable_correction_acknowledged_then_supplied_and_single_record_removal(influenced):
    record = remember(influenced)
    result(influenced.call("runtime.memory.record.write", record_id="other", content="Keep this unrelated fact"))
    output = influenced.turn()
    receipt = result(control(influenced, output, record, "correct", "correct", "general", content="Use detailed sentences"))
    assert receipt["status"] == "memory_acknowledged" and receipt["acknowledged_version"] == 2
    assert receipt["applied_run_id"] is None
    current = influenced.turn()
    assert any(ref["record_id"] == "style" and ref["version"] == 2 for ref in current["references"])
    assert result(influenced.call("runtime.memory.output.control.get", control_id="correct"))["status"] == "context_supplied"
    changed = result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]
    removed = result(control(influenced, current, changed, "remove", "remove", "general"))
    assert removed["acknowledged_version"] == 3 and removed["deletion_semantics"] == "tombstone_not_physical_erasure"
    assert result(influenced.call("runtime.memory.record.get", record_id="other"))["record"]["content"] == "Keep this unrelated fact"
    assert len(influenced.agent._session_db.get_messages_as_conversation("session")) > 0
    assert influenced.turn()["references"][0]["deletion_state"] == "deleted"


def test_stale_output_exact_namespace_and_version_conflicts(influenced):
    record = remember(influenced)
    old = influenced.turn()
    latest = influenced.turn()
    denied(control(influenced, old, record), "output_context_stale")
    assert latest["run_id"] != old["run_id"]
    # A new fresh version produces a new exact reference; prior record is stale.
    changed = result(influenced.call("runtime.memory.record.write", record_id="style", expected_version=1,
                                    content="New durable style"))["outcome"]["record"]
    current = influenced.turn()
    denied(control(influenced, current, {**changed, "namespace_id": "foreign"}), "identity_mismatch")
    result(influenced.call("runtime.memory.record.write", record_id="style", expected_version=2, content="Concurrent update"))
    denied(control(influenced, current, changed), "version_conflict")


def test_response_correction_never_promotes_preference_and_late_flag(influenced):
    record = remember(influenced)
    output = influenced.turn()
    influenced.agent._active_runtime_run = SimpleNamespace(run_id="newer-active")
    receipt = result(control(influenced, output, record, "once", "correct", "response", content="Use playful language once"))
    assert receipt["current_run_application"] == "late_not_applied"
    influenced.agent._active_runtime_run = None
    next_output = influenced.turn()
    assert next_output["controls"][0]["replacement"] == "Use playful language once"
    stored = result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]
    assert stored["content"] == "Use short sentences" and stored["version"] == 1


def test_project_ignore_and_durable_correction_do_not_cross_projects(influenced):
    first, second = influenced.projects
    record = remember(influenced)
    result(influenced.call("runtime.memory.scope.set", project_id=first))
    output = influenced.turn()
    receipt = result(control(influenced, output, record, "project-ignore", "ignore", "project", project_id=first))
    assert receipt["project_id"] == first
    result(influenced.call("runtime.memory.scope.set", project_id=second))
    other = influenced.turn()
    assert not other["controls"]
    assert other["references"][0]["record_id"] == record["record_id"]
    result(influenced.call("runtime.memory.scope.set", project_id=first))
    own = influenced.turn()
    assert own["controls"][0]["control_id"] == "project-ignore"
    local = result(influenced.call("runtime.memory.record.write", record_id="project-style", content="Use diagrams",
                                  kind="preference", scope="project:" + first))["outcome"]["record"]
    supplied = influenced.turn()
    denied(control(influenced, supplied, local, "wrong-scope", "correct", "general", content="Global override"),
           "memory_scope_mismatch")
    corrected = result(control(influenced, supplied, local, "project-correct", "correct", "project", project_id=first,
                               content="Use lists"))
    assert corrected["acknowledged_version"] == 2
    result(influenced.call("runtime.memory.scope.set", project_id=second))
    unrelated = influenced.turn()
    assert all(ref["record_id"] != "project-style" for ref in unrelated["references"])
    assert result(influenced.call("runtime.memory.output.control.get", control_id="project-correct"))["applied_run_id"] is None
    result(influenced.call("runtime.memory.scope.set", project_id=first))
    related = influenced.turn()
    assert any(ref["record_id"] == "project-style" and ref["version"] == 2 for ref in related["references"])


def test_same_profile_a_b_a_output_namespace_isolation(influenced):
    from agent.agent_identity import resolve_agent_context, parse_agent_identity_config
    from dataclasses import replace
    from agent.memory_router import initialize_routed_memory
    from tui_gateway import server
    remember(influenced)
    original = influenced.turn()
    first = influenced.agent
    policy = parse_agent_identity_config(influenced.raw).agents["b"]
    context = resolve_agent_context(influenced.raw, session_id="agent-b", profile_home=influenced.home)
    context = replace(context, policy=policy, identity=replace(context.identity, agent_id="b", policy_digest=policy.digest))
    db = first._session_db
    db.create_session("agent-b", source="tui")
    db.claim_session_agent_identity("agent-b", context.identity.to_record())
    other = SimpleNamespace(runtime_context=context, _session_db=db, session_id="agent-b", disabled_toolsets=[],
                            _emit_startup_warning=lambda message: None)
    with agent_runtime_scope(context):
        initialize_routed_memory(other, influenced.raw)
    original_session = server._sessions["live"]
    server._sessions["live"] = {**original_session, "agent": other, "session_key": "agent-b"}
    denied(influenced.call("runtime.memory.output.get", run_id=original["run_id"]), "output_context_unavailable")
    assert result(influenced.call("runtime.memory.output.list"))["outputs"] == []
    result(influenced.call("runtime.memory.record.write", record_id="style", content="B private preference"))
    server._sessions["live"] = original_session
    assert result(influenced.call("runtime.memory.output.get", run_id=original["run_id"])) == original
    assert result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]["content"] == "Use short sentences"


def test_removed_sidecar_is_not_reported_as_supplied_context(influenced, monkeypatch):
    remember(influenced)
    def strip_context(payload, **kwargs):
        changed = {**payload, "messages": [dict(message) for message in payload["messages"]]}
        for message in changed["messages"]:
            if message["role"] == "user":
                message["content"] = "A middleware-replaced user message"
        return SimpleNamespace(payload=changed, original_payload=payload, trace=[])
    monkeypatch.setattr("hermes_cli.middleware.apply_llm_request_middleware", strip_context)
    output = influenced.turn()
    assert output["references"] == [] and output["degraded"]


def test_ignore_superseded_before_boundary_is_stale_not_applied(influenced):
    record = remember(influenced)
    output = influenced.turn()
    result(control(influenced, output, record))
    result(influenced.call("runtime.memory.record.write", record_id="style", expected_version=1,
                           content="Changed before next turn"))
    following = influenced.turn()
    assert following["controls"] == [] and following["references"][0]["version"] == 2
    assert result(influenced.call("runtime.memory.output.control.get", control_id="ignore"))["status"] == "stale_not_applied"


def test_store_ack_with_lost_control_receipt_remains_pending_and_does_not_replay(influenced, monkeypatch):
    from agent import output_influences
    record = remember(influenced)
    output = influenced.turn()
    original = output_influences._save_control
    calls = []
    def fail_after_memory_ack(db, agent, actor, row):
        calls.append(row["status"])
        if row["status"] == "memory_acknowledged":
            raise OSError("Synthetic control receipt write failure")
        return original(db, agent, actor, row)
    monkeypatch.setattr(output_influences, "_save_control", fail_after_memory_ack)
    denied(control(influenced, output, record, "uncertain", "correct", "general", content="A durable correction"))
    assert calls == ["mutation_pending", "memory_acknowledged"]
    stored = result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]
    assert stored["version"] == 2 and stored["content"] == "A durable correction"
    pending = result(influenced.call("runtime.memory.output.control.get", control_id="uncertain"))
    assert pending["status"] == "mutation_pending" and pending["acknowledged_version"] is None
    retry = result(control(influenced, output, record, "uncertain", "correct", "general", content="A durable correction"))
    assert retry == pending and len(calls) == 2
    assert result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]["version"] == 2


def test_concurrent_control_identity_with_different_payloads_conflicts(influenced):
    from concurrent.futures import ThreadPoolExecutor
    record = remember(influenced)
    output = influenced.turn()
    def request(text):
        return control(influenced, output, record, "same-control", "correct", "response", content=text)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(request, ("First replacement", "Second replacement")))
    assert sum("result" in response for response in replies) == 1
    failure = next(response for response in replies if "error" in response)
    denied(failure, "idempotency_conflict")
    assert result(influenced.call("runtime.memory.record.get", record_id="style"))["record"]["version"] == 1


def test_content_correction_preserves_evidence_metadata_and_cannot_promote_procedures(influenced):
    import time
    original = result(influenced.call("runtime.memory.record.write", record_id="inference", content="Tentative old fact",
        kind="inference", confidence=0.4, validity="uncertain", valid_to=time.time() + 600))["outcome"]["record"]
    output = influenced.turn()
    corrected = result(control(influenced, output, original, "correct-inference", "correct", "general", content="Tentative revised fact"))
    assert corrected["acknowledged_version"] == 2
    changed = result(influenced.call("runtime.memory.record.get", record_id="inference"))["record"]
    assert all(changed[key] == original[key] for key in ("kind", "scope", "confidence", "validity", "valid_from", "valid_to"))
    procedure = result(influenced.call("runtime.memory.record.write", record_id="procedure", content="Reuse this procedure",
        kind="procedure_reference", source_ref="workflow:manual:1"))["outcome"]["record"]
    next_output = influenced.turn()
    denied(control(influenced, next_output, procedure, "promote", "correct", "general", content="Approved procedure"),
           "memory_operation_unsupported")
    assert result(influenced.call("runtime.memory.record.get", record_id="procedure"))["record"]["version"] == 1
