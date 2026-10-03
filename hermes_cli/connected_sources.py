"""Read once, review exact bytes, then retain through canonical artifact effects.

The bounded pending bundle belongs to one live agent/control generation. Loss of
that volatile bundle is explicitly unrecoverable without a new user refresh;
publication never rereads the external service or accepts client source bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
import uuid

from agent.connected_sources import ConnectedSourceError, canonical, require, selection, sha
from agent.artifact_commands import assert_artifact_dispatch, finish_artifact_control
from agent.project_context import authorize_project, project_access
from agent.result_artifacts import artifact_actor
from hermes_cli.artifact_store import prepare_artifact, publish_artifact


@dataclass(frozen=True)
class _Preparation:
    preparation_id: str
    run_id: str
    holder: str
    generation: int
    project_id: str
    request_id: str
    selected: dict
    original: object
    projection: object
    metadata: dict


def _cache(run):
    rows = getattr(run.agent, "_connected_source_preparations", {})
    rows = {key: value for key, value in rows.items() if value.original.expires_at > time.time()}
    run.agent._connected_source_preparations = rows
    return rows


def _response(project_id, selected, *, state, metadata=None, original=None, projection=None, preparation_id=None):
    metadata = metadata or {"coverage": "unavailable", "errors": ["source_unavailable"]}
    return {"project_id": project_id, "state": state, "preparation_id": preparation_id,
        "source_kind": selected["kind"], "account_id": selected["account_id"],
        "observed_at": metadata.get("retrieved_at"), "fresh_until": metadata.get("fresh_until"),
        "coverage": metadata["coverage"], "errors": metadata["errors"],
        "original": original.public_record() if original else None,
        "projection": projection.public_record() if projection else None,
        "record_json": canonical(metadata).decode()}


def _ready(bundle):
    return _response(bundle.project_id, bundle.selected, state="awaiting_approval", metadata=bundle.metadata,
        original=bundle.original, projection=bundle.projection, preparation_id=bundle.preparation_id)


def preview_source(run, project_id, preparation_id, part, *, offset=0, limit=65536):
    """Return only the bound prepared bytes; no publication, renewal or network."""
    from hermes_cli.artifact_store import _chunk
    from tools.capability_broker import project_artifact_action, recover_approval_preview
    assert_artifact_dispatch(run)
    require(part in {"original", "projection"}, "source_preview_part_invalid")
    bundle = _cache(run).get(preparation_id)
    require(bundle is not None, "source_preparation_lost")
    require(bundle.run_id == run.run_id and bundle.holder == run.holder and bundle.generation == run.generation
            and bundle.project_id == project_id, "source_preparation_mismatch")
    proposal = bundle.original if part == "original" else bundle.projection
    require(proposal is not None, "source_projection_unavailable")
    actor, access = artifact_actor(run.context), project_access(run.context)
    with access.guard(project_id, actor, "read"):
        scope = proposal.scope
        descriptor = scope["descriptor"]
        reservation = run.db.read_artifact_reservation(descriptor["artifact_id"], descriptor["version"], actor, access=access)
        require(reservation["session_id"] == run.session_id and reservation["run_id"] == run.run_id
                and reservation["command_id"] == run.command_id and reservation["project_id"] == project_id,
                "source_preparation_mismatch")
        preview = recover_approval_preview(proposal.approval_id, project_artifact_action(scope))
        approval = run.db.get_effect_approval(proposal.approval_id, actor)
        require(preview.approval_digest == proposal.approval_digest
                and approval["status"] in {"pending", "approved", "consumed"}, "source_approval_mismatch")
        data = proposal.content_bytes
        require(isinstance(data, bytes) and len(data) == descriptor["size"] and sha(data) == descriptor["sha256"],
                "source_preparation_bytes_mismatch")
        result = _chunk(project_id, descriptor, data, offset, limit)
        assert_artifact_dispatch(run)
        return {**result, "preparation_id": preparation_id, "part": part,
                "approval_id": proposal.approval_id, "approval_digest": proposal.approval_digest}


def _previous(run, project, artifact_id):
    from hermes_state_runtime import RuntimeStoreError
    actor, access = artifact_actor(run.context), project_access(run.context)
    try:
        head = run.db.get_artifact_head(artifact_id, actor, access=access)
    except RuntimeStoreError as exc:
        if exc.code != "artifact_not_found":
            raise
        return {}
    if head is None:
        return {}
    require(head["project_id"] == project, "source_scope_mismatch")
    return {"parent_version": head["version"], "expected_head_version": head["version"]}


def prepare_source(run, project_id, request_id, selected, *, _http_transport=None):
    from tools.connectors.source_reads import read_source
    assert_artifact_dispatch(run)
    selected = selection(selected)
    authorize_project(run.context, project_id, "write")
    rows = _cache(run)
    for bundle in rows.values():
        if bundle.run_id == run.run_id:
            require(bundle.holder == run.holder and bundle.generation == run.generation
                    and bundle.project_id == project_id and bundle.request_id == request_id
                    and bundle.selected == selected, "source_preparation_mismatch")
            return _ready(bundle)
    # A claimed command must never silently refetch after process loss or a
    # partial preparation. The marker records only bounded IDs, never source text.
    marker = "connected-source-read-" + sha(canonical({"run_id": run.run_id, "request_id": request_id}))
    prior = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
    # Pending source bytes are not a second durable store. Retain the attempted
    # command in the existing run timeline, so restart cannot replay the read.
    require(len(rows) < 4, "source_preparation_limit")
    def start_read(conn):
        run.db._runtime_fence_on_conn(conn, run.session_id, run.holder, run.generation)
        attempted = conn.execute("SELECT 1 FROM runtime_events WHERE session_id=? AND operation_id=?",
                                 (run.session_id, marker)).fetchone()
        require(attempted is None, "source_preparation_lost")
        run.db._append_runtime_event_on_conn(conn, run.session_id, "decision.observed",
            {"kind": "connected_source_read_started", "project_id": project_id,
             "selection_sha256": sha(canonical(selected)), "accepted_at": prior},
            run.generation, operation_id=marker, run_id=run.run_id)
    run.db._execute_write(start_read)
    try:
        original, projection = read_source(run, project_id, selected, _http_transport=_http_transport)
    except ConnectedSourceError as exc:
        response = _response(project_id, selected, state="unavailable", metadata={"coverage": "unavailable", "errors": [exc.code]})
        finish_artifact_control(run, response, status="blocked")
        return response
    assert_artifact_dispatch(run)
    actor = artifact_actor(run.context)
    source_key = sha(canonical({"actor": actor, "project_id": project_id, "selection": selected}))
    aid = "connected_original_" + source_key
    request_key = sha(request_id.encode())
    original_proposal = prepare_artifact(run, project_id=project_id, request_id="source-original-" + request_key,
        artifact_id=aid, content_bytes=canonical(original), mime="application/json",
        provenance={"kind": "source", "source_ref": "connector:" + source_key}, **_previous(run, project_id, aid))
    projection_proposal = None
    if projection is not None:
        original_ref = {key: original_proposal.public_record()[key] for key in ("artifact_id", "version")}
        pid = "connected_projection_" + source_key
        projection_proposal = prepare_artifact(run, project_id=project_id, request_id="source-projection-" + request_key,
            artifact_id=pid, content_bytes=canonical(projection), mime="application/json", derived_from=[original_ref],
            provenance={"kind": "source", "source_ref": "connector:" + source_key}, **_previous(run, project_id, pid))
    metadata = {key: original[key] for key in ("source_kind", "selection", "scope", "retrieved_at", "fresh_until",
        "provider_version", "provider_version_basis", "tool_schema_sha256", "payload_sha256", "representation",
        "coverage", "errors", "execution_authority", "claim_verification", "attachments", "account_binding",
        "gateway_http_attempts", "upstream_retry_count", "cost_tracking", "connector", "tool",
        "gateway_recipient_id", "gateway_endpoint_sha256", "arguments_sha256")}
    metadata.update(projection_kind="inbox_snapshot" if selected["kind"] == "gmail_thread" else "calendar_availability_snapshot",
        publication_atomic=False, live_qualification="pending", attachment_bytes_fetched=False,
        recipient_identity_status="explicit_ids_not_person_identity_verification")
    bundle = _Preparation("source-preparation-" + uuid.uuid4().hex, run.run_id, run.holder, run.generation,
        project_id, request_id, selected, original_proposal, projection_proposal, metadata)
    rows[bundle.preparation_id] = bundle
    return _ready(bundle)


def publish_source(run, project_id, preparation_id, approvals):
    assert_artifact_dispatch(run)
    bundle = _cache(run).get(preparation_id)
    require(bundle is not None, "source_preparation_lost")
    require(bundle.run_id == run.run_id and bundle.holder == run.holder and bundle.generation == run.generation
            and bundle.project_id == project_id, "source_preparation_mismatch")
    proposals = [bundle.original] + ([bundle.projection] if bundle.projection else [])
    expected = [(item.approval_id, item.approval_digest) for item in proposals]
    require(approvals == expected, "source_approval_mismatch")
    actor, access = artifact_actor(run.context), project_access(run.context)
    from tools.connectors.gateway.names import format_connector_name
    from agent.connected_sources import TOOLS
    require(run.context.policy.allows_tool(format_connector_name(*TOOLS[bundle.selected["kind"]])), "source_tool_not_granted")
    published = []
    with access.guard(project_id, actor, "write"):
        for proposal in proposals:
            decision = run.db.get_effect_approval(proposal.approval_id, actor)
            if decision["status"] == "pending":
                run.db.resolve_effect_approval(proposal.approval_id, actor, holder=run.holder, generation=run.generation,
                    approval_digest=proposal.approval_digest, choice="once")
            try:
                published.append(publish_artifact(run, proposal))
            except (OSError, ValueError, PermissionError):
                if not published:
                    raise
                partial = dict(bundle.metadata)
                partial.update(original_ref={key: published[0][key] for key in ("artifact_id", "version", "sha256")},
                    projection_ref=None, publication_status="projection_not_confirmed",
                    recovery="retry_exact_publish_without_refetch")
                return _response(project_id, bundle.selected, state="partial", metadata=partial,
                    original=bundle.original, projection=bundle.projection, preparation_id=preparation_id)
    metadata = dict(bundle.metadata)
    metadata["original_ref"] = {key: published[0][key] for key in ("artifact_id", "version", "sha256")}
    metadata["projection_ref"] = ({key: published[1][key] for key in ("artifact_id", "version", "sha256")}
                                  if len(published) == 2 else None)
    metadata["research_request"] = {"source_id": published[0]["artifact_id"], "source_type": "project_artifact",
        "project_id": project_id, "version": published[0]["version"], "sha256": published[0]["sha256"],
        "authority": "source_claim", "fresh_until": metadata["fresh_until"]}
    metadata["projection_research_request"] = ({**metadata["research_request"],
        "source_id": published[1]["artifact_id"], "version": published[1]["version"], "sha256": published[1]["sha256"]}
        if len(published) == 2 else None)
    response = _response(project_id, bundle.selected, state="published", metadata=metadata,
        original=bundle.original, projection=bundle.projection, preparation_id=preparation_id)
    finish_artifact_control(run, response)
    _cache(run).pop(preparation_id, None)
    return response
