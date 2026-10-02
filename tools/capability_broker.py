"""Short-lived dispatch authority, minted only from the live trusted runtime.

These process-local one-use tickets are not effect receipts or recovery records.
Restart drops them; SQLite owns exact approvals, intent and outcome records. Scope
strings describe a request, never grant access. Certified adapters still enforce
filesystem, recipient and transport boundaries at their actual effect edge.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


class CapabilityDenied(PermissionError):
    def __init__(self, code: str, message: str, *, pending: bool = False):
        self.code, self.pending = code, pending
        super().__init__(message)

    def result(self) -> str:
        return json.dumps({"error": self.code, "message": str(self),
                           "status": "pending" if self.pending else "denied"})


def _canonical(value) -> str:
    def validate(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Action keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Action must contain only JSON values")
    validate(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise CapabilityDenied("capability_input_limit", "Exact action exceeds the certified inline input limit")
    return encoded


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, init=False)
class ActionSpec:
    name: str
    input_json: str = field(repr=False)
    operation_class: str
    resource_roots: tuple[str, ...]
    destination: str
    destination_purpose: str
    contract_digest: str

    def __init__(self, name, arguments, operation_class, resource_roots=(),
                 destination="", destination_purpose="", contract_digest=""):
        if not isinstance(arguments, dict) or not isinstance(name, str) or not name:
            raise CapabilityDenied("invalid_action", "An exact named JSON action is required")
        if any(not isinstance(value, str) for value in (operation_class, destination, destination_purpose, contract_digest)):
            raise CapabilityDenied("invalid_action", "Action scope must contain exact strings")
        if (not isinstance(resource_roots, (tuple, list))
                or any(not isinstance(root, (str, Path)) or not Path(root).is_absolute() for root in resource_roots)):
            raise CapabilityDenied("invalid_action", "Resource roots must be exact absolute paths")
        roots = tuple(str(Path(root).resolve()) for root in resource_roots)
        for key, value in (("name", name), ("input_json", _canonical(arguments)),
                           ("operation_class", operation_class), ("resource_roots", roots),
                           ("destination", destination), ("destination_purpose", destination_purpose),
                           ("contract_digest", contract_digest)):
            object.__setattr__(self, key, value)

    def to_record(self):
        return {"name": self.name, "arguments": json.loads(self.input_json),
                "operation_class": self.operation_class, "resource_roots": list(self.resource_roots),
                "destination": self.destination, "destination_purpose": self.destination_purpose,
                "contract_digest": self.contract_digest}

    @property
    def digest(self):
        return _digest(self.to_record())


def require_live_policy(*, require_run=True):
    """Re-read trusted policy, including disable/revoke, never reuse stale grants."""
    from agent.runtime_context import current_agent_context
    from agent.identity_lifecycle import identity_config
    from agent.agent_identity import parse_agent_identity_config
    context = current_agent_context()
    parsed = parse_agent_identity_config(identity_config())
    if context is None:
        if parsed is not None:
            raise CapabilityDenied("identity_required", "Trusted agent context is required")
        return None
    if parsed is None or parsed.digest != context.config_digest:
        raise CapabilityDenied("policy_revoked", "Agent policy changed; start a newly authorized session")
    if require_run:
        from agent.runtime_commands import assert_runtime_dispatch
        assert_runtime_dispatch()
    return context


@dataclass(frozen=True)
class DispatchAuthority:
    principal_id: str
    profile_id: str
    agent_id: str
    profile_home: str = field(repr=False)
    session_id: str
    run_id: str
    holder: str = field(repr=False)
    generation: int
    policy_version: int
    policy_digest: str
    config_digest: str


def _authority():
    context = require_live_policy()
    if context is None:
        raise CapabilityDenied("identity_required", "Capabilities require an identity-bound durable run")
    from agent.runtime_commands import assert_runtime_dispatch
    run = assert_runtime_dispatch()
    identity = context.identity
    return DispatchAuthority(identity.principal_id, identity.profile_id, identity.agent_id,
            context.profile_home, run.session_id, run.run_id, run.holder, run.generation,
            context.policy.policy_version, context.policy.digest, context.config_digest)


def _action_authority(action):
    if action.operation_class != "project_artifact_publish":
        return _authority()
    from agent.artifact_commands import assert_artifact_dispatch
    run = assert_artifact_dispatch()
    context, identity = run.context, run.context.identity
    return DispatchAuthority(identity.principal_id, identity.profile_id, identity.agent_id,
        context.profile_home, run.session_id, run.run_id, run.holder, run.generation,
        context.policy.policy_version, context.policy.digest, context.config_digest)


def project_artifact_action(scope):
    """Host-owned exact local artifact contract; no new model-facing tool."""
    fields = {"project_id", "request_id", "descriptor", "metadata", "expected_head_version",
              "project_revision", "base_sha256"}
    if not isinstance(scope, dict) or set(scope) != fields:
        raise CapabilityDenied("invalid_artifact_action", "An exact artifact proposal is required")
    descriptor = scope["descriptor"]
    if not isinstance(descriptor, dict):
        raise CapabilityDenied("invalid_artifact_action", "An immutable artifact descriptor is required")
    return ActionSpec("runtime.project_artifact_publish", scope, "project_artifact_publish",
        destination=f"project:{scope['project_id']}", destination_purpose="project_artifact",
        contract_digest=hashlib.sha256(b"be07.project-artifact-markdown.v1").hexdigest())


_SESSION_MODULES = {"todo_list": "tools.todo_tool", "clarify": "tools.clarify_tool",
                    "tool_search": "tools.tool_search", "tool_describe": "tools.tool_search",
                    "tool_call": "tools.tool_search"}


def tool_action(name: str, arguments: dict, *, entry=None) -> ActionSpec:
    """Classify host-owned adapters. Model arguments and MCP hints cannot classify."""
    from tools.registry import registry
    entry = entry or registry.get_entry(name)
    module = getattr(getattr(entry, "handler", None), "__module__", "")
    schema_digest = _digest({"schema": entry.schema, "handler": id(entry.handler)}) if entry is not None else ""
    if name in _SESSION_MODULES and (entry is None or module == _SESSION_MODULES[name]):
        operation, destination, purpose = "session_control", "", ""
    elif name == "delegate_task" and module == "tools.delegate_tool":
        operation, destination, purpose = "delegation", "", ""
    elif name == "execute_code" and module == "tools.code_execution_tool":
        operation, destination, purpose = "isolated_compute", "", ""
    elif name in ("memory", "session_search") and module == {
            "memory": "tools.memory_tool", "session_search": "tools.session_search_tool"}[name]:
        from tools.session_search_scope import local_recall_context
        context = local_recall_context(name)
        if context is None:
            raise CapabilityDenied("identity_required", "An individual memory owner is required")
        identity = context.identity
        operation = "builtin_memory" if name == "memory" else "session_read"
        destination = _canonical({"principal_id": identity.principal_id, "profile_id": identity.profile_id,
                                  "agent_id": identity.agent_id, "home": identity.profile_home_digest})
        purpose = "individual_memory" if name == "memory" else "session_recall"
    elif entry is not None and entry.toolset.startswith("mcp-"):
        target = getattr(entry.handler, "_agent_mcp_target", None)
        if not isinstance(target, tuple) or len(target) != 2:
            raise CapabilityDenied("mcp_provenance_missing", "MCP target provenance is required")
        from tools.mcp_tool_policy import policy_for, require_operation
        try:
            require_operation(*target, arguments, require_run=True)
        except (PermissionError, ValueError) as exc:
            raise CapabilityDenied("mcp_scope_denied", str(exc)) from exc
        grant = policy_for(target[0])
        if grant is None:
            raise CapabilityDenied("mcp_scope_denied", "MCP transport policy is required")
        # Explicit operator read grants classify; remote readOnlyHint never does.
        read_only = target[1] in grant["read_only_tools"] or target[1] in grant["read_scopes"]
        if not read_only:
            raise CapabilityDenied("effect_adapter_unsupported",
                "This MCP mutation has no durable semantic effect adapter; approval alone cannot certify its outcome")
        operation = "mcp_read" if read_only else "mcp_call"
        destination = _canonical({"server": target[0], "tool": target[1], "endpoint": grant["endpoint"]})
        purpose = "mcp"
    else:
        raise CapabilityDenied("capability_unsupported", "This tool has no certified execution contract")
    return ActionSpec(name, arguments, operation, destination=destination,
                      destination_purpose=purpose, contract_digest=schema_digest)


def _authorize_action(action):
    if action.operation_class == "project_artifact_publish":
        from agent.artifact_commands import assert_artifact_dispatch
        from agent.project_context import authorize_project
        from agent.result_artifacts import validate_artifact_descriptor
        run = assert_artifact_dispatch()
        scope = json.loads(action.input_json)
        if project_artifact_action(scope) != action:
            raise CapabilityDenied("capability_scope_mismatch", "Artifact action contract changed")
        descriptor = validate_artifact_descriptor(run.context, scope["descriptor"])
        from agent.mission_runtime import assert_mission_project_scope
        assert_mission_project_scope(run, scope["project_id"])
        project = authorize_project(run.context, scope["project_id"], "write")
        if (descriptor["producing_run"] != run.run_id
                or project["revision"] != scope["project_revision"]):
            raise CapabilityDenied("artifact_revision_changed", "Artifact owner or project authorization revision changed")
        return
    from tools.agent_policy_gate import authorize_tool
    denied = authorize_tool(action.name)
    if denied is not None:
        raise CapabilityDenied("agent_policy_denied", json.loads(denied)["message"])
    expected = tool_action(action.name, json.loads(action.input_json))
    # Resource roots may narrow the isolated executor's separately enforced
    # workspace only. They never authorize ambient filesystem access.
    if action.operation_class == "isolated_compute":
        expected = ActionSpec(expected.name, json.loads(expected.input_json), expected.operation_class,
                              resource_roots=action.resource_roots, contract_digest=expected.contract_digest)
    if expected != action:
        raise CapabilityDenied("capability_scope_mismatch", "Action scope differs from its trusted adapter contract")


@dataclass(frozen=True)
class ApprovalPreview:
    approval_id: str
    authority: DispatchAuthority = field(repr=False)
    action: ActionSpec
    approval_digest: str
    expires_at: float


@dataclass(frozen=True)
class Capability:
    capability_id: str
    authority: DispatchAuthority = field(repr=False)
    action: ActionSpec
    approval_digest: str | None
    expires_at: float

    def to_record(self):
        authority, action = self.authority, self.action
        return {"capability_id": self.capability_id, "actor": authority.principal_id,
                "profile_id": authority.profile_id, "agent_id": authority.agent_id,
                "run_id": authority.run_id, "generation": authority.generation,
                "operation_class": action.operation_class, "resource_scope": list(action.resource_roots),
                "destination": action.destination, "destination_purpose": action.destination_purpose,
                "input_digest": action.digest, "policy_version": authority.policy_version,
                "policy_digest": authority.policy_digest, "approval_digest": self.approval_digest,
                "expires_at": self.expires_at}


_LOCK = threading.Lock()
_CAPABILITIES: dict[str, Capability] = {}
_MAX_OUTSTANDING = 4096


def _prune(now):
    for key, capability in list(_CAPABILITIES.items()):
        if capability.expires_at <= now:
            del _CAPABILITIES[key]
    if len(_CAPABILITIES) >= _MAX_OUTSTANDING:
        raise CapabilityDenied("capability_capacity", "Outstanding capability limit reached")


def _expiry(ttl, maximum):
    if type(ttl) not in (float, int) or not math.isfinite(ttl) or not 0 < ttl <= maximum:
        raise CapabilityDenied("invalid_expiry", "Capability lifetime is outside its bounded contract")
    return time.time() + ttl


def _approval_binding(authority, action):
    # Inline input bytes are the revision. Persist only digests/references, never
    # private tool arguments or bearer credentials from destination URLs.
    input_digest = hashlib.sha256(action.input_json.encode()).hexdigest()
    target_ref = _digest({"destination": action.destination, "purpose": action.destination_purpose,
                          "resource_roots": list(action.resource_roots)})
    result = {"session_id": authority.session_id, "run_id": authority.run_id,
            "holder": authority.holder, "generation": authority.generation,
            "action_digest": action.digest, "input_digest": input_digest, "target_ref": target_ref,
            "policy_version": str(authority.policy_version), "policy_digest": authority.policy_digest,
            "input_revision": "inline:" + input_digest, "artifact_revision": "none"}
    if action.operation_class == "project_artifact_publish":
        from agent.result_artifacts import descriptor_digest
        scope = json.loads(action.input_json)
        descriptor = scope["descriptor"]
        result.update(input_digest=descriptor_digest(descriptor),
            target_ref=f"artifact:{descriptor['artifact_id']}:{descriptor['version']}",
            input_revision=scope["base_sha256"], artifact_revision=str(descriptor["version"]))
    return result


def _approval_store(authority, action=None):
    if action is not None and action.operation_class == "project_artifact_publish":
        from agent.artifact_commands import assert_artifact_dispatch
        run = assert_artifact_dispatch()
    else:
        from agent.runtime_commands import assert_runtime_dispatch
        run = assert_runtime_dispatch()
    actor = {"principal_id": authority.principal_id, "profile_id": authority.profile_id,
             "agent_id": authority.agent_id}
    return run.db, actor


def _approval_record(preview, authority, action):
    from hermes_state_runtime import RuntimeStoreError
    if (not isinstance(preview, ApprovalPreview) or preview.authority != authority
            or preview.action != action or preview.expires_at <= time.time()):
        raise CapabilityDenied("approval_mismatch", "Approval expired, changed, or belongs to another runtime owner")
    db, actor = _approval_store(authority, action)
    try:
        record = db.get_effect_approval(preview.approval_id, actor)
    except RuntimeStoreError as exc:
        raise CapabilityDenied(exc.code, str(exc)) from exc
    if (record["approval_digest"] != preview.approval_digest
            or record["expires_at"] != preview.expires_at
            or record["binding"] != _approval_binding(authority, action)):
        raise CapabilityDenied("approval_mismatch", "Approval does not match its durable exact-action record")
    return db, actor, record


def preview_action(action: ActionSpec, *, ttl_seconds=300, approval_id=None, expires_at=None) -> ApprovalPreview:
    from hermes_state_runtime import RuntimeStoreError
    authority = _action_authority(action)
    _authorize_action(action)
    expires = _expiry(ttl_seconds, 300)
    if expires_at is not None:
        if (type(expires_at) not in (int, float) or not math.isfinite(expires_at)
                or not time.time() < expires_at <= expires):
            raise CapabilityDenied("approval_expired", "Exact proposal approval has expired")
        expires = expires_at
    db, actor = _approval_store(authority, action)
    try:
        record = db.request_effect_approval(actor=actor, expires_at=expires, approval_id=approval_id,
                                            **_approval_binding(authority, action))
    except RuntimeStoreError as exc:
        raise CapabilityDenied(exc.code, str(exc)) from exc
    return ApprovalPreview(record["approval_id"], authority, action,
                           record["approval_digest"], record["expires_at"])


def recover_approval_preview(approval_id, action):
    """Reconstruct a projection; durable exact scope remains the authority."""
    authority = _action_authority(action)
    _authorize_action(action)
    db, actor = _approval_store(authority, action)
    record = db.get_effect_approval(approval_id, actor)
    preview = ApprovalPreview(record["approval_id"], authority, action,
                              record["approval_digest"], record["expires_at"])
    _approval_record(preview, authority, action)
    return preview


def resolve_approval(preview: ApprovalPreview, digest: str, choice: str) -> dict:
    """Only the bound human surface supplies a decision; SQLite owns its status."""
    from hermes_state_runtime import RuntimeStoreError
    if not isinstance(preview, ApprovalPreview) or digest != preview.approval_digest:
        raise CapabilityDenied("approval_mismatch", "The answer does not name the exact approval digest")
    authority = _action_authority(preview.action)
    _authorize_action(preview.action)
    db, actor, _record = _approval_record(preview, authority, preview.action)
    try:
        # Older surfaces returning broad choices cannot create standing authority.
        return db.resolve_effect_approval(preview.approval_id, actor, holder=authority.holder,
            generation=authority.generation, approval_digest=digest, choice="once" if choice == "once" else "deny")
    except RuntimeStoreError as exc:
        raise CapabilityDenied(exc.code, str(exc)) from exc


def issue_capability(action: ActionSpec, *, approval: ApprovalPreview | None = None,
                     ttl_seconds=60) -> Capability:
    from hermes_state_runtime import RuntimeStoreError
    authority = _action_authority(action)
    if action.operation_class == "project_artifact_publish":
        raise CapabilityDenied("effect_dispatch_required", "Artifact approval must be consumed with durable effect dispatch")
    _authorize_action(action)
    expires = _expiry(ttl_seconds, 60)
    identifier, digest = uuid.uuid4().hex, None
    with _LOCK:
        _prune(time.time())
        if action.operation_class == "mcp_call" or approval is not None:
            if approval is None:
                raise CapabilityDenied("exact_approval_required", "This exact action requires a current one-use approval", pending=True)
            db, actor, record = _approval_record(approval, authority, action)
            if record["status"] != "approved":
                raise CapabilityDenied("exact_approval_required", "This exact action requires a current one-use approval",
                                       pending=record["status"] == "pending")
            try:
                db.consume_effect_approval(approval.approval_id, actor, consumer_id=identifier,
                                           **_approval_binding(authority, action))
            except RuntimeStoreError as exc:
                raise CapabilityDenied(exc.code, str(exc)) from exc
            digest = approval.approval_digest
            expires = min(expires, approval.expires_at)
        capability = Capability(identifier, authority, action, digest, expires)
        _CAPABILITIES[capability.capability_id] = capability
    return capability


def validate_capability(capability: Capability, action: ActionSpec, *, consumed=False):
    authority = _authority()
    _authorize_action(action)
    if (not isinstance(capability, Capability) or capability.authority != authority
            or capability.action != action or capability.expires_at <= time.time()):
        raise CapabilityDenied("capability_mismatch", "Capability scope, owner generation, policy or expiry changed")
    if not consumed:
        with _LOCK:
            if _CAPABILITIES.get(capability.capability_id) is not capability:
                raise CapabilityDenied("capability_consumed", "Capability is missing or has already been consumed")


def consume_capability(capability: Capability, action: ActionSpec):
    validate_capability(capability, action)
    with _LOCK:
        if _CAPABILITIES.pop(capability.capability_id, None) is not capability:
            raise CapabilityDenied("capability_consumed", "Capability has already been consumed")


def prepare_tool_capability(name: str, arguments: dict, *, entry=None):
    if require_live_policy() is None:
        return None
    action = tool_action(name, arguments, entry=entry)
    _authorize_action(action)
    approval = None
    if action.operation_class == "mcp_call":
        from tools.capability_approval import request_exact_approval
        approval = request_exact_approval(action)
    return issue_capability(action, approval=approval)


@dataclass
class _Dispatch:
    capability: Capability
    forwarded: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_DISPATCH: ContextVar[_Dispatch | None] = ContextVar("capability_dispatch", default=None)


@contextmanager
def dispatch_capability(capability, name, arguments, *, entry=None):
    """Outer dispatch consumes once; one registry hop may validate the same input."""
    if capability is None:
        if require_live_policy() is not None:
            raise CapabilityDenied("capability_required", "Strict dispatch requires an exact capability")
        yield
        return
    action = tool_action(name, arguments, entry=entry)
    consume_capability(capability, action)
    token = _DISPATCH.set(_Dispatch(capability))
    try:
        yield
    finally:
        _DISPATCH.reset(token)


@dataclass
class _HandlerContinuation:
    capability: Capability
    handler: object
    used: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_HANDLER: ContextVar[_HandlerContinuation | None] = ContextVar("capability_handler", default=None)


def _invoke_registered(capability, entry, callback):
    token = _HANDLER.set(_HandlerContinuation(capability, entry.handler))
    try:
        return callback()
    finally:
        _HANDLER.reset(token)


def invoke_tool_dispatch(name, arguments, callback, *, entry=None):
    """The registry's concrete consumer, also covering calls bypassing the loop."""
    if require_live_policy() is None:
        return callback()
    from tools.registry import registry
    entry = entry or registry.get_entry(name)
    if entry is None:
        raise CapabilityDenied("handler_missing", "Registered handler provenance is required")
    active = _DISPATCH.get()
    if active is not None and active.capability.action.name == name:
        action = tool_action(name, arguments, entry=entry)
        with active.lock:
            if active.forwarded:
                raise CapabilityDenied("capability_consumed", "A dispatch capability cannot invoke its handler twice")
            validate_capability(active.capability, action, consumed=True)
            active.forwarded = True
        return _invoke_registered(active.capability, entry, callback)
    capability = prepare_tool_capability(name, arguments, entry=entry)
    with dispatch_capability(capability, name, arguments, entry=entry):
        _DISPATCH.get().forwarded = True
        return _invoke_registered(capability, entry, callback)


