"""Exact owned human operator controls over the existing maintenance broker.

Transport ownership does not widen repair scope. The broker revalidates complete
plans, policy, revisions and target evidence and journals under its own fence.
"""
import json

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _operations_memory(agent, record_id):
    if record_id is None:
        return None
    from agent.operations_control import require
    from tools.individual_memory_store import IndividualMemoryStore
    require(agent.runtime_context.policy.memory_backend == "builtin", "deletion_backend_unsupported")
    return IndividualMemoryStore(agent.runtime_context)


def _operations_call(agent, db, request, name):
    from agent import operations_control as controls
    from agent import operations_privacy as privacy
    from agent.runtime_commands import _RUN
    from tools.individual_memory_store import IndividualMemoryError

    controls.require(_RUN.get() is None, "operations_human_control_required")
    context = agent.runtime_context

    def audit():
        from agent.operations_audit import project_event
        sid, _ = controls.authority(db, context)
        page = db.replay_runtime_events(sid, cursor=request.cursor, limit=request.limit)
        return {"status": page["status"], "last_cursor": page["last_cursor"], "has_more": page["has_more"],
                "events": [project_event(event) for event in page["events"]]}

    def delete():
        plan = json.loads(request.plan_json)
        controls.require(isinstance(plan, dict) and isinstance(plan.get("source"), dict), "deletion_invalid_manifest")
        return privacy.apply_deletion(db, context, plan, authorization_digest=request.authorization_digest,
                                      memory_store=_operations_memory(agent, plan["source"].get("record_id")))

    handlers = {
        "runtime.operations.inspect": lambda: controls.inspect_runtime(db, context),
        "runtime.operations.audit": audit,
        "runtime.operations.retention": lambda: privacy.retention_inventory(db, context),
        "runtime.operations.checkpoint": lambda: controls.qualify_checkpoint_restore(db, context),
        "runtime.operations.repair.prepare": lambda: controls.preview_repair(db, context, request.action, request.target_id),
        "runtime.operations.repair.apply": lambda: controls.apply_repair(db, context, json.loads(request.plan_json),
                                                                         authorization_digest=request.authorization_digest),
        "runtime.operations.deletion.prepare": lambda: privacy.preview_deletion(db, context,
            record_id=request.memory_record_id, memory_store=_operations_memory(agent, request.memory_record_id)),
        "runtime.operations.deletion.apply": delete,
    }
    try:
        result = handlers[name]()
    except (IndividualMemoryError, PermissionError) as exc:
        raise controls.OperationsError("operations_memory_unavailable", "Owned memory operation is unavailable") from exc
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    controls.require(len(encoded.encode()) <= 262144, "operations_result_bound")
    return {"record_json": encoded}


def _operations_handler(name, model):
    @_profile_scoped
    def handle(rid, params):
        return _artifact_request(rid, params, model, lambda agent, db, request: _operations_call(agent, db, request, name))
    return handle


from .contracts.operations_control import _SPECS
for _name, _model in _SPECS.items():
    method(_name)(_operations_handler(_name, _model))


def register(server):
    bind_module(globals(), server)
