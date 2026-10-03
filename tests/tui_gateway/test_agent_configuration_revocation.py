"""Managed narrowing revokes live authority monotonically, never cached prompts."""
import json

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.result_artifacts import artifact_actor
from tests.tui_gateway.test_artifact_rpc import artifacts, denied, result  # noqa: F401
from tests.tui_gateway.test_workflow_delivery_rpc import configured, start_specialist
from tools.capability_broker import CapabilityDenied, require_live_policy

pytestmark = pytest.mark.platforms("linux")


def setup_researcher(rt, *, tools=True):
    project = configured(rt)["id"]
    home, primary = rt.homes["a"], rt.agents["a"]
    raw = json.loads((home / "config.yaml").read_text())
    raw["agent_identity"]["agents"]["researcher"]["allowed_tools"] = ["memory", "todo_list"] if tools else []
    raw["agent_identity"]["child_policy"] = {"policy_version": 1, "role": "child", "memory_backend": "builtin",
        "project_grants": [project], "allowed_tools": ["memory", "todo_list"]}
    raw["delegation"] = {"specialists": {"researcher": {
        "responsibility": "Review supplied sources", "methods_ref": {"id": "fixture-methods", "version": 1, "sha256": "0" * 64},
        "limits": {"max_depth": 1, "max_total_children": 1, "max_concurrent_children": 1},
        "output_contract": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}}}}
    (home / "config.yaml").write_text(json.dumps(raw))
    primary.session_id = "revocation-owner"
    primary.runtime_context = resolve_agent_context(raw, session_id=primary.session_id, profile_home=home)
    primary._session_db.create_session(primary.session_id, source="tui")
    primary._session_db.claim_session_agent_identity(primary.session_id, primary.runtime_context.identity.to_record())
    from tui_gateway import server
    server._sessions["live-a"]["session_key"] = primary.session_id
    researcher, values = start_specialist(rt, "research-session")

    def call(method, **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "research-control", "method": method,
            "params": {"schema_version": 1, "session_id": "live-research-session", **params}}, transport=rt.peers["a"])

    return project, researcher, values, call


@pytest.mark.parametrize("change,tools", [
    ({"memory_allowed": False}, True),
    ({"research_allowed": False}, True),
    ({"project_grants": []}, True),
    ({"memory_allowed": False}, False),
    ({"research_allowed": False}, False),
])
def test_narrow_regrant_never_revives_live_reads_or_prepared_approval(artifacts, change, tools):
    from agent.project_context import authorize_project
    from tools.individual_memory_store import IndividualMemoryStore
    from hermes_state import SessionDB
    project, agent, values, call = setup_researcher(artifacts, tools=tools)
    original_context, original_values = agent.runtime_context, dict(values)
    with agent_runtime_scope(original_context):
        memory = IndividualMemoryStore(original_context)
        memory.load_from_disk()
        memory.add("memory", "Private specialist memory")
        assert authorize_project(original_context, project, "read")
        assert require_live_policy(require_run=False) == original_context
    proposal = result(call("runtime.artifact.prepare", command_id="pending-effect", request_id="pending-effect",
                           project_id=project, content="Reviewed before revocation"))
    initial = result(artifacts.call("runtime.agent.get", agent_id="researcher"))["agent"]
    narrowed = result(artifacts.call("runtime.agent.update", agent_id="researcher", expected_revision=initial["revision"],
        config={**initial["config"], **change}))["agent"]
    assert agent.runtime_context == original_context and values == original_values
    with agent_runtime_scope(original_context):
        for operation in (lambda: require_live_policy(require_run=False), lambda: memory.memory_entries,
                          lambda: authorize_project(original_context, project, "read")):
            with pytest.raises(CapabilityDenied) as error:
                operation()
            assert error.value.code == "agent_configuration_revoked"
    restored = result(artifacts.call("runtime.agent.update", agent_id="researcher", expected_revision=narrowed["revision"],
                                    config=initial["config"]))["agent"]
    denied(call("runtime.artifact.publish", command_id="pending-effect", request_id="pending-effect", project_id=project,
        content="Reviewed before revocation", approval_id=proposal["approval_id"], approval_digest=proposal["approval_digest"]),
        "agent_configuration_revoked")
    approval = agent._session_db.get_effect_approval(proposal["approval_id"], artifact_actor(original_context))
    assert approval["status"] == "pending"
    with agent._session_db._runtime_read() as conn:
        floor = conn.execute("SELECT revoked_before_revision FROM agent_configuration_revocations WHERE agent_id='researcher'").fetchone()[0]
        assert floor == narrowed["revision"] < restored["revision"]
    fresh, _ = start_specialist(artifacts, "restored-researcher")
    with agent_runtime_scope(fresh.runtime_context):
        assert require_live_policy(require_run=False) == fresh.runtime_context
        assert authorize_project(fresh.runtime_context, project, "read")
    if not tools:
        assert fresh.runtime_context.policy.digest == original_context.policy.digest
    path = agent._session_db.db_path
    agent._session_db.close()
    artifacts.agents["a"]._session_db = SessionDB(path)
    with agent_runtime_scope(original_context), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)


