"""Human-owned scheduling/commitment controls over finite local adapters."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _schedule_operation(agent, db, request, name):
    import json
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.identity_lifecycle import agent_runtime_scope
    from cron.durable_contract import canonical, digest
    from hermes_state_schedules import ScheduleRegistry
    from hermes_state_commitments import CommitmentRegistry

    context = agent.runtime_context
    schedules, commitments = ScheduleRegistry(context, db), CommitmentRegistry(context, db)
    if name in {"runtime.schedule.create", "runtime.schedule.import", "runtime.schedule.update",
                "runtime.schedule.run_now", "runtime.schedule.cutover"}:
        from cron.durable_contract import validate_definition
        definition = (validate_definition(json.loads(request.definition_json)) if hasattr(request, "definition_json") else
                      schedules.get(request.project_id, request.schedule_id)["definition"])
        if isinstance(definition, dict) and definition.get("kind") == "command":
            from hermes_state_command_schedules import CommandScheduleRegistry
            return {"record_json": canonical(CommandScheduleRegistry(context, db).control(agent, request, name))}
        if name in {"runtime.schedule.run_now", "runtime.schedule.cutover"}:
            from cron.durable_contract import require
            require(False, "This control requires a command schedule", "invalid_schedule")
    if name == "runtime.schedule.grant":
        from cron.durable_contract import require
        require(schedules.get(request.project_id, request.schedule_id)["definition"]["kind"] != "command",
                "Command authority is pinned by the immutable definition and activation", "invalid_schedule")
    writes = {
        "runtime.schedule.create": lambda run: schedules.create(run, json.loads(request.definition_json), expected_revision=request.expected_revision),
        "runtime.schedule.import": lambda run: schedules.create(run, json.loads(request.definition_json), expected_revision=request.expected_revision,
                                                                imported=json.loads(request.import_json)),
        "runtime.schedule.update": lambda run: schedules.update(run, request.project_id, request.schedule_id, request.expected_revision, request.state),
        "runtime.schedule.grant": lambda run: schedules.grant(run, request.project_id, request.schedule_id,
            expected_revision=request.expected_revision, expires_at=request.expires_at, max_age_seconds=request.max_age_seconds, max_fires=request.max_fires),
        "runtime.schedule.reconcile": lambda run: schedules.reconcile(run, request.project_id, request.schedule_id,
            occurrence=request.occurrence, evidence_ref=json.loads(request.evidence_ref_json)),
        "runtime.correspondence.draft": lambda run: commitments.draft_correspondence(run, request.project_id,
            request.correspondence_id, request.recipients, request.content, json.loads(request.source_refs_json)),
        "runtime.correspondence.receipt": lambda run: commitments.record_correspondence_receipt(run, request.project_id,
            request.correspondence_id, request.effect_id),
        "runtime.inbox.prepare": lambda run: commitments.prepare_inbox(run, request.project_id,
            json.loads(request.source_ref_json), json.loads(request.selection_json)),
        "runtime.commitment.accept": lambda run: commitments.accept(run, request.project_id, request.candidate_id,
            request.expected_revision, request.owner, request.outcome, request.due_or_check_at.model_dump() if request.due_or_check_at else None),
        "runtime.commitment.decline": lambda run: commitments.decline(run, request.project_id, request.candidate_id,
            request.expected_revision, request.reason),
        "runtime.commitment.update": lambda run: commitments.update(run, request.project_id, request.commitment_id,
            request.expected_revision, request.state, json.loads(request.evidence_ref_json), request.superseded_by, request.due_or_check_at.model_dump() if request.due_or_check_at else None),
    }
    reads = {
        "runtime.schedule.get": lambda: schedules.get(request.project_id, request.schedule_id),
        "runtime.schedule.list": lambda: {"schedules": schedules.list(request.project_id)},
        "runtime.correspondence.get": lambda: commitments.correspondence(request.project_id, request.correspondence_id),
        "runtime.commitment.candidate": lambda: commitments.candidate(request.project_id, request.candidate_id),
        "runtime.commitment.get": lambda: commitments.get(request.project_id, request.commitment_id),
        "runtime.commitment.list": lambda: {"commitments": commitments.list(request.project_id)},
        "runtime.commitment.review": lambda: commitments.weekly_review(request.project_id),
        "runtime.calendar.preview": lambda: commitments.preview_calendar(request.project_id,
            json.loads(request.availability_ref_json), timezone=request.timezone, participants=request.participants, start_at=request.start_at,
            end_at=request.end_at, duration_minutes=request.duration_minutes),
        "runtime.agenda.plan": lambda: commitments.plan_agenda(request.project_id,
            json.loads(request.availability_ref_json), timezone=request.timezone, participants=request.participants,
            windows=[row.model_dump() for row in request.windows], work=[row.model_dump() for row in request.work],
            buffer_minutes=request.buffer_minutes, daily_capacity_minutes=request.daily_capacity_minutes),
    }
    if name in reads:
        with agent_runtime_scope(context):
            return {"record_json": canonical(reads[name]())}
    data = request.model_dump(exclude={"session_id", "command_id", "schema_version"})
    run = begin_artifact_control(agent, request.session_id, request.command_id,
        {"mode": name, "request_sha256": digest(data)})
    with artifact_control_scope(run):
        result = writes[name](run)
        finish_artifact_control(run, result)
        return {"record_json": canonical(result)}


def _schedule_handler(name, model):
    @_profile_scoped
    def handler(rid, params):
        return _artifact_request(rid, params, model, lambda agent, db, request: _schedule_operation(agent, db, request, name))
    return handler


from .contracts.schedules import _SPECS
from .contracts.command_schedules import COMMAND_SCHEDULE_SPECS
for _name, (_model, _doc) in {**_SPECS, **COMMAND_SCHEDULE_SPECS}.items():
    method(_name)(_schedule_handler(_name, _model))


@method("runtime.schedule.scheduler.status")
@_profile_scoped
def _schedule_scheduler_status(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    from tui_gateway.runtime_schedule_tick import scheduler_status
    from tui_gateway import server
    return _artifact_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: scheduler_status(server, agent.runtime_context.profile_home))


def register(server):
    bind_module(globals(), server)
