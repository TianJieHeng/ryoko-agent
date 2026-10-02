"""Per-agent memory routing with one authoritative backend and no implicit fallback."""
from __future__ import annotations

import hashlib
import json

from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryCapabilities


def assert_memory_owner(context):
    from agent.runtime_context import current_agent_context
    from tools.mcp_tool_policy import live_context
    current = current_agent_context()
    if current is None or current != context:
        raise PermissionError("Memory belongs to a different authenticated agent")
    live_context()  # Re-read immutable policy/config; revoked sessions cannot reuse a provider.
    return current


def memory_backend_for(context):
    assert_memory_owner(context)
    backend = context.policy.memory_backend
    if (backend == "personal_mcp") != (context.policy.role == "primary"):
        raise PermissionError("Memory backend does not match the authenticated identity")
    return backend


def memory_scope_digest(context, config):
    """Private cache scope; credential values are hashed by the existing MCP owner layer."""
    from tools.mcp_tool_policy import registry_scope
    material = [context.identity.to_record(), context.profile_home, context.config_digest,
                context.policy.digest, config.get("memory", {}),
                registry_scope(context) if context.policy.memory_backend == "personal_mcp" else None]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RoutedMemoryManager(MemoryManager):
    """Strict manager: only explicitly supported operations receive payloads.

    This class deliberately never runs legacy fan-out, mirroring, private write
    queues, extraction hooks or automatic provider recovery.
    """
    def __init__(self, context, config, *, store=None, provider=None, disabled=False):
        super().__init__()
        self.owner_context = context
        self.cache_scope_digest = memory_scope_digest(context, config)
        self.backend = memory_backend_for(context)
        self.store = store
        self.provider = provider
        self.disabled = disabled
        self._closed = False
        self._recall_unavailable = False
        # A personal context pack is never spilled into a second local memory store.
        self._external_prefetch_spill_config = {"enabled": False}
        # Conservative on resume: the restored immutable prefix may predate this process.
        self._fresh_revisions = {"individual": 0}
        self._selected_project_id = None
        self._scope_needs_notice = False
        self._pending_fresh = None
        self._capabilities = MemoryCapabilities(
            backend=self.backend, recall=not disabled and (store is not None or bool(provider and provider.is_available())),
            write=not disabled and store is not None, supersede=not disabled and store is not None,
            delete=not disabled and store is not None, export=not disabled and store is not None, session_ingest=False)
        # No add_provider: its legacy schema/lifecycle fan-out is intentionally inapplicable.

    def assert_owner(self):
        assert_memory_owner(self.owner_context)
        from agent.identity_lifecycle import identity_config
        if memory_scope_digest(self.owner_context, identity_config()) != self.cache_scope_digest:
            raise PermissionError("Memory configuration or credential scope changed")
        if self._closed:
            raise PermissionError("Memory manager is closed")
        return self.owner_context

    def matches(self, context, config):
        return (not self._closed and self.owner_context == context
                and self.cache_scope_digest == memory_scope_digest(context, config))

    def capability_manifest(self):
        self.assert_owner()
        return self._capabilities.to_record()

    def health(self):
        self.assert_owner()
        if self.disabled:
            return {"backend": self.backend, "status": "disabled", "reason_code": "memory_disabled", "supported_operations": []}
        if self.provider is not None:
            result = self.provider.health()
            if self._recall_unavailable:
                result.update(status="degraded", reason_code="recall_unavailable")
            return result
        return {"backend": self.backend, "status": "ready", "reason_code": None,
                "supported_operations": ["recall", "write", "supersede", "delete", "export"]}

    def add_provider(self, provider):
        raise PermissionError("Strict memory routing does not permit provider federation")

    def _each_provider(self, label, call, **kwargs):
        self.assert_owner()
        return []  # Disabled/unsupported hooks receive zero payload, including startup and teardown.

    def prefetch_all(self, query, *, session_id=""):
        self.assert_owner()
        if self.disabled or self.provider is None:
            return ""
        clean = self._strip_skill_scaffolding(query)
        if not clean:
            return ""
        try:
            result = (self._prefetch_provider(self.provider, clean, session_id=session_id)
                      if self.provider.is_available() else self.provider.prefetch(clean, session_id=session_id))
        except Exception:
            result = ""
        self._recall_unavailable = not bool(result)
        if not result:
            from agent.memory_mcp_provider import _DEGRADED
            return _DEGRADED
        return result

    def set_project_scope(self, project_id):
        """Explicit session-local applicability; never infer a project from cwd or recall."""
        self.assert_owner()
        if project_id is not None:
            if self.backend != "builtin" or self.disabled:
                raise PermissionError("Project memory scope is unsupported by this backend")
            if not isinstance(project_id, str) or not 1 <= len(project_id) <= 256:
                raise ValueError("Project memory scope requires a bounded project ID")
            from agent.project_context import authorize_project
            authorize_project(self.owner_context, project_id, operation="read")
        if self._selected_project_id != project_id:
            self._scope_needs_notice = True
        self._selected_project_id = project_id
        self._pending_fresh = None
        scope_key = "individual" if project_id is None else "project:" + project_id
        if scope_key not in self._fresh_revisions:
            if len(self._fresh_revisions) >= 64:
                oldest = next(key for key in self._fresh_revisions if key != "individual")
                del self._fresh_revisions[oldest]  # Conservative replay if selected again.
            self._fresh_revisions[scope_key] = 0
        return {"scope_key": scope_key if self.backend == "builtin" else self.backend,
                "project_id": project_id}

    def fresh_context(self, query, session_id="", budget=8192):
        self.assert_owner()
        if type(budget) is not int or not 256 <= budget <= 32768:
            raise ValueError("Memory context budget must be between 256 and 32768")
        if self.store is not None and not self.disabled:
            scope_key = "individual" if self._selected_project_id is None else "project:" + self._selected_project_id
            changes = self.store.changes_since(self._fresh_revisions.get(scope_key, 0),
                limit=32, max_chars=budget, project_id=self._selected_project_id)
            records = [{key: row.get(key) for key in
                        ("record_id", "version", "validity", "deletion_state", "content", "source_ref", "scope", "namespace_id")}
                       for row in changes["records"]]
            for row in records:
                row["namespace_id"] = changes["namespace_id"]
            cursor = f'{changes["namespace_id"]}:{scope_key}:{changes["revision"]}'
            self._pending_fresh = (cursor, scope_key, changes["revision"])
            packet = {"schema_version": 1, "cursor": cursor, "scope_key": scope_key,
                    "records": records, "invalidation_refs": [row["record_id"] for row in records
                        if row["version"] > 1 or row["deletion_state"] != "present" or row["validity"] == "invalid"],
                    "degraded": changes["truncated"]}
            if self._scope_needs_notice:
                packet["context_text"] = ("Memory applicability changed explicitly to " + scope_key
                    + ". Earlier project-scoped memory applies only to its named project, not to unrelated work.")
            return packet
        return {"schema_version": 1, "cursor": self.cache_scope_digest, "scope_key": self.backend, "records": [],
                "invalidation_refs": [], "degraded": self.health()["status"] not in {"ready", "disabled"}}

    def acknowledge_fresh_context(self, cursor):
        """Advance only after the caller durably persisted this exact fresh sidecar."""
        self.assert_owner()
        if self._pending_fresh is not None and cursor == self._pending_fresh[0]:
            self._fresh_revisions[self._pending_fresh[1]] = self._pending_fresh[2]
            self._scope_needs_notice = False
            self._pending_fresh = None
            return True
        return False

    def describe_recall(self):
        self.assert_owner()
        if self.provider is None or self.disabled:
            return ""
        status = self.health()
        return "Personal memory context is degraded" if status["status"] != "ready" else "Personal memory context recalled"

    def sync_all(self, *args, **kwargs):
        self.assert_owner()

    def queue_prefetch_all(self, *args, **kwargs):
        self.assert_owner()

    def on_pre_compress(self, *args, **kwargs):
        self.assert_owner()
        return ""

    def notify_memory_tool_write(self, *args, **kwargs):
        self.assert_owner()

    def handle_tool_call(self, tool_name, args, **kwargs):
        self.assert_owner()
        from tools.registry import tool_error
        return tool_error("Operation is unsupported by the selected memory backend", status="unsupported")

    def shutdown_all(self):
        # No transcript/secret payload or remote flush at teardown. Reject cross-owner teardown.
        self.assert_owner()
        self._closed = True


