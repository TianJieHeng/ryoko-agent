"""Owned specialist selections consumed by the existing admitted turn lifecycle.

Preview is read-only. A selection is immutable input, never an identity grant.
Only the normal submit lease can execute it; uncertain claims cannot relaunch.
"""
from __future__ import annotations

import hashlib
import time

from agent.delegation_contract import DelegationLimits, canonical, digest, require
from agent.identity_lifecycle import agent_runtime_scope, identity_config
from agent.project_context import authorize_project, project_access
from agent.result_artifacts import artifact_actor, read_project_artifact
from agent.specialist_manifest import SpecialistManifest

PREVIEW_SECONDS = 300
_PAYLOAD_KEY = "specialist_handoff"


def _configuration(agent, project_id):
    from tools.capability_broker import require_live_policy
    from tools.agent_policy_gate import authorize_tool
    import tools.delegate_tool  # The existing certified handler owns execution.
    context = agent.runtime_context
    require(require_live_policy(require_run=False) == context,
            "specialist_policy_changed", "Current owning policy is required")
    require(authorize_tool("delegate_task", context=context) is None,
            "specialist_tool_denied", "The parent must have the certified delegation grant")
    authorize_project(context, project_id, "read")
    config = identity_config()
    section = config.get("delegation") or {}
    require(isinstance(section, dict), "delegation_unconfigured", "Configured local delegation is required")
    durable = section.get("durable")
    require(isinstance(durable, dict) and set(durable) == {"enabled", "limits"}
            and durable["enabled"] is True, "delegation_unconfigured", "Configured durable local delegation is required")
    limits = DelegationLimits(**durable["limits"])
    require(limits.max_concurrent_children == 1, "delegation_team_unqualified", "Teams await measured qualification")
    require(getattr(agent, "_runtime_budget_policy", None) is not None,
            "delegation_budget_required", "Finite configured budget is required")
    return config, durable


def _resolve(agent, project_id, specialist_id):
    config, durable = _configuration(agent, project_id)
    manifest, child = SpecialistManifest.resolve(config, specialist_id, parent=agent.runtime_context,
                                                session_id="specialist-preview")
    record = manifest.to_record()
    require(child.policy.memory_backend == "builtin"
            and not child.policy.secret_refs & child.policy.personal_secret_refs
            and not child.policy.mcp_grants.keys() & child.policy.personal_mcp_servers,
            "specialist_grant_denied", "Specialists require isolated built-in memory without personal access")
    # A configured orchestrator remains usable through the ordinary delegation
    # path, but this explicit one-child control cannot admit a hidden team.
    require("delegate_task" not in child.policy.allowed_tools,
            "specialist_team_unqualified", "This control requires a leaf specialist")
    _verify_refs(agent, child, project_id, [record["methods_ref"]], [], methods=record["methods_ref"])
    config_digest = digest({"identity": agent.runtime_context.config_digest,
                            "durable": durable, "manifest": record})
    return manifest, child, config_digest


def _verify_refs(agent, child, project_id, artifacts, evidence, *, methods=None):
    """Both live ACLs and exact source bytes are checked without minting a run."""
    for context in (agent.runtime_context, child):
        with agent_runtime_scope(context):
            authorize_project(context, project_id, "read")
            actor, access = artifact_actor(context), project_access(context)
            for ref in artifacts:
                row = agent._session_db.read_artifact_version(ref["id"], ref["version"], actor, access=access)
                require(row["project_id"] == project_id and row["derived_validity"] == "current"
                        and row["descriptor"]["sha256"] == ref["sha256"],
                        "specialist_artifact_changed", "Exact current project artifact required")
                if ref == methods:
                    require(row["descriptor"]["mime"] in {"text/plain", "text/markdown"}
                            and row["descriptor"]["size"] <= 32768,
                            "specialist_methods_unsupported", "Methods require bounded exact text")
                    data = read_project_artifact(context, agent._session_db, project_id, ref["id"], ref["version"])
                    require(len(data) <= 32768 and hashlib.sha256(data).hexdigest() == ref["sha256"],
                            "specialist_methods_changed", "Exact method bytes changed")
                    data.decode("utf-8")
            for ref in evidence:
                row = agent._session_db.get_evidence_anchor(ref["id"], actor, access=access)
                require(row["project_id"] == project_id and row["source_version"] == str(ref["version"])
                        and row["effective_validity"] == "current"
                        and digest({key: value for key, value in row.items()
                                    if key not in {"effective_validity", "grants_execution"}}) == ref["sha256"],
                        "specialist_evidence_changed", "Exact current project evidence required")


