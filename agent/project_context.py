"""Live project authority: immutable agent ceiling intersected with project grants.

Project references are filing metadata, not permission to read another project or
personal memory. User controls are minted only by the owned local RPC transport;
model-side mutation adapters cannot bootstrap or broaden their own grants.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from contextvars import ContextVar
import json
import threading

from agent.runtime_context import AgentContext
from hermes_cli import projects_db as pdb
from hermes_cli.sqlite_util import write_txn
from tools.capability_broker import CapabilityDenied, require_live_policy

_PERMISSIONS = frozenset({"read", "write", "share"})
_REFERENCE_FIELDS = {
    "source_refs": {"capture_id"},
    "canonical_artifact_refs": {"artifact_id", "version"},
    "active_mission_refs": {"session_id", "run_id"},
}
_CONTROL_SEAL = object()
_PROJECT_GUARD = ContextVar("project_grant_writer", default=None)
_CONTROL_RPCS = frozenset({"runtime.project.create", "runtime.project.get", "runtime.project.list",
                           "runtime.project.update", "runtime.project.grants.set", "runtime.project.claim"})


def _deny(condition, code, message):
    if not condition:
        raise CapabilityDenied(code, message)


def _identifier(value):
    _deny(isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value
          and "*" not in value, "invalid_project", "An exact bounded project reference is required")
    return value


def _actor(context):
    return {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def _live(context):
    _deny(isinstance(context, AgentContext), "identity_required", "A bound agent identity is required")
    context.validate_profile_home()
    _deny(require_live_policy(require_run=False) == context,
          "project_scope_mismatch", "Project access requires the live owning context")
    return context


def _project_on_conn(conn, context, project_id, permission):
    _live(context)
    _identifier(project_id)
    _deny(permission in _PERMISSIONS, "invalid_project", "Unsupported project permission")
    _deny(project_id in context.policy.project_grants,
          "project_not_granted", "Project is outside the immutable agent policy")
    row = pdb.project_record(conn, project_id)
    _deny(row is not None and not row["archived"], "project_not_granted", "Project is unavailable")
    actor = _actor(context)
    _deny(pdb.project_permission(conn, project_id, actor["principal_id"], actor["agent_id"], permission),
          "project_grant_revoked", "The live project grant does not authorize this operation")
    return row


@contextmanager
def _project_transaction(context):
    _live(context)
    active = _PROJECT_GUARD.get()
    if active is not None:
        thread_id, owner, conn = active
        _deny(thread_id == threading.get_ident() and owner == context,
              "project_scope_mismatch", "Project transaction cannot cross threads or actor contexts")
        yield conn
        return
    with pdb.connect_closing() as conn, write_txn(conn):
        token = _PROJECT_GUARD.set((threading.get_ident(), context, conn))
        try:
            yield conn
        finally:
            _PROJECT_GUARD.reset(token)


@dataclass(frozen=True)
class ProjectAccess:
    context: AgentContext

    def __post_init__(self):
        _deny(isinstance(self.context, AgentContext), "identity_required", "A bound agent identity is required")

    @contextmanager
    def guard(self, project_id, actor, permission):
        """Serialize live grant revocation with a dependent authoritative store operation."""
        _deny(actor == _actor(self.context), "project_scope_mismatch", "Project actor differs from bound context")
        _live(self.context)
        with _project_transaction(self.context) as conn:
            yield _project_on_conn(conn, self.context, project_id, permission)

    def assert_access(self, project_id, actor, permission):
        _deny(actor == _actor(self.context), "project_scope_mismatch", "Project actor differs from bound context")
        with self.guard(project_id, actor, permission) as record:
            return record


def project_access(context):
    _live(context)
    return ProjectAccess(context)


def authorize_project(context, project_id, operation="read"):
    _live(context)
    with pdb.connect_closing() as conn:
        return _project_on_conn(conn, context, project_id, operation)


def list_authorized_projects(context):
    _live(context)
    with pdb.connect_closing() as conn:
        rows = []
        for project_id in sorted(context.policy.project_grants):
            if pdb.project_permission(conn, project_id, context.identity.principal_id, context.identity.agent_id, "read"):
                row = pdb.project_record(conn, project_id)
                if row is not None and not row["archived"]:
                    rows.append(row)
        return rows


def _validated_changes(changes):
    _deny(isinstance(changes, dict) and bool(changes)
          and set(changes) <= {"purpose", *_REFERENCE_FIELDS},
          "invalid_project", "Only bounded project metadata may be revised")
    result = {}
    for key, value in changes.items():
        if key == "purpose":
            _deny(isinstance(value, str) and len(value) <= 8192,
                  "invalid_project", "Project purpose exceeds its bound")
            result[key] = value
            continue
        _deny(isinstance(value, list) and len(value) <= 100,
              "invalid_project", "Project references exceed their bound")
        seen = set()
        for reference in value:
            _deny(isinstance(reference, dict) and set(reference) == _REFERENCE_FIELDS[key],
                  "invalid_project", "Project reference fields are invalid")
            for field, item in reference.items():
                if field == "version":
                    _deny(type(item) is int and item > 0, "invalid_project", "Artifact version must be positive")
                else:
                    _identifier(item)
            canonical = json.dumps(reference, sort_keys=True)
            _deny(canonical not in seen, "invalid_project", "Duplicate project references are not allowed")
            seen.add(canonical)
        result[f"{key}_json"] = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return result


def _expected_revision(actual, expected):
    _deny(type(expected) is int and expected >= 0, "invalid_project", "An exact project revision is required")
    _deny(actual == expected, "project_revision_conflict", "Project changed; refresh before updating")


def cas_project_metadata(context, project_id, expected_revision, changes):
    columns = _validated_changes(changes)
    _live(context)
    with _project_transaction(context) as conn:
        row = _project_on_conn(conn, context, project_id, "write")
        _expected_revision(row["revision"], expected_revision)
        conn.execute("UPDATE projects SET " + ",".join(f"{key}=?" for key in columns)
                     + ",revision=revision+1 WHERE id=? AND revision=?",
                     (*columns.values(), project_id, expected_revision))
        return pdb.project_record(conn, project_id)


def cas_project_artifact(context, project_id, artifact_id, version, expected_project_revision):
    """Select a project reference; this never advances the authoritative artifact head."""
    _live(context)
    with _project_transaction(context) as conn:
        row = _project_on_conn(conn, context, project_id, "write")
        _expected_revision(row["revision"], expected_project_revision)
        refs = [item for item in row["canonical_artifact_refs"] if item["artifact_id"] != artifact_id]
        refs.append({"artifact_id": artifact_id, "version": version})
        changes = _validated_changes({"canonical_artifact_refs": refs})
        conn.execute("UPDATE projects SET canonical_artifact_refs_json=?,revision=revision+1 WHERE id=? AND revision=?",
                     (changes["canonical_artifact_refs_json"], project_id, expected_project_revision))
        return pdb.project_record(conn, project_id)


@dataclass(frozen=True, init=False)
class ProjectControl:
    context: AgentContext
    agent: object
    session_id: str
    transport: object

    def __init__(self, seal, agent, session_id, transport):
        _deny(seal is _CONTROL_SEAL, "project_control_required", "Owned transport control is required")
        for key, value in (("context", agent.runtime_context), ("agent", agent),
                           ("session_id", session_id), ("transport", transport)):
            object.__setattr__(self, key, value)


def project_control(agent, ui_session_id):
    from tui_gateway import server
    from agent.runtime_commands import _RUN
    _deny(server._current_rpc_method.get() in _CONTROL_RPCS and _RUN.get() is None,
          "project_control_required", "Only an explicit user project RPC may manage project authority")
    transport, session = server._current_session_steer_authority(ui_session_id)
    _deny(transport is not None and session is not None and session.get("agent") is agent,
          "project_control_required", "Project controls require the owned live session transport")
    _live(agent.runtime_context)
    return ProjectControl(_CONTROL_SEAL, agent, ui_session_id, transport)


def _control_context(control):
    _deny(type(control) is ProjectControl, "project_control_required", "Owned transport control is required")
    from tui_gateway import server
    from agent.runtime_commands import _RUN
    _deny(server._current_rpc_method.get() in _CONTROL_RPCS and _RUN.get() is None,
          "project_control_required", "Only an explicit user project RPC may manage project authority")
    transport, session = server._current_session_steer_authority(control.session_id)
    _deny(transport is control.transport and session is not None and session.get("agent") is control.agent
          and control.agent.runtime_context == control.context,
          "project_control_required", "Project control transport is no longer current")
    return _live(control.context)


def _validated_grants(grants):
    _deny(isinstance(grants, list) and len(grants) <= 100, "invalid_project", "Explicit bounded project grants are required")
    seen, rows = set(), []
    for grant in grants:
        _deny(isinstance(grant, dict) and set(grant) == {"principal_id", "agent_id", "permissions"},
              "invalid_project", "Grant requires an exact principal and agent")
        principal, agent = _identifier(grant["principal_id"]), _identifier(grant["agent_id"])
        permissions = grant["permissions"]
        _deny(isinstance(permissions, list) and 1 <= len(permissions) <= 3
              and all(isinstance(item, str) and item in _PERMISSIONS for item in permissions)
              and len(set(permissions)) == len(permissions) and "read" in permissions,
              "invalid_project", "Grant permissions must explicitly include read and any requested write/share")
        _deny((principal, agent) not in seen, "invalid_project", "Duplicate project grant")
        seen.add((principal, agent))
        rows.append({"principal_id": principal, "agent_id": agent, "permissions": sorted(permissions)})
    return rows


def _owned_project(conn, context, project_id):
    row = pdb.project_record(conn, _identifier(project_id))
    _deny(row is not None and row["owner_principal_id"] == context.identity.principal_id,
          "project_owner_required", "Only the project's owning principal may manage its grants")
    return row


def create_owned_project(control, *, name, folders=None, slug=None, primary_path=None, purpose="", grants=None):
    context = _control_context(control)
    _validated_changes({"purpose": purpose})
    grants = _validated_grants([] if grants is None else grants)
    with pdb.connect_closing() as conn:
        try:
            project_id = pdb.create_project(conn, name=name, folders=folders, slug=slug, primary_path=primary_path,
                owner_principal_id=context.identity.principal_id, purpose=purpose, grants=grants)
        except ValueError as exc:
            # Legacy duplicate-path diagnostics include another project's slug/id.
            raise CapabilityDenied("project_create_conflict", "Project could not be created with that name or folder") from exc
        return pdb.project_record(conn, project_id)


def get_owned_project(control, project_id):
    context = _control_context(control)
    with pdb.connect_closing() as conn:
        return _owned_project(conn, context, project_id)


def list_owned_projects(control):
    context = _control_context(control)
    with pdb.connect_closing() as conn:
        return [pdb.project_record(conn, row[0]) for row in conn.execute(
            "SELECT id FROM projects WHERE owner_principal_id=? ORDER BY created_at,id",
            (context.identity.principal_id,))]


def set_project_grants(control, project_id, expected_revision, grants):
    context = _control_context(control)
    grants = _validated_grants(grants)
    with _project_transaction(context) as conn:
        row = _owned_project(conn, context, project_id)
        _expected_revision(row["revision"], expected_revision)
        pdb._replace_grants_locked(conn, project_id, grants)
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=? AND revision=?", (project_id, expected_revision))
        return pdb.project_record(conn, project_id)


def update_owned_project(control, project_id, expected_revision, changes):
    context = _control_context(control)
    columns = _validated_changes(changes)
    with _project_transaction(context) as conn:
        row = _owned_project(conn, context, project_id)
        _expected_revision(row["revision"], expected_revision)
        conn.execute("UPDATE projects SET " + ",".join(f"{key}=?" for key in columns)
                     + ",revision=revision+1 WHERE id=? AND revision=?",
                     (*columns.values(), project_id, expected_revision))
        return pdb.project_record(conn, project_id)


def claim_owned_project(control, project_id, expected_revision):
    """Explicitly adopt an existing unowned legacy record without creating model grants."""
    context = _control_context(control)
    with _project_transaction(context) as conn:
        row = pdb.project_record(conn, _identifier(project_id))
        _deny(row is not None and row["owner_principal_id"] is None,
              "project_owner_required", "Only an unowned legacy project can be adopted")
        _expected_revision(row["revision"], expected_revision)
        conn.execute("UPDATE projects SET owner_principal_id=?,revision=revision+1 WHERE id=? AND revision=?",
                     (context.identity.principal_id, project_id, expected_revision))
        return pdb.project_record(conn, project_id)


def legacy_project_surface_allowed():
    """Old profile-wide scanners/catalog methods have no exact agent/transport boundary."""
    from agent.identity_lifecycle import strict_identity_enabled
    return not strict_identity_enabled()
