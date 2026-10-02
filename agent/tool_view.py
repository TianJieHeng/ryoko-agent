"""Immutable session tool exposure derived from the existing registry and BE01 grants.

A view is an inspection snapshot, never an authorization token. Dispatch still checks
live trusted context. Only authorized catalog metadata enters the public record, so
neither denied registrations nor a sibling's unavailable MCP sources are enumerable.
Toolset selection is the session capability gate; process-cached probes are only
service reachability/opt-in checks. No planner filtering or mid-turn schema refresh.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class ToolView:
    catalog_version: str
    session_policy_version: str
    installed_tool_ids: tuple[str, ...]
    authorized_tool_ids: tuple[str, ...]
    discoverable_tool_ids: tuple[str, ...]
    selected_tool_ids: tuple[str, ...]
    unavailable_reasons: Mapping[str, str]
    # Construction/approved-refresh bookkeeping, never part of public inspection.
    _base_catalog_version: str = field(default="", repr=False)
    _injected_tool_ids: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        for field in ("installed_tool_ids", "authorized_tool_ids", "discoverable_tool_ids", "selected_tool_ids"):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        object.__setattr__(self, "unavailable_reasons", MappingProxyType(dict(self.unavailable_reasons)))

    def with_selection(self, definitions) -> "ToolView":
        """Freeze final exposure (including admitted bridges), without changing discovery."""
        return replace(self, selected_tool_ids=tuple(
            td["function"]["name"] for td in definitions
            if td["function"]["name"] in self.authorized_tool_ids))

    def to_record(self) -> dict:
        """Safe inspection of the stored snapshot; does not probe or refresh schemas."""
        return {
            "catalog_version": self.catalog_version,
            "session_policy_version": self.session_policy_version,
            "installed_tool_ids": list(self.installed_tool_ids),
            "authorized_tool_ids": list(self.authorized_tool_ids),
            "discoverable_tool_ids": list(self.discoverable_tool_ids),
            "selected_tool_ids": list(self.selected_tool_ids),
            "unavailable_reasons": dict(self.unavailable_reasons),
        }

    def unavailable_reason(self, name: str) -> str | None:
        # Unknown and denied names are deliberately indistinguishable.
        if name not in self.authorized_tool_ids:
            return "not_authorized_or_unavailable"
        return self.unavailable_reasons.get(name)


def derive_tool_view(*, registry, requested_tool_ids, available_definitions,
                     selected_definitions=None, reachable_tool_ids=None) -> ToolView:
    """Derive one session view from a shared immutable profile catalog snapshot.

    available_definitions must be the uncollapsed result of the existing schema
    resolver. This function never invokes check_fn or reloads configuration.
    """
    from agent.runtime_context import current_agent_context
    from tools.agent_policy_gate import authorize_tool, filter_tool_definitions

    context = current_agent_context()
    catalog = registry.catalog_metadata()
    admitted = tuple(metadata for metadata in catalog
                     if authorize_tool(metadata.name, context=context,
                                       entry=registry.get_entry(metadata.name)) is None)
    from tools.tool_search_catalog import BRIDGE_TOOL_NAMES
    # Bridge handlers are virtual built-ins, deliberately not separate registrations.
    bridges = frozenset(name for name in BRIDGE_TOOL_NAMES
                        if authorize_tool(name, context=context) is None)
    installed = frozenset(metadata.name for metadata in admitted)
    authorized = installed | bridges
    requested = frozenset(requested_tool_ids)
    available = frozenset(td["function"]["name"] for td in
                          filter_tool_definitions(available_definitions, context=context)) & authorized
    reachable = frozenset(reachable_tool_ids) if reachable_tool_ids is not None else available
    reasons = {name: ("not_selected_for_session" if name not in requested else
                      "session_capability_unavailable" if name in reachable else "service_unavailable")
               for name in sorted(installed - available)}
    # Hash only this agent's visible metadata: global generations would reveal
    # sibling catalog churn, and must not be used as a public version identifier.
    version = hashlib.sha256(json.dumps(
        [(m.name, m.toolset, m.schema_json) for m in admitted], separators=(",", ":")
    ).encode()).hexdigest()
    from tools.agent_policy_gate import missing_context_denial
    policy_version = context.policy.digest if context else ("unbound" if missing_context_denial() else "legacy")
    view = ToolView(version, policy_version,
                    tuple(installed | bridges), tuple(authorized), tuple(available), tuple(available), reasons,
                    _base_catalog_version=version)
    return view.with_selection(selected_definitions) if selected_definitions is not None else view


def finalize_tool_view(view: ToolView, definitions) -> ToolView:
    """Capture trusted post-build tools at construction or an approved refresh only.

    Memory/context-engine tools can be injected without registry registrations.
    Recheck BE01 authority here, record their immutable schema fingerprint, and
    remove prior injected metadata when that tool is no longer in the final set.
    This does not register tools, run probes, refresh schemas or grant invocation.
    Prefix restoration must use the non-expanding ``with_selection`` instead.
    """
    from agent.runtime_context import current_agent_context
    from tools.agent_policy_gate import authorize_tool, filter_tool_definitions, missing_context_denial

    context = current_agent_context()
    expected_policy = context.policy.digest if context else ("unbound" if missing_context_denial() else "legacy")
    if view.session_policy_version != expected_policy:
        raise ValueError("Tool view does not belong to the active agent policy")
    admitted = filter_tool_definitions(definitions, context=context)
    selected = frozenset(td["function"]["name"] for td in admitted)
    previous_injected = frozenset(view._injected_tool_ids)
    base_authorized = frozenset(name for name in set(view.authorized_tool_ids) - previous_injected
                                if authorize_tool(name, context=context) is None)
    base_installed = (set(view.installed_tool_ids) - previous_injected) & base_authorized
    injected = selected - base_installed
    authorized = base_authorized | injected
    metadata = sorted((td["function"]["name"], json.dumps(td, sort_keys=True, separators=(",", ":")))
                      for td in admitted if td["function"]["name"] in injected)
    base_version = view._base_catalog_version or view.catalog_version
    version = hashlib.sha256(json.dumps([base_version, metadata], separators=(",", ":")).encode()).hexdigest() if metadata else base_version
    return replace(
        view, catalog_version=version, _base_catalog_version=base_version,
        _injected_tool_ids=tuple(sorted(injected)),
        installed_tool_ids=tuple(base_installed | injected),
        authorized_tool_ids=tuple(authorized),
        discoverable_tool_ids=tuple(((set(view.discoverable_tool_ids) - previous_injected) & authorized) | injected),
        selected_tool_ids=tuple(selected),
        unavailable_reasons={name: reason for name, reason in view.unavailable_reasons.items()
                             if name in base_authorized},
    )
