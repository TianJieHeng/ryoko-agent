"""Owned mission controls share the authoritative writer, lease and deterministic verifier."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _mission_request(rid, params, model, callback):
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    from agent.budget_account import BudgetBlocked
    from agent.task_scope import TaskCancelled
    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        with agent_runtime_scope(agent.runtime_context):
            return _ok(rid, callback(agent, db, request))
    except (RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)
    except (BudgetBlocked, TaskCancelled, InterruptedError):
        return _err(rid, 4090, "Mission control is blocked by its existing runtime owner", {"code": "mission_run_blocked"})
    except PermissionError:
        return _err(rid, 4030, "Mission scope is not authorized", {"code": "mission_scope_denied"})
    except (ValueError, TypeError):
        return _err(rid, 4000, "Mission request is invalid", {"code": "invalid_mission"})
    except OSError:
        return _err(rid, 5030, "Mission evidence is unavailable", {"code": "mission_evidence_unavailable"})


def _mission_access(agent):
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    return artifact_actor(agent.runtime_context), project_access(agent.runtime_context)


def _mission_public(row):
    from tui_gateway.contracts.missions import MissionRecord
    if row is None:
        return None
    return {**{key: row[key] for key in MissionRecord.model_fields if key in row},
            "archived": row.get("archived", False), "archived_at": row.get("archived_at")}


def _mission_receipt_public(row):
    from tui_gateway.contracts.missions import MissionVerificationReceipt
    from hashlib import sha256
    import re
    result = {key: row[key] for key in MissionVerificationReceipt.model_fields if key in row}
    reference = result.get("evidence_ref", "")
    if not isinstance(reference, str) or re.fullmatch(r"(?:sha256:[0-9a-f]{64}|mission_test_execution:[A-Za-z0-9_-]{1,256})", reference) is None:
        result["evidence_ref"] = "sha256:" + sha256(str(reference).encode()).hexdigest()
    return result


def _mission_read(agent, db):
    actor, access = _mission_access(agent)
    return db.get_mission(agent.session_id, actor, access=access)


@method("runtime.mission.get")
@_profile_scoped
def _runtime_mission_get(rid, params):
    from tui_gateway.contracts.missions import MissionGetParams
    def get(agent, db, request):
        actor, access = _mission_access(agent)
        return {"mission": _mission_public(db.get_mission(agent.session_id, actor, access=access, mission_id=request.mission_id))}
    return _mission_request(rid, params, MissionGetParams, get)


@method("runtime.mission.list")
@_profile_scoped
def _runtime_mission_list(rid, params):
    from tui_gateway.contracts.missions import MissionListParams
    def listing(agent, db, request):
        actor, access = _mission_access(agent)
        rows = db.list_missions(actor, access=access, limit=request.limit)
        return {"missions": [_mission_public(row) for row in rows], "limit": request.limit,
                "limit_reached": len(rows) == request.limit, "complete": False}
    return _mission_request(rid, params, MissionListParams, listing)


@method("runtime.mission.create")
@_profile_scoped
def _runtime_mission_create(rid, params):
    from tui_gateway.contracts.missions import MissionCreateParams
    def create(agent, db, request):
        from agent.mission_controls import mission_user_control
        actor, access = _mission_access(agent)
        with mission_user_control(agent, request.session_id) as control:
            row = db.create_mission(control.session_id, actor, holder=control.holder, generation=control.generation,
                mission_id=request.mission_id, contract=request.contract.model_dump(by_alias=True, exclude_unset=True), access=access,
                previous_mission_id=request.previous_mission_id, previous_revision=request.previous_revision)
        return {"mission": _mission_public(row), "dispatch_performed": False}
    return _mission_request(rid, params, MissionCreateParams, create)


@method("runtime.mission.revise")
@_profile_scoped
def _runtime_mission_revise(rid, params):
    from tui_gateway.contracts.missions import MissionReviseParams
    def revise(agent, db, request):
        from agent.mission_controls import mission_user_control
        from hermes_state_runtime import RuntimeStoreError
        actor, access = _mission_access(agent)
        with mission_user_control(agent, request.session_id) as control:
            from hermes_state_mission_history import require_active_target
            require_active_target(db, control.session_id, actor, request.mission_id)
            current = db.get_mission(control.session_id, actor, access=access)
            if current is None:
                raise RuntimeStoreError("mission_not_found", "Mission does not exist")
            contract = request.contract.model_dump(by_alias=True, exclude_unset=True)
            for field in ("project_id", "budget_ref"):
                if field in contract and contract[field] != current[field]:
                    raise RuntimeStoreError("mission_scope_immutable", "Mission scope cannot change")
                contract.pop(field, None)
            if current["state"] == "completed":
                contract["state"] = "ready"
            row = db.update_mission(control.session_id, actor, holder=control.holder, generation=control.generation,
                expected_revision=request.expected_revision, changes=contract, changed_inputs=request.changed_inputs,
                changed_targets=request.changed_targets, changed_plan_steps=request.changed_plan_steps, access=access, mission_id=request.mission_id)
        return {"mission": _mission_public(row), "dispatch_performed": False}
    return _mission_request(rid, params, MissionReviseParams, revise)


def _mission_control_operation(agent, db, request, operation):
    from agent.mission_controls import mission_user_control
    actor, access = _mission_access(agent)
    with mission_user_control(agent, request.session_id) as control:
        from hermes_state_mission_history import require_active_target
        require_active_target(db, control.session_id, actor, request.mission_id)
        if operation == "accept":
            row = db.accept_mission(control.session_id, actor, holder=control.holder, generation=control.generation,
                                    expected_revision=request.expected_revision, access=access, mission_id=request.mission_id)
        else:
            state = {"pause": "paused", "resume": "ready", "cancel": "cancelled"}[operation]
            changes = {"state": state}
            if operation == "pause":
                changes["paused_reason"] = request.reason or "user-paused"
            elif operation == "resume":
                changes["paused_reason"] = None
            row = db.update_mission(control.session_id, actor, holder=control.holder, generation=control.generation,
                expected_revision=request.expected_revision, changes=changes, access=access, mission_id=request.mission_id)
            if operation in {"pause", "cancel"}:
                active = getattr(agent, "_active_runtime_run", None)
                task_scope = getattr(active, "task_scope", None)
                if task_scope is not None:
                    task_scope.request_cancel("Mission " + state + " by user")
    return {"mission": _mission_public(row), "dispatch_performed": False}


def _mission_control_handler(operation):
    from tui_gateway.contracts.missions import MissionControlParams, MissionRevisionParams
    model = MissionRevisionParams if operation == "accept" else MissionControlParams
    def handler(rid, params):
        return _mission_request(rid, params, model,
            lambda agent, db, request: _mission_control_operation(agent, db, request, operation))
    return handler


for _operation in ("pause", "resume", "cancel", "accept"):
    _handler = _mission_control_handler(_operation)
    _handler.__name__ = "_runtime_mission_" + _operation
    method("runtime.mission." + _operation)(_profile_scoped(_handler))


@method("runtime.mission.receipts.list")
@_profile_scoped
def _runtime_mission_receipts(rid, params):
    from tui_gateway.contracts.missions import MissionReceiptListParams
    def receipts(agent, db, request):
        actor, access = _mission_access(agent)
        rows = db.list_verification_receipts(agent.session_id, actor, access=access, limit=request.limit, mission_id=request.mission_id)
        return {"receipts": [_mission_receipt_public(row) for row in rows], "limit": request.limit,
                "limit_reached": len(rows) == request.limit, "complete": False}
    return _mission_request(rid, params, MissionReceiptListParams, receipts)


@method("runtime.mission.verify")
@_profile_scoped
def _runtime_mission_verify(rid, params):
    from tui_gateway.contracts.missions import MissionRevisionParams
    def verify(agent, db, request):
        from agent.mission_controls import mission_user_control
        from agent.mission_verifier import verify_mission
        from hermes_state_runtime import RuntimeStoreError
        import time
        actor, access = _mission_access(agent)
        with mission_user_control(agent, request.session_id) as control:
            from hermes_state_mission_history import require_active_target
            require_active_target(db, control.session_id, actor, request.mission_id)
            row = db.get_mission(control.session_id, actor, access=access)
            if row is None or row["revision"] != request.expected_revision:
                raise RuntimeStoreError("revision_conflict", "Mission revision changed")
            observed = verify_mission(control.context, db, row, deadline_at=min(control.expires_at, time.time() + 10))
            receipts = []
            for receipt in observed:
                result = db.append_verification_receipt(control.session_id, actor, holder=control.holder,
                    generation=control.generation, expected_revision=row["revision"], receipt=receipt, access=access)
                receipts.append(result)
                row = db.get_mission(control.session_id, actor, access=access)
            required = {item["criterion_id"] for item in row["acceptance"]
                        if item["required"] and item["kind"] != "user_acceptance"}
            passed = {item["criterion_id"] for item in receipts if item["result"] == "pass"}
            if required and required <= passed:
                state = "ready_to_review"
            elif any(item["result"] == "blocked" for item in receipts):
                state = "waiting_for_source"
            elif passed:
                state = "partially_completed"
            else:
                state = "waiting_for_user"
            row = db.update_mission(control.session_id, actor, holder=control.holder, generation=control.generation,
                expected_revision=row["revision"], changes={"state": state}, access=access, mission_id=request.mission_id)
        return {"mission": _mission_public(row), "receipts": [_mission_receipt_public(r) for r in receipts],
                "dispatch_performed": False}
    return _mission_request(rid, params, MissionRevisionParams, verify)


@method("runtime.mission.history")
@_profile_scoped
def _runtime_mission_history(rid, params):
    from tui_gateway.contracts.missions import MissionListParams
    def history(agent, db, request):
        from hermes_state_mission_history import list_conversation_missions
        actor, access = _mission_access(agent)
        rows = list_conversation_missions(db, agent.session_id, actor, access=access, limit=request.limit)
        return {"missions": [_mission_public(row) for row in rows], "limit": request.limit,
                "limit_reached": len(rows) == request.limit, "complete": False}
    return _mission_request(rid, params, MissionListParams, history)


def register(server):
    bind_module(globals(), server)
