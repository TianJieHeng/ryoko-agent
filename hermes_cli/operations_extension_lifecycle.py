"""Opt-in pin enforcement at the existing PluginManager activation boundary.

Explicit approval publishes a private, sealed copy before the pin is referenced
by trusted profile configuration. Imports use that copy, never a source tree
which can change between source verification and activation. This is not a
sandbox against a compromised process/account that can rewrite its own config.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from agent.operations_control import digest, require
from hermes_cli.operations_extensions import _files, verify_extension, read_source_file


def _config():
    from hermes_cli.config_effective import load_user_config_effective
    return load_user_config_effective(fail_closed=True)


def _pins(config):
    plugins = config.get("plugins") or {}
    require(isinstance(plugins, dict), "extension_pin_policy_invalid")
    pins = plugins.get("pinned_manifests", {})
    require(isinstance(pins, dict), "extension_pin_policy_invalid")
    return pins


def _grants(config, key):
    from hermes_cli.plugin_capabilities import granted_capabilities
    return sorted(granted_capabilities(key, config=config))


def refuse_unsupported_activation(name, path=None):
    """Category/entrypoint loaders cannot bypass a configured general-plugin pin."""
    pins = _pins(_config())
    names = {name, Path(path).name if path is not None else name}
    matches = [key for key in pins if key in names or key.rsplit("/", 1)[-1] in names]
    require(not matches, "extension_pinned_category_activation_unsupported")


def _environment_compatible(manifest):
    from hermes_platform.host.facts import os_family, process_arch
    environment = manifest["environment"]["record"]
    require(environment["python_version"] == sys.version.split()[0]
            and environment["platform"] == f"{os_family()}-{process_arch()}", "extension_environment_mismatch")


def _seal_tree(home, source, manifest):
    from tools.workspace_manifest import _open_root
    base = home / "extension-artifacts"
    base.mkdir(mode=0o700, exist_ok=True)
    check = _open_root(base)
    os.close(check)
    final = base / manifest["source_digest"]
    if final.exists():
        require(_files(final) == manifest["files"], "extension_sealed_artifact_changed")
        return final
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=base))
    for item in manifest["files"]:
        data = read_source_file(source, item["path"], item["size"])
        require(len(data) == item["size"] and hashlib.sha256(data).hexdigest() == item["sha256"],
                "extension_source_changed")
        target = staging / item["path"]
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o400)
    require(_files(staging) == manifest["files"], "extension_source_changed")
    for directory in sorted((path for path in staging.rglob("*") if path.is_dir()), reverse=True):
        directory.chmod(0o500)
    staging.chmod(0o500)
    os.rename(staging, final)
    fd = _open_root(base)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return final


def preview_extension_change(key, *, action, root=None, manifest=None, expires_at=None):
    from hermes_constants import get_hermes_home
    require(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", key),
            "extension_invalid_key")
    require(action in {"pin", "revoke"}, "extension_invalid_action")
    config = _config()
    pins = _pins(config)
    expiry = time.time() + 300 if expires_at is None else expires_at
    require(type(expiry) in (int, float) and time.time() < expiry <= time.time() + 301, "extension_preview_expired")
    if action == "pin":
        require(root is not None and not Path(root).is_symlink(), "extension_invalid_root")
        root = Path(root).absolute()
        verify_extension(root, manifest, granted_capabilities=_grants(config, key))
        _environment_compatible(manifest)
        declared = json.loads(json.dumps(manifest))
    else:
        require(key in pins, "extension_pin_missing")
        root = Path(pins[key]["source_root"])
        declared = pins[key]["manifest"]
    body = {"schema_version": 1, "action": action, "plugin_key": key, "profile_home": str(get_hermes_home()),
        "source_root": str(root), "manifest": declared, "config_digest": digest(config),
        "granted_capabilities": _grants(config, key), "expires_at": expiry,
        "effects": "publish_sealed_pin_without_enabling" if action == "pin" else "revoke_pin_and_targeted_local_unload"}
    return {**body, "approval_id": digest(body)}


def apply_extension_change(plan, *, approval_id, manager=None):
    from hermes_cli.config import atomic_config_write, read_user_config_raw
    from hermes_constants import get_hermes_home
    require(isinstance(plan, dict) and plan.get("approval_id") == approval_id,
            "extension_exact_authorization_required")
    current = preview_extension_change(plan.get("plugin_key"), action=plan.get("action"),
        root=plan.get("source_root"), manifest=plan.get("manifest"), expires_at=plan.get("expires_at"))
    require(current == plan, "extension_preview_changed")
    home, key = get_hermes_home(), plan["plugin_key"]
    require(manager is None or Path(manager.home_path).resolve() == home.resolve(), "extension_wrong_profile")
    if plan["action"] == "pin":
        _seal_tree(home, Path(plan["source_root"]), plan["manifest"])
    require(digest(_config()) == plan["config_digest"], "extension_preview_changed")
    raw = read_user_config_raw(home / "config.yaml")
    pins = raw.setdefault("plugins", {}).setdefault("pinned_manifests", {})
    pins[key] = {"manifest": plan["manifest"], "source_root": plan["source_root"],
                 "revoked": plan["action"] == "revoke"}
    atomic_config_write(home / "config.yaml", raw)
    unloaded = manager.unload(key) if plan["action"] == "revoke" and manager is not None else False
    return {"plugin_key": key, "digest": plan["manifest"]["digest"], "revoked": plan["action"] == "revoke",
            "local_manager_unloaded": bool(unloaded), "other_processes_require_restart": True,
            "enabled_or_installed": False}


def enforce_activation(manifest):
    from hermes_cli.plugins_manifest import manifest_key
    from hermes_constants import get_hermes_home
    config = _config()
    pins = _pins(config)
    key = manifest_key(manifest)
    if key not in pins:
        return manifest
    pin = pins[key]
    require(isinstance(pin, dict) and set(pin) == {"manifest", "source_root", "revoked"},
            "extension_pin_policy_invalid")
    require(pin["revoked"] is False, "extension_revoked")
    require(manifest.source in {"user", "project"} and manifest.kind not in {"exclusive", "model-provider"}
            and manifest.path is not None, "extension_pinned_loader_unsupported")
    source = Path(manifest.path)
    require(str(source.absolute()) == pin["source_root"] and not source.is_symlink(), "extension_source_mismatch")
    verify_extension(source, pin["manifest"], granted_capabilities=_grants(config, key))
    _environment_compatible(pin["manifest"])
    sealed = get_hermes_home() / "extension-artifacts" / pin["manifest"]["source_digest"]
    require(_files(sealed) == pin["manifest"]["files"], "extension_sealed_artifact_changed")
    for path in (sealed, *sealed.rglob("*")):
        require(not path.stat().st_mode & 0o222, "extension_artifact_not_sealed")
    # All imports (including relative imports) now resolve only the approved copy.
    return replace(manifest, path=str(sealed))
