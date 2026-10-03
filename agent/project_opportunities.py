"""Finite local opportunity rules over canonical project evidence.

These rules establish that a review condition is present, not that acting has
business value or that any communication, workflow or mission is authorized.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from hermes_state_workflows import canonical, digest, require

_CONTROL_SEAL = object()
_CONTROL_METHODS = frozenset({"runtime.opportunity.discover", "runtime.opportunity.disposition"})


@dataclass(frozen=True)
class OpportunityControl:
    agent: object
    db: object
    context: object
    ui_session_id: str
    method: str
    seal: object

    def assert_current(self, method):
        from agent.runtime_commands import _RUN
        from tools.capability_broker import require_live_policy
        from tui_gateway import server
        transport, session = server._current_session_steer_authority(self.ui_session_id)
        require(self.seal is _CONTROL_SEAL and self.method == method
                and method in _CONTROL_METHODS and server._current_rpc_method.get() == method
                and _RUN.get() is None and transport is not None and session is not None
                and session.get("agent") is self.agent,
                "opportunity_human_control_required", "An exact owned human review control is required")
        require(self.agent.runtime_context == self.context and self.agent._session_db is self.db
                and require_live_policy(require_run=False) == self.context
                and Path(self.db.db_path).parent.resolve() == Path(self.context.profile_home).resolve()
                and self.db.get_session_model_config_value(self.agent.session_id, "agent_identity")
                == self.context.identity.to_record(),
                "identity_mismatch", "Opportunity review owner changed")
        return self


def opportunity_control(agent, db, ui_session_id, method):
    control = OpportunityControl(agent, db, agent.runtime_context, ui_session_id, method, _CONTROL_SEAL)
    return control.assert_current(method)


def _evidence(store, project, identifier, *, version=None, revision=None, sha256=None, detail):
    return {"store": store, "project_id": project, "record_id": identifier,
            "version": version, "revision": revision, "sha256": sha256, "detail": detail}


def _candidate(kind, project, identifier, evidence, material, title, action, benefit, effort, explanation):
    return {"kind": kind, "project_id": project, "source_id": identifier,
            "authorized_project_refs": [project], "title": title, "evidence_refs": [evidence],
            "evidence_digest": digest(material), "suggested_action": action, "benefit": benefit,
            "effort": effort, "confidence": {"level": "deterministic_rule_match", "explanation": explanation},
            "execution_authorized": False}


def _artifact(conn, project, row, now):
    if row is None or row["publication_state"] != "committed":
        return None
    head = conn.execute("SELECT version,revision FROM artifact_heads WHERE artifact_id=?", (row["artifact_id"],)).fetchone()
    invalid = conn.execute("SELECT 1 FROM artifact_derivations WHERE artifact_id=? AND version=? "
                           "AND invalidated_at IS NOT NULL LIMIT 1", (row["artifact_id"], row["version"])).fetchone()
    if not head or head["version"] != row["version"] or not invalid:
        return None
    descriptor = json.loads(row["descriptor_json"])
    identifier, version = row["artifact_id"], row["version"]
    return _candidate("stale_artifact", project, identifier,
        _evidence("runtime_artifact_versions", project, identifier, version=version,
                  revision=head["revision"], sha256=descriptor["sha256"], detail="Current artifact head has an invalidated derivation"),
        {"artifact_id": identifier, "version": version, "sha256": descriptor["sha256"], "derived_validity": "stale"},
        "Review a stale derived artifact",
        {"kind": "review_artifact", "target_id": identifier, "target_version": version,
         "description": "Open this artifact version and review its stale-source status before reusing it"},
        "Makes the recorded stale-source status visible before reuse; correctness is not assessed",
        "One artifact review; any revision or regeneration is a separate decision",
        "The canonical artifact head has a retained invalidation record; source contents were not evaluated")


def _commitment(conn, project, row, now):
    from hermes_state_commitment_sources import timestamp
    if row is None or row["state"] != "waiting":
        return None
    record = json.loads(row["record_json"])
    due = record.get("due_or_check_at")
    if not due or timestamp(due["at"]) > now:
        return None
    identifier = row["commitment_id"]
    material = {key: record.get(key) for key in ("owner", "outcome", "due_or_check_at", "source_refs", "evidence_refs")}
    material.update(commitment_id=identifier, state=row["state"])
    return _candidate("waiting_check", project, identifier,
        _evidence("accepted_commitments", project, identifier, revision=row["revision"],
                  sha256=digest(material), detail=f"Accepted waiting item has a recorded {due['kind']} time of {due['at']} ({due['timezone']})"),
        material, "Review an accepted waiting item",
        {"kind": "review_commitment", "target_id": identifier, "target_version": None,
         "description": "Open this accepted commitment and verify whether its waiting status still applies"},
        "Provides a review point using the explicitly accepted due/check time; no missed promise or urgency is inferred",
        "One commitment-status review; contacting anyone requires a separate choice",
        "The item is explicitly accepted, remains waiting, and its recorded due/check time has been reached")


def _workflow(conn, project, row, now):
    if row is None or row["state"] != "draft" or row["evaluation_ref"] is not None:
        return None
    latest = conn.execute("SELECT MAX(version) FROM workflow_versions WHERE workflow_key=?", (row["workflow_key"],)).fetchone()[0]
    if latest != row["version"]:
        return None
    require(hashlib.sha256(row["definition_json"].encode()).hexdigest() == row["sha256"],
            "workflow_digest_mismatch", "Canonical workflow bytes changed")
    definition = json.loads(row["definition_json"])
    identifier, version = definition["workflow_id"], row["version"]
    return _candidate("workflow_draft", project, identifier,
        _evidence("workflow_versions", project, identifier, version=version, revision=row["revision"],
                  sha256=row["sha256"], detail="Latest workflow version is a draft with no successful evaluation reference"),
        {"workflow_id": identifier, "version": version, "sha256": row["sha256"], "state": "draft"},
        "Review an unevaluated workflow draft",
        {"kind": "evaluate_workflow", "target_id": identifier, "target_version": version,
         "description": "Open this exact workflow draft and choose evaluation cases before any approval or execution"},
        "Identifies an unevaluated draft for review; no quality or performance improvement is predicted",
        "Select and review evaluation cases; runtime and editing effort are not estimated",
        "The canonical latest version is a draft; this rule does not assess whether it is useful")


_RULES = {"stale_artifact": _artifact, "waiting_check": _commitment, "workflow_draft": _workflow}


def scan_evidence(conn, actor, project, limit, now):
    """Only selected-project rows are read. No global project or personal-memory scan."""
    owner = canonical(actor)
    queries = {
        "stale_artifact": ("SELECT v.* FROM runtime_artifact_versions v JOIN artifact_heads h "
            "ON v.artifact_id=h.artifact_id AND v.version=h.version "
            "WHERE v.project_id=? AND v.publication_state='committed' "
            "ORDER BY v.created_at DESC,v.artifact_id LIMIT ?", (project, limit + 1)),
        "waiting_check": ("SELECT * FROM accepted_commitments WHERE project_id=? AND owner_json=? "
            "AND state='waiting' ORDER BY updated_at DESC,commitment_id LIMIT ?", (project, owner, limit + 1)),
        "workflow_draft": ("SELECT v.* FROM workflow_versions v WHERE v.project_id=? AND v.owner_json=? "
            "AND v.version=(SELECT MAX(n.version) FROM workflow_versions n WHERE n.workflow_key=v.workflow_key) "
            "ORDER BY v.created_at DESC,v.workflow_key LIMIT ?", (project, owner, limit + 1)),
    }
    candidates, scanned = [], []
    for kind, (sql, params) in queries.items():
        rows = conn.execute(sql, params).fetchall()
        scanned.append({"project_id": project, "kind": kind, "scanned": min(len(rows), limit), "limit_reached": len(rows) > limit})
        for row in rows[:limit]:
            candidate = _RULES[kind](conn, project, row, now)
            if candidate:
                candidates.append(candidate)
    return candidates, scanned


def current_evidence(conn, actor, candidate, now):
    project, identifier, kind = candidate["project_id"], candidate["source_id"], candidate["kind"]
    owner = canonical(actor)
    queries = {
        "stale_artifact": ("SELECT v.* FROM runtime_artifact_versions v JOIN artifact_heads h "
            "ON v.artifact_id=h.artifact_id AND v.version=h.version WHERE v.project_id=? AND v.artifact_id=?", (project, identifier)),
        "waiting_check": ("SELECT * FROM accepted_commitments WHERE project_id=? AND owner_json=? AND commitment_id=?",
            (project, owner, identifier)),
        "workflow_draft": ("SELECT * FROM workflow_versions WHERE project_id=? AND owner_json=? AND workflow_key=? ORDER BY version DESC LIMIT 1",
            (project, owner, digest({"principal_id": actor["principal_id"], "profile_id": actor["profile_id"],
                                   "project_id": project, "workflow_id": identifier}))),
    }
    sql, params = queries[kind]
    return _RULES[kind](conn, project, conn.execute(sql, params).fetchone(), now)


def changed_reason(previous, current):
    before, after = previous["evidence_refs"][0], current["evidence_refs"][0]
    reasons = {
        "stale_artifact": f"The stale artifact evidence changed from version {before['version']} to version {after['version']}",
        "workflow_draft": f"The latest unevaluated workflow changed from version {before['version']} to version {after['version']}",
        "waiting_check": "The accepted waiting item's owner, outcome, due/check time or attached evidence changed",
    }
    return reasons[current["kind"]]