def _descriptor(manifest):
    row = manifest.to_record()
    grants = row["grants"]
    return {"agent_id": row["agent_id"], "responsibility": row["responsibility"],
            "manifest_sha256": manifest.sha256, "methods_ref": row["methods_ref"], "limits": row["limits"],
            "grants": {key: grants[key] for key in ("allowed_tools", "project_grants", "mcp_grants", "memory_backend")}
                      | {"personal_memory_access": False},
            "builtin_memory_namespace": row["builtin_memory_namespace"],
            "output_contract_json": canonical(row["output_contract"])}


def specialist_catalog(agent, project_id):
    config, _ = _configuration(agent, project_id)
    configured = (config.get("delegation") or {}).get("specialists", {})
    require(isinstance(configured, dict) and len(configured) <= 64
            and all(isinstance(key, str) and 0 < len(key) <= 256 for key in configured),
            "invalid_specialist_catalog", "A bounded configured specialist catalog is required")
    rows, unavailable = [], []
    for agent_id in sorted(configured):
        try:
            manifest, _, _ = _resolve(agent, project_id, agent_id)
            rows.append(_descriptor(manifest))
        except (ValueError, PermissionError, OSError) as exc:
            unavailable.append({"agent_id": agent_id, "code": getattr(exc, "code", "specialist_unavailable")})
    return {"specialists": rows, "unavailable": unavailable, "teams_enabled": False, "execution": "local_single_child"}


def _mission(agent, project_id):
    context = agent.runtime_context
    mission = agent._session_db.get_mission(agent.session_id, artifact_actor(context), access=project_access(context))
    if mission is not None:
        require(mission["project_id"] == project_id and mission["state"] in {"ready", "working"},
                "specialist_mission_scope", "The handoff must use the current ready or working mission project")
    return mission


def specialist_preview(agent, task):
    from tui_gateway.contracts.specialists import SpecialistTask
    task = SpecialistTask.model_validate(task).model_dump()
    require(task["objective"].strip(), "invalid_handoff", "An explicit objective is required")
    manifest, child, config_digest = _resolve(agent, task["project_id"], task["specialist_id"])
    _verify_refs(agent, child, task["project_id"], task["artifacts"], task["evidence"])
    mission = _mission(agent, task["project_id"])
    selection = {**task, "manifest_sha256": manifest.sha256, "config_digest": config_digest,
                 "parent_policy_digest": agent.runtime_context.policy.digest,
                 "mission_id": mission["mission_id"] if mission else None,
                 "mission_revision": mission["revision"] if mission else None,
                 "expires_at": time.time() + PREVIEW_SECONDS}
    return {"specialist": _descriptor(manifest), "selection": selection, "preview_sha256": digest(selection),
            "runtime_revision": agent._session_db.read_runtime_snapshot(agent.session_id)["revision"]}


def validate_specialist_payload(payload):
    """The generic public command DTO does not accept this host-built extension."""
    from tui_gateway.contracts.specialists import SpecialistSelection
    require(isinstance(payload, dict) and set(payload) == {"text", _PAYLOAD_KEY},
            "invalid_handoff", "Exact specialist submit payload required")
    handoff = payload[_PAYLOAD_KEY]
    require(isinstance(handoff, dict) and set(handoff) == {"selection", "preview_sha256"},
            "invalid_handoff", "Exact specialist preview required")
    selection = SpecialistSelection.model_validate(handoff["selection"]).model_dump()
    require(selection == handoff["selection"] and payload["text"] == selection["objective"]
            and selection["objective"].strip() and digest(selection) == handoff["preview_sha256"],
            "specialist_preview_changed", "The exact prepared objective and selection are required")
    return selection


