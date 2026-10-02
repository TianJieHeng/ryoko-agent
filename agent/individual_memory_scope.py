"""Constructor-bound authority for individual built-in memory.

Stable identities share their own namespace across sessions; child UUIDs retain
an independent namespace across supported resume. Neither a display name nor the
ambient profile can select a different store after construction.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.agent_identity import IdentityPolicyError
from agent.runtime_context import AgentContext
from tools.workspace_manifest import _open_root


@dataclass(frozen=True)
class IndividualMemoryScope:
    principal_id: str
    profile_id: str
    agent_id: str
    profile_home: str
    lifecycle: str
    parent_agent_id: str | None

    @classmethod
    def from_context(cls, context):
        if (not isinstance(context, AgentContext) or context.policy.memory_backend != "builtin"
                or context.policy.role not in {"specialist", "child"}):
            raise IdentityPolicyError("Individual built-in memory requires a specialist or child identity")
        identity = context.identity
        return cls(identity.principal_id, identity.profile_id, identity.agent_id,
                   context.profile_home, identity.lifecycle, identity.parent_agent_id)

    @property
    def namespace_id(self):
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()

    @property
    def canonical_json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def directory(self):
        return Path(self.profile_home) / "individual-memory" / self.namespace_id

    def assert_current(self):
        from tools.capability_broker import require_live_policy
        context = require_live_policy(require_run=False)
        if IndividualMemoryScope.from_context(context) != self:
            raise IdentityPolicyError("Individual memory owner does not match the current actor")
        return context

    @contextmanager
    def open_directory(self):
        self.assert_current()
        fd = _open_root(Path(self.profile_home))
        try:
            for part in ("individual-memory", self.namespace_id):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    os.fsync(fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(child)
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    os.close(child)
                    raise IdentityPolicyError("Individual memory directory must be private and locally owned")
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)


def checked_file(directory_fd, name, *, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        os.close(fd)
        raise IdentityPolicyError("Individual memory state must be a private regular file with one link")
    return fd
