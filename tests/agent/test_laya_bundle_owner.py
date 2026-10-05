"""Offline synthetic owner integration, never production release evidence.

The normal admitted turn, real request assembly, fenced SessionDB, policy book,
three-stage planner and bridge dispatch are exercised together. Only provider
HTTP and classifier inference are synthetic; no private data is reclassified.
"""
from dataclasses import asdict, replace
import json
import time
from types import SimpleNamespace
import uuid

import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, ModelBundle, digest
from agent.decisions.planner_owner import OwnerQualification, request_schemas
from agent.decisions.point_policies import PointPolicyBook, PointRule
from agent.decisions.receipts import JournalSink, scope_digest
from agent.decisions.registry import contract_for
from agent.decisions.release_gates import ReleaseEvidence, OperatorApproval, dp16_release_bindings, metric_gates
from agent.decisions.tool_planner import BRIDGES
from tests.agent.test_decision_planner_runtime import agents, execute

BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


@pytest.fixture
def full_agents(agents, monkeypatch):
    def make(*args, **kwargs):
        from tools.todo_tool import TODO_SCHEMA
        result = agents(*args, **kwargs)
        agent = result[1]
        if "todo_list" not in names(agent.tools):
            agent.tools = [{"type": "function", "function": TODO_SCHEMA}, *agent.tools]
        # Exercise the real first-build persistence path, unlike the base
        # observer fixture which seeds only its in-memory prompt.
        prompt = agent._cached_system_prompt
        monkeypatch.setattr(agent, "_build_system_prompt", lambda *args, **kwargs: prompt)
        agent._cached_system_prompt = None
        return result
    return make


class SyntheticTransport:
    protocol = "laya_systemone"

    def __init__(self, *, needs_tools=False):
        self.admission_key = "owner-synthetic:" + uuid.uuid4().hex
        self.needs_tools, self.calls = needs_tools, []

    def decide_many(self, requests, timeout):
        self.calls.append(tuple(requests))
        records = []
        for request in requests:
            selected = {"need": "needs_tools" if self.needs_tools else "no_tools", "effort": "one",
                        "family": "yes" if self.needs_tools else "no", "include": "yes", "verify": "yes"}[request.question_id]
            source = request.to_record()
            keys = ("request_id", "point_id", "contract_version", "contract_digest", "question_id", "input_digest", "scope_digest")
            records.append({**{key: source[key] for key in keys}, **asdict(BUNDLE),
                "distribution": {choice: float(choice == selected) for choice in request.live_options},
                "selected": selected, "unclear": False, "latency_ms": .01})
        return SimpleNamespace(records=records, usage=SimpleNamespace(input_tokens=100, output_tokens=3))


def promote_fixture(run, *, version=2):
    from agent.decisions.planner_runtime import PolicyJournal
    scope = scope_digest(run.context)
    rule = PointRule("DP16", "enforce", timeout_seconds=1,
                     allowed_effects=("bundle", "authorized_full"), rollout_scope=(scope,))
    now = time.time()
    reports = tuple((name, digest(name)) for name in ("calibration", "holdout", "red_team", "live_shadow", "latency", "budget", "paired_outcomes", "cache_costs"))
    if version == 2:
        reports += tuple(dp16_release_bindings().items())
    # Simulated trusted evidence tests the release workflow itself. It is not
    # real-candidate evidence and is never exported as an evaluation report.
    evidence = ReleaseEvidence("DP16", contract_for("DP16", version).contract_digest,
        BUNDLE.model_digest, BUNDLE.calibration_digest, BUNDLE.service_digest, rule.policy_digest,
        "4" * 64, "5" * 64, ("6" * 64,), reports,
        tuple((gate.name, gate.minimum if gate.minimum is not None else 0) for gate in metric_gates("DP16")),
        "real_candidate", "human", True, True, scope, now - 1, now + 3600)
    approval = OperatorApproval("synthetic_operator", "DP16", evidence.evidence_digest, rule.policy_digest, now - 1, now + 3600)
    book = PointPolicyBook(journal=PolicyJournal(run), authorize_operator=lambda value: value == approval)
    book.promote(rule, bundle=BUNDLE, evidence=evidence, approval=approval,
                 scope_digest=scope, consumer_ready=True, contract_version=version)
    return book


