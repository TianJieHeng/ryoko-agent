"""BE13 real durable ledger, local ownership, existing delegate/async consumers."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import httpx
import openai
import pytest

from agent.agent_identity import resolve_agent_context
from agent.budget_account import BudgetRuntime, actor_for, parse_budget_policy
from agent.delegation_contract import DelegationError, DelegationLimits, ImmutableHandoff
from agent.delegation_runtime import prepare_batch, strict_preflight
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from hermes_state_delegations import DelegationRegistry


def policy():
    return {"schema_version": 1, "mode": "tokens", "limits": {"tokens": 100000, "attempts": 10,
        "cost_micros": None, "wall_ms": 60000, "provider_slots": 2, "executor_slots": 2},
        "deadline_seconds": 120, "request_timeout_ms": 10000,
        "routes": [{"model": "fixture", "base_url": "https://fixture.invalid/v1", "max_input_tokens": 4096,
                    "max_output_tokens": 64, "input_overhead_tokens": 128, "output_token_parameter": "max_tokens",
                    "input_cost_micros_per_million": None, "output_cost_micros_per_million": None, "bounds_verified": True}]}


def config():
    return {"runtime_budget": policy(), "delegation": {"durable": {"enabled": True,
        "limits": {"max_depth": 2, "max_total_children": 4, "max_concurrent_children": 1}},
        "specialists": {"researcher": {"responsibility": "Summarize exact supplied sources",
            "methods_ref": {"id": "methods", "version": 1, "sha256": "a" * 64},
            "limits": {"max_depth": 1, "max_total_children": 1, "max_concurrent_children": 1},
            "output_contract": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}}}},
        "agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "ryoko", "active_agent_id": "ryoko", "personal_mcp_servers": ["private"],
        "personal_secret_refs": ["PRIVATE_TOKEN"],
        "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
            "allowed_tools": ["delegate_task", "todo_list", "memory"], "secret_refs": ["PRIVATE_TOKEN"], "mcp_grants": {"private": ["read"]}},
            "researcher": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                           "allowed_tools": ["todo_list", "memory"]}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin", "allowed_tools": ["delegate_task", "todo_list", "memory"]}}}


def command(command_id):
    return {"schema_version": 1, "command_id": command_id, "idempotency_key": command_id,
            "operation": "submit", "expected_revision": None, "payload": {"text": "bounded delegation"}}


def make_run(db, context, budget_policy, *, parent_id=None, command_id="command", holder="holder"):
    actor = actor_for(context)
    sid = context.identity.session_id
    db.create_session(sid, source="cli")
    db.claim_session_agent_identity(sid, context.identity.to_record())
    receipt = db.submit_runtime_command(sid, actor, command(command_id), budget_policy_json=budget_policy.snapshot)
    assert db.try_acquire_session_turn_lease(sid, holder, ttl_seconds=120)
    generation = db.get_session_turn_lease(sid)["generation"]
    assert db.claim_runtime_command(sid, command_id, holder=holder, generation=generation)
    account = db.create_budget_account(sid, actor, receipt["run_id"], budget_policy.limits,
        deadline=time.time() + 100, parent_id=parent_id, holder=holder, generation=generation, policy_snapshot=budget_policy.record)
    client = openai.OpenAI(api_key="fixture-not-live", base_url="https://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))))
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=sid, client=client,
        provider="openai", api_mode="chat_completions", platform="cli", _runtime_budget_policy=budget_policy,
        _active_runtime_run=None, _interrupt_requested=False, _delegate_depth=0)
    budget = BudgetRuntime(db, account["account_id"], account["root_id"], budget_policy, actor, holder, generation, account["deadline"], agent)
    run = RuntimeRun(agent, db, sid, command_id, receipt["run_id"], holder, generation, context, budget)
    return run


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = config()
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    db = SessionDB(tmp_path / "state.db")
    context = resolve_agent_context(raw, session_id="parent", profile_home=tmp_path)
    run = make_run(db, context, parse_budget_policy(raw))
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield raw, run
        finally:
            reset_runtime_run(token, run)
            run.agent.client.close()
            db.close()


def batch_for(raw, run, *, session=None):
    context = resolve_agent_context(raw, session_id=session or uuid.uuid4().hex,
        profile_home=run.context.profile_home, parent_context=run.context, is_child=True)
    run.db.create_session(context.identity.session_id, source="subagent", parent_session_id=run.session_id)
    run.db.claim_session_agent_identity(context.identity.session_id, context.identity.to_record())
    run.db.bind_budget_child(run.budget.account_id, run.budget.actor, context.identity.session_id, **run.budget.fence)
    child = SimpleNamespace(runtime_context=context, _delegate_output_schema=None, close=lambda: None)
    task = {"goal": "Analyze the supplied test fixture"}
    return SimpleNamespace(parent_agent=run.agent, task_list=[task], children=[(0, task, child)])


def test_admitted_child_uses_immutable_grants_isolated_workspace_and_real_reservation(owner):
    raw, run = owner
    batch = batch_for(raw, run)
    prepare_batch(batch)
    prepared = batch.children[0][2]._durable_delegation
    data = prepared.handoff.to_record()
    assert data["grants"]["secret_refs"] == []
    assert data["grants"]["mcp_grants"] == {}
    assert data["budget"]["account_id"] == run.budget.account_id
    assert prepared.workspace.manifest.inputs == ()
    assert Path(data["workspace"]["root"]).is_relative_to(run.context.profile_home)
    data["grants"]["allowed_tools"].append("terminal")
    assert "terminal" not in prepared.handoff.to_record()["grants"]["allowed_tools"]
    assert prepared.registry.read(prepared.ticket.child_id)["state"] == "accepted"


def test_registry_bounds_aggregate_fanout_and_one_shot_execution(owner):
    raw, run = owner
    batch = batch_for(raw, run)
    prepare_batch(batch)
    prepared = batch.children[0][2]._durable_delegation
    with pytest.raises(DelegationError, match="Aggregate active"):
        prepare_batch(batch_for(raw, run))
    prepared.registry.start(run, prepared.ticket)
    with pytest.raises(DelegationError, match="only once"):
        prepared.registry.start(run, prepared.ticket)
    prepared.registry.complete(prepared.ticket, {"status": "completed", "summary": "done"})
    assert prepared.registry.read(prepared.ticket.child_id)["delivery_state"] == "pending"
    for _ in range(3):
        nxt = batch_for(raw, run); prepare_batch(nxt)
        item = nxt.children[0][2]._durable_delegation
        item.registry.start(run, item.ticket)
        item.registry.complete(item.ticket, {"status": "completed"})
    with pytest.raises(DelegationError, match="Aggregate run-tree"):
        prepare_batch(batch_for(raw, run))


def test_parent_completion_claim_is_separate_and_generation_fenced(owner):
    raw, run = owner
    batch = batch_for(raw, run); prepare_batch(batch)
    prepared = batch.children[0][2]._durable_delegation
    prepared.registry.start(run, prepared.ticket)
    prepared.registry.complete(prepared.ticket, {"status": "completed", "summary": "done"})
    receipt = prepared.registry.claim_delivery(run, prepared.ticket.child_id)
    with pytest.raises(DelegationError, match="Another current"):
        prepared.registry.claim_delivery(run, prepared.ticket.child_id)
    with pytest.raises(DelegationError, match="exact live parent"):
        prepared.registry.finish_delivery(run, prepared.ticket.child_id, "forged")
    prepared.registry.finish_delivery(run, prepared.ticket.child_id, receipt["claim_id"])
    assert prepared.registry.claim_delivery(run, prepared.ticket.child_id) is None
    with pytest.raises(DelegationError, match="Only the exact parent"):
        DelegationRegistry(batch.children[0][2].runtime_context, run.db).read(prepared.ticket.child_id)


def test_crash_recovery_classifies_running_unknown_without_respawning(owner):
    raw, run = owner
    batch = batch_for(raw, run); prepare_batch(batch)
    prepared = batch.children[0][2]._durable_delegation
    prepared.registry.start(run, prepared.ticket)
    run.db.release_session_turn_lease(run.session_id, run.holder, generation=run.generation)
    assert run.db.try_acquire_session_turn_lease(run.session_id, "supervisor")
    generation = run.db.get_session_turn_lease(run.session_id)["generation"]
    successor = replace(run, holder="supervisor", generation=generation)
    # The old claimed command is not replayed. This test's supervisor owns the
    # recovery transaction only; no constructor/provider invocation is possible.
    recovered = prepared.registry.recover(successor)
    assert recovered == {"classified": 1, "execution_resumed": False, "respawned": False}
    assert prepared.registry.read(prepared.ticket.child_id)["state"] == "unknown"
    with pytest.raises(DelegationError, match="Recovered/orphaned"):
        prepared.registry.complete(prepared.ticket, {"status": "completed"})


def test_executor_disconnect_and_forged_generation_never_migrate(owner):
    _raw, run = owner
    from agent.executor_capabilities import authenticate_local_executor, require_executor, disconnect_executor
    entry = authenticate_local_executor(run.context)
    assert require_executor(run.context, entry.reference()) is entry
    with pytest.raises(DelegationError, match="cannot migrate"):
        require_executor(run.context, {**entry.reference(), "generation": entry.generation + 1})
    disconnect_executor(run.context, entry.reference())
    with pytest.raises(DelegationError, match="cannot migrate"):
        require_executor(run.context, entry.reference())


def test_strict_preflight_denies_unqualified_teams_and_injected_authority(owner):
    _raw, run = owner
    with pytest.raises(DelegationError, match="Parallel teams"):
        strict_preflight(run.agent, [{"goal": "one"}, {"goal": "two"}])
    with pytest.raises(DelegationError, match="cannot carry"):
        strict_preflight(run.agent, [{"goal": "one", "executor": "source-chosen"}])
    with pytest.raises(DelegationError, match="text-only"):
        strict_preflight(run.agent, [{"goal": "one", "images": ["private.png"]}])


def test_same_specialist_memory_persists_but_ephemeral_children_never_share(owner):
    raw, run = owner
    from agent.specialist_manifest import SpecialistManifest
    from agent.individual_memory_scope import IndividualMemoryScope
    one, context_one = SpecialistManifest.resolve(raw, "researcher", parent=run.context, session_id="one")
    two, context_two = SpecialistManifest.resolve(raw, "researcher", parent=run.context, session_id="two")
    assert one.to_record()["builtin_memory_namespace"] == two.to_record()["builtin_memory_namespace"]
    assert context_one.policy.secret_refs == context_two.policy.secret_refs == frozenset()
    a, b = batch_for(raw, run), batch_for(raw, run)
    assert IndividualMemoryScope.from_context(a.children[0][2].runtime_context).namespace_id != IndividualMemoryScope.from_context(b.children[0][2].runtime_context).namespace_id
    grandchild = resolve_agent_context(raw, session_id="grandchild", profile_home=run.context.profile_home,
        parent_context=a.children[0][2].runtime_context, is_child=True)
    assert grandchild.policy.secret_refs == frozenset()
    assert not grandchild.policy.mcp_grants


def test_handoff_rejects_nan_digest_and_mutable_identity(owner):
    raw, run = owner
    batch = batch_for(raw, run); prepare_batch(batch)
    handoff = batch.children[0][2]._durable_delegation.handoff
    data = handoff.to_record(); data["deadline"] = float("nan")
    with pytest.raises(DelegationError): ImmutableHandoff(data)
    data = handoff.to_record(); data["workspace"]["manifest_digest"] = "forged"
    with pytest.raises(DelegationError): ImmutableHandoff(data)
    data = handoff.to_record(); data["child_identity"]["principal_id"] = "another"
    with pytest.raises(DelegationError): ImmutableHandoff(data)


def test_existing_batch_runner_records_real_completion_before_return(owner, monkeypatch):
    raw, run = owner
    from tools.delegate_tool_dispatch import _Batch, _run_batch
    import tools.delegate_tool as delegation
    source = batch_for(raw, run)
    called = []
    def finite_runner(**kwargs):
        called.append(kwargs["child"].runtime_context.identity.agent_id)
        return {"task_index": 0, "status": "completed", "summary": "finite result", "api_calls": 0}
    monkeypatch.setattr(delegation, "_run_single_child", finite_runner)
    batch = _Batch(source.task_list, source.children, run.agent, {"model": "fixture"}, None, "leaf", 1,
        None, [], [], "", "", None, None, False, time.monotonic())
    result = json.loads(_run_batch(batch, False))
    assert len(called) == 1
    child_id = result["results"][0]["durable_child_id"]
    recorded = DelegationRegistry(run.context, run.db).read(child_id)
    assert recorded["completion"]["summary"] == "finite result"
    assert recorded["delivery_state"] == "pending"
    assert result["results"][0]["execution_resumed"] is False


def test_existing_async_ledger_requires_exact_owner_and_keeps_claim_separate(owner, monkeypatch):
    raw, run = owner
    import tools.delegate_tool as delegation
    from tools.delegate_tool_dispatch import _Batch, _run_batch
    import tools.async_delegation as ad
    from tools.process_registry import process_registry
    import queue
    source = batch_for(raw, run)
    monkeypatch.setattr(delegation, "_run_single_child", lambda **kwargs:
        {"task_index": 0, "status": "completed", "summary": "durable async result"})
    monkeypatch.setattr("tools.delegate_tool_dispatch._resolve_async_wake_sid", lambda *args: run.session_id)
    monkeypatch.setattr(process_registry, "completion_queue", queue.Queue())
    batch = _Batch(source.task_list, source.children, run.agent, {"model": "fixture"}, None, "leaf", 1,
        None, [], [], run.session_id, "", None, None, True, time.monotonic())
    try:
        handle = json.loads(_run_batch(batch, True))
        assert handle["status"] == "dispatched"
        event = process_registry.completion_queue.get(timeout=5)
        assert event["durable_handoffs"] and event["execution_resumed"] is False
        assert not ad.claim_completion_delivery(event["delegation_id"], "no-owner")
        child_context = source.children[0][2].runtime_context
        assert not ad.claim_completion_delivery(event["delegation_id"], "wrong-owner", owner_context=child_context)
        claim = ad.claim_event_delivery(event, "owned-test", owner_context=run.context)
        assert claim
        child_id = event["durable_handoffs"][0]["child_id"]
        assert DelegationRegistry(run.context, run.db).read(child_id)["delivery_state"] == "claimed"
        assert not ad.mark_completion_delivered(event["delegation_id"])
        ad.complete_event_delivery(event, claim)
        assert DelegationRegistry(run.context, run.db).read(child_id)["delivery_state"] == "delivered"
    finally:
        ad._reset_for_tests()


def test_stale_or_tampered_workspace_blocks_before_child_runner(owner, monkeypatch):
    raw, run = owner
    from agent.delegation_runtime import run_child
    source = batch_for(raw, run); prepare_batch(source)
    child = source.children[0][2]
    prepared = child._durable_delegation
    (prepared.workspace.inputs / "not-accepted").write_text("private material")
    called = []
    with pytest.raises(ValueError, match="undeclared"):
        run_child(source, 0, source.task_list[0], child, lambda: called.append(True))
    assert not called


def test_stable_specialist_constructor_uses_trusted_scope_and_rejects_injected_target(owner):
    raw, run = owner
    from agent.specialist_manifest import SpecialistManifest, specialist_construction
    from agent.identity_lifecycle import identity_construction
    from agent.runtime_context import current_agent_context
    from agent.secret_scope import get_secret
    @identity_construction
    def constructor(agent, session_id=None, session_db=None, side_agent=False, skip_background_review=False):
        agent.session_id = session_id
        agent._session_init_model_config = {}
        agent.observed = current_agent_context()
        agent.private = get_secret("PRIVATE_TOKEN")
    manifest, context = SpecialistManifest.resolve(raw, "researcher", parent=run.context, session_id="stable-task")
    child = SimpleNamespace()
    with specialist_construction(manifest, context, run.context):
        constructor(child, session_id="stable-task", session_db=run.db, side_agent=True)
    assert child.observed == context
    assert child.observed.identity.lifecycle == "stable" and child.private is None
    with specialist_construction(manifest, context, run.context), pytest.raises(DelegationError, match="lost its parent/session"):
        constructor(SimpleNamespace(), session_id="injected", session_db=run.db, side_agent=True)
    assert run.db.get_session_model_config_value("stable-task", "agent_identity") == context.identity.to_record()


def test_additive_schema40_migration_preserves_existing_runtime_and_async_records(tmp_path, monkeypatch):
    from hermes_state_delegation_schema import DELEGATION_SCHEMA_SQL
    from hermes_state_media_schema import MEDIA_SCHEMA_SQL
    import hermes_state_schema as schema
    import sqlite3
    path = tmp_path / "migration.db"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_SQL", schema.SCHEMA_SQL.replace(DELEGATION_SCHEMA_SQL, "").replace(MEDIA_SCHEMA_SQL, ""))
        legacy.setattr(schema, "SCHEMA_VERSION", 40)
        legacy.setattr(schema, "_READ_PROBE_STATEMENTS", None)
        db = SessionDB(path)
        db.create_session("existing", source="test")
        db.append_message("existing", "user", content="preserved private fixture")
        db.close()
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT * FROM messages").fetchall()
    db = SessionDB(path)
    with db._runtime_read() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM messages")] == before
        assert conn.execute("SELECT COUNT(*) FROM delegation_handoffs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM bounded_service_pipelines").fetchone()[0] == 0
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 41
    db.close()


def test_strict_direct_async_entry_cannot_fall_back_to_legacy_runner(owner):
    from tools.async_delegation import dispatch_async_delegation
    _raw, run = owner
    calls = []
    with pytest.raises(DelegationError, match="canonical admitted handoffs"):
        dispatch_async_delegation(goal="bypass", context=None, toolsets=None, role="leaf", model="fixture",
            session_key=run.session_id, parent_session_id=run.session_id, runner=lambda: calls.append(True))
    assert not calls