def validate_specialist_selection(agent, payload, *, mission_started=False):
    selection = validate_specialist_payload(payload)
    require(time.time() < selection["expires_at"] <= time.time() + PREVIEW_SECONDS + 1,
            "specialist_preview_expired", "Prepare a fresh specialist selection")
    manifest, child, config_digest = _resolve(agent, selection["project_id"], selection["specialist_id"])
    require(manifest.sha256 == selection["manifest_sha256"] and config_digest == selection["config_digest"]
            and agent.runtime_context.policy.digest == selection["parent_policy_digest"],
            "specialist_policy_changed", "Prepared specialist policy or methods changed")
    _verify_refs(agent, child, selection["project_id"], selection["artifacts"], selection["evidence"])
    if mission_started:
        from agent.runtime_commands import assert_runtime_dispatch
        from agent.mission_runtime import assert_mission_project_scope
        assert_mission_project_scope(assert_runtime_dispatch(agent), selection["project_id"])
    else:
        mission = _mission(agent, selection["project_id"])
        require((mission["mission_id"] if mission else None) == selection["mission_id"]
                and (mission["revision"] if mission else None) == selection["mission_revision"],
                "specialist_mission_changed", "Prepared mission scope changed")
    return selection


def prepare_specialist_turn(agent, command):
    if command is not None and _PAYLOAD_KEY in command["command"]["payload"]:
        selection = validate_specialist_selection(agent, command["command"]["payload"])
        mission = _mission(agent, selection["project_id"])
        require((mission["mission_id"] if mission else None) == selection["mission_id"]
                and (mission["revision"] if mission else None) == selection["mission_revision"],
                "specialist_mission_changed", "Prepared mission scope changed")
        return (mission["mission_id"], mission["revision"] + int(mission["state"] == "ready")) if mission else (None, None)
    return None


def _completion(selection, child=None):
    result = (child or {}).get("completion") or {}
    state = (child or {}).get("state", "blocked")
    state = state if state in {"completed", "failed", "cancelled", "blocked"} else "unknown"
    if result.get("exit_reason") == "interrupted":
        state = "cancelled"
    elif result.get("truncated") is True:
        state = "failed"
    summary = result.get("summary", "")
    summary = summary if isinstance(summary, str) else ""
    if result.get("truncated") is True:
        summary = "Execution reached its configured iteration limit. Partial result:\n" + summary
    return {"specialist_id": selection["specialist_id"], "manifest_sha256": selection["manifest_sha256"],
            "project_id": selection["project_id"],
            "child_id": child["handoff"]["child_id"] if child else None,
            "handoff_sha256": child["handoff_sha256"] if child else None,
            "state": state, "summary": summary[:32768], "summary_truncated": len(summary) > 32768,
            "schema_valid": result.get("schema_valid") if type(result.get("schema_valid")) is bool else None,
            "parent_review_required": True, "execution_resumed": False}


def _owned_child(agent, run_id, selection):
    from hermes_state_delegations import DelegationRegistry
    with agent._session_db._runtime_read() as conn:
        rows = conn.execute("SELECT child_id FROM delegation_handoffs WHERE parent_run_id=? LIMIT 2", (run_id,)).fetchall()
    require(len(rows) <= 1, "specialist_multiple_children", "A specialist control can launch only one child")
    if not rows:
        return None
    child = DelegationRegistry(agent.runtime_context, agent._session_db).read(rows[0]["child_id"])
    require(child["handoff"]["specialist"] == {"agent_id": selection["specialist_id"], "sha256": selection["manifest_sha256"]},
            "specialist_handoff_changed", "The durable child differs from the selected specialist")
    return child


