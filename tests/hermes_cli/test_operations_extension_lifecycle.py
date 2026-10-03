"""Real offline activation, immutable copy, revocation and category bypass gates."""
import hashlib
import json
import logging
import socket
import sys
from types import SimpleNamespace

import pytest

from agent.operations_control import OperationsError
from hermes_cli.operations_extension_lifecycle import (apply_extension_change, enforce_activation,
                                                       preview_extension_change)
from hermes_cli.operations_extensions import pin_extension
from hermes_cli.plugins import PluginManager, PluginManifest

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "plugins" / "fixture"
    root.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(json.dumps({"plugins": {"enabled": ["fixture"],
        "entries": {"fixture": {"granted_capabilities": []}}}}))
    (root / "plugin.yaml").write_text("name: fixture\nmanifest_version: 2\ncapabilities: []\n")
    (root / "__init__.py").write_text("def register(ctx):\n    ctx.register_hook('fixture_hook', lambda **kw: 'approved')\n")
    (root / "uv.lock").write_text("version = 1\nrequires-python = '>=3.12'\n")
    from hermes_platform.host.facts import os_family, process_arch
    (root / "environment.json").write_text(json.dumps({"schema_version": 1, "python_version": sys.version.split()[0],
        "platform": f"{os_family()}-{process_arch()}", "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()}))
    manifest = pin_extension(root, source="https://example.invalid/fixture", revision="b" * 40, publisher="fixture")
    plan = preview_extension_change("fixture", action="pin", root=root, manifest=manifest)
    assert not (home / "extension-artifacts").exists()
    with pytest.raises(OperationsError):
        apply_extension_change(plan, approval_id="not-approved")
    receipt = apply_extension_change(plan, approval_id=plan["approval_id"])
    runtime_manifest = PluginManifest(name="fixture", key="fixture", path=str(root), source="user")
    manager = PluginManager()
    yield SimpleNamespace(home=home, root=root, manifest=manifest, runtime=runtime_manifest,
                          manager=manager, receipt=receipt)
    manager.unload()


def test_real_native_loader_uses_sealed_approved_code_and_revokes_only_target(pinned, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network probe belongs in activation")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    pinned.manager._load_plugin(pinned.runtime)
    loaded = pinned.manager._plugins["fixture"]
    assert loaded.enabled and loaded.error is None
    assert "extension-artifacts" in loaded.manifest.path
    assert pinned.manager.invoke_hook("fixture_hook") == ["approved"]
    pinned.manager._hooks["unrelated_fixture"] = [lambda **kw: "unrelated"]
    plan = preview_extension_change("fixture", action="revoke")
    receipt = apply_extension_change(plan, approval_id=plan["approval_id"], manager=pinned.manager)
    assert receipt["local_manager_unloaded"]
    assert pinned.manager.invoke_hook("fixture_hook") == []
    assert pinned.manager.invoke_hook("unrelated_fixture") == ["unrelated"]
    pinned.manager._load_plugin(pinned.runtime)
    assert not pinned.manager._plugins["fixture"].enabled
    assert pinned.manager._plugins["fixture"].error == "extension_revoked"


def test_original_source_swap_after_gate_cannot_change_executed_code(pinned, monkeypatch):
    import hermes_cli.operations_extension_lifecycle as lifecycle
    actual = lifecycle.enforce_activation
    def swap_after_validation(manifest):
        sealed = actual(manifest)
        (pinned.root / "__init__.py").write_text("raise AssertionError('Unapproved swapped source executed')\n")
        return sealed
    monkeypatch.setattr(lifecycle, "enforce_activation", swap_after_validation)
    pinned.manager._load_plugin(pinned.runtime)
    assert pinned.manager._plugins["fixture"].enabled
    assert pinned.manager.invoke_hook("fixture_hook") == ["approved"]
    pinned.manager._load_plugin(pinned.runtime)
    assert pinned.manager._plugins["fixture"].error == "extension_bytes_changed"


def test_changed_grants_or_artifact_refuse_before_import(pinned):
    from hermes_cli.config import read_user_config_raw, atomic_config_write
    config = read_user_config_raw(pinned.home / "config.yaml")
    config["plugins"]["entries"]["fixture"]["granted_capabilities"] = ["tools.override"]
    atomic_config_write(pinned.home / "config.yaml", config)
    pinned.manager._load_plugin(pinned.runtime)
    assert pinned.manager._plugins["fixture"].error == "extension_exact_grants_required"
    config["plugins"]["entries"]["fixture"]["granted_capabilities"] = []
    atomic_config_write(pinned.home / "config.yaml", config)
    sealed = pinned.home / "extension-artifacts" / pinned.manifest["source_digest"] / "__init__.py"
    sealed.chmod(0o600)
    sealed.write_text("raise AssertionError('bad sealed code')")
    pinned.manager._load_plugin(pinned.runtime)
    assert pinned.manager._plugins["fixture"].error == "extension_sealed_artifact_changed"


def test_category_and_entrypoint_paths_cannot_bypass_opted_in_pin(pinned):
    from plugins.plugin_loader import load_plugin_module, load_named
    from plugins.memory import _load_provider_from_entry_point
    from providers import _import_plugin_dir
    called = []
    entry = SimpleNamespace(name="fixture", load=lambda: called.append("entrypoint"))
    with pytest.raises(OperationsError, match="unsupported"):
        _load_provider_from_entry_point(entry)
    with pytest.raises(OperationsError, match="unsupported"):
        load_plugin_module("fixture.category", pinned.root, parents=(), logger=logging.getLogger(__name__))
    assert load_named("fixture", pinned.root, lambda _: called.append("category"), kind="Memory", noun="provider",
                      logger=logging.getLogger(__name__)) is None
    _import_plugin_dir(pinned.root, "user", home_key=str(pinned.home))
    pinned.manager._load_plugin(PluginManifest(name="fixture", source="entrypoint", key="fixture"))
    assert pinned.manager._plugins["fixture"].error == "extension_pinned_loader_unsupported"
    assert called == []


def test_absent_pin_preserves_legacy_loader_and_disabled_gate(pinned, monkeypatch):
    from hermes_cli.plugins_discovery import gate_manifest
    other = PluginManifest(name="unrelated", key="unrelated", source="user", path=str(pinned.root))
    assert enforce_activation(other) is other
    gate = gate_manifest(pinned.runtime, {"fixture"}, {"fixture"})
    assert gate.action == "placeholder" and not gate.enabled
    from hermes_cli.config import read_user_config_raw, atomic_config_write
    config = read_user_config_raw(pinned.home / "config.yaml")
    config["plugins"]["disabled"] = ["fixture"]
    atomic_config_write(pinned.home / "config.yaml", config)
    monkeypatch.setattr(pinned.manager, "_collect_directory_manifests", lambda: [pinned.runtime])
    monkeypatch.setattr(pinned.manager, "_scan_entry_points", lambda: [])
    pinned.manager.discover_and_load()
    assert not pinned.manager._plugins["fixture"].enabled
    assert pinned.manager.invoke_hook("fixture_hook") == []


def test_pin_registry_is_profile_local_and_rejects_changed_preview(pinned, tmp_path):
    from hermes_cli.config import read_user_config_raw, atomic_config_write
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.yaml").write_text("{}")
    token = set_hermes_home_override(str(other))
    try:
        assert enforce_activation(pinned.runtime) is pinned.runtime
    finally:
        reset_hermes_home_override(token)
    plan = preview_extension_change("fixture", action="revoke")
    config = read_user_config_raw(pinned.home / "config.yaml")
    config["display"] = {"skin": "default"}
    atomic_config_write(pinned.home / "config.yaml", config)
    with pytest.raises(OperationsError, match="preview changed"):
        apply_extension_change(plan, approval_id=plan["approval_id"], manager=pinned.manager)