def invoke_bound_handler(name, arguments, callback, *, handler):
    """MCP's raw handler entry consumes its exact registry-issued continuation.

    Calling a handler directly must mint a fresh capability/approval. A stale
    handler cannot borrow the provenance of a newer registration with its name.
    """
    if require_live_policy() is None:
        return callback()
    from tools.registry import registry
    entry = registry.get_entry(name)
    if entry is None or entry.handler is not handler:
        raise CapabilityDenied("handler_changed", "Handler registration changed; rediscovery is required")
    continuation = _HANDLER.get()
    if continuation is not None and continuation.handler is handler:
        action = tool_action(name, arguments, entry=entry)
        with continuation.lock:
            if continuation.used:
                raise CapabilityDenied("capability_consumed", "The exact handler continuation was already consumed")
            validate_capability(continuation.capability, action, consumed=True)
            continuation.used = True
        return callback()
    return invoke_tool_dispatch(name, arguments, callback, entry=entry)


def invoke_effect_dispatch(operation_type, *, run, input_ref, payload, operation_id, intent_key,
                           action=None, approval=None):
    """Internal durable mutation edge, separate from model-facing tool tickets.

    Only host-certified adapters may classify an effect. Immutable result storage
    is a finalization duty: cancellation stops new work but does not discard partial
    output. The adapter rechecks owner/policy and commits intent before any write.
    Opaque tool callbacks cannot acquire this authority by supplying a class name.
    """
    from agent.effect_reconciler import dispatch_certified_effect
    return dispatch_certified_effect(operation_type, run=run, input_ref=input_ref,
        payload=payload, operation_id=operation_id, intent_key=intent_key, action=action, approval=approval)
