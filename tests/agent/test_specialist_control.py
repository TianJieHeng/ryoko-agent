"""Real named child construction, SDK wire, durable admission, and isolated memory."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import bind_submitted_command, submit_command
from agent.specialist_control import specialist_catalog, specialist_preview, specialist_status
from tests.agent.test_budget_runtime import BASE_URL, MODEL, active, config as base_config, guarded_http, response
from tests.hermes_cli.test_artifact_store import publish_fixture

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def specialist_runtime(tmp_path, monkeypatch):
    from hermes_cli import projects_db
    from hermes_state import SessionDB
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    with projects_db.connect_closing() as conn:
        project = projects_db.create_project(conn, name="Specialist fixture", owner_principal_id="owner", grants=[
            {"principal_id": "owner", "agent_id": name, "permissions": ["read", "write", "share"]}
            for name in ("primary", "researcher")])
    raw = base_config()
    primary = raw["agent_identity"]["agents"]["primary"]
    primary["project_grants"] = [project]
    primary["allowed_tools"] += ["memory"]
    raw["agent_identity"]["personal_mcp_servers"] = ["private"]
    raw["agent_identity"]["personal_secret_refs"] = ["PRIVATE_TOKEN"]
    primary["secret_refs"].append("PRIVATE_TOKEN")
    raw["agent_identity"]["agents"]["researcher"] = {
        **primary, "role": "specialist", "memory_backend": "builtin", "allowed_tools": ["todo_list", "memory"],
        "secret_refs": ["OPENAI_API_KEY"]}
    raw["delegation"] = {"durable": {"enabled": True, "limits": {
        "max_depth": 1, "max_total_children": 4, "max_concurrent_children": 1}},
        "max_iterations": 2, "max_concurrent_children": 1, "specialists": {}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(raw))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-provider-key\nPRIVATE_TOKEN=fixture-private\n")
    clients, calls, contexts = [], [], []
    handler = {"value": None}

    def client_factory(*args, **kwargs):
        from agent.runtime_context import current_agent_context
        context = current_agent_context()
        contexts.append(context)
        def dispatch(request):
            calls.append((context.identity.agent_id, json.loads(request.content)))
            if handler["value"] is not None:
                return handler["value"](request)
            result = response()
            result["choices"][0]["message"]["content"] = '{"answer":"bounded specialist answer"}'
            return httpx.Response(200, json=result)
        client = openai.OpenAI(api_key="fixture", base_url=BASE_URL, max_retries=0,
                              http_client=guarded_http(dispatch))
        clients.append(client)
        return client

    monkeypatch.setattr("tools.egress_policy.build_model_client", client_factory)
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    from tools.todo_tool import TODO_SCHEMA
    from tools.memory_tool import MEMORY_SCHEMA
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [
        {"type": "function", "function": schema} for schema in (TODO_SCHEMA, MEMORY_SCHEMA, DELEGATE_TASK_SCHEMA)])
    from run_agent import AIAgent
    db = SessionDB(tmp_path / "state.db")
    agent = AIAgent(model=MODEL, provider="openai", api_key="fixture-provider-key", base_url=BASE_URL,
        session_id="parent", session_db=db, quiet_mode=True, skip_context_files=True, skip_memory=True,
        max_iterations=2, enabled_toolsets=["todo", "memory", "delegation"])
    agent._cached_system_prompt = "Unchanged parent prefix"
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent.tool_delay = 0
    # Seed the exact method artifact through the real publisher under a genuine
    # admitted submit. Only the synthetic fixture approves its synthetic bytes.
    with active(agent) as run:
        methods = publish_fixture(run, project, "specialist-methods", "# Method\nSummarize only supplied sources.\n")
        db.finish_runtime_command(run.session_id, run.command_id, holder=run.holder, generation=run.generation,
                                  result={"fixture": "method publication"})
    raw["delegation"]["specialists"]["researcher"] = {
        "responsibility": "Summarize exact supplied sources", "methods_ref": {
            "id": methods["artifact_id"], "version": methods["version"], "sha256": methods["sha256"]},
        "limits": {"max_depth": 1, "max_total_children": 1, "max_concurrent_children": 1},
        "output_contract": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}}
    config_path.write_text(json.dumps(raw))

    def preview(**updates):
        with agent_runtime_scope(agent.runtime_context):
            return specialist_preview(agent, {"project_id": project, "specialist_id": "researcher",
                "objective": "Summarize the configured method", **updates})

    def envelope(prepared, key="specialist-command"):
        return {"schema_version": 1, "command_id": key, "idempotency_key": key, "expected_revision": None,
                "operation": "submit", "payload": {"text": prepared["selection"]["objective"], "specialist_handoff": {
                    "selection": prepared["selection"], "preview_sha256": prepared["preview_sha256"]}}}

    def submit(prepared, key="specialist-command"):
        with agent_runtime_scope(agent.runtime_context):
            return submit_command(agent, envelope(prepared, key))

    def execute(receipt):
        with bind_submitted_command(agent, receipt):
            return agent.run_conversation("Display text cannot choose a different specialist")

    yield SimpleNamespace(agent=agent, db=db, home=tmp_path, raw=raw, project=project, calls=calls,
        contexts=contexts, handler=handler, methods=methods, preview=preview, envelope=envelope, submit=submit,
        execute=execute, config_path=config_path)
    agent.close()
    for client in clients:
        client.close()
    db.close()


def test_real_named_child_exact_methods_budget_memory_and_duplicate_are_preserved(specialist_runtime):
    rt = specialist_runtime
    with agent_runtime_scope(rt.agent.runtime_context):
        catalog = specialist_catalog(rt.agent, rt.project)
    assert len(catalog["specialists"]) == 1 and catalog["teams_enabled"] is False
    descriptor = catalog["specialists"][0]
    assert descriptor["grants"]["personal_memory_access"] is False
    prepared = rt.preview()
    receipt = rt.submit(prepared)
    result = rt.execute(receipt)
    assert result["completed"] is True, result
    assert result["runtime_status"] == "specialist_review_required"
    assert [name for name, _ in rt.calls] == ["researcher"]
    assert "Summarize only supplied sources" in json.dumps(rt.calls[0][1])
    assert rt.agent._cached_system_prompt == "Unchanged parent prefix"
    child_context = next(context for context in rt.contexts if context.identity.agent_id == "researcher")
    assert child_context.policy.role == "specialist" and child_context.identity.lifecycle == "stable"
    assert child_context.policy.memory_backend == "builtin" and "PRIVATE_TOKEN" not in child_context.policy.secret_refs
    assert not child_context.policy.mcp_grants
    from agent.individual_memory_scope import IndividualMemoryScope
    scope = IndividualMemoryScope.from_context(child_context)
    assert scope.namespace_id == descriptor["builtin_memory_namespace"] and scope.directory.is_dir()
    with agent_runtime_scope(rt.agent.runtime_context):
        state = specialist_status(rt.agent, receipt["command_id"])
    assert state["outcome"] == "completed" and state["completion"]["schema_valid"] is True
    from hermes_state_delegations import DelegationRegistry
    child = DelegationRegistry(rt.agent.runtime_context, rt.db).read(state["completion"]["child_id"])
    assert child["delivery_state"] == "delivered"
    assert child["handoff"]["budget"]["root_id"] == result["runtime_budget"]["root_id"]
    assert child["handoff"]["artifacts"] == [prepared["specialist"]["methods_ref"]]
    assert result["runtime_budget"]["consumed"]["attempts"] == 1
    assert rt.submit(prepared) == receipt
    assert rt.execute(receipt)["final_response"] == result["final_response"]
    assert len(rt.calls) == 1
    history = rt.db.get_messages_as_conversation(rt.agent.session_id)
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert history[0]["content"] == prepared["selection"]["objective"]


@pytest.mark.parametrize("change", ["manifest", "expiry", "project", "digest", "extra"])
def test_changed_selection_never_constructs_or_dispatches_child(specialist_runtime, change):
    from agent.delegation_contract import digest
    rt = specialist_runtime
    prepared = rt.preview()
    if change == "manifest":
        rt.raw["delegation"]["specialists"]["researcher"]["responsibility"] = "Changed responsibility"
        rt.config_path.write_text(json.dumps(rt.raw))
    elif change == "expiry":
        prepared["selection"]["expires_at"] = 1.0
        prepared["preview_sha256"] = digest(prepared["selection"])
    elif change == "project":
        prepared["selection"]["project_id"] = "foreign-project"
        prepared["preview_sha256"] = digest(prepared["selection"])
    elif change == "digest":
        prepared["selection"]["manifest_sha256"] = "a" * 64
        prepared["preview_sha256"] = digest(prepared["selection"])
    else:
        prepared["selection"]["credential"] = "forged"
        prepared["preview_sha256"] = digest(prepared["selection"])
    with pytest.raises((ValueError, PermissionError)):
        receipt = rt.submit(prepared)
        rt.execute(receipt)
    assert not rt.calls
    assert all(context.identity.agent_id == "primary" for context in rt.contexts)


def test_claimed_unknown_never_reexecutes_and_status_is_read_only(specialist_runtime):
    from agent.runtime_commands import claim_turn_command, read_command_state
    from agent.turn_facade_lease import admit_durable_turn_lease
    rt = specialist_runtime
    prepared = rt.preview()
    receipt = rt.submit(prepared)
    with agent_runtime_scope(rt.agent.runtime_context):
        lease = admit_durable_turn_lease(rt.agent, session_id=rt.agent.session_id, relay_turn_id="lost-parent",
            task_context={"session_id": rt.agent.session_id, "platform": "cli"}, conversation_history=[]).lease
        claim_turn_command(rt.agent, read_command_state(rt.agent, receipt["command_id"]), lease)
        lease.release()
        before = rt.db.read_runtime_snapshot(rt.agent.session_id)["revision"]
        assert specialist_status(rt.agent, receipt["command_id"])["outcome"] == "unknown"
        assert specialist_status(rt.agent, receipt["command_id"])["execution_resumed"] is False
        assert rt.db.read_runtime_snapshot(rt.agent.session_id)["revision"] == before
    assert rt.execute(receipt)["runtime_status"] == "outcome_uncertain"
    assert not rt.calls


def test_output_schema_failure_is_retained_without_acceptance(specialist_runtime):
    rt = specialist_runtime
    rt.handler["value"] = lambda request: httpx.Response(200, json=response())
    prepared = rt.preview()
    result = rt.execute(rt.submit(prepared))
    assert result["failed"] is True and result["completed"] is False
    assert [name for name, _ in rt.calls] == ["researcher", "researcher"]
    with agent_runtime_scope(rt.agent.runtime_context):
        state = specialist_status(rt.agent, "specialist-command")
    assert state["completion"]["state"] == "failed" and state["completion"]["schema_valid"] is False
    assert "bounded answer" in state["completion"]["summary"]


def test_no_parent_model_or_auto_acceptance_for_a_verifiable_mission(specialist_runtime):
    from agent.mission_runtime import consume_goal_decision
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    from tests.agent.test_mission_verifier import criterion, ref
    rt = specialist_runtime
    with active(rt.agent) as run:
        rt.db.create_mission(run.session_id, artifact_actor(run.context), holder=run.holder,
            generation=run.generation, access=project_access(run.context), contract={
                "outcome": "Review the configured method", "project_id": rt.project, "policy": "direct",
                "risk": "low", "uncertainty": "low",
                "deliverables": [{"deliverable_id": "method", "artifact_ref": ref(rt.methods), "required": True}],
                "acceptance": [criterion("exists", "existence", [rt.methods])]})
        rt.db.finish_runtime_command(run.session_id, run.command_id, holder=run.holder, generation=run.generation,
                                    result={"fixture": "mission creation"})
    prepared = rt.preview()
    result = rt.execute(rt.submit(prepared))
    assert result["completed"] is True and result["mission"]["state"] == "waiting_for_user"
    assert result["mission"]["goal_decision"]["should_continue"] is False
    with agent_runtime_scope(rt.agent.runtime_context):
        decision = consume_goal_decision(rt.agent, run_id=result["mission"]["run_id"])
    assert not decision["should_continue"] and decision["continuation_prompt"] is None
    assert [name for name, _ in rt.calls] == ["researcher"]


def test_revoked_child_acl_blocks_before_construction(specialist_runtime):
    from hermes_cli import projects_db
    rt = specialist_runtime
    prepared = rt.preview()
    with projects_db.connect_closing() as conn:
        conn.execute("DELETE FROM project_grants WHERE project_id=? AND agent_id=?", (rt.project, "researcher"))
        conn.commit()
    with pytest.raises(PermissionError):
        rt.execute(rt.submit(prepared))
    assert not rt.calls and all(context.identity.agent_id == "primary" for context in rt.contexts)


def test_method_head_cannot_silently_replace_selected_exact_version(specialist_runtime):
    rt = specialist_runtime
    prepared = rt.preview()
    with active(rt.agent) as run:
        publish_fixture(run, rt.project, "revise-method", "# Method\nChanged after preview.\n",
                        artifact_id=rt.methods["artifact_id"], parent_version=1)
        rt.db.finish_runtime_command(run.session_id, run.command_id, holder=run.holder, generation=run.generation,
                                    result={"fixture": "method revision"})
    result = rt.execute(rt.submit(prepared))
    assert result["completed"]
    assert "Summarize only supplied sources" in json.dumps(rt.calls[0][1])
    assert "Changed after preview" not in json.dumps(rt.calls[0][1])


def test_missing_exact_method_bytes_block_before_construction(specialist_runtime):
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    rt = specialist_runtime
    prepared = rt.preview()
    with agent_runtime_scope(rt.agent.runtime_context):
        row = rt.db.read_artifact_version(rt.methods["artifact_id"], 1, artifact_actor(rt.agent.runtime_context),
                                         access=project_access(rt.agent.runtime_context))
    (rt.home / row["descriptor"]["locator"]).unlink()
    with pytest.raises(OSError):
        rt.execute(rt.submit(prepared))
    assert not rt.calls and all(context.identity.agent_id == "primary" for context in rt.contexts)


def test_specialist_memory_survives_distinct_real_child_sessions(specialist_runtime):
    rt = specialist_runtime
    memory_text = "Fixture specialist remembers exact source verification."
    def remember(request):
        result = response()
        if len(rt.calls) == 1:
            result["choices"][0] = {"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "memory-fixture", "type": "function",
                    "function": {"name": "memory", "arguments": json.dumps({
                        "action": "add", "target": "memory", "content": memory_text})}}]}}
        else:
            result["choices"][0]["message"]["content"] = '{"answer":"memory retained"}'
        return httpx.Response(200, json=result)
    rt.handler["value"] = remember
    first = rt.execute(rt.submit(rt.preview(), "first-memory"))
    second = rt.execute(rt.submit(rt.preview(), "second-memory"))
    assert first["completed"] and second["completed"]
    assert len(rt.calls) == 3 and memory_text in json.dumps(rt.calls[-1][1])
    children = list({context.identity.session_id: context for context in rt.contexts
                     if context.identity.agent_id == "researcher"}.values())
    assert len(children) == 2
    from agent.individual_memory_scope import IndividualMemoryScope
    assert IndividualMemoryScope.from_context(children[0]) == IndividualMemoryScope.from_context(children[1])
    assert first["runtime_budget"]["root_id"] == second["runtime_budget"]["root_id"]
    assert rt.agent._cached_system_prompt == "Unchanged parent prefix"


def test_preexisting_submit_row_is_adopted_without_duplicate_user_message(specialist_runtime):
    rt = specialist_runtime
    prepared = rt.preview()
    objective = prepared["selection"]["objective"]
    rt.db.append_message(rt.agent.session_id, "user", content=objective)
    rt.agent._pending_cli_user_message = rt.db.get_messages_as_conversation(rt.agent.session_id, include_row_ids=True)[-1]
    result = rt.execute(rt.submit(prepared))
    assert result["completed"] is True
    assert [row["role"] for row in rt.db.get_messages_as_conversation(rt.agent.session_id)] == ["user", "assistant"]


def test_running_parent_cancel_reaches_child_without_relaunch(specialist_runtime):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    rt = specialist_runtime
    entered, release = threading.Event(), threading.Event()
    def blocked(request):
        entered.set()
        assert release.wait(10)
        result = response()
        result["choices"][0]["message"]["content"] = '{"answer":"retained cancellation evidence"}'
        return httpx.Response(200, json=result)
    rt.handler["value"] = blocked
    prepared = rt.preview()
    receipt = rt.submit(prepared)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(rt.execute, receipt)
        try:
            assert entered.wait(10)
            with agent_runtime_scope(rt.agent.runtime_context):
                cancel = submit_command(rt.agent, {"schema_version": 1, "command_id": "stop-specialist",
                    "idempotency_key": "stop-specialist", "expected_revision": None, "operation": "cancel",
                    "payload": {"reason": "Stop the selected specialist"}})
            assert cancel["status"] == "accepted"
        finally:
            release.set()
        result = pending.result(timeout=20)
    assert result["interrupted"] is True and result["completed"] is False
    with agent_runtime_scope(rt.agent.runtime_context):
        state = specialist_status(rt.agent, receipt["command_id"])
    assert state["outcome"] == "cancelled"
    rt.execute(receipt)
    assert [name for name, _ in rt.calls] == ["researcher"]


def test_completion_projection_preserves_partial_and_cancelled_truth():
    from agent.specialist_control import _completion
    selected = {"specialist_id": "researcher", "manifest_sha256": "a" * 64, "project_id": "project"}
    child = {"handoff": {"child_id": "child"}, "handoff_sha256": "b" * 64, "state": "completed",
             "completion": {"summary": '{"answer":"partial"}', "schema_valid": True, "truncated": True,
                            "exit_reason": "max_iterations"}}
    partial = _completion(selected, child)
    assert partial["state"] == "failed" and "Partial result" in partial["summary"] and partial["schema_valid"]
    child.update(state="failed", completion={"summary": "retained evidence", "schema_valid": False,
                                             "exit_reason": "interrupted"})
    cancelled = _completion(selected, child)
    assert cancelled["state"] == "cancelled" and cancelled["summary"] == "retained evidence"
