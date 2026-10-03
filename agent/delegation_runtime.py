"""Strict delegate_task consumer: narrowed identities, exact inputs and durable launch.

Legacy profiles retain their existing behavior. Strict profiles opt in to finite
local delegation; neither a display name nor model-supplied attestation selects a
credential, memory namespace, executor or budget.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
import uuid

from agent.budget_account import actor_for
from agent.delegation_contract import DelegationLimits, ImmutableHandoff, require, digest, sha, text
from agent.runtime_context import AgentContext
from hermes_state_delegations import DelegationRegistry


@dataclass(frozen=True)
class PreparedDelegation:
    parent_run: object
    registry: DelegationRegistry
    ticket: object
    handoff: ImmutableHandoff
    workspace: object


def strict_preflight(parent, tasks):
    if not isinstance(getattr(parent, "runtime_context", None), AgentContext):
        return None
    from agent.runtime_commands import assert_runtime_dispatch
    from agent.identity_lifecycle import identity_config
    from tools.capability_broker import require_live_policy
    run = assert_runtime_dispatch()
    require(run.agent is parent and require_live_policy() == run.context,
            "delegation_owner_required", "Delegation requires the live parent run")
    raw = (identity_config().get("delegation") or {}).get("durable")
    require(isinstance(raw, dict) and set(raw) == {"enabled", "limits"} and raw["enabled"] is True,
            "delegation_unconfigured", "Strict delegation requires the configured durable local adapter")
    limits = DelegationLimits(**raw["limits"])
    # No fabricated performance certificate: team qualification remains a release
    # gate until approved infrastructure can record paired task outcomes.
    require(limits.max_concurrent_children == 1 and len(tasks) == 1,
            "delegation_team_unqualified", "Parallel teams await measured benefit qualification; use one specialist")
    require(run.budget is not None, "delegation_budget_required", "Finite inherited budget is required")
    run.budget.check()
    for task in tasks:
        require(not task.get("images"), "delegation_media_unsupported", "This local delegation adapter is text-only")
        require(not set(task) - {"goal", "context", "role", "output_schema", "images", "group", "specialist", "artifacts", "evidence", "constraints"},
                "invalid_handoff", "Task cannot carry identity, executor, credential or budget overrides")
        require(task.get("specialist") is None or isinstance(task["specialist"], str),
                "invalid_specialist", "Configured specialist ID must be a string")
        for key in ("artifacts", "evidence"):
            refs = task.get(key, [])
            require(isinstance(refs, list) and len(refs) <= 64, "invalid_handoff", "Bounded exact references required")
            for ref in refs:
                require(isinstance(ref, dict) and set(ref) == {"id", "version", "sha256"}, "invalid_handoff", "Exact ID/version/digest required")
                text(ref["id"], "reference id")
                sha(ref["sha256"])
                require(type(ref["version"]) is int and ref["version"] > 0, "invalid_handoff", "Exact positive version required")
        require(isinstance(task.get("constraints", []), list), "invalid_handoff", "Constraints must be a bounded list")
    return run, limits


def _verify_references(run, child, task):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.project_context import project_access
    from agent.mission_runtime import assert_mission_project_scope
    artifacts, evidence = task.get("artifacts", []), task.get("evidence", [])
    for context in (run.context, child.runtime_context):
        with agent_runtime_scope(context):
            access, actor = project_access(context), actor_for(context)
            for ref in artifacts:
                row = run.db.read_artifact_version(ref["id"], ref["version"], actor, access=access)
                require(row["descriptor"]["sha256"] == ref["sha256"] and row["derived_validity"] == "current",
                        "delegation_artifact_changed", "Accepted artifact digest/version must remain current")
                if context is run.context:
                    assert_mission_project_scope(run, row["project_id"])
            for ref in evidence:
                row = run.db.get_evidence_anchor(ref["id"], actor, access=access)
                require(row["source_version"] == str(ref["version"]) and digest({key: value for key, value in row.items()
                        if key not in {"effective_validity", "grants_execution"}}) == ref["sha256"]
                        and row["effective_validity"] == "current",
                        "delegation_evidence_changed", "Exact current evidence required")
                if context is run.context:
                    assert_mission_project_scope(run, row["project_id"])


def _stage_inputs(run, child, task, root):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.project_context import project_access
    from agent.result_artifacts import read_project_artifact
    from tools.workspace_manifest import StagedWorkspace
    import tempfile
    # Source bytes are materialized only after BOTH parent and child ACL checks.
    with tempfile.TemporaryDirectory(prefix="input-", dir=root) as temporary:
        names = []
        with agent_runtime_scope(child.runtime_context):
            access, actor = project_access(child.runtime_context), actor_for(child.runtime_context)
            for index, ref in enumerate(task.get("artifacts", [])):
                row = run.db.read_artifact_version(ref["id"], ref["version"], actor, access=access)
                data = read_project_artifact(child.runtime_context, run.db, row["project_id"], ref["id"], ref["version"])
                require(hashlib.sha256(data).hexdigest() == ref["sha256"], "delegation_artifact_changed", "Materialized source differs")
                name = f"artifact-{index}"
                Path(temporary, name).write_bytes(data)
                names.append(name)
        return StagedWorkspace.create(root / uuid.uuid4().hex, parent_root=Path(temporary),
            input_paths=tuple(names), base_revision=digest(task.get("artifacts", [])))


def prepare_batch(batch):
    checked = strict_preflight(batch.parent_agent, batch.task_list)
    if checked is None:
        return
    from agent.executor_capabilities import authenticate_local_executor
    run, limits = checked
    registry = DelegationRegistry(run.context, run.db)
    handoffs, workspaces, reservations = [], [], []
    root = Path(run.context.profile_home) / "delegation-workspaces"
    root.mkdir(mode=0o700, exist_ok=True)
    from tools.workspace_manifest import _open_root
    import os
    fd = _open_root(root)
    os.close(fd)
    try:
        with run.db._runtime_read() as conn:
            ancestor = conn.execute("SELECT handoff_json FROM delegation_handoffs WHERE child_session_id=?", (run.session_id,)).fetchone()
        import json
        depth = json.loads(ancestor[0])["depth"] + 1 if ancestor else 1
        for _index, task, child in batch.children:
            context = child.runtime_context
            specialist = getattr(child, "_specialist_manifest", None)
            if specialist is not None:
                manifest = specialist.to_record()
                require(depth <= manifest["limits"]["max_depth"], "specialist_depth_limit", "Specialist depth ceiling exceeded")
                refs = list(task.get("artifacts", []))
                if manifest["methods_ref"] not in refs:
                    refs.append(manifest["methods_ref"])
                task = {**task, "artifacts": refs}
            require(isinstance(context, AgentContext) and context.policy.allowed_tools <= run.context.policy.allowed_tools
                    and context.policy.secret_refs <= run.context.policy.secret_refs
                    and context.policy.project_grants <= run.context.policy.project_grants
                    and not context.policy.secret_refs & context.policy.personal_secret_refs
                    and not context.policy.mcp_grants.keys() & context.policy.personal_mcp_servers,
                    "delegation_grant_expansion", "Child cannot inherit personal access or expand parent grants")
            executor = authenticate_local_executor(run.context, isolated_python="execute_code" in context.policy.allowed_tools)
            reservation = run.budget.reserve({"wall_ms": 1})
            reservations.append(reservation)
            _verify_references(run, child, task)
            workspace = _stage_inputs(run, child, task, root)
            workspaces.append(workspace)
            specialist = getattr(child, "_specialist_manifest", None)
            handoff = ImmutableHandoff({"schema_version": 1, "child_id": uuid.uuid4().hex,
                "parent_run_id": run.run_id, "parent_session_id": run.session_id, "root_run_id": run.budget.root_id,
                "objective": task["goal"], "constraints": task.get("constraints", []) + ([task["context"]] if task.get("context") else []),
                "deadline": run.budget.deadline, "artifacts": task.get("artifacts", []), "evidence": task.get("evidence", []),
                "budget": {"account_id": run.budget.account_id, "root_id": run.budget.root_id,
                    "reservation_id": reservation, "policy_digest": hashlib.sha256(run.budget.policy.snapshot.encode()).hexdigest()},
                "parent_identity": run.context.identity.to_record(), "child_identity": context.identity.to_record(),
                "grants": context.policy.to_record(), "workspace": {"root": str(workspace.root), "manifest_digest": workspace.manifest.digest},
                "executor": executor.reference(), "depth": depth,
                "specialist": {"agent_id": context.identity.agent_id, "sha256": specialist.sha256} if specialist else None,
                "output_contract": getattr(child, "_delegate_output_schema", None)})
            _verify_references(run, child, handoff.to_record())
            handoffs.append(handoff)
        tickets = registry.admit(run, handoffs, limits)
    except BaseException:
        import shutil
        for workspace in workspaces:
            shutil.rmtree(workspace.root)
        for reservation in reservations:
            run.budget.db.release_budget_reservation(run.budget.account_id, run.budget.actor, reservation, **run.budget.fence)
        raise
    for (_, _, child), ticket, handoff, workspace in zip(batch.children, tickets, handoffs, workspaces):
        child._durable_delegation = PreparedDelegation(run, registry, ticket, handoff, workspace)


def run_child(batch, index, task, child, runner):
    if not isinstance(getattr(child, "runtime_context", None), AgentContext):
        return runner()
    prepared = getattr(child, "_durable_delegation", None)
    require(isinstance(prepared, PreparedDelegation), "delegation_handoff_required", "Strict child execution requires an admitted immutable handoff")
    from agent.executor_capabilities import require_executor
    from agent.identity_lifecycle import agent_runtime_scope, identity_config
    require(child.runtime_context.identity.to_record() == prepared.handoff.to_record()["child_identity"],
            "delegation_identity_mismatch", "The running child differs from its immutable handoff")
    specialist = getattr(child, "_specialist_manifest", None)
    if specialist is not None:
        from agent.specialist_manifest import SpecialistManifest
        current, context = SpecialistManifest.resolve(identity_config(), child.runtime_context.identity.agent_id,
            parent=prepared.parent_run.context, session_id=child.runtime_context.identity.session_id)
        require(current == specialist and context == child.runtime_context, "specialist_policy_changed", "Specialist methods/limits changed before dispatch")
    with agent_runtime_scope(prepared.parent_run.context):
        require_executor(prepared.parent_run.context, prepared.handoff.to_record()["executor"])
        _verify_references(prepared.parent_run, child, prepared.handoff.to_record())
        prepared.workspace.verify_inputs()
        prepared.registry.start(prepared.parent_run, prepared.ticket)
        prepared.parent_run.budget.dispatched(prepared.handoff.to_record()["budget"]["reservation_id"])
        prepared.parent_run.budget.settle(prepared.handoff.to_record()["budget"]["reservation_id"], {"wall_ms": 1})
    try:
        result = runner()
    except BaseException as exc:
        result = {"task_index": index, "status": "error", "error": type(exc).__name__}
        prepared.registry.complete(prepared.ticket, result)
        raise
    if result.get("schema_valid") is False:
        result = {**result, "status": "failed", "failure_reason": "output_contract_invalid"}
    prepared.registry.complete(prepared.ticket, result)
    result = {**result, "handoff_sha256": prepared.handoff.sha256,
              "durable_child_id": prepared.ticket.child_id, "execution_resumed": False,
              "parent_delivery": "pending"}
    # The provider descendants already settle their own physical reservations.
    # This reservation accounts only for finite host admission overhead.
    return result


def attach_async(batch, delegation_id):
    prepared = [getattr(child, "_durable_delegation", None) for _, _, child in batch.children]
    prepared = [item for item in prepared if isinstance(item, PreparedDelegation)]
    if not prepared:
        return None
    prepared[0].registry.link_async([item.ticket for item in prepared], delegation_id)
    return [{"child_id": item.ticket.child_id, "sha256": item.handoff.sha256} for item in prepared]