def enable_owner(monkeypatch, agent, *, needs_tools=False, source_texts=("synthetic owner request",), prepared_transform=None, version=2, expected_release=BUNDLE):
    from agent.decisions import planner_owner, planner_runtime_context
    from agent.decisions.planner_context import build_planner_context
    transport = SyntheticTransport(needs_tools=needs_tools)
    original_capture = planner_owner.capture_turn_boundary
    books = []

    def source_context(request, **kwargs):
        if request in source_texts:
            kwargs["classification"] = "synthetic"
        return build_planner_context(request, **kwargs)
    monkeypatch.setattr(planner_runtime_context, "build_planner_context", source_context)

    def capture(target, **kwargs):
        original_capture(target, **kwargs)
        if target is agent and not books:
            run = planner_owner._run(target)
            book = promote_fixture(run, version=version)
            books.append(book)
            def factory(current, policy, gate, bundle):
                return DecisionClient(bundle=bundle, policies={"DP16": policy}, gates={"DP16": gate},
                    sink=JournalSink(current), transport=transport)
            target._decision_bundle_qualification = OwnerQualification(book, expected_release, factory)
    monkeypatch.setattr(planner_owner, "capture_turn_boundary", capture)
    if prepared_transform:
        from agent.decisions import integration
        original_prepare = integration.prepare_turn_planner
        def prepare(*args, **kwargs):
            value = original_prepare(*args, **kwargs)
            return prepared_transform(value) if value is not None else None
        monkeypatch.setattr(integration, "prepare_turn_planner", prepare)
    return transport, books


def execute_history(agent, text, history):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import bind_submitted_command, submit_command
    with agent_runtime_scope(agent.runtime_context):
        receipt = submit_command(agent, {"schema_version": 1, "command_id": text, "idempotency_key": text,
            "expected_revision": None, "operation": "submit", "payload": {"text": text}})
    with bind_submitted_command(agent, receipt):
        return agent.run_conversation(text, conversation_history=history)


def names(tools):
    return {tool["function"]["name"] for tool in tools}


def pin(db):
    return db.get_session_model_config_value("session", "decision_request_bundle")


