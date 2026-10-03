"""Owned source reads and durable approval-ready living-brief refreshes."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _research_parse_json(encoded):
    import json
    from agent.source_manifest import require
    require(len(encoded.encode("utf-8")) <= 2 * 1024 * 1024,
            "research_request_limit", "Research request exceeds its UTF-8 byte bound")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "invalid_source", "Duplicate research fields are not allowed")
            result[key] = value
        return result
    def constant(_value):
        raise ValueError("Research JSON must contain finite values")
    try:
        return json.loads(encoded, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Research JSON must be bounded valid data") from error


def _research_response(record):
    import json
    from agent.source_manifest import require
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    require(len(encoded.encode()) <= 3 * 1024 * 1024,
            "research_response_limit", "Research response exceeds its complete byte bound")
    return {"response_json": encoded, **({"project_id": record["project_id"]} if "project_id" in record else {})}


def _research_requests(records):
    from agent.source_manifest import MAX_SOURCES, SourceRequest, require
    require(isinstance(records, list) and 0 < len(records) <= MAX_SOURCES,
            "invalid_source", "Research requires a bounded nonempty exact source list")
    return tuple(SourceRequest.from_record(record) for record in records)


def _research_resolve_operation(agent, db, request):
    from hermes_cli.domain_research import resolve_sources
    requests = _research_requests(_research_parse_json(request.request_json))
    return _research_response(resolve_sources(agent.runtime_context, db, requests).to_record())


def _research_restore_dependencies(agent, db, request):
    import json
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor, read_project_artifact
    from agent.source_manifest import SourceManifest, SourceRequest, require
    from hermes_cli.domain_research import ClaimDependency, resolve_sources
    ref = request.manifest_ref
    actor, access = artifact_actor(agent.runtime_context), project_access(agent.runtime_context)
    row = db.read_artifact_version(ref.artifact_id, ref.version, actor, access=access)
    require(row["project_id"] == request.project_id and row["descriptor"]["sha256"] == ref.sha256
            and row["descriptor"]["mime"] == "application/json",
            "brief_manifest_mismatch", "Dependency manifest must name its exact approved JSON version")
    data = read_project_artifact(agent.runtime_context, db, request.project_id, ref.artifact_id, ref.version)
    require(len(data) <= 2 * 1024 * 1024, "brief_manifest_limit", "Dependency manifest exceeds its byte bound")
    record = _research_parse_json(data.decode("utf-8"))
    require(isinstance(record, dict) and record.get("schema_version") == 1
            and record.get("kind") == "living_brief_dependencies",
            "brief_manifest_mismatch", "Artifact is not a living-brief dependency manifest")
    brief = record.get("brief_ref", {})
    require(isinstance(brief, dict) and set(brief) == {"project_id", "artifact_id", "version", "sha256"}
            and brief["project_id"] == request.project_id and brief["artifact_id"] == request.artifact_id,
            "brief_manifest_mismatch", "Dependency manifest belongs to a different brief")
    baseline = db.read_artifact_version(brief["artifact_id"], brief["version"], actor, access=access)
    require(baseline["descriptor"]["sha256"] == brief["sha256"],
            "brief_manifest_mismatch", "Dependency manifest's brief bytes do not match")
    source_records, claim_records = record.get("dependency_sources"), record.get("claim_dependencies")
    require(isinstance(source_records, list) and 0 < len(source_records) <= 64
            and isinstance(claim_records, list) and 0 < len(claim_records) <= 100,
            "brief_manifest_mismatch", "Dependency manifest needs bounded exact sources and claims")
    sources = []
    for stored in source_records:
        require(isinstance(stored, dict) and "retrieved_at" not in stored
                and {"source_id", "source_type", "version", "sha256", "authority", "evidence_ranges", "fresh_until", "scope"} <= set(stored)
                and isinstance(stored["scope"], dict) and "project_id" in stored["scope"],
                "brief_manifest_mismatch", "Durable dependencies omit volatile completed-read timestamps")
        source_request = SourceRequest.from_record({key: stored[key] for key in (
            "source_id", "source_type", "version", "sha256", "authority", "evidence_ranges", "fresh_until")}
            | {"project_id": stored["scope"]["project_id"]})
        current = resolve_sources(agent.runtime_context, db, (source_request,)).sources[0].manifest
        require(current.availability == "available", "brief_prior_source_unavailable", "Retained dependency source cannot be reopened")
        # This is a new completed read, never the operation's acceptance time.
        sources.append(SourceManifest.from_record({**stored, "retrieved_at": current.retrieved_at}))
    return tuple(sources), tuple(ClaimDependency.from_record(claim) for claim in claim_records)


def _research_brief_inputs(agent, db, request):
    from agent.source_manifest import SourceManifest, require
    from hermes_cli.domain_research import ClaimDependency, ClaimUpdate
    body = _research_parse_json(request.request_json)
    required = {"requests", "updates"} if request.manifest_ref else {"previous_sources", "claims", "requests", "updates"}
    require(isinstance(body, dict) and set(body) == required, "invalid_claim", "Brief request fields differ from its dependency mode")
    requests = _research_requests(body["requests"])
    require(isinstance(body["updates"], list) and 0 < len(body["updates"]) <= 100,
            "invalid_claim", "Brief updates must be bounded")
    updates = tuple(ClaimUpdate.from_record(item) for item in body["updates"])
    if request.manifest_ref is not None:
        previous, claims = _research_restore_dependencies(agent, db, request)
    else:
        require(isinstance(body["previous_sources"], list) and 0 < len(body["previous_sources"]) <= 64
                and isinstance(body["claims"], list) and 0 < len(body["claims"]) <= 100,
                "invalid_claim", "Initial brief dependencies must be bounded")
        previous = tuple(SourceManifest.from_record(item) for item in body["previous_sources"])
        claims = tuple(ClaimDependency.from_record(item) for item in body["claims"])
    return previous, requests, claims, updates


def _research_durable_manifest(run, refresh):
    from dataclasses import asdict
    def stable(source):
        record = source.to_record()
        record.pop("retrieved_at")
        return record
    prepared = refresh.proposal.public_record()
    source_manifest = refresh.research.to_record()
    source_manifest["sources"] = [stable(source.manifest) for source in refresh.research.sources]
    return {"schema_version": 1, "kind": "living_brief_dependencies",
        "brief_ref": {key: prepared[key] for key in ("project_id", "artifact_id", "version", "sha256")},
        "dependency_sources": [stable(source) for source in refresh.dependency_sources],
        "claim_dependencies": [asdict(claim) for claim in refresh.dependencies],
        "source_manifest": source_manifest,
        "factual_changes": list(refresh.factual_changes), "interpretation_changes": list(refresh.interpretation_changes),
        "pending_claims": [claim for claim in refresh.affected_claims if claim in refresh.unchanged_claims],
        "claim_verification": "not_performed",
        "observation_started_at": run.db.read_runtime_run_accepted_at(run.session_id, run.run_id),
        "observation_started_at_basis": "command_acceptance; not a completed-read timestamp",
        "retrieval_timestamps": "omitted_from_deterministic_sidecar; resume_performs_new_observed_reads"}


def _research_prepare_bundle(run, request, inputs):
    import hashlib
    import json
    from agent.source_manifest import require
    from hermes_cli.artifact_store import prepare_artifact
    from hermes_cli.domain_research import prepare_living_brief_refresh
    previous, requests, claims, updates = inputs
    refresh = prepare_living_brief_refresh(run, project_id=request.project_id, artifact_id=request.artifact_id,
        parent_version=request.parent_version, request_id=request.request_id, previous_sources=previous,
        requests=requests, claims=claims, updates=updates)
    require(all(source.manifest.availability == "available" and source.manifest.freshness != "stale"
                for source in refresh.research.sources),
            "brief_source_unavailable", "A publishable brief requires all requested current source evidence")
    record = _research_durable_manifest(run, refresh)
    data = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()
    require(len(data) <= 2 * 1024 * 1024, "brief_manifest_limit", "Dependency manifest exceeds its byte bound")
    ref = request.manifest_ref
    sidecar = prepare_artifact(run, project_id=request.project_id,
        request_id="brief-dependencies-" + hashlib.sha256(request.request_id.encode()).hexdigest(),
        content_bytes=data, mime="application/json",
        artifact_id=ref.artifact_id if ref else None, parent_version=ref.version if ref else None,
        expected_head_version=ref.version if ref else None,
        derived_from=[{"artifact_id": refresh.proposal.public_record()["artifact_id"],
                       "version": refresh.proposal.public_record()["version"]}])
    return refresh, sidecar


def _research_publish_bundle(run, request, refresh, sidecar):
    from agent.artifact_commands import finish_artifact_control
    from agent.result_artifacts import ArtifactConflict, artifact_actor
    from hermes_cli.artifact_store import publish_artifact
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    expected = [(refresh.proposal.approval_id, refresh.proposal.approval_digest), (sidecar.approval_id, sidecar.approval_digest)]
    supplied = [(request.brief_approval_id, request.brief_approval_digest), (request.manifest_approval_id, request.manifest_approval_digest)]
    if supplied != expected:
        raise RuntimeStoreError("approval_mismatch", "Both exact brief and dependency manifest approvals are required")
    actor = artifact_actor(run.context)
    for proposal in (refresh.proposal, sidecar):
        decision = run.db.get_effect_approval(proposal.approval_id, actor)
        if decision["status"] == "pending":
            run.db.resolve_effect_approval(proposal.approval_id, actor, holder=run.holder, generation=run.generation,
                                          approval_digest=proposal.approval_digest, choice="once")
    brief = publish_artifact(run, refresh.proposal)
    try:
        manifest = publish_artifact(run, sidecar)
    except (RuntimeStoreError, CapabilityDenied, ArtifactConflict, OSError):
        return _research_response({"project_id": request.project_id, "state": "partial", "brief": brief,
            "manifest": None, "publication_atomic": False, "manifest_status": "not_confirmed_committed",
            "recovery": "inspect_artifact_effects_and_retry_exact_live_command"})
    response = _research_response({"project_id": request.project_id, "state": "published", "brief": brief,
        "manifest": manifest, "publication_atomic": False, "publication_order": ["brief", "dependency_manifest"]})
    finish_artifact_control(run, response)
    return response


def _research_brief_operation(agent, db, request, publish):
    from contextlib import ExitStack
    import hashlib
    import json
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    arguments = request.model_dump(exclude={"schema_version", "session_id", "command_id", "brief_approval_id",
        "brief_approval_digest", "manifest_approval_id", "manifest_approval_digest"})
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    run = begin_artifact_control(agent, request.session_id, request.command_id,
        {"mode": "living_brief", "project_id": request.project_id, "request_id": request.request_id,
         "request_sha256": hashlib.sha256(encoded).hexdigest(), "request_size": len(encoded)})
    with artifact_control_scope(run):
        inputs = _research_brief_inputs(agent, db, request)
        project_ids = {request.project_id, *(source.scope.project_id for source in inputs[0]),
                       *(source.project_id for source in inputs[1])}
        # Same project-store transaction holds grants across both non-atomic
        # artifact effects, so revocation cannot interleave after source reads.
        with ExitStack() as guards:
            access, actor = project_access(run.context), artifact_actor(run.context)
            for project_id in sorted(project_ids):
                guards.enter_context(access.guard(project_id, actor, "read"))
            refresh, sidecar = _research_prepare_bundle(run, request, inputs)
            if publish:
                return _research_publish_bundle(run, request, refresh, sidecar)
            return _research_response({"project_id": request.project_id, "state": "awaiting_approval", "brief": refresh.proposal.public_record(),
                "manifest": sidecar.public_record(), "publication_atomic": False,
                "factual_changes": list(refresh.factual_changes), "interpretation_changes": list(refresh.interpretation_changes),
                "source_manifest": refresh.research.to_record()})


@method("runtime.research.resolve")
@_profile_scoped
def _runtime_research_resolve(rid, params):
    from tui_gateway.contracts.research import ResearchResolveParams
    return _artifact_request(rid, params, ResearchResolveParams, _research_resolve_operation)


@method("runtime.brief.prepare")
@_profile_scoped
def _runtime_brief_prepare(rid, params):
    from tui_gateway.contracts.research import BriefPrepareParams
    return _artifact_request(rid, params, BriefPrepareParams,
                             lambda agent, db, request: _research_brief_operation(agent, db, request, False))


@method("runtime.brief.publish")
@_profile_scoped
def _runtime_brief_publish(rid, params):
    from tui_gateway.contracts.research import BriefPublishParams
    return _artifact_request(rid, params, BriefPublishParams,
                             lambda agent, db, request: _research_brief_operation(agent, db, request, True))


def register(server):
    bind_module(globals(), server)
