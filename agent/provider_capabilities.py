"""Adapter contracts and private protocol replay in the existing transcript sidecar.

These declarations describe implemented adapters, never every model or endpoint.
Budget admission still verifies the concrete request and configured route bounds.
The nullable provider_sidecar column owns otherwise unrepresented replay state;
legacy reasoning columns remain authoritative and signed values are not rewritten.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import sys
from typing import Any, Mapping


@dataclass(frozen=True)
class ProviderCapabilities:
    api_mode: str
    adapter: str
    streaming: str = "unknown"
    parallel_tools: str = "unknown"
    media_inputs: tuple[str, ...] = ()
    usage: str = "unknown"
    cancellation: str = "unknown"
    cache_semantics: str = "unknown"
    opaque_state_version: int | None = None
    execution_owner: str = "unknown"
    durable_execution: bool = False
    bounded_budget: str = "unsupported"
    schema_version: int = 1
    declaration_scope: str = "adapter"
    model_capabilities: str = "unverified"

    def to_record(self) -> dict[str, Any]:
        """Public-safe declaration: no endpoint, account, model or opaque contents."""
        result = asdict(self)
        result["media_inputs"] = list(self.media_inputs)
        return result


_ADAPTERS = {
    "chat_completions": ProviderCapabilities(
        api_mode="chat_completions", adapter="openai_chat", streaming="supported",
        parallel_tools="supported", media_inputs=("image", "audio"), usage="final_response",
        cancellation="local_only", cache_semantics="stable_prefix_route_dependent",
        opaque_state_version=1, execution_owner="hermes"),
    "anthropic_messages": ProviderCapabilities(
        api_mode="anthropic_messages", adapter="anthropic_messages", streaming="supported",
        parallel_tools="supported", media_inputs=("image",), usage="final_response",
        cancellation="local_only", cache_semantics="explicit_breakpoints_route_dependent",
        opaque_state_version=1, execution_owner="hermes"),
    "bedrock_converse": ProviderCapabilities(
        api_mode="bedrock_converse", adapter="bedrock_converse", streaming="supported",
        parallel_tools="supported", media_inputs=("image",), usage="final_response",
        cancellation="local_only", cache_semantics="route_dependent",
        opaque_state_version=1, execution_owner="hermes"),
    "codex_responses": ProviderCapabilities(
        api_mode="codex_responses", adapter="openai_responses", streaming="supported",
        parallel_tools="supported", media_inputs=("image",), usage="final_response",
        cancellation="local_only", cache_semantics="stable_prefix_and_opaque_continuation",
        opaque_state_version=1, execution_owner="hermes"),
    "codex_app_server": ProviderCapabilities(
        api_mode="codex_app_server", adapter="codex_app_server", streaming="supported",
        parallel_tools="unknown", media_inputs=("image",), usage="provider_reported",
        cancellation="local_only", cache_semantics="provider_owned_session",
        execution_owner="provider"),
}


def capabilities_for_api_mode(api_mode: str) -> ProviderCapabilities:
    """Format support alone does not authorize executing a concrete client."""
    return _ADAPTERS.get(api_mode, ProviderCapabilities(api_mode=api_mode, adapter="unknown"))


def provider_capabilities_for(agent: Any) -> ProviderCapabilities:
    """Recognize implementation ownership without trusting client duck typing.

    No client is constructed and no credential or endpoint probe is performed.
    Bedrock Converse dispatch is owned directly by its in-tree adapter (no client
    slot on AIAgent). Unknown plugin clients stay explicitly unsupported.
    """
    from providers import get_provider_profile

    mode = getattr(agent, "api_mode", "chat_completions")
    contract = capabilities_for_api_mode(mode)
    provider = getattr(agent, "provider", "")
    profile = get_provider_profile(provider) if isinstance(provider, str) and provider else None
    if provider == "moa" or getattr(profile, "auth_type", None) == "external_process":
        return ProviderCapabilities(
            api_mode=mode, adapter="external_agent", streaming="supported",
            usage="provider_reported", cancellation="local_only",
            cache_semantics="provider_owned_session", execution_owner="provider")
    if mode == "codex_app_server":
        return contract
    if mode == "bedrock_converse" and provider == "bedrock":
        return replace(contract, durable_execution=True)
    if mode == "anthropic_messages":
        sdk = sys.modules.get("anthropic")
        known = tuple(getattr(sdk, name, None) for name in ("Anthropic", "AnthropicBedrock", "AnthropicVertex"))
        if type(getattr(agent, "_anthropic_client", None)) in known:
            return replace(contract, durable_execution=True)
        return replace(contract, execution_owner="unknown")
    if mode not in {"chat_completions", "codex_responses"}:
        return contract
    client = getattr(agent, "client", None)
    import openai
    if type(client) in (openai.OpenAI, openai.AzureOpenAI):
        bounded = "conditional_openai_text" if mode == "chat_completions" else "unsupported"
        return replace(contract, durable_execution=True, bounded_budget=bounded)
    from agent.gemini_native_adapter import GeminiNativeClient
    if mode == "chat_completions" and type(client) is GeminiNativeClient:
        return replace(contract, adapter="gemini_native", media_inputs=("image",),
                       cache_semantics="route_dependent", durable_execution=True)
    return replace(contract, adapter="unknown", execution_owner="unknown")


class UnsupportedProviderState(ValueError):
    """Opaque protocol state cannot be replayed without a compatible reader."""


_PROTOCOL_VERSION = 1
_ORDERED_FIELDS = ("anthropic_content_blocks", "bedrock_content_blocks")
_REPLAY_FIELDS = (*_ORDERED_FIELDS, "reasoning_details", "codex_reasoning_items", "codex_message_items")


def decode_protocol_sidecar(value: Any) -> dict | None:
    """An absent carrier is legacy version 0; unknown versions stop replay."""
    if value is None:
        return None
    try:
        envelope = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise UnsupportedProviderState("invalid private provider state") from exc
    if not isinstance(envelope, dict) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != _PROTOCOL_VERSION:
        raise UnsupportedProviderState("unsupported private provider state version")
    if set(envelope) != {"schema_version", "fields"} or not isinstance(envelope["fields"], dict):
        raise UnsupportedProviderState("invalid private provider state fields")
    fields = envelope["fields"]
    if set(fields) - set(_ORDERED_FIELDS) or any(not isinstance(value, list) for value in fields.values()):
        raise UnsupportedProviderState("unknown private provider block state")
    return envelope


def encode_protocol_sidecar(message: Mapping[str, Any]) -> str | None:
    """Version native state without copying existing signed/Codex columns.

    A restored or imported sidecar is validated before storage; current ordered
    blocks supersede its older fields after legitimate live transcript repairs.
    """
    existing = decode_protocol_sidecar(message.get("provider_sidecar"))
    fields = dict(existing["fields"]) if existing else {}
    fields.update({key: message[key] for key in _ORDERED_FIELDS if message.get(key) is not None})
    if not existing and not fields and not any(message.get(key) for key in _REPLAY_FIELDS):
        return None
    envelope = {"schema_version": _PROTOCOL_VERSION, "fields": fields}
    decode_protocol_sidecar(envelope)
    return json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def restore_protocol_sidecar(message: dict[str, Any]) -> None:
    """Expand on canonical replay/repair copies; never send storage metadata."""
    envelope = decode_protocol_sidecar(message.pop("provider_sidecar", None))
    if envelope is not None:
        message.update(envelope["fields"])
