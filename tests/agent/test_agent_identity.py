"""Identity configuration is immutable, exact, fail-closed and resume-stable."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from agent.agent_identity import (
    AgentPolicy,
    IdentityBinding,
    IdentityPolicyError,
    parse_agent_identity_config,
    resolve_agent_context,
)


def config_for(active="ryoko"):
    return {
        "agent_identity": {
            "schema_version": 1,
            "principal_id": "owner",
            "profile_id": "profile_a",
            "primary_agent_id": "ryoko",
            "active_agent_id": active,
            "personal_mcp_servers": ["personal-memory"],
            "personal_secret_refs": ["PERSONAL_TOKEN"],
            "agents": {
                "ryoko": {
                    "policy_version": 1,
                    "role": "primary",
                    "memory_backend": "personal_mcp",
                    "allowed_tools": ["read_file", "memory", "web_search"],
                    "mcp_grants": {
                        "personal-memory": ["recall"],
                        "research": ["search", "read"],
                    },
                    "secret_refs": ["PERSONAL_TOKEN", "RESEARCH_TOKEN"],
                    "project_grants": [],
                    "egress_purposes": [],
                },
                "specialist": {
                    "policy_version": 1,
                    "role": "specialist",
                    "memory_backend": "builtin",
                    "allowed_tools": ["read_file"],
                    "mcp_grants": {"research": ["read"]},
                    "secret_refs": ["RESEARCH_TOKEN"],
                },
            },
            "child_policy": {
                "policy_version": 1,
                "role": "child",
                "memory_backend": "builtin",
                "allowed_tools": ["read_file", "terminal"],
                "mcp_grants": {"research": ["read", "write"], "other": ["do"]},
                "secret_refs": ["RESEARCH_TOKEN", "CHILD_TOKEN"],
                "project_grants": [],
                "egress_purposes": [],
            },
        }
    }


def resolve(config, tmp_path, **kwargs):
    return resolve_agent_context(
        config, session_id="session-a", profile_home=tmp_path, **kwargs
    )


def test_legacy_is_only_missing_or_empty_mapping(tmp_path):
    for config in ({}, {"model": "not-an-identity"}, {"agent_identity": {}}):
        assert parse_agent_identity_config(config) is None
        assert resolve(config, tmp_path) is None
    for value in (None, False, 0, "", [], "ryoko"):
        with pytest.raises(IdentityPolicyError):
            resolve({"agent_identity": value}, tmp_path)


def test_immutable_snapshot_and_inspection_never_expose_secrets(tmp_path):
    raw = config_for()
    raw["provider"] = {"api_key": "real-secret-value"}
    parsed = parse_agent_identity_config(raw)
    ctx = resolve(raw, tmp_path)
    digest = ctx.config_digest
    raw["agent_identity"]["agents"]["ryoko"]["allowed_tools"].append("terminal")
    raw["agent_identity"]["agents"]["ryoko"]["mcp_grants"]["research"].append("write")
    assert not ctx.policy.allows_tool("terminal")
    assert not ctx.policy.allows_mcp("research", "write")
    assert ctx.config_digest == digest
    with pytest.raises(FrozenInstanceError):
        ctx.policy.role = "specialist"
    with pytest.raises(TypeError):
        ctx.policy.mcp_grants["research"] = frozenset({"write"})
    with pytest.raises(TypeError):
        parsed.agents["forged"] = parsed.agents["ryoko"]
    assert ctx.policy.secret_refs == frozenset({"PERSONAL_TOKEN", "RESEARCH_TOKEN"})
    assert ctx.policy.all_secret_refs == frozenset({
        "PERSONAL_TOKEN",
        "RESEARCH_TOKEN",
        "CHILD_TOKEN",
    })
    inspection = json.dumps(ctx.redacted_inspection())
    assert "real-secret-value" not in inspection
    assert "PERSONAL_TOKEN" not in inspection
    assert str(tmp_path) not in inspection
    assert "PERSONAL_TOKEN" not in repr(ctx)
    assert ctx.redacted_inspection()["policy"]["secret_refs"] == {"redacted_count": 2}
    assert (
        ctx.redacted_inspection()["provenance"]["source"]
        == "effective_config.agent_identity"
    )


@pytest.mark.parametrize(
    "location,value",
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("principal_id",), "../owner"),
        (("profile_id",), "NUL"),
        (("active_agent_id",), "fake"),
        (("primary_agent_id",), "fake"),
        (("agents", "ryoko", "policy_version"), "1"),
        (("agents", "ryoko", "memory_backend"), "builtin"),
        (("agents", "specialist", "memory_backend"), "personal_mcp"),
        (("agents", "specialist", "role"), "primary"),
        (("agents", "specialist", "role"), "child"),
        (("agents", "specialist", "allowed_tools"), "read_file"),
        (("agents", "specialist", "allowed_tools"), ["*"]),
        (("agents", "specialist", "allowed_tools"), ["read_file", "read_file"]),
        (("agents", "specialist", "mcp_grants"), {"research": ["*"]}),
        (("agents", "specialist", "secret_refs"), ["TOKEN?"]),
        (("agents", "specialist", "project_grants"), {"project": True}),
        (("agents", "specialist", "egress_purposes"), [1]),
        (("personal_mcp_servers",), ["*"]),
        (("agents", "specialist", "project_grants"), ["*"]),
        (("agents", "specialist", "egress_purposes"), ["research"]),
        (("child_policy", "role"), "specialist"),
    ],
)
def test_invalid_fields_and_types_fail_closed(tmp_path, location, value):
    raw = config_for()
    node = raw["agent_identity"]
    for key in location[:-1]:
        node = node[key]
    node[location[-1]] = value
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path)


@pytest.mark.parametrize("location", [(), ("agents", "ryoko"), ("child_policy",)])
def test_unknown_fields_cannot_create_authority(tmp_path, location):
    raw = config_for()
    node = raw["agent_identity"]
    for key in location:
        node = node[key]
    node["secret_values"] = {"token": "never-log-this"}
    with pytest.raises(IdentityPolicyError) as exc:
        resolve(raw, tmp_path)
    assert "never-log-this" not in str(exc.value)


def test_primary_is_unique_and_inventory_is_not_a_grant(tmp_path):
    primary = resolve(config_for(), tmp_path)
    specialist = resolve(config_for("specialist"), tmp_path)
    assert primary.policy.allows_mcp("personal-memory", "recall")
    assert primary.policy.allows_secret("PERSONAL_TOKEN")
    assert not specialist.policy.allows_mcp("personal-memory")
    assert not specialist.policy.allows_secret("PERSONAL_TOKEN")
    assert specialist.policy.personal_secret_refs == frozenset({"PERSONAL_TOKEN"})
    assert specialist.policy.allows_mcp("research", "read")
    assert not specialist.policy.allows_mcp("research", "search")
    for role in ("specialist", "child_policy"):
        for field, value in (
            ("mcp_grants", {"personal-memory": []}),
            ("secret_refs", ["PERSONAL_TOKEN"]),
        ):
            raw = config_for()
            policy = (
                raw["agent_identity"]["child_policy"]
                if role == "child_policy"
                else raw["agent_identity"]["agents"][role]
            )
            policy[field] = value
            with pytest.raises(IdentityPolicyError):
                resolve(raw, tmp_path)


def test_child_namespace_and_all_grants_are_parent_intersections(tmp_path):
    raw = config_for()
    parent = resolve(raw, tmp_path)
    child = resolve_agent_context(
        raw,
        session_id="child-session",
        profile_home=tmp_path,
        parent_context=parent,
        is_child=True,
    )
    sibling = resolve_agent_context(
        raw,
        session_id="another-session",
        profile_home=tmp_path,
        parent_context=parent,
        is_child=True,
    )
    assert child.identity.agent_id.startswith("child_")
    assert child.identity.agent_id != sibling.identity.agent_id
    assert child.identity.parent_agent_id == parent.identity.agent_id
    assert child.identity.lifecycle == "ephemeral"
    assert child.policy.role == "child" and child.policy.memory_backend == "builtin"
    assert child.policy.allowed_tools == frozenset({"read_file"})
    assert dict(child.policy.mcp_grants) == {"research": frozenset({"read"})}
    assert child.policy.secret_refs == frozenset({"RESEARCH_TOKEN"})
    assert not child.policy.project_grants
    assert not child.policy.egress_purposes
    resumed = resolve_agent_context(
        raw,
        session_id="child-session",
        profile_home=tmp_path,
        parent_context=parent,
        is_child=True,
        stored_binding=child.identity.to_record(),
    )
    assert resumed == child
    grandchild = resolve_agent_context(
        raw,
        session_id="grandchild-session",
        profile_home=tmp_path,
        parent_context=child,
        is_child=True,
    )
    assert grandchild.policy == child.policy
    assert grandchild.identity.parent_agent_id == child.identity.agent_id
    with pytest.raises(IdentityPolicyError):
        resolve_agent_context(
            raw,
            session_id="child-session",
            profile_home=tmp_path / "other",
            parent_context=parent,
            is_child=True,
        )
    raw["agent_identity"]["agents"][child.identity.agent_id] = raw["agent_identity"][
        "agents"
    ].pop("specialist")
    with pytest.raises(IdentityPolicyError, match="reserved"):
        resolve(raw, tmp_path)


def test_children_require_explicit_policy_and_parent(tmp_path):
    raw = config_for()
    parent = resolve(raw, tmp_path)
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path, is_child=True)
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path, parent_context=parent)
    del raw["agent_identity"]["child_policy"]
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path, parent_context=parent, is_child=True)


def test_stored_binding_roundtrip_and_every_authority_change_rejects_resume(tmp_path):
    raw = config_for()
    ctx = resolve(raw, tmp_path)
    record = json.loads(json.dumps(ctx.identity.to_record()))
    assert IdentityBinding.from_record(record) == ctx.identity
    assert resolve(raw, tmp_path, stored_binding=record) == ctx
    for key, value in (
        ("agent_id", "specialist"),
        ("session_id", "other-session"),
        ("principal_id", "other-owner"),
        ("binding_revision", 2),
        ("policy_digest", "0" * 64),
        ("profile_home_digest", "0" * 64),
    ):
        modified = dict(record, **{key: value})
        with pytest.raises(IdentityPolicyError):
            resolve(raw, tmp_path, stored_binding=modified)
    for stale in ({}, {**record, "unexpected": "field"}):
        with pytest.raises(IdentityPolicyError):
            resolve(raw, tmp_path, stored_binding=stale)
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path / "other", stored_binding=record)
    for changed in (config_for("specialist"), deepcopy(raw)):
        changed["agent_identity"]["agents"]["ryoko"]["allowed_tools"].append("terminal")
        with pytest.raises(IdentityPolicyError):
            resolve(changed, tmp_path, stored_binding=record)
    with pytest.raises(IdentityPolicyError):
        resolve({}, tmp_path, stored_binding=record)


def test_canonical_digest_ignores_order_but_tracks_authority(tmp_path):
    raw = config_for()
    reordered = deepcopy(raw)
    reordered["agent_identity"]["agents"]["ryoko"]["allowed_tools"].reverse()
    reordered["agent_identity"]["agents"] = dict(
        reversed(list(reordered["agent_identity"]["agents"].items()))
    )
    assert resolve(raw, tmp_path) == resolve(reordered, tmp_path)
    raw["agent_identity"]["personal_secret_refs"].append("NEW_PERSONAL_TOKEN")
    assert (
        resolve(raw, tmp_path).config_digest
        != resolve(reordered, tmp_path).config_digest
    )


def test_empty_mcp_grant_is_not_server_admission():
    policy = AgentPolicy(
        policy_version=1,
        role="specialist",
        memory_backend="builtin",
        mcp_grants={"server": []},
    )
    assert not policy.allows_mcp("server")
    assert not policy.allows_mcp("server", "anything")
    assert not policy.allows_tool("read_file")
    assert not policy.allows_secret("TOKEN")


def test_generated_reference_is_fresh_and_check_detects_stale_output(tmp_path):
    from scripts.gen_agent_identity_reference import (
        DEFAULT_OUTPUT,
        main,
        render_reference,
    )

    assert DEFAULT_OUTPUT.read_text(encoding="utf-8") == render_reference()
    output = tmp_path / "reference.md"
    assert main(["--output", str(output)]) == 0
    assert main(["--check", "--output", str(output)]) == 0
    output.write_text("stale", encoding="utf-8")
    assert main(["--check", "--output", str(output)]) == 1


@pytest.mark.parametrize(
    "bad_id",
    [
        "../owner",
        ".",
        "owner.name",
        "owner/child",
        "owner\\child",
        "owner:child",
        " owner",
        "owner ",
        "owner*",
        "NUL",
        "com1",
        "LPT9",
        "équipe",
        "a" * 65,
    ],
)
def test_identifiers_reject_path_and_platform_aliases(tmp_path, bad_id):
    raw = config_for()
    raw["agent_identity"]["principal_id"] = bad_id
    with pytest.raises(IdentityPolicyError):
        resolve(raw, tmp_path)


def test_duplicate_primary_role_and_configured_inventory_override_are_rejected(
    tmp_path,
):
    raw = config_for()
    specialist = raw["agent_identity"]["agents"]["specialist"]
    specialist.update(role="primary", memory_backend="personal_mcp")
    with pytest.raises(IdentityPolicyError, match="exactly primary_agent_id"):
        resolve(raw, tmp_path)
    raw = config_for()
    raw["agent_identity"]["agents"]["specialist"]["all_secret_refs"] = []
    with pytest.raises(IdentityPolicyError, match="unknown fields"):
        resolve(raw, tmp_path)
    del raw["agent_identity"]["agents"]["specialist"]["all_secret_refs"]
    del raw["agent_identity"]["agents"]["specialist"]["policy_version"]
    with pytest.raises(IdentityPolicyError, match="missing required"):
        resolve(raw, tmp_path)
