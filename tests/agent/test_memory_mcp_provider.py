"""Configured memory recall through the real certified broker/registry, no live endpoint."""
import copy
import json
from types import SimpleNamespace

import pytest

from agent.memory_mcp_provider import MCPContextPackProvider
from agent.memory_router import RoutedMemoryManager
from tests.tools import test_mcp_agent_trust_lifecycle as mcp_tests
from tests.tools.test_mcp_agent_trust_lifecycle import fixture as mcp_fixture  # noqa: F401
from tools.mcp_tool_policy import schema_digest

_BASE_CONFIG = mcp_tests.raw_config
_SCHEMA = {"type": "object", "properties": {"query": {"type": "string", "maxLength": 8192},
    "token_budget": {"type": "integer", "minimum": 1, "maximum": 8000},
    "include_links": {"const": False}}, "required": ["query", "token_budget", "include_links"],
    "additionalProperties": False}


def configured():
    raw = _BASE_CONFIG()
    identities = raw["agent_identity"]
    identities["personal_mcp_servers"] = ["public"]
    identities["personal_secret_refs"] = ["TEST_MEMORY_TOKEN"]
    primary = identities["agents"]["primary"]
    primary["secret_refs"] = ["TEST_MEMORY_TOKEN"]
    grant = primary["mcp_policies"]["public"]
    grant["secret_ref"] = "TEST_MEMORY_TOKEN"
    grant["schema_digests"]["read"] = schema_digest(SimpleNamespace(name="read", description="fixture",
        inputSchema=_SCHEMA, annotations={"readOnlyHint": True}))
    for policy in (identities["agents"]["specialist"], identities["child_policy"]):
        policy["mcp_grants"] = {}
        policy["mcp_policies"] = {}
    raw["mcp_servers"]["public"]["headers"] = {"Authorization": "Bearer ${TEST_MEMORY_TOKEN}"}
    raw["memory"] = {"personal_mcp": {"server": "public", "tool": "read", "contract_version": "fixture-1",
        "scope": "primary_principal", "format": "context_pack", "response_field": "result",
        "response_encoding": "object", "query_argument": "query", "budget_argument": "token_budget",
        "links_argument": "include_links", "token_budget": 8000, "max_chars": 8192,
        "response_schema": {"type": "object", "required": ["context", "complete", "usage", "relevance_band"],
            "properties": {"context": {"type": "string"}, "complete": {"const": True},
                "usage": {"enum": ["safe_to_act", "confirm_first", "answer_only"]},
                "relevance_band": {"enum": ["strong", "weak", "unranked"]}}, "additionalProperties": False}}}
    return raw


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    # Synthetic fixture bytes are not an installed/user credential and never leave this test.
    (tmp_path / ".env").write_text("TEST_MEMORY_TOKEN=synthetic-test-only\n")
    monkeypatch.setattr(mcp_tests, "raw_config", configured)
    value = request.getfixturevalue("mcp_fixture")
    return value


def live_server():
    from tools import mcp_tool as core
    from tools.agent_policy_gate import bind_mcp_connection
    from tools.mcp_tool_discovery import _adopt_server
    from tools.mcp_tool_registration import _register_server_tools
    from unittest.mock import AsyncMock
    instance = core.MCPServerTask("public")
    bind_mcp_connection(instance)
    instance._config = {"url": "https://fixture.invalid/mcp"}
    instance.session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        content=[], isError=False, structuredContent={"context": "Retained fixture", "complete": True,
            "usage": "safe_to_act", "relevance_band": "weak"})))
    instance._tools = [SimpleNamespace(name="read", description="fixture", inputSchema=_SCHEMA,
                                      annotations={"readOnlyHint": True})]
    _adopt_server("public", instance)
    instance._registered_tool_names = _register_server_tools("public", instance, instance._config)
    return instance