def execute_specialist_turn(run, command, *, mission_witness=None, persist_user_message=None,
                           persist_user_timestamp=None, persist_user_platform_id=None,
                           persist_user_display_kind=None, persist_user_display_metadata=None):
    """Return None for ordinary submits; never enter a second parent model loop."""
    if command is None or _PAYLOAD_KEY not in command["command"]["payload"]:
        return None
    from agent.runtime_commands import assert_runtime_dispatch, invoke_runtime_operation
    from tools.capability_broker import invoke_tool_dispatch
    from tools.delegate_tool import delegate_task
    from hermes_state_delegations import DelegationRegistry
    selection = validate_specialist_selection(run.agent, command["command"]["payload"], mission_started=True)
    mission = _mission(run.agent, selection["project_id"])
    require(mission_witness == ((mission["mission_id"], mission["revision"]) if mission else (None, None)),
            "specialist_mission_changed", "The admitted handoff's mission changed before execution")
    task = {"goal": selection["objective"], "specialist": selection["specialist_id"],
            **{key: selection[key] for key in ("artifacts", "evidence", "constraints")}}
    arguments = {"tasks": [task], "background": False}

    def launch():
        assert_runtime_dispatch(run.agent)
        invoke_tool_dispatch("delegate_task", arguments,
            lambda: delegate_task(tasks=[task], background=False, parent_agent=run.agent))
        child = _owned_child(run.agent, run.run_id, selection)
        return _completion(selection, child)

    completion = invoke_runtime_operation("tool", launch, agent=run.agent, name="delegate_task")
    state = completion["state"]
    cancelled = state == "cancelled" or bool(run.task_scope and run.task_scope.cancelled.is_set())
    response = (f"Specialist {selection['specialist_id']}: {'cancelled' if cancelled else state}. Parent review is required."
                + ("\n\n" + completion["summary"] if completion["summary"] else ""))
    # The canonical conversation store owns the two new rows, including any
    # submit-time row supplied by the ordinary gateway input pipeline.
    from agent.turn_context import _stage_turn_user_message
    user, _ = _stage_turn_user_message(run.agent, selection["objective"], persist_user_message,
        persist_user_timestamp, persist_user_platform_id, persist_user_display_kind, persist_user_display_metadata)
    user = dict(user)
    if persist_user_message is not None:
        user["content"] = persist_user_message
    rows = [user, {"role": "assistant", "content": response}]
    run.db.append_messages_batch(run.session_id, rows, turn_lease_holder=run.holder)
    run.agent._pending_cli_user_message = None
    messages = run.db.get_messages_as_conversation(run.session_id, include_row_ids=True)
    run.agent._session_messages = messages
    if completion["child_id"] is not None and state in {"completed", "failed", "cancelled"}:
        registry = DelegationRegistry(run.context, run.db)
        claim = registry.claim_delivery(run, completion["child_id"])
        if claim is not None:
            registry.finish_delivery(run, completion["child_id"], claim["claim_id"])
    return {"final_response": response, "messages": messages, "completed": state == "completed" and not cancelled,
            "failed": state in {"failed", "blocked", "unknown"}, "interrupted": cancelled,
            "runtime_status": "specialist_review_required", "api_calls": 0}


def specialist_status(agent, command_id):
    from agent.runtime_commands import read_command_state
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == agent.runtime_context,
            "specialist_policy_changed", "Current owning policy is required")
    record = read_command_state(agent, command_id)
    require(record is not None and record["command"]["operation"] == "submit"
            and _PAYLOAD_KEY in record["command"]["payload"], "specialist_command_not_found", "Owned specialist command not found")
    selection = validate_specialist_payload(record["command"]["payload"])
    authorize_project(agent.runtime_context, selection["project_id"], "read")
    run_id, status = record["receipt"]["run_id"], record["status"]
    child = _owned_child(agent, run_id, selection)
    lease = agent._session_db.get_session_turn_lease(agent.session_id)
    live = (lease is not None and lease["expires_at"] > time.time()
            and lease["holder"] == record.get("claimed_holder")
            and lease["generation"] == record.get("claimed_generation")
            and getattr(getattr(agent, "_active_runtime_run", None), "run_id", None) == run_id)
    outcome = {"accepted": "pending", "claimed": "running" if live else "unknown"}.get(status, status)
    return {"command_id": record["receipt"]["command_id"], "run_id": run_id,
            "specialist_id": selection["specialist_id"], "manifest_sha256": selection["manifest_sha256"],
            "project_id": selection["project_id"], "status": status, "outcome": outcome,
            "completion": _completion(selection, child) if child and child["completion"] is not None else None,
            "execution_resumed": False}
