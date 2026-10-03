"""Owned, finite production adapters. No arbitrary code or remote execution."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _domain_operation(agent, db, request, publish):
    import hashlib
    import json
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.result_artifacts import artifact_actor
    from hermes_cli.domain_jobs import DomainJob, prepare_domain_job, publish_domain_job
    from hermes_state_runtime import RuntimeStoreError
    job = DomainJob.from_record(json.loads(request.job_json))
    canonical = json.dumps(job.to_record(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    run = begin_artifact_control(agent, request.session_id, request.command_id,
        {"mode": "domain", "project_id": job.project_id, "request_id": job.job_id,
         "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "request_size": len(canonical.encode())})
    with artifact_control_scope(run):
        proposals = prepare_domain_job(run, job)
        if not publish:
            return {"proposals": [proposal.public_record() for proposal in proposals], "publication_atomic": False}
        expected = [(p.approval_id, p.approval_digest) for p in proposals]
        supplied = [(a.approval_id, a.approval_digest) for a in request.approvals]
        if expected != supplied:
            raise RuntimeStoreError("approval_mismatch", "All exact ordered output approvals are required")
        actor = artifact_actor(run.context)
        for proposal in proposals:
            decision = db.get_effect_approval(proposal.approval_id, actor)
            if decision["status"] == "pending":
                db.resolve_effect_approval(proposal.approval_id, actor, holder=run.holder,
                    generation=run.generation, approval_digest=proposal.approval_digest, choice="once")
        result = publish_domain_job(run, proposals)
        finish_artifact_control(run, result)
        return result


@method("runtime.domain.prepare")
@_profile_scoped
def _domain_prepare(rid, params):
    from .contracts.domains import DomainPrepareParams
    return _artifact_request(rid, params, DomainPrepareParams,
                             lambda agent, db, request: _domain_operation(agent, db, request, False))


@method("runtime.domain.publish")
@_profile_scoped
def _domain_publish(rid, params):
    from .contracts.domains import DomainPublishParams
    return _artifact_request(rid, params, DomainPublishParams,
                             lambda agent, db, request: _domain_operation(agent, db, request, True))


def register(server):
    bind_module(globals(), server)