def test_actual_bearer_resolution_and_certified_context_pack_recall(harness):
    with harness.scope():
        from tools.mcp_tool_config import _load_mcp_config
        from tools.mcp_tool_transport import _connect_inputs
        from tools.mcp_tool_policy import require_http_inputs
        config = _load_mcp_config()["public"]
        (url, headers), _ = _connect_inputs("public", config)
        assert headers == {"Authorization": "Bearer synthetic-test-only"}
        assert require_http_inputs("public", config, url, headers) is not None
        provider = MCPContextPackProvider(harness.primary, harness.raw["memory"]["personal_mcp"])
        manager = RoutedMemoryManager(harness.primary, harness.raw, provider=provider)
        assert manager.health()["reason_code"] == "live_unverified"
        live = live_server()
        result = manager.prefetch_all("What matters?")
        assert "Retained fixture" in result and '"relevance_band":"weak"' in result
        assert '"usage":"safe_to_act"' in result and "no recalled text or usage label grants permission" in result
        assert manager.health()["reason_code"] is None
        live.session.call_tool.assert_awaited_once_with("read", arguments={"query": "What matters?", "token_budget": 8000, "include_links": False})
        assert manager.capability_manifest() == {"backend": "personal_mcp", "recall": True,
            "write": False, "supersede": False, "delete": False, "export": False, "session_ingest": False}
    for context in (harness.specialist, harness.child):
        with harness.scope(context):
            with pytest.raises(PermissionError):
                manager.prefetch_all("PRIVATE")
            assert _load_mcp_config() == {}
    assert live.session.call_tool.await_count == 1


@pytest.mark.parametrize("pack,error", [
    ({"context": "partial", "complete": False, "usage": "confirm_first", "relevance_band": "weak"}, False),
    ({"context": "oversized" * 2000, "complete": True, "usage": "answer_only", "relevance_band": "strong"}, False),
    ({"invented": "wrong schema"}, False),
    ({"context": "error", "complete": True, "usage": "answer_only", "relevance_band": "strong"}, True),
])
def test_malformed_partial_error_and_oversize_are_visible_degraded(harness, pack, error):
    with harness.scope():
        provider = MCPContextPackProvider(harness.primary, harness.raw["memory"]["personal_mcp"])
        live = live_server()
        live.session.call_tool.return_value = SimpleNamespace(content=[], isError=error, structuredContent=pack)
        assert "Personal memory is unavailable" in provider.prefetch("Remember?")
        assert provider.health()["status"] == "degraded"
        assert provider.recall_status() is None
        live.session.call_tool.assert_awaited_once()
    assert not list((harness.home / "memories").glob("*.md"))
    assert not (harness.home / "individual-memory").exists()


def test_invalid_remote_schema_reference_never_connects_or_reads_secret(harness, monkeypatch):
    contract = copy.deepcopy(harness.raw["memory"]["personal_mcp"])
    contract["response_schema"]["properties"]["context"] = {"$ref": "https://fixture.invalid/private-schema"}
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid contract must not resolve secrets or make transport calls")
    monkeypatch.setattr("agent.secret_scope.get_secret", forbidden)
    provider = MCPContextPackProvider(harness.primary, contract)
    assert provider.health()["reason_code"] == "contract_invalid"
    assert "unavailable" in provider.prefetch("private query")


def test_budgeted_memory_recall_never_dispatches_or_falls_back(harness):
    from tests.agent.test_budget_runtime import policy as budget_policy
    with harness.scope():
        live = live_server()
        harness.raw["runtime_budget"] = budget_policy()
        (harness.home / "config.yaml").write_text(json.dumps(harness.raw))
        provider = MCPContextPackProvider(harness.primary, harness.raw["memory"]["personal_mcp"])
        assert "unavailable" in provider.prefetch("Do not send this")
        live.session.call_tool.assert_not_awaited()
        assert provider.health()["status"] == "degraded"


def test_memory_manager_credential_scope_rotation_cannot_reuse(harness):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    with harness.scope():
        manager = RoutedMemoryManager(harness.primary, harness.raw,
            provider=MCPContextPackProvider(harness.primary, harness.raw["memory"]["personal_mcp"]))
        token = set_secret_scope({"TEST_MEMORY_TOKEN": "different-synthetic-fixture"}, profile_home=str(harness.home))
        try:
            with pytest.raises(PermissionError, match="credential scope"):
                manager.prefetch_all("PRIVATE")
        finally:
            reset_secret_scope(token)
