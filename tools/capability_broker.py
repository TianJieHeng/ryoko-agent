"""Short-lived dispatch authority, minted only from the live trusted runtime.

These process-local one-use tickets are not effect receipts or recovery records.
Restart drops them; BE06 owns durable intent and outcome reconciliation. Scope
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
from dataclasses import asdict, dataclass, field
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
        operation = "mcp_read" if read_only else "mcp_call"
        destination = _canonical({"server": target[0], "tool": target[1], "endpoint": grant["endpoint"]})
        purpose = "mcp"
    else:
        raise CapabilityDenied("capability_unsupported", "This tool has no certified execution contract")
    return ActionSpec(name, arguments, operation, destination=destination,
                      destination_purpose=purpose, contract_digest=schema_digest)


def _authorize_action(action):
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


@dataclass
class _ApprovalState:
    preview: ApprovalPreview
    status: str = "pending"


_LOCK = threading.Lock()
_APPROVALS: dict[str, _ApprovalState] = {}
_CAPABILITIES: dict[str, Capability] = {}
_MAX_OUTSTANDING = 4096


def _prune(now):
    for mapping in (_APPROVALS, _CAPABILITIES):
        for key, value in list(mapping.items()):
            record = value.preview if isinstance(value, _ApprovalState) else value
            if record.expires_at <= now:
                del mapping[key]
    if len(_APPROVALS) + len(_CAPABILITIES) >= _MAX_OUTSTANDING:
        raise CapabilityDenied("capability_capacity", "Outstanding capability/approval limit reached")


def _expiry(ttl, maximum):
    if type(ttl) not in (float, int) or not math.isfinite(ttl) or not 0 < ttl <= maximum:
        raise CapabilityDenied("invalid_expiry", "Capability lifetime is outside its bounded contract")
    return time.time() + ttl


def preview_action(action: ActionSpec, *, ttl_seconds=300) -> ApprovalPreview:
    authority = _authority()
    _authorize_action(action)
    expires = _expiry(ttl_seconds, 300)
    identifier = uuid.uuid4().hex
    digest = _digest({"approval_id": identifier, "authority": asdict(authority),
                      "action": action.to_record(), "expires_at": expires})
    preview = ApprovalPreview(identifier, authority, action, digest, expires)
    with _LOCK:
        _prune(time.time())
        _APPROVALS[identifier] = _ApprovalState(preview)
    return preview


def resolve_approval(preview: ApprovalPreview, digest: str, choice: str) -> None:
    """Called by the bound human surface, never by model input or a tool argument."""
    authority = _authority()
    _authorize_action(preview.action)
    with _LOCK:
        state = _APPROVALS.get(preview.approval_id)
        if (state is None or state.preview is not preview or preview.authority != authority
                or preview.expires_at <= time.time() or digest != preview.approval_digest
                or state.status != "pending"):
            raise CapabilityDenied("approval_mismatch", "Approval expired, changed, or no longer belongs to this action")
        # Older surfaces returning broad choices cannot create standing authority.
        state.status = "approved" if choice == "once" else "denied"


def issue_capability(action: ActionSpec, *, approval: ApprovalPreview | None = None,
                     ttl_seconds=60) -> Capability:
    authority = _authority()
    _authorize_action(action)
    expires = _expiry(ttl_seconds, 60)
    digest = None
    with _LOCK:
        _prune(time.time())
        if action.operation_class == "mcp_call" or approval is not None:
            state = _APPROVALS.get(approval.approval_id) if approval is not None else None
            if (state is None or state.preview is not approval or state.status != "approved"
                    or approval.authority != authority or approval.action != action
                    or approval.expires_at <= time.time()):
                raise CapabilityDenied("exact_approval_required", "This exact action requires a current one-use approval", pending=True)
            digest = approval.approval_digest
            del _APPROVALS[approval.approval_id]
        capability = Capability(uuid.uuid4().hex, authority, action, digest, expires)
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