def test_stable_actual_identity_and_ephemeral_ancestors_cannot_evade_archive(artifacts):
    from agent.agent_configuration import prepare_construction, base_configuration
    project, specialist, _, _ = setup_researcher(artifacts)
    primary, db = artifacts.agents["a"], artifacts.agents["a"]._session_db
    # A named delegated specialist can inherit its primary's snapshot selector;
    # its own actual stable ID must still supply the revocation revision.
    db.create_session("named-delegated", source="tui", parent_session_id=primary.session_id)
    with agent_runtime_scope(primary.runtime_context):
        inherited, _ = prepare_construction(base_configuration(), db, "named-delegated", parent=primary.runtime_context,
                                           is_child=True)
    from agent.agent_identity import parse_agent_identity_config, _child_policy, _digest, IdentityBinding
    from agent.runtime_context import AgentContext
    parsed = parse_agent_identity_config(inherited)
    policy = _child_policy(parsed.agents["ryoko"], parsed.agents["researcher"])
    binding = IdentityBinding(parsed.principal_id, parsed.profile_id, "researcher", "named-delegated",
                              "stable", policy.digest, parsed.digest, _digest(specialist.runtime_context.profile_home))
    named = AgentContext(binding, policy, parsed.digest, specialist.runtime_context.profile_home)
    db.claim_session_agent_identity("named-delegated", binding.to_record())
    with agent_runtime_scope(named):
        assert require_live_policy(require_run=False) == named
    # The ephemeral child's UUID is never itself a managed configuration key.
    db.create_session("ephemeral-child", source="tui", parent_session_id=primary.session_id)
    with agent_runtime_scope(primary.runtime_context):
        config, _ = prepare_construction(base_configuration(), db, "ephemeral-child", parent=primary.runtime_context, is_child=True)
    child = resolve_agent_context(config, session_id="ephemeral-child", profile_home=primary.runtime_context.profile_home,
                                  parent_context=primary.runtime_context, is_child=True)
    db.claim_session_agent_identity("ephemeral-child", child.identity.to_record())
    with agent_runtime_scope(child):
        assert require_live_policy(require_run=False) == child
    target = result(artifacts.call("runtime.agent.get", agent_id="researcher"))["agent"]
    result(artifacts.call("runtime.agent.archive", agent_id="researcher", expected_revision=target["revision"]))
    with agent_runtime_scope(named), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    owner = result(artifacts.call("runtime.agent.get", agent_id="ryoko"))["agent"]
    narrowed = result(artifacts.call("runtime.agent.update", agent_id="ryoko", expected_revision=owner["revision"],
        config={**owner["config"], "research_allowed": False}))["agent"]
    with agent_runtime_scope(child), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    with agent_runtime_scope(primary.runtime_context), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    # Exact owned settings RPCs still repair the primary; they do not restore
    # execution on its old context or lend this exception to another transport.
    repaired = result(artifacts.call("runtime.agent.update", agent_id="ryoko", expected_revision=narrowed["revision"],
                                    config=owner["config"]))["agent"]
    assert repaired["revision"] > narrowed["revision"]
    assert result(artifacts.call("runtime.agent.get", agent_id="ryoko"))["agent"] == repaired
    denied(artifacts.call("runtime.agent.get", agent_id="ryoko", via=artifacts.peers["b"]))
    with agent_runtime_scope(primary.runtime_context), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    fresh_primary, _ = start_specialist(artifacts, "fresh-primary", name="ryoko")
    db.publish_compression_child(parent_session_id=fresh_primary.session_id, child_session_id="primary-tip", source="tui",
        messages=[{"role": "user", "content": "Continue this session"}],
        model_config={"agent_identity": fresh_primary.runtime_context.identity.to_record()}, require_compression_lease=False)
    db.create_session("child-after-compression", source="tool", parent_session_id="primary-tip")
    with agent_runtime_scope(fresh_primary.runtime_context):
        config, _ = prepare_construction(base_configuration(), db, "child-after-compression", parent=fresh_primary.runtime_context, is_child=True)
    compressed_child = resolve_agent_context(config, session_id="child-after-compression", profile_home=fresh_primary.runtime_context.profile_home,
        parent_context=fresh_primary.runtime_context, is_child=True)
    db.claim_session_agent_identity("child-after-compression", compressed_child.identity.to_record())
    # Parent's physical tip has no enrollment row; its proved logical root pins
    # the post-regrant revision, so the old revocation floor must not reject it.
    with agent_runtime_scope(compressed_child):
        assert require_live_policy(require_run=False) == compressed_child
    result(artifacts.call("runtime.agent.update", agent_id="ryoko", expected_revision=repaired["revision"],
        config={**repaired["config"], "memory_allowed": False}))
    with agent_runtime_scope(compressed_child), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    configured(artifacts, "b")
    with agent_runtime_scope(artifacts.agents["b"].runtime_context):
        independent, _ = start_specialist(artifacts, "independent-profile", label="b")
    with agent_runtime_scope(independent.runtime_context):
        assert require_live_policy(require_run=False) == independent.runtime_context


