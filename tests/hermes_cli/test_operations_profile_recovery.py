"""Real isolated full-owning-store/key-loss/corruption drills, with no live import."""
import io
import hashlib
import json
import zipfile

import pytest

from agent.operations_control import OperationsError
from agent.result_artifacts import _publish_bytes, result_artifact_descriptor
from hermes_cli.operations_backup import open_backup, seal_backup, rotate_backup
from hermes_cli.operations_profile_recovery import MANIFEST, snapshot_owning_stores, verify_owning_store_restore
from hermes_state_common import SCHEMA_VERSION
from tools.individual_memory_store import IndividualMemoryStore
from tests.agent.test_operations_control import runtime, own
from tests.agent.test_operations_checkpoint_recovery import checkpoint, evidence, protected

pytestmark = pytest.mark.platforms("linux")


def build(runtime, tmp_path):
    return snapshot_owning_stores(runtime.db, runtime.context, staging_directory=tmp_path, code_version="fixture-v1")


def verify(data, runtime, tmp_path, **changes):
    args = dict(expected_store_schema=SCHEMA_VERSION, expected_code_version="fixture-v1", temporary_parent=tmp_path)
    return verify_owning_store_restore(data, runtime.context, **{**args, **changes})


def prepare(runtime):
    from hermes_cli.projects_db import connect_closing, create_project
    with connect_closing(runtime.home / "projects.db") as conn:
        project = create_project(conn, name="Recovery fixture", owner_principal_id="owner")
    memory = IndividualMemoryStore(runtime.context)
    first = memory.write_record("synthetic individual memory")["record"]
    memory.write_record("newer memory version", record_id=first["record_id"], expected_version=1)
    memory.write_record("conflicting version retained", record_id=first["record_id"], expected_version=1)
    generation = own(runtime)
    checkpoint(runtime, generation)
    evidence(runtime, generation)
    data = b"durable artifact bytes"
    descriptor = result_artifact_descriptor(runtime.context, runtime.run_id, data, "artifact-one")
    _publish_bytes(runtime.context, descriptor, data)
    runtime.db._write_sql("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,run_id,"
        "principal_id,profile_id,agent_id,descriptor_json,created_at,project_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("artifact-one", 1, "session", "command", runtime.run_id, "owner", "profile", "specialist", json.dumps(descriptor), 1, project))
    runtime.db.release_session_turn_lease("session", "fixture", generation=generation)
    return memory, descriptor


def alter(data, name, payload, *, remove=False):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as result:
        for entry in source.infolist():
            if entry.filename == name and remove:
                continue
            result.writestr(entry.filename, payload if entry.filename == name else source.read(entry.filename))
    return out.getvalue()


def test_owning_stores_artifacts_memory_restore_and_external_key_rotation(runtime, tmp_path, capsys):
    memory, descriptor = prepare(runtime)
    before = protected(runtime.db)
    memory_bytes = {file.name: file.read_bytes() for file in memory._scope.directory.iterdir() if file.is_file()}
    archive = build(runtime, tmp_path)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        manifest = json.loads(source.read(MANIFEST))
        assert {row["kind"] for row in manifest["files"]} == {
            "session_store", "project_store", "artifact_bytes", "individual_memory", "memory_projection"}
        assert "config.yaml" not in source.namelist()
        assert "configuration_and_credentials_not_copied" in manifest["limitations"]
    key, next_key = bytes(range(32)), bytes(reversed(range(32)))
    envelope = seal_backup(archive, key=key, key_id="drill-a", store_schema=SCHEMA_VERSION, code_version="fixture-v1")
    rotated = rotate_backup(envelope, old_key=key, old_key_id="drill-a", new_key=next_key, new_key_id="drill-b",
        store_schema=SCHEMA_VERSION, code_version="fixture-v1")
    params = dict(expected_key_id="drill-b", supported_schema=SCHEMA_VERSION, expected_code_version="fixture-v1")
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        open_backup(rotated, key=key, **params)
    with pytest.raises(ValueError, match="external_256_bit_key_required"):
        open_backup(rotated, key=None, **params)
    receipt = verify(open_backup(rotated, key=next_key, **params), runtime, tmp_path)
    assert receipt["all_supported_owning_stores_validated"] and receipt["store_count"] == 3
    assert receipt["checkpoint_count"] == 1
    assert receipt["artifact_count"] == 1 and receipt["safety"]["consumed_approvals"] == 1
    assert receipt["safety"]["unresolved_effects"] == 1 and not receipt["live_profile_modified"]
    assert not receipt["full_profile_cutover_certified"] and receipt["external_effects_replayed"] == 0
    assert protected(runtime.db) == before
    assert memory_bytes == {file.name: file.read_bytes() for file in memory._scope.directory.iterdir() if file.is_file()}
    assert not list(tmp_path.glob("operations-*"))
    for name in (descriptor["locator"], "projects.db", f"individual-memory/{memory.namespace_id}/records.sqlite3"):
        with pytest.raises(OperationsError):
            verify(alter(archive, name, b"corrupt"), runtime, tmp_path)
        with pytest.raises(OperationsError):
            verify(alter(archive, name, b"", remove=True), runtime, tmp_path)
    with pytest.raises(OperationsError, match="version mismatch"):
        verify(archive, runtime, tmp_path, expected_code_version="future")
    with pytest.raises(OperationsError, match="version mismatch"):
        verify(archive, runtime, tmp_path, expected_store_schema=SCHEMA_VERSION + 1)
    # A->B->A in one process must not accept a bundle under another home.
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    other = tmp_path / "other-profile"
    other.mkdir(mode=0o700)
    (other / "config.yaml").write_text(json.dumps(runtime.raw))
    other_context = resolve_agent_context(runtime.raw, session_id="session", profile_home=other)
    with agent_runtime_scope(other_context):
        with pytest.raises(OperationsError, match="scope mismatch"):
            verify_owning_store_restore(archive, other_context, expected_store_schema=SCHEMA_VERSION,
                expected_code_version="fixture-v1", temporary_parent=tmp_path)
    assert verify(archive, runtime, tmp_path)["restored"]
    from hermes_cli.operations_cli import main
    assert main(["drill-recovery", "--session", "session", "--code-version", "fixture-v1",
                 "--temporary-parent", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["restored"] and not output["live_profile_modified"]
    assert "synthetic individual memory" not in json.dumps(output)


def test_live_owner_unsupported_store_missing_bytes_and_restore_crash_fail_closed(runtime, tmp_path, monkeypatch):
    memory, descriptor = prepare(runtime)
    assert runtime.db.try_acquire_session_turn_lease("session", "busy")
    with pytest.raises(OperationsError, match="profile busy"):
        build(runtime, tmp_path)
    runtime.db.release_session_turn_lease("session", "busy")
    (runtime.home / "unknown.sqlite3").write_bytes(b"unsupported owner")
    with pytest.raises(OperationsError, match="unsupported owning store"):
        build(runtime, tmp_path)
    (runtime.home / "unknown.sqlite3").unlink()
    archive = build(runtime, tmp_path)
    # Even a self-consistent plaintext manifest cannot certify corrupt SQLite.
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        manifest = json.loads(source.read(MANIFEST))
    corrupt = b"not a SQLite database"
    for item in manifest["files"]:
        if item["path"] == "projects.db":
            item.update(size=len(corrupt), sha256=hashlib.sha256(corrupt).hexdigest())
    corrupt_archive = alter(alter(archive, "projects.db", corrupt), MANIFEST, json.dumps(manifest).encode())
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        verify(corrupt_archive, runtime, tmp_path)
    assert not list(tmp_path.glob("operations-*"))
    import hermes_cli.operations_profile_recovery as recovery
    original = recovery._inspect_restored
    def crash(*args, **kwargs):
        raise OSError("synthetic restore interruption")
    monkeypatch.setattr(recovery, "_inspect_restored", crash)
    with pytest.raises(OSError, match="synthetic restore"):
        verify(archive, runtime, tmp_path)
    assert not list(tmp_path.glob("operations-*"))
    monkeypatch.setattr(recovery, "_inspect_restored", original)
    (runtime.home / descriptor["locator"]).unlink()
    with pytest.raises(OperationsError, match="artifact missing"):
        build(runtime, tmp_path)