def test_qualified_first_freeze_actual_requests_bridge_dispatch_and_followup(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("owner", "off")
    canonical_before = json.dumps(agent.tools, sort_keys=True)
    transport, books = enable_owner(monkeypatch, agent)
    first = execute(agent, "synthetic owner request")
    assert first["final_response"] == "recorded answer"
    assert transport.calls and len(transport.calls) == 1
    assert all(names(request["tools"]) == set(BRIDGES) for request in requests)
    assert json.dumps(agent.tools, sort_keys=True) == canonical_before
    frozen = pin(db)
    assert frozen and frozen["plan_bundle_id"] and frozen["receipt_ids"]
    # The false-negative no-tools choice still recovers through the actual
    # tool_call bridge and invokes the already-authorized todo implementation.
    assert any(event["type"] == "tool.completed" for event in db.replay_runtime_events("session", limit=500)["events"])
    execute_history(agent, "synthetic followup request", first["messages"])
    assert len(transport.calls) == 1
    assert pin(db) == frozen
    assert all(request["tools"] == requests[0]["tools"] for request in requests)
    systems = [[message for message in request["messages"] if message["role"] == "system"] for request in requests]
    assert systems[0] and all(system == systems[0] for system in systems)
    assert all(request["messages"][:len(requests[0]["messages"])] == requests[0]["messages"] for request in requests)
    assert len(books) == 1
    assert "synthetic owner request" not in json.dumps(frozen)
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    from tui_gateway.methods_runtime import _runtime_event_projection
    events = db.replay_runtime_events("session", limit=500)["events"]
    published = [event for event in events if event["type"] == "decision.bundle"]
    assert len(published) == 1
    projected = RuntimeEventEnvelope.model_validate(_runtime_event_projection(published[0])).model_dump(mode="json")
    assert "schemas_json" not in json.dumps(projected) and "synthetic owner request" not in json.dumps(projected)


@pytest.mark.parametrize("transform", [
    lambda value: replace(value, turn_id="past_turn"),
    lambda value: replace(value, run_id="past_run"),
    lambda value: replace(value, generation=value.generation + 1),
    lambda value: replace(value, scope_digest="f" * 64),
    lambda value: replace(value, request_digest="f" * 64),
    lambda value: replace(value, plan=replace(value.plan, live_catalog_version="f" * 64)),
    lambda value: replace(value, plan=replace(value.plan, decision_receipt_ids=("f" * 64,))),
])
def test_stale_plan_owner_catalog_or_missing_receipt_keeps_exact_incumbent(full_agents, monkeypatch, transform):
    _, agent, db, requests = full_agents("stale", "off")
    before = json.loads(json.dumps(agent.tools))
    enable_owner(monkeypatch, agent, prepared_transform=transform)
    execute(agent, "synthetic owner request")
    assert requests[0]["tools"] == before and pin(db) is None


def test_private_source_remains_blocked_with_a_qualified_book(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("private", "off")
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "private payload may not be exported")
    assert not transport.calls and pin(db) is None
    assert requests[0]["tools"] == before


def test_v1_release_cannot_qualify_a_v2_owner(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("wrong_contract", "off")
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent, version=1)
    execute(agent, "synthetic owner request")
    assert not transport.calls and pin(db) is None and requests[0]["tools"] == before


def test_off_has_no_classifier_calls_or_schema_change(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("off", "off")
    monkeypatch.setattr(DecisionClient, "decide_many", lambda *a, **k: pytest.fail("off classified"))
    before = json.loads(json.dumps(agent.tools))
    execute(agent, "synthetic off request")
    assert requests[0]["tools"] == before and pin(db) is None


def test_retry_and_cache_redecoration_keep_frozen_prefix(full_agents, monkeypatch):
    import httpx
    _, agent, db, requests = full_agents("retry", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    def transient():
        raise httpx.ConnectError("synthetic retry")
    agent._fixture_on_request = transient
    execute(agent, "synthetic owner request")
    assert len(requests) >= 2 and len(transport.calls) == 1
    assert all(request["tools"] == requests[0]["tools"] for request in requests)
    from agent.conversation_loop import _redecorate_prompt_cache_for_provider
    from agent.prompt_caching import strip_anthropic_tool_cache_control
    agent._use_prompt_caching = True
    for provider in ("openai", "anthropic", "openai"):
        agent.provider = provider
        _, _, tools = _redecorate_prompt_cache_for_provider(agent, requests[0]["messages"],
                                                           tools_for_api=request_schemas(agent))
        assert strip_anthropic_tool_cache_control(tools) == request_schemas(agent)


def restart_fixture(db, monkeypatch):
    from run_agent import AIAgent
    restarted = AIAgent(model="gpt-4.1-mini", provider="openai", api_key="synthetic-provider-key",
        base_url="https://fixture.invalid/v1", session_id="session", session_db=db, quiet_mode=True,
        skip_context_files=True, skip_memory=True, max_iterations=4, enabled_toolsets=["todo"])
    from tools.todo_tool import TODO_SCHEMA
    if "todo_list" not in names(restarted.tools):
        restarted.tools = [{"type": "function", "function": TODO_SCHEMA}, *restarted.tools]
    restarted._use_prompt_caching = False
    restarted._disable_streaming = True
    restarted.compression_enabled = False
    restarted.save_trajectories = False
    monkeypatch.setattr(restarted, "_create_request_openai_client", lambda **kwargs: restarted.client)
    monkeypatch.setattr(restarted, "_close_request_openai_client", lambda *a, **k: None)
    monkeypatch.setattr(restarted, "_cleanup_task_resources", lambda *a, **k: None)
    monkeypatch.setattr(restarted, "_save_trajectory", lambda *a, **k: None)
    return restarted


def test_restart_without_release_and_fresh_discovery_cannot_expand_request_pin(full_agents, monkeypatch):
    home, agent, db, requests = full_agents("restart", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    restarted = restart_fixture(db, monkeypatch)
    try:
        execute_history(restarted, "synthetic resumed request", db.get_messages_as_conversation("session"))
        assert "todo_list" in names(restarted.tools)
        assert all(names(request["tools"]) == set(BRIDGES) for request in requests)
        assert pin(db) == frozen and len(transport.calls) == 1
        systems = [[message for message in request["messages"] if message["role"] == "system"] for request in requests]
        assert systems[0] and all(system == systems[0] for system in systems)
        # Simulate the normal MCP refresh's full canonical discovery expansion.
        restarted.tools = [*restarted.tools, {"type": "function", "function": {
            "name": "mcp__synthetic_new_tool", "description": "Synthetic new tool", "parameters": {"type": "object", "properties": {}}}}]
        execute(restarted, "synthetic refresh request")
        assert names(requests[-1]["tools"]) == set(BRIDGES)
        assert "mcp__synthetic_new_tool" in names(restarted.tools)
    finally:
        restarted.close()


@pytest.mark.parametrize("committed,in_place", [(False, False), (False, True), (True, False), (True, True)])
def test_only_real_committed_compression_allows_rollback_to_full(full_agents, monkeypatch, committed, in_place):
    _, agent, db, requests = full_agents("compression", "off")
    before = json.loads(json.dumps(agent.tools))
    transport, books = enable_owner(monkeypatch, agent)
    initial = []
    def compress_during_turn():
        from agent.conversation_compression import _finish_compaction_boundary
        from agent.context_projection import context_commit_for_current_run
        initial.append(pin(db))
        books[0].rollback("DP16", reason="operator", mode="off")
        compressed = [{"role": "user", "content": "Synthetic committed context"}]
        if committed and in_place:
            metadata = context_commit_for_current_run(db, "session", system_prompt=agent._cached_system_prompt)
            db.archive_and_compact("session", compressed, context_commit=metadata)
        else:
            # The real rebuild path can refresh/persist canonical tools BEFORE
            # a failed commit. It must not touch the independent request pin.
            from tools.mcp_tool_agent import tool_pin_version
            agent.tools = [*agent.tools, {"type": "function", "function": {
                "name": "mcp__synthetic_refresh", "description": "Synthetic schema refresh",
                "parameters": {"type": "object", "properties": {}}}}]
            db.update_session_tool_names("session", {"version": tool_pin_version(), "tools": agent.tools})
        _finish_compaction_boundary(agent, compressed, new_system_prompt=agent._cached_system_prompt,
            old_session_id=None, in_place=in_place, compacted_in_place=in_place,
            session_commit_succeeded=committed, defer_context_engine_notification=False,
            compression_made_progress=True, compression_used_fallback=False,
            compression_feasibility_skip=False, task_id="synthetic_compression")
    agent._fixture_on_request = compress_during_turn
    execute(agent, "synthetic owner request")
    assert len(transport.calls) == 1 and names(requests[0]["tools"]) == set(BRIDGES)
    if committed and in_place:
        assert requests[-1]["tools"] == before
        assert pin(db)["boundary"] == "compression" and pin(db)["plan_bundle_id"] is None
        assert pin(db)["context_id"] != initial[0]["context_id"]
    else:
        assert all(names(request["tools"]) == set(BRIDGES) for request in requests)
        assert pin(db) == initial[0]


def test_rollback_freezes_existing_prefix_until_boundary(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("rollback", "off")
    transport, books = enable_owner(monkeypatch, agent)
    agent._fixture_on_request = lambda: books[0].rollback("DP16", reason="operator", mode="off")
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    execute(agent, "synthetic request after rollback")
    assert len(transport.calls) == 1 and pin(db) == frozen
    assert all(names(request["tools"]) == set(BRIDGES) for request in requests)


def test_current_grant_revocation_blocks_dispatch_but_keeps_frozen_schema(full_agents, monkeypatch):
    home, agent, db, requests = full_agents("revocation", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    def revoke():
        config = json.loads((home / "config.yaml").read_text())
        config["agent_identity"]["agents"]["primary"]["allowed_tools"] = []
        (home / "config.yaml").write_text(json.dumps(config))
    agent._fixture_on_request = revoke
    from tools.capability_broker import CapabilityDenied
    with pytest.raises(CapabilityDenied, match="policy changed"):
        execute(agent, "synthetic owner request")
    assert names(requests[0]["tools"]) == set(BRIDGES)
    assert pin(db)["plan_bundle_id"]
    events = db.replay_runtime_events("session", limit=500)["events"]
    assert not any(event["type"] == "tool.completed" for event in events)
    assert names(request_schemas(agent)) == set(BRIDGES)


def test_profile_scopes_cannot_borrow_another_owner_release(full_agents, monkeypatch):
    home_a, a, db_a, requests_a = full_agents("scope_a", "off")
    transport, _ = enable_owner(monkeypatch, a)
    execute(a, "synthetic owner request")
    _, b, db_b, requests_b = full_agents("scope_b", "off")
    b._decision_bundle_qualification = a._decision_bundle_qualification
    before_b = json.loads(json.dumps(b.tools))
    execute(b, "synthetic owner request")
    assert requests_b[0]["tools"] == before_b and pin(db_b) is None
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    execute(a, "synthetic return to a")
    assert names(requests_a[-1]["tools"]) == set(BRIDGES) and len(transport.calls) == 1


def test_v2_release_requires_exact_renderer_catalog_and_qualification_reports():
    from tests.agent.test_decision_point_policies import Journal, evidence_for, approval_for, rule
    policy = rule()
    evidence = replace(evidence_for(policy, provenance="real_candidate"),
                       contract_digest=contract_for("DP16", 2).contract_digest)
    book = PointPolicyBook(journal=Journal(), authorize_operator=lambda value: True)
    with pytest.raises(DecisionError, match="release_not_qualified"):
        book.promote(policy, bundle=BUNDLE, evidence=evidence, approval=approval_for(evidence),
                     scope_digest=evidence.scope_digest, consumer_ready=True, contract_version=2, now=200)
    qualified = replace(evidence, report_digests=evidence.report_digests + tuple(dp16_release_bindings().items()))
    book.promote(policy, bundle=BUNDLE, evidence=qualified, approval=approval_for(qualified),
                 scope_digest=qualified.scope_digest, consumer_ready=True, contract_version=2, now=200)
    assert book.inspect("DP16", scope_digest=qualified.scope_digest, now=200)["contract_version"] == 2


def test_three_stage_qualified_plan_has_complete_durable_receipts(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("three_stage", "off")
    transport, _ = enable_owner(monkeypatch, agent, needs_tools=True)
    execute(agent, "synthetic owner request")
    assert len(transport.calls) == 3
    assert [set(request.question_id for request in batch) for batch in transport.calls] == [
        {"need", "effort", "family"}, {"include"}, {"verify"}]
    assert names(requests[0]["tools"]) == {*BRIDGES, "todo_list"}
    assert len(pin(db)["receipt_ids"]) == sum(map(len, transport.calls))


def test_missing_bridge_grant_prevents_inference_and_reduction(full_agents, monkeypatch):
    from tests.agent import test_decision_planner_runtime as runtime_fixture
    original_configuration = runtime_fixture.configuration
    def configuration(*args, **kwargs):
        value = original_configuration(*args, **kwargs)
        value["agent_identity"]["agents"]["primary"]["allowed_tools"].remove("tool_describe")
        return value
    monkeypatch.setattr(runtime_fixture, "configuration", configuration)
    _, agent, db, requests = full_agents("missing_bridge", "off")
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    assert not transport.calls and pin(db) is None and requests[0]["tools"] == before


def test_stored_legacy_history_is_not_a_new_schema_context(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("history", "off")
    agent._ensure_db_session()
    db.append_message("session", "user", "Synthetic old context")
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    assert not transport.calls and pin(db) is None and requests[0]["tools"] == before


def test_real_bridge_description_recovers_without_rewriting_request(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("describe", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    recovered = []
    def describe():
        from tools.tool_search import dispatch_tool_describe
        from tools.todo_tool import TODO_SCHEMA
        before = pin(db)
        recovered.append(json.loads(dispatch_tool_describe({"names": ["todo_list", "other_owner_secret"]},
            current_tool_defs=[{"type": "function", "function": TODO_SCHEMA}])))
        assert pin(db) == before
    agent._fixture_on_request = describe
    execute(agent, "synthetic owner request")
    assert "todo_list" in recovered[0]["tools"] and "other_owner_secret" not in recovered[0]["tools"]
    assert all(names(request["tools"]) == set(BRIDGES) for request in requests)
    events = db.replay_runtime_events("session", limit=500)["events"]
    misses = [event["payload"] for event in events if event["type"] == "decision.planner_miss"]
    assert len(misses) == 2 and all(not event["observation_only"] for event in misses)
    assert all(event["prefix_digest"] == pin(db)["prefix_digest"] for event in misses)


def test_other_owner_cannot_reuse_frozen_request_pin(full_agents, monkeypatch):
    _, a, _, _ = full_agents("owner_one", "off")
    enable_owner(monkeypatch, a)
    execute(a, "synthetic owner request")
    _, b, _, _ = full_agents("owner_two", "off")
    b._decision_bundle_owner = a._decision_bundle_owner
    assert request_schemas(b) == b.tools and "todo_list" in names(request_schemas(b))


def test_missing_retained_receipt_rejects_an_otherwise_persisted_plan(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("missing_receipt", "off")
    before = json.loads(json.dumps(agent.tools))
    def remove_receipt(value):
        receipt = value.plan.decision_receipt_ids[0]
        db._execute_write(lambda conn: conn.execute("DELETE FROM runtime_events WHERE session_id='session' "
            "AND type='decision.observed' AND json_extract(payload_json, '$.receipt_id')=?", (receipt,)))
        return value
    enable_owner(monkeypatch, agent, prepared_transform=remove_receipt)
    execute(agent, "synthetic owner request")
    assert pin(db) is None and requests[0]["tools"] == before


def test_bundle_persistence_failure_rolls_back_schema_pin_atomically(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("disk_failure", "off")
    before = json.loads(json.dumps(agent.tools))
    append = db._append_runtime_event_on_conn
    def fail_bundle(conn, session_id, event_type, *args, **kwargs):
        if event_type == "decision.bundle":
            raise OSError("synthetic disk failure")
        return append(conn, session_id, event_type, *args, **kwargs)
    monkeypatch.setattr(db, "_append_runtime_event_on_conn", fail_bundle)
    enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    assert pin(db) is None and all(request["tools"] == before for request in requests)


def test_release_rollback_between_plan_and_install_keeps_incumbent(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("release_changed", "off")
    before = json.loads(json.dumps(agent.tools))
    def rollback(value):
        agent._decision_bundle_qualification.policy_book.rollback("DP16", reason="operator", mode="off")
        return value
    transport, _ = enable_owner(monkeypatch, agent, prepared_transform=rollback)
    execute(agent, "synthetic owner request")
    assert len(transport.calls) == 1 and pin(db) is None
    assert all(request["tools"] == before for request in requests)


@pytest.mark.parametrize("field", ["model_digest", "calibration_digest", "service_digest"])
def test_expected_release_pin_mismatch_cannot_start_inference(full_agents, monkeypatch, field):
    _, agent, db, requests = full_agents("wrong_release", "off")
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent, expected_release=replace(BUNDLE, **{field: "a" * 64}))
    execute(agent, "synthetic owner request")
    assert not transport.calls and pin(db) is None and requests[0]["tools"] == before


def test_detached_manual_boundary_cannot_reuse_previous_turn_plan(full_agents, monkeypatch):
    from agent.decisions.planner_owner import committed_compaction
    _, agent, db, requests = full_agents("manual_boundary", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    before = pin(db)
    assert agent._decision_bundle_owner.prepared is not None
    # No admitted runtime owns this detached/manual notification. Even a claimed
    # successful status cannot reopen installation or replay the prior plan.
    committed_compaction(agent, session_commit_succeeded=True, old_session_id=None, compacted_in_place=True)
    assert agent._decision_bundle_owner.prepared is None
    assert agent._decision_bundle_owner.boundary is None
    execute(agent, "synthetic next turn after manual notification")
    assert pin(db) == before and len(transport.calls) == 1
    assert all(names(request["tools"]) == set(BRIDGES) for request in requests)


def test_unknown_persisted_schema_pin_is_not_a_new_context(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("legacy_pin", "off")
    from tools.mcp_tool_agent import tool_pin_version
    agent._ensure_db_session()
    db.update_session_tool_names("session", {"version": tool_pin_version(), "tools": agent.tools})
    before = json.loads(json.dumps(agent.tools))
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    assert not transport.calls and pin(db) is None and requests[0]["tools"] == before


@pytest.mark.parametrize("restore,rollback", [(False, False), (True, False), (False, True), (True, True)])
def test_bridge_recovery_uses_installed_pin_without_a_current_plan(full_agents, monkeypatch, restore, rollback):
    _, agent, db, requests = full_agents("bridge_followup", "off")
    transport, books = enable_owner(monkeypatch, agent)
    if rollback:
        agent._fixture_on_request = lambda: books[0].rollback("DP16", reason="operator", mode="off")
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    agent._decision_tool_plan = None
    if restore:
        agent._decision_bundle_owner = None
        del agent._decision_bundle_qualification
    recovered = []
    def describe():
        from tools.tool_search import dispatch_tool_describe
        from tools.todo_tool import TODO_SCHEMA
        assert agent._decision_tool_plan is None
        recovered.append(json.loads(dispatch_tool_describe({"names": ["todo_list"]},
            current_tool_defs=[{"type": "function", "function": TODO_SCHEMA}])))
    agent._fixture_on_request = describe
    requests.clear()
    execute_history(agent, "synthetic bridge followup", db.get_messages_as_conversation("session"))
    assert "todo_list" in recovered[0]["tools"] and len(transport.calls) == 1
    assert pin(db) == frozen and all(names(request["tools"]) == set(BRIDGES) for request in requests)
    misses = [event["payload"] for event in db.replay_runtime_events("session", limit=500)["events"]
              if event["type"] == "decision.planner_miss"]
    assert len(misses) == 1
    assert misses[0]["recovered"] and not misses[0]["observation_only"]
    assert misses[0]["prefix_digest"] == frozen["prefix_digest"]
    assert misses[0]["bundle_id"] == frozen["plan_bundle_id"]
    assert misses[0]["previous_catalog_version"] == frozen["catalog_version"]


def test_bridge_misses_follow_actual_selected_schemas_not_new_plan(full_agents, monkeypatch):
    _, agent, db, requests = full_agents("selected_schema", "off")
    enable_owner(monkeypatch, agent, needs_tools=True)
    def describe():
        from tools.tool_search import dispatch_tool_describe
        from tools.todo_tool import TODO_SCHEMA
        run_id, plan, prefix = agent._decision_tool_plan
        agent._decision_tool_plan = (run_id, replace(plan, verified_tool_ids=()), prefix)
        output = json.loads(dispatch_tool_describe({"names": ["todo_list"]},
            current_tool_defs=[{"type": "function", "function": TODO_SCHEMA}]))
        assert "todo_list" in output["tools"]
    agent._fixture_on_request = describe
    execute(agent, "synthetic owner request")
    assert "todo_list" in names(requests[0]["tools"])
    assert not [event for event in db.replay_runtime_events("session", limit=500)["events"]
                if event["type"] == "decision.planner_miss"]


def test_uninstalled_enforcing_plan_cannot_be_recorded_as_actual_miss(full_agents, monkeypatch):
    _, agent, db, _ = full_agents("uninstalled", "off")
    enable_owner(monkeypatch, agent, prepared_transform=lambda value: replace(value, turn_id="stale"))
    def describe():
        from tools.tool_search import dispatch_tool_describe
        from tools.todo_tool import TODO_SCHEMA
        assert pin(db) is None
        dispatch_tool_describe({"names": ["todo_list"]},
            current_tool_defs=[{"type": "function", "function": TODO_SCHEMA}])
    agent._fixture_on_request = describe
    execute(agent, "synthetic owner request")
    assert not [event for event in db.replay_runtime_events("session", limit=500)["events"]
                if event["type"] == "decision.planner_miss"]


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(schema_version=True),
    lambda row: row.update(boundary_witness={}),
    lambda row: row.update(boundary_witness={"first_request": 1}),
    lambda row: row.update(boundary="compression", boundary_witness={}),
    lambda row: row["receipt_ids"].append(row["receipt_ids"][0]),
    lambda row: row.update(receipt_ids=[[]]),
    lambda row: row.update(release=None),
    lambda row: row["release"].update(contract_version=2.0),
])
def test_persisted_owner_pin_rejects_malformed_versions_and_boundary_witnesses(full_agents, monkeypatch, mutation):
    from hermes_state_decision_bundles import validate_record
    from agent.runtime_commands import assert_runtime_dispatch
    _, agent, db, _ = full_agents("malformed_pin", "off")
    enable_owner(monkeypatch, agent)
    checked = []
    def inspect_during_turn():
        record = pin(db)
        mutation(record)
        record["record_digest"] = digest({key: value for key, value in record.items() if key != "record_digest"})
        with pytest.raises(DecisionError):
            validate_record(record, assert_runtime_dispatch())
        checked.append(True)
    agent._fixture_on_request = inspect_during_turn
    execute(agent, "synthetic owner request")
    assert checked


def test_off_accepts_65_harmless_multimodal_parts_without_planner_digest(full_agents, monkeypatch):
    from agent.identity_lifecycle import agent_runtime_scope
    _, agent, db, requests = full_agents("off_multimodal", "off")
    parts = [{"type": "text", "text": "Synthetic harmless part " + str(index)} for index in range(65)]
    before = json.loads(json.dumps(agent.tools))
    monkeypatch.setattr("agent.decisions.planner_runtime._request_digest",
                        lambda *a, **k: pytest.fail("off parsed planner request"))
    monkeypatch.setattr(DecisionClient, "decide_many", lambda *a, **k: pytest.fail("off classified"))
    with agent_runtime_scope(agent.runtime_context):
        result = agent.run_conversation(parts)
    assert result["final_response"] == "recorded answer"
    assert requests and all(request["tools"] == before for request in requests)
    assert pin(db) is None


def test_cold_restart_retries_failed_pin_read_before_sending_exact_schemas(full_agents, monkeypatch):
    from agent.decisions import planner_owner
    _, agent, db, requests = full_agents("restore_retry", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    restarted = restart_fixture(db, monkeypatch)
    read = planner_owner.read_bundle_state
    attempts = []
    def fail_first(run):
        if run.agent is restarted:
            attempts.append(run.run_id)
            if len(attempts) == 1:
                raise OSError("synthetic transient pin read")
        return read(run)
    monkeypatch.setattr(planner_owner, "read_bundle_state", fail_first)
    prior_requests = len(requests)
    try:
        execute_history(restarted, "synthetic restart after transient read", db.get_messages_as_conversation("session"))
        assert len(attempts) == 2 and len(requests) > prior_requests
        assert all(request["tools"] == json.loads(frozen["schemas_json"]) for request in requests)
        assert pin(db) == frozen and len(transport.calls) == 1
        assert restarted._decision_bundle_owner.restore_failed is False
    finally:
        restarted.close()


@pytest.mark.parametrize("failure", ["read_unavailable", "malformed_pin"])
def test_cold_restart_never_sends_unknown_prefix_and_recovers_without_compression(full_agents, monkeypatch, failure):
    from agent.decisions import planner_owner
    from agent.runtime_commands import RuntimeFenceError
    _, agent, db, requests = full_agents("restore_blocked", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    restarted = restart_fixture(db, monkeypatch)
    read = planner_owner.read_bundle_state
    unavailable = [True]
    def fail_read(run):
        if run.agent is restarted and unavailable[0]:
            raise OSError("synthetic persistent pin read")
        return read(run)
    if failure == "read_unavailable":
        monkeypatch.setattr(planner_owner, "read_bundle_state", fail_read)
    else:
        db.patch_session_model_config("session", {"decision_request_bundle": {"schema_version": -1}})
    prior_requests = len(requests)
    try:
        with pytest.raises(RuntimeFenceError, match="schema prefix could not be restored"):
            execute_history(restarted, "synthetic blocked restart", db.get_messages_as_conversation("session"))
        assert len(requests) == prior_requests
        assert restarted._decision_bundle_owner.restore_failed is True
        assert restarted._decision_bundle_owner.record is None
        unavailable[0] = False
        if failure == "malformed_pin":
            db.patch_session_model_config("session", {"decision_request_bundle": frozen})
        execute_history(restarted, "synthetic restored retry", db.get_messages_as_conversation("session"))
        assert len(requests) > prior_requests
        assert all(request["tools"] == json.loads(frozen["schemas_json"]) for request in requests)
        assert pin(db) == frozen and len(transport.calls) == 1
        assert pin(db)["boundary"] == "new_context"
    finally:
        restarted.close()


def test_warm_pin_read_failure_keeps_known_frozen_request_bytes(full_agents, monkeypatch):
    from agent.decisions import planner_owner
    _, agent, db, requests = full_agents("warm_restore", "off")
    transport, _ = enable_owner(monkeypatch, agent)
    execute(agent, "synthetic owner request")
    frozen = pin(db)
    def unavailable(run):
        raise OSError("synthetic transient read")
    monkeypatch.setattr(planner_owner, "read_bundle_state", unavailable)
    execute_history(agent, "synthetic warm turn during read failure", db.get_messages_as_conversation("session"))
    assert agent._decision_bundle_owner.restore_failed is True
    assert all(request["tools"] == json.loads(frozen["schemas_json"]) for request in requests)
    assert pin(db) == frozen and len(transport.calls) == 1
