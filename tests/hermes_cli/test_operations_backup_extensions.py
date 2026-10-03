"""Real synthetic backup/key-loss/rotation drills and offline extension reads."""
import hashlib
import io
import json
import socket
import subprocess
import zipfile

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.operations_control import OperationsError
from agent.result_artifacts import artifact_actor
from hermes_cli.operations_backup import (open_backup, rotate_backup, seal_backup, snapshot_session_store,
                                         validate_archive, verify_session_store_restore)
from hermes_cli.operations_extensions import catalog_metadata, pin_extension, revoke_manifest, verify_extension
from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION


def test_real_sqlite_snapshot_authenticated_restore_rotation_and_key_loss(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    config = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}
    (home / "config.yaml").write_text(json.dumps(config))
    context = resolve_agent_context(config, session_id="session", profile_home=home)
    db = SessionDB(home / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    db.append_message("session", "user", "synthetic-private-data")
    actor = artifact_actor(context)
    db.submit_runtime_command("session", actor, {"schema_version": 1, "command_id": "one", "idempotency_key": "one",
        "expected_revision": None, "operation": "submit", "payload": {"text": "fixture"}, "identity_binding": actor})
    try:
        with agent_runtime_scope(context):
            archive = snapshot_session_store(db, context, staging_directory=tmp_path)
        first_key, second_key = bytes(range(32)), bytes(reversed(range(32)))
        sealed = seal_backup(archive, key=first_key, key_id="fixture-key-one", store_schema=SCHEMA_VERSION,
                             code_version="fixture-version")
        assert b"synthetic-private-data" not in sealed
        params = dict(expected_key_id="fixture-key-one", supported_schema=SCHEMA_VERSION,
                      expected_code_version="fixture-version")
        opened = open_backup(sealed, key=first_key, **params)
        receipt = verify_session_store_restore(opened, expected_sessions=["session"],
            expected_store_schema=SCHEMA_VERSION, temporary_parent=tmp_path)
        assert receipt["restored"] and not receipt["full_profile_recovery_certified"]
        assert db.get_messages("session")[0]["content"] == "synthetic-private-data"
        with pytest.raises(ValueError, match="schema_mismatch"):
            verify_session_store_restore(opened, expected_sessions=["session"],
                expected_store_schema=SCHEMA_VERSION + 1, temporary_parent=tmp_path)
        from cryptography.exceptions import InvalidTag
        with pytest.raises(InvalidTag):
            open_backup(sealed, key=second_key, **params)
        tampered = json.loads(sealed)
        tampered["header"]["sha256"] = "0" * 64
        with pytest.raises(InvalidTag):
            open_backup(json.dumps(tampered).encode(), key=first_key, **params)
        with pytest.raises(ValueError, match="version_mismatch"):
            open_backup(sealed, key=first_key, **{**params, "supported_schema": SCHEMA_VERSION + 1})
        rotated = rotate_backup(sealed, old_key=first_key, old_key_id="fixture-key-one", new_key=second_key,
                                new_key_id="fixture-key-two", store_schema=SCHEMA_VERSION, code_version="fixture-version")
        assert open_backup(rotated, key=second_key, **{**params, "expected_key_id": "fixture-key-two"}) == archive
        with pytest.raises(ValueError):
            open_backup(rotated, key=first_key, **params)
        assert not list(tmp_path.glob("operations-restore-*"))
        assert not list(tmp_path.glob("*.db"))
    finally:
        db.close()


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a\\b"])
def test_restore_rejects_unsafe_archive_paths_before_extraction(name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "payload")
    with pytest.raises(ValueError, match="unsafe_backup_member"):
        validate_archive(buffer.getvalue())


@pytest.fixture
def extension(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("name: fixture\nmanifest_version: 2\ncapabilities: [tools.override]\n")
    (root / "__init__.py").write_text("raise AssertionError('Catalog must never execute me')\n")
    (root / "uv.lock").write_text("version = 1\nrevision = 1\nrequires-python = '>=3.12'\n")
    (root / "environment.json").write_text(json.dumps({"schema_version": 1, "python_version": "3.14.0",
        "platform": "linux-x86_64", "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()}))
    return root


def pin(root):
    return pin_extension(root, source="https://example.invalid/publisher/extension", revision="a" * 40,
                         publisher="fixture-publisher")


def test_catalog_pin_verify_revoke_are_offline_and_execute_zero_plugin_code(extension, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network or dependency process belongs in catalog discovery")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    metadata = catalog_metadata(extension)
    assert metadata["imports_executed"] is False
    manifest = pin(extension)
    assert pin(extension) == manifest
    qualified = verify_extension(extension, manifest, granted_capabilities=["tools.override"])
    assert not qualified["runtime_activation_certified"]
    with pytest.raises(OperationsError, match="grants"):
        verify_extension(extension, manifest, granted_capabilities=[])
    revoked = revoke_manifest(manifest, authorization_digest=manifest["digest"])
    with pytest.raises(OperationsError, match="revoked"):
        verify_extension(extension, revoked, granted_capabilities=["tools.override"])
    assert not manifest["revoked"]
    (extension / "__init__.py").write_text("changed bytes")
    with pytest.raises(OperationsError, match="bytes changed"):
        verify_extension(extension, manifest, granted_capabilities=["tools.override"])


def test_extension_refuses_unpinned_source_missing_lock_and_undeclared_grants(extension):
    with pytest.raises(OperationsError, match="not pinned"):
        pin_extension(extension, source="https://example.invalid/plugin", revision="main", publisher="owner")
    (extension / "uv.lock").unlink()
    with pytest.raises(OperationsError, match="lock and environment"):
        pin(extension)
    (extension / "plugin.yaml").write_text("name: fixture\ncapabilities: [arbitrary-root-shell]\n")
    with pytest.raises(OperationsError, match="unknown capability"):
        catalog_metadata(extension)


@pytest.mark.platforms("posix")
def test_extension_refuses_symlink_inventory(extension, tmp_path):
    external = tmp_path / "secret"
    external.write_text("do not read through plugin")
    (extension / "symlink").symlink_to(external)
    with pytest.raises(OperationsError, match="symlink"):
        catalog_metadata(extension)
