"""Owned memory inspection and structured built-in controls."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _owned_memory_manager(agent):
    from agent.memory_router import memory_backend_for
    from hermes_state_runtime import RuntimeStoreError
    context = agent.runtime_context
    backend = memory_backend_for(context)
    manager = getattr(agent, "_memory_manager", None)
    if manager is None:
        raise RuntimeStoreError("memory_not_initialized", "Owned memory routing is not initialized")
    capabilities = manager.capability_manifest()
    if capabilities["backend"] != backend:
        raise RuntimeStoreError("identity_mismatch", "Memory backend does not match its immutable owner")
    return manager


@method("runtime.memory.status")
@_profile_scoped
def _runtime_memory_status(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams

    def status(agent, db, request):
        from hermes_state_runtime import RuntimeStoreError
        try:
            manager = _owned_memory_manager(agent)
            return {"capabilities": manager.capability_manifest(), "health": manager.health()}
        except PermissionError as exc:
            raise RuntimeStoreError("memory_owner_denied", "Memory ownership or policy changed") from exc
    return _artifact_request(rid, params, RuntimeSessionParams, status)


def _individual_memory_operation(agent, operation, *args, capability="recall", **kwargs):
    from hermes_state_runtime import RuntimeStoreError
    from tools.individual_memory_store import IndividualMemoryStore, IndividualMemoryError
    try:
        manager = _owned_memory_manager(agent)
        capabilities = manager.capability_manifest()
        if capabilities["backend"] != "builtin" or not capabilities.get(capability):
            raise RuntimeStoreError("memory_operation_unsupported", "This memory backend does not support the requested operation")
        store = manager.store
        if not isinstance(store, IndividualMemoryStore):
            raise RuntimeStoreError("identity_mismatch", "Memory store is not an isolated built-in owner")
        return getattr(store, operation)(*args, **kwargs)
    except IndividualMemoryError as exc:
        raise RuntimeStoreError(exc.code, "Owned memory operation rejected") from exc
    except PermissionError as exc:
        raise RuntimeStoreError("memory_owner_denied", "Memory ownership or policy changed") from exc


def _memory_snapshot(agent, request):
    from hermes_state_runtime import RuntimeStoreError
    snapshot = _individual_memory_operation(agent, "export_snapshot", include_deleted=request.include_deleted,
                                            project_id=request.project_id)
    if request.expected_revision is not None and snapshot["revision"] != request.expected_revision:
        raise RuntimeStoreError("revision_conflict", "Memory changed; restart the bounded read from a new revision")
    return snapshot


@method("runtime.memory.record.get")
@_profile_scoped
def _runtime_memory_record_get(rid, params):
    from tui_gateway.contracts.memory import MemoryRecordParams
    return _artifact_request(rid, params, MemoryRecordParams,
        lambda agent, db, request: {"record": _individual_memory_operation(agent, "read_record",
            request.record_id, version=request.version)})


@method("runtime.memory.record.write")
@_profile_scoped
def _runtime_memory_record_write(rid, params):
    from tui_gateway.contracts.memory import MemoryWriteParams
    return _artifact_request(rid, params, MemoryWriteParams,
        lambda agent, db, request: {"outcome": _individual_memory_operation(agent, "write_record",
            capability="write", **request.model_dump(exclude={"session_id", "schema_version"}))})


@method("runtime.memory.record.delete")
@_profile_scoped
def _runtime_memory_record_delete(rid, params):
    from tui_gateway.contracts.memory import MemoryDeleteParams
    return _artifact_request(rid, params, MemoryDeleteParams,
        lambda agent, db, request: {"outcome": _individual_memory_operation(agent, "delete_record",
            request.record_id, expected_version=request.expected_version, capability="delete")})


@method("runtime.memory.records.list")
@_profile_scoped
def _runtime_memory_records_list(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.memory import MemoryListParams

    def listing(agent, db, request):
        snapshot = _memory_snapshot(agent, request)
        records = snapshot["records"]
        if request.offset > len(records):
            raise RuntimeStoreError("invalid_command", "Memory page offset exceeds its snapshot")
        page = records[request.offset:request.offset + request.limit]
        next_offset = request.offset + len(page)
        return {"revision": snapshot["revision"], "records": page, "offset": request.offset,
                "next_offset": next_offset, "total": len(records), "has_more": next_offset < len(records)}
    return _artifact_request(rid, params, MemoryListParams, listing)


@method("runtime.memory.export")
@_profile_scoped
def _runtime_memory_export(rid, params):
    import base64
    import hashlib
    import json
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.memory import MemoryExportParams

    def export(agent, db, request):
        manager = _owned_memory_manager(agent)
        if not manager.capability_manifest()["export"]:
            raise RuntimeStoreError("memory_operation_unsupported", "This backend has no verified export contract")
        snapshot = _memory_snapshot(agent, request)
        data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeStoreError("memory_export_limit", "Memory export exceeds its explicit byte bound")
        if request.offset > len(data):
            raise RuntimeStoreError("invalid_command", "Memory export offset exceeds its snapshot")
        chunk = data[request.offset:request.offset + request.limit]
        next_offset = request.offset + len(chunk)
        return {"revision": snapshot["revision"], "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                "format": "json", "deletion_semantics": "tombstones_not_physical_erasure", "offset": request.offset,
                "data_base64": base64.b64encode(chunk).decode("ascii"), "next_offset": next_offset,
                "eof": next_offset == len(data)}
    return _artifact_request(rid, params, MemoryExportParams, export)


@method("runtime.memory.scope.set")
@_profile_scoped
def _runtime_memory_scope_set(rid, params):
    from tui_gateway.contracts.memory import MemoryScopeParams
    from hermes_state_runtime import RuntimeStoreError

    def select(agent, db, request):
        try:
            return _owned_memory_manager(agent).set_project_scope(request.project_id)
        except PermissionError as exc:
            raise RuntimeStoreError("memory_scope_denied", "Memory scope is not authorized for this backend") from exc
    return _artifact_request(rid, params, MemoryScopeParams, select)


def register(server):
    bind_module(globals(), server)
