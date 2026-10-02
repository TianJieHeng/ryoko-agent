"""Declared format support never certifies an opaque execution owner or model."""
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agent.provider_capabilities import capabilities_for_api_mode, provider_capabilities_for
from agent.runtime_commands import supports_runtime_execution
from agent.transports import get_transport


@pytest.mark.parametrize("mode", ["chat_completions", "anthropic_messages", "bedrock_converse", "codex_responses"])
def test_transport_contract_separates_format_and_model_support(mode):
    contract = get_transport(mode).capabilities
    assert contract.api_mode == mode
    assert contract.declaration_scope == "adapter"
    assert contract.model_capabilities == "unverified"
    assert contract.execution_owner == "hermes"
    assert contract.cancellation == "local_only"
    assert contract.opaque_state_version == 1
    assert not contract.durable_execution
    assert contract.bounded_budget == "unsupported"


def test_runtime_gate_resolves_actual_sdk_and_external_loop_ownership():
    client = OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("no probes"))))
    try:
        agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client)
        contract = provider_capabilities_for(agent)
        assert supports_runtime_execution(agent)
        assert contract.bounded_budget == "conditional_openai_text"
        record = contract.to_record()
        assert all(key not in record for key in ("endpoint", "provider", "api_key", "model", "opaque_state"))
        agent.provider = "copilot-acp"
        assert provider_capabilities_for(agent).execution_owner == "provider"
        assert not supports_runtime_execution(agent)
        agent.provider = "openai"
        agent.api_mode = "codex_app_server"
        assert not supports_runtime_execution(agent)
        agent.api_mode = "chat_completions"
        class UnknownClient:
            def __getattr__(self, name):
                return True
        agent.client = UnknownClient()
        assert not supports_runtime_execution(agent)
        assert provider_capabilities_for(agent).bounded_budget == "unsupported"
        agent.client = None
        assert not supports_runtime_execution(agent)
    finally:
        client.close()


def test_unknown_protocol_does_not_inherit_openai_contract():
    unknown = capabilities_for_api_mode("plugin_protocol")
    assert unknown.execution_owner == "unknown"
    assert unknown.streaming == "unknown"
    assert unknown.opaque_state_version is None
    assert not unknown.durable_execution
