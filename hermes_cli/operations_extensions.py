"""Offline, metadata-only pinned extension qualification before explicit PM setup.

This gate does not install, import, probe, or activate extension code. Existing PM
owns provisioning and selection. A qualifying manifest is evidence of local bytes,
not a publisher signature, sandbox, or proof that a future loader enforces the pin.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlsplit

from agent.operations_control import digest, require
from hermes_cli.plugin_capabilities import VALID_CAPABILITY_IDS
from pm.plugin_declarations import read_python_declaration

_MAX_FILES = 512
_MAX_BYTES = 32 * 1024 * 1024


def read_source_file(root, relative, maximum):
    """Anchor every component and read the same checked descriptor we hashed."""
    from tools.workspace_manifest import _parent_fd
    parent, name = _parent_fd(root, relative)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
                    "extension_unsafe_source_file")
            data = source.read(maximum + 1)
            require(len(data) <= maximum, "extension_inventory_limit")
            return data
    finally:
        os.close(parent)


def _files(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "extension_invalid_root")
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        require(not path.is_symlink(), "extension_symlink_denied")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "extension_special_file_denied")
        total += info.st_size
        require(len(rows) < _MAX_FILES and total <= _MAX_BYTES, "extension_inventory_limit")
        data = read_source_file(root, relative.as_posix(), info.st_size)
        rows.append({"path": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return rows


def catalog_metadata(root):
    # Bound and validate inputs before the established PM declaration reader.
    files = _files(root)
    declaration = read_python_declaration(Path(root))
    name = declaration.manifest.get("name")
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name),
            "extension_invalid_name")
    grants = declaration.manifest.get("capabilities", [])
    require(isinstance(grants, list) and all(isinstance(value, str) and value in VALID_CAPABILITY_IDS for value in grants),
            "extension_unknown_capability")
    return {"name": name, "declared_grants": sorted(set(grants)),
            "requirements": list(declaration.requirements), "files": files,
            "imports_executed": False, "network_probes": False, "dependencies_installed": False}


def pin_extension(root, *, source, revision, publisher, lock_name="uv.lock", environment_name="environment.json"):
    parsed = urlsplit(source)
    require(parsed.scheme == "https" and bool(parsed.netloc) and parsed.username is None
            and parsed.password is None and not parsed.query and not parsed.fragment, "extension_source_invalid")
    require(isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision), "extension_revision_not_pinned")
    require(isinstance(publisher, str) and 0 < len(publisher) <= 128, "extension_publisher_required")
    metadata = catalog_metadata(root)
    index = {row["path"]: row for row in metadata["files"]}
    require(lock_name in index and environment_name in index, "extension_lock_and_environment_required")
    lock = tomllib.loads(read_source_file(root, lock_name, index[lock_name]["size"]).decode())
    require(type(lock.get("version")) is int and lock["version"] >= 1
            and isinstance(lock.get("requires-python"), str), "extension_invalid_lock")
    environment = json.loads(read_source_file(root, environment_name, index[environment_name]["size"]))
    require(isinstance(environment, dict) and set(environment) == {"schema_version", "python_version", "platform", "lock_sha256"}
            and environment["schema_version"] == 1 and environment["lock_sha256"] == index[lock_name]["sha256"]
            and all(isinstance(environment[key], str) and 0 < len(environment[key]) <= 128
                    for key in ("python_version", "platform")), "extension_environment_invalid")
    body = {"schema_version": 1, "name": metadata["name"], "source": source, "revision": revision,
        "publisher": publisher, "publisher_verified": False, "files": metadata["files"],
        "source_digest": digest(metadata["files"]), "lock": index[lock_name],
        "environment": {"record": environment, "artifact": index[environment_name]},
        "capabilities": metadata["declared_grants"], "state": "pinned", "revoked": False,
        "runtime_activation_certified": False, "environment_reproduction_verified": False}
    return {**body, "digest": digest(body)}


def verify_extension(root, manifest, *, granted_capabilities):
    require(isinstance(manifest, dict) and manifest.get("revoked") is False and manifest.get("state") == "pinned",
            "extension_revoked_or_inactive")
    body = {key: value for key, value in manifest.items() if key != "digest"}
    require(digest(body) == manifest.get("digest"), "extension_manifest_changed")
    expected = pin_extension(root, source=manifest["source"], revision=manifest["revision"],
        publisher=manifest["publisher"], lock_name=manifest["lock"]["path"],
        environment_name=manifest["environment"]["artifact"]["path"])
    require(expected == manifest, "extension_bytes_changed")
    require(isinstance(granted_capabilities, (list, tuple))
            and sorted(set(granted_capabilities)) == manifest["capabilities"], "extension_exact_grants_required")
    return {"digest": manifest["digest"], "state": "qualified_for_explicit_pm_provisioning",
            "runtime_activation_certified": False, "imports_executed": False,
            "blockers": ["publisher_authenticity_not_verified", "environment_reproduction_not_verified",
                         "activation_requires_profile_pin"]}


def revoke_manifest(manifest, *, authorization_digest):
    require(isinstance(manifest, dict) and manifest.get("digest") == authorization_digest
            and digest({key: value for key, value in manifest.items() if key != "digest"}) == authorization_digest,
            "extension_exact_authorization_required")
    body = {**{key: value for key, value in manifest.items() if key != "digest"},
            "state": "revoked", "revoked": True, "previous_digest": authorization_digest}
    return {**body, "digest": digest(body)}
