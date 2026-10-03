"""Live local placement attestations; serialized capability claims are not authority.

A process-local registry loses all tickets on restart/disconnect. It cannot move
work to another machine. OS isolation is probed by the actual finite adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from agent.delegation_contract import digest, require
from agent.runtime_context import AgentContext


@dataclass(frozen=True)
class ExecutorCapabilities:
    executor_id: str
    generation: int
    principal_id: str
    profile_id: str
    profile_home_digest: str
    location: str
    capabilities: tuple[str, ...]
    max_input_bytes: int
    max_output_bytes: int
    timeout_seconds: float
    authenticated_until: float
    owner_pid: int
    owner_start: float
    cost: str = "local_unpriced"

    @property
    def sha256(self):
        return digest(asdict(self))

    def reference(self):
        return {"executor_id": self.executor_id, "generation": self.generation,
                "capability_digest": self.sha256, "location": self.location}


_LOCK = threading.RLock()
_LIVE: dict[str, ExecutorCapabilities] = {}
_PID_NONCE = uuid.uuid4().hex


def authenticate_local_executor(context: AgentContext, *, isolated_python=False):
    """Only the host calls this; no caller-supplied IDs/attestations are accepted."""
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == context, "executor_identity_mismatch", "Live identity is required")
    from gateway.status import get_process_start_time
    started = get_process_start_time(os.getpid())
    require(started is not None, "executor_authentication_unavailable", "Local process identity is unavailable")
    capabilities = ["agent.text", "document.utf8", "document.extract"]
    if isolated_python:
        from tools.environments.isolated_python import execute_isolated_python
        from tools.workspace_manifest import StagedWorkspace
        with tempfile.TemporaryDirectory(prefix="ryoko-executor-probe-") as root:
            workspace = StagedWorkspace.create(Path(root) / "probe")
            result = execute_isolated_python("print('bounded-local-probe')", workspace=workspace)
            require(result.get("isolated") is True and result.get("status") == "completed",
                    "executor_isolation_unavailable", "Live isolation probe failed; unsafe fallback is disabled")
        capabilities.append("python.isolated")
    identity = context.identity
    executor_id = "local-" + digest({"pid": os.getpid(), "nonce": _PID_NONCE,
        "principal": identity.principal_id, "profile": identity.profile_id, "home": identity.profile_home_digest})[:32]
    with _LOCK:
        previous = _LIVE.get(executor_id)
        entry = ExecutorCapabilities(executor_id, previous.generation + 1 if previous else 1,
            identity.principal_id, identity.profile_id, identity.profile_home_digest, "local", tuple(capabilities),
            8 * 1024 * 1024, 8 * 1024 * 1024, 30.0, time.time() + 60, os.getpid(), started)
        _LIVE[executor_id] = entry
        return entry


def require_executor(context, reference, *, capability="agent.text"):
    """Check live registry plus process identity at every dispatch; never deserialize trust."""
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == context, "executor_identity_mismatch", "Live policy changed")
    from gateway.status import get_process_start_time, start_time_fingerprints_match
    with _LOCK:
        entry = _LIVE.get(reference.get("executor_id")) if isinstance(reference, dict) else None
        require(entry is not None and entry.reference() == reference and entry.owner_pid == os.getpid()
                and start_time_fingerprints_match(entry.owner_start, get_process_start_time(os.getpid()) or 0),
                "executor_disconnected", "Executor authentication is unavailable; private work cannot migrate")
        identity = context.identity
        require((entry.principal_id, entry.profile_id, entry.profile_home_digest) ==
                (identity.principal_id, identity.profile_id, identity.profile_home_digest),
                "executor_locality_mismatch", "Executor belongs to another principal/profile/location")
        require(entry.authenticated_until > time.time() and capability in entry.capabilities,
                "executor_capability_unavailable", "Executor capability is expired or unsupported")
        return entry


def disconnect_executor(context, reference):
    entry = require_executor(context, reference)
    with _LOCK:
        if _LIVE.get(entry.executor_id) is entry:
            del _LIVE[entry.executor_id]


def executor_inspection(context):
    identity = context.identity
    with _LOCK:
        return [asdict(entry) | {"available": entry.authenticated_until > time.time(),
                                 "capability_digest": entry.sha256}
            for entry in _LIVE.values()
            if (entry.principal_id, entry.profile_id, entry.profile_home_digest) ==
               (identity.principal_id, identity.profile_id, identity.profile_home_digest)]