def initialize_routed_memory(agent, config, *, skip_memory=False, memory_manager=None):
    context = agent.runtime_context
    backend = memory_backend_for(context)
    # Effective operator config is authoritative, never constructor/model overrides.
    from agent.identity_lifecycle import identity_config
    config = identity_config()
    from tools.memory_tool import get_builtin_memory_config, get_builtin_memory_store_flags
    mem_config = get_builtin_memory_config(config)
    disabled = bool(skip_memory or "memory" in (agent.disabled_toolsets or []))
    if backend == "builtin" and not any(get_builtin_memory_store_flags(config)):
        disabled = True
    if memory_manager is not None:
        if not isinstance(memory_manager, RoutedMemoryManager) or not memory_manager.matches(context, config):
            raise PermissionError("Cached memory manager does not belong to this identity and configuration")
        if memory_manager.disabled != disabled:
            raise PermissionError("Cached memory manager has a different enablement scope")
        memory_manager.assert_owner()
        manager = memory_manager
        agent._memory_store = manager.store
        if manager.store is not None:
            agent._memory_enabled, agent._user_profile_enabled = get_builtin_memory_store_flags(config)
    elif backend == "builtin":
        store = None
        if not disabled:
            from tools.individual_memory_store import create_individual_memory_store
            agent._memory_enabled, agent._user_profile_enabled = get_builtin_memory_store_flags(config)
            if agent._memory_enabled or agent._user_profile_enabled:
                store = create_individual_memory_store(context,
                    memory_char_limit=mem_config.get("memory_char_limit", 2200),
                    user_char_limit=mem_config.get("user_char_limit", 1375),
                    memory_enabled=agent._memory_enabled, user_profile_enabled=agent._user_profile_enabled)
                store.load_from_disk()
        disabled = disabled or store is None
        agent._memory_store = store
        manager = RoutedMemoryManager(context, config, store=store, disabled=disabled)
    else:
        from agent.memory_mcp_provider import MCPContextPackProvider
        provider = None if disabled else MCPContextPackProvider(context, mem_config.get("personal_mcp"))
        manager = RoutedMemoryManager(context, config, provider=provider, disabled=disabled)
    agent._memory_manager = manager
    health = manager.health()
    agent.runtime_memory_status = health["status"]
    if health["status"] in {"degraded", "unconfigured"}:
        emit = getattr(agent, "_emit_startup_warning", None)
        if callable(emit):
            emit("Personal memory is unavailable; using only authorized active context and project artifacts.")