def test_transient_managed_specialist_preview_uses_frozen_source_and_never_regrants_old_authority(artifacts):
    from agent.identity_lifecycle import identity_config
    from agent.specialist_manifest import SpecialistManifest
    from dataclasses import replace
    setup_researcher(artifacts)
    target = result(artifacts.call("runtime.agent.get", agent_id="researcher"))["agent"]
    copied = result(artifacts.call("runtime.agent.create", copy_from_agent_id="researcher", config=target["config"]))["agent"]
    parent, _ = start_specialist(artifacts, "preview-owner", name="ryoko")
    with agent_runtime_scope(parent.runtime_context):
        _, preview = SpecialistManifest.resolve(identity_config(), copied["agent_id"],
            parent=parent.runtime_context, session_id="specialist-preview")
    assert parent._session_db.get_session("specialist-preview") is None
    assert preview.configuration_session_id == parent.runtime_context.identity.session_id
    with agent_runtime_scope(preview):
        assert require_live_policy(require_run=False) == preview
    narrowed = result(artifacts.call("runtime.agent.update", agent_id=copied["agent_id"], expected_revision=copied["revision"],
        config={**copied["config"], "memory_allowed": False}))["agent"]
    with agent_runtime_scope(preview), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    result(artifacts.call("runtime.agent.update", agent_id=copied["agent_id"], expected_revision=narrowed["revision"], config=copied["config"]))
    with agent_runtime_scope(preview), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        require_live_policy(require_run=False)
    fresh_parent, _ = start_specialist(artifacts, "fresh-preview-owner", name="ryoko")
    with agent_runtime_scope(fresh_parent.runtime_context):
        _, current = SpecialistManifest.resolve(identity_config(), copied["agent_id"],
            parent=fresh_parent.runtime_context, session_id="fresh-preview")
    with agent_runtime_scope(current):
        assert require_live_policy(require_run=False) == current
    enrolled, _ = start_specialist(artifacts, "enrolled-copy", name=copied["agent_id"])
    own = replace(enrolled.runtime_context, configuration_session_id=parent.runtime_context.identity.session_id)
    with agent_runtime_scope(own):
        assert require_live_policy(require_run=False) == own
