"""Owned controls for exact source reads and separately approved retention."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _sources_operation(agent, db, request, *, publish=False, preview=False):
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope
    from agent.connected_sources import ConnectedSourceError, canonical, selection, sha, require
    from hermes_cli.connected_sources import prepare_source, publish_source, preview_source
    from hermes_state_runtime import RuntimeStoreError
    try:
        if publish or preview:
            record = db.read_runtime_command(agent.runtime_context.identity.session_id, request.command_id)
            require(record is not None and record["command"]["operation"] == "artifact", "source_preparation_lost")
            payload = record["command"]["payload"]
            require(payload.get("mode") == "connected_source" and payload.get("project_id") == request.project_id,
                    "source_preparation_mismatch")
        else:
            selected = selection(request.selection.model_dump())
            payload = {"mode": "connected_source", "project_id": request.project_id,
                       "request_id": request.request_id, "selection_sha256": sha(canonical(selected))}
        run = begin_artifact_control(agent, request.session_id, request.command_id, payload)
        with artifact_control_scope(run):
            if preview:
                return preview_source(run, request.project_id, request.preparation_id, request.part,
                                      offset=request.offset, limit=request.limit)
            if not publish:
                return prepare_source(run, request.project_id, request.request_id, selected)
            approvals = [(request.original_approval_id, request.original_approval_digest)]
            if request.projection_approval_id is not None or request.projection_approval_digest is not None:
                approvals.append((request.projection_approval_id, request.projection_approval_digest))
            return publish_source(run, request.project_id, request.preparation_id, approvals)
    except ConnectedSourceError as exc:
        raise RuntimeStoreError(exc.code, "The exact connected source operation is unavailable") from exc


@method("runtime.sources.prepare")
@_profile_scoped
def _runtime_sources_prepare(rid, params):
    from .contracts.connected_sources import ConnectedSourcePrepareParams
    return _artifact_request(rid, params, ConnectedSourcePrepareParams, _sources_operation)


@method("runtime.sources.publish")
@_profile_scoped
def _runtime_sources_publish(rid, params):
    from .contracts.connected_sources import ConnectedSourcePublishParams
    return _artifact_request(rid, params, ConnectedSourcePublishParams,
        lambda agent, db, request: _sources_operation(agent, db, request, publish=True))


@method("runtime.sources.preview")
@_profile_scoped
def _runtime_sources_preview(rid, params):
    from .contracts.connected_sources import ConnectedSourcePreviewParams
    return _artifact_request(rid, params, ConnectedSourcePreviewParams,
        lambda agent, db, request: _sources_operation(agent, db, request, preview=True))


def register(server):
    bind_module(globals(), server)
