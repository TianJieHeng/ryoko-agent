"""Immutable actor-private result bytes, shared by delivery and effect recovery.

SQLite owns ArtifactVersion identity and visibility. This module owns only the
checked blob representation; an orphan is not a committed result. Publication
uses Linux's no-replace rename, so interruption never exposes a partial file or
overwrites even an unexpected existing entry. Unsupported hosts fail closed.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

from tools.workspace_manifest import _open_root

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_FIELDS = frozenset({"artifact_id", "version", "locator", "sha256", "size", "mime", "producing_run"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactConflict(ValueError):
    """The expected immutable bytes or checked storage boundary changed."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def descriptor_digest(descriptor: dict) -> str:
    return _digest(json.dumps(descriptor, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode())


def artifact_actor(context) -> dict:
    return {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def _identifier(value, name):
    if (not isinstance(value, str) or not 0 < len(value) <= 256 or value.strip() != value
            or any(ord(character) < 32 for character in value)):
        raise ArtifactConflict(f"A bounded {name} is required")
    return value


def _locator(context, artifact_id: str, version: int) -> str:
    actor = descriptor_digest(artifact_actor(context))
    return f"runtime-artifacts/{actor}/{_digest(artifact_id.encode())}.{version}.blob"


def result_artifact_descriptor(context, producing_run: str, payload_bytes: bytes,
                               artifact_id: str, version=1, mime="application/json") -> dict:
    _identifier(artifact_id, "artifact identity")
    _identifier(producing_run, "producing run")
    _identifier(mime, "MIME type")
    if type(version) is not int or not 0 < version < 2 ** 31:
        raise ArtifactConflict("Artifact version must be a positive bounded integer")
    if not isinstance(payload_bytes, bytes) or len(payload_bytes) > MAX_ARTIFACT_BYTES:
        raise ArtifactConflict("Artifact bytes exceed the supported result bound")
    return {"artifact_id": artifact_id, "version": version,
            "locator": _locator(context, artifact_id, version),
            "sha256": _digest(payload_bytes), "size": len(payload_bytes),
            "mime": mime, "producing_run": producing_run}


def validate_artifact_descriptor(context, descriptor: dict) -> dict:
    if not isinstance(descriptor, dict) or set(descriptor) != _FIELDS:
        raise ArtifactConflict("Invalid artifact descriptor")
    _identifier(descriptor["artifact_id"], "artifact identity")
    _identifier(descriptor["producing_run"], "producing run")
    _identifier(descriptor["mime"], "MIME type")
    version = descriptor["version"]
    if type(version) is not int or not 0 < version < 2 ** 31:
        raise ArtifactConflict("Invalid artifact version")
    if (type(descriptor["size"]) is not int or not 0 <= descriptor["size"] <= MAX_ARTIFACT_BYTES
            or not isinstance(descriptor["sha256"], str) or not _HEX.fullmatch(descriptor["sha256"])):
        raise ArtifactConflict("Invalid artifact content bounds")
    if descriptor["locator"] != _locator(context, descriptor["artifact_id"], version):
        raise ArtifactConflict("Artifact locator does not belong to this actor")
    return dict(descriptor)


def _open_namespace(context, descriptor, *, create=False):
    context.validate_profile_home()
    parts = descriptor["locator"].split("/")
    fd = _open_root(Path(context.profile_home))
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    os.fsync(fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                os.close(child)
                raise ArtifactConflict("Artifact namespace must be private and locally owned")
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _read_at(parent_fd, leaf, descriptor):
    fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o077
                or before.st_size != descriptor["size"]):
            raise ArtifactConflict("Artifact is not the expected bounded private regular file")
        data = bytearray()
        while len(data) <= descriptor["size"]:
            chunk = os.read(fd, min(65536, descriptor["size"] + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != descriptor["size"] or _digest(data) != descriptor["sha256"]):
            raise ArtifactConflict("Artifact content changed or failed its digest check")
        return bytes(data)
    finally:
        os.close(fd)


def _rename_noreplace():
    # This is a required primitive, not a best-effort overwrite fallback.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ArtifactConflict("Atomic no-replace artifact publication is unsupported on this host")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    return rename


def _publish_bytes(context, descriptor: dict, payload_bytes: bytes):
    """Called only after the durable broker's dispatched transition commits."""
    validate_artifact_descriptor(context, descriptor)
    if (not isinstance(payload_bytes, bytes) or len(payload_bytes) != descriptor["size"]
            or _digest(payload_bytes) != descriptor["sha256"]):
        raise ArtifactConflict("Artifact bytes changed after intent preparation")
    rename = _rename_noreplace()
    parent, leaf = _open_namespace(context, descriptor, create=True)
    temporary = f".pending-{secrets.token_hex(16)}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload_bytes)
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
            if rename(parent, temporary.encode(), parent, leaf.encode(), 1):
                code = ctypes.get_errno()
                if code != errno.EEXIST:
                    raise OSError(code, os.strerror(code))
                # A repeated idempotency key is a no-op only for exact bytes.
                _read_at(parent, leaf, descriptor)
            else:
                temporary = None
            os.fsync(parent)
        finally:
            if temporary is not None:
                os.unlink(temporary, dir_fd=parent)
        _read_at(parent, leaf, descriptor)
    finally:
        os.close(parent)
    return {"locator": descriptor["locator"], "sha256": descriptor["sha256"],
            "size": descriptor["size"], "acknowledgment_level": "local_fsync"}


def read_result_artifact(context, descriptor: dict) -> bytes:
    """Read authorized bytes without creating directories or changing any blob."""
    from tools.capability_broker import require_live_policy
    if require_live_policy(require_run=False) != context:
        raise ArtifactConflict("Artifact read requires its live owning context")
    descriptor = validate_artifact_descriptor(context, descriptor)
    parent, leaf = _open_namespace(context, descriptor)
    try:
        return _read_at(parent, leaf, descriptor)
    finally:
        os.close(parent)


def publish_result_artifact(run, payload_bytes: bytes, artifact_id: str, version=1,
                            mime="application/json") -> dict:
    """The final-result consumer persists intent before publishing its bytes."""
    from tools.capability_broker import invoke_effect_dispatch
    descriptor = result_artifact_descriptor(run.context, run.run_id, payload_bytes,
                                            artifact_id, version, mime)
    identity = descriptor_digest({"artifact_id": artifact_id, "version": version,
                                  "producing_run": run.run_id})
    invoke_effect_dispatch("artifact_publish", run=run, input_ref=descriptor,
                           payload=payload_bytes, operation_id=f"artifact-{identity}",
                           intent_key=f"artifact-{identity}")
    return descriptor
