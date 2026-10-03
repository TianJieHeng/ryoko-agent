"""Bounded local owning-store bundles and isolated, non-executing restore drills.

This is an operator drill format, not an updater snapshot tier or a live import.
Only a single-actor local profile with the listed owners is supported. Secrets,
configuration and external memory providers are explicitly excluded. Plaintext
staging and returned ZIP bytes are sensitive; operations_backup.seal_backup is
archive encryption only, never live database/file encryption or key custody.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import zipfile

from agent.operations_control import authority, digest, require
from agent.result_artifacts import _validate_descriptor, artifact_actor, descriptor_digest
from hermes_cli.operations_backup import _MAX_BYTES, _json, validate_archive
from tools.workspace_manifest import _open_root, _parent_fd

FORMAT = "ryoko-local-owning-stores-v1"
MAX_FILES = 4095
MAX_ROWS = 10000
MAX_SESSIONS = 100
MANIFEST = "recovery-manifest.json"
# These owners are outside this narrowly certified profile, never silently omitted.
UNSUPPORTED_OWNERS = {"cron", "kanban", "kanban.db", "memory", "memories", "memory_store.db", "response_store.db",
    "verification_evidence.db", "runs_idempotency.db", "shared-state.db", "gateway", "pairing",
    "platforms", "plugins", "feishu_comment_pairing.json"}
EXCLUSIONS = ["configuration_and_credentials_not_copied", "external_memory_provider_not_restored",
    "external_project_workspaces_not_copied", "logs_caches_exports_and_backup_history_not_restored",
    "no_live_profile_cutover", "no_effect_or_delivery_redispatch", "live_at_rest_encryption_unqualified",
    "plaintext_staging_and_archive_require_operator_custody"]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _bounded_entries(path):
    with os.scandir(path) as entries:
        result = []
        for item in entries:
            require(len(result) < MAX_FILES, "recovery_inventory_limit")
            result.append(item.name)
    return sorted(result)


def _regular(home, relative):
    parent, leaf = _parent_fd(home, relative)
    try:
        info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= _MAX_BYTES,
                "recovery_unsafe_file")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    finally:
        os.close(parent)


def _read_bytes(home, relative):
    parent, leaf = _parent_fd(home, relative)
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_size <= 8 * 1024 * 1024, "recovery_unsafe_file")
            data = stream.read(8 * 1024 * 1024 + 1)
            after = os.fstat(fd)
            require(len(data) == before.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "recovery_file_changed")
            return data
    finally:
        os.close(parent)


def _walk(home, root):
    files, pending = [], [root]
    visited = 0
    while pending:
        relative = pending.pop()
        fd = _open_root(home / relative)
        try:
            for name in _bounded_entries(fd):
                visited += 1
                require(visited <= MAX_FILES, "recovery_inventory_limit")
                child = relative + "/" + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(child)
                else:
                    _regular(home, child)
                    files.append(child)
        finally:
            os.close(fd)
    return sorted(files)


def _inventory(home, context):
    root_entries = _bounded_entries(home)
    for name in UNSUPPORTED_OWNERS.intersection(root_entries):
        # Startup creates empty scaffolds even when a legacy owner is unused.
        path = home / name
        require(not path.is_symlink() and path.is_dir() and not _bounded_entries(path),
                "recovery_unsupported_owning_store")
    files = {"state.db": "session_store"}
    if "projects.db" in root_entries:
        files["projects.db"] = "project_store"
    for name in root_entries:
        require(not (name.endswith((".db", ".sqlite", ".sqlite3")) and name not in files),
                "recovery_unsupported_owning_store")
    namespace = descriptor_digest(artifact_actor(context))
    if "runtime-artifacts" in root_entries:
        for name in _walk(home, "runtime-artifacts"):
            parts = name.split("/")
            require(len(parts) == 3 and parts[1] == namespace and parts[2].endswith(".blob"),
                    "recovery_artifact_scope_mismatch")
            files[name] = "artifact_bytes"
    if "individual-memory" in root_entries:
        from agent.individual_memory_scope import IndividualMemoryScope
        scope = IndividualMemoryScope.from_context(context)
        for name in _walk(home, "individual-memory"):
            parts = name.split("/")
            require(len(parts) == 3 and parts[1] == scope.namespace_id, "recovery_memory_scope_mismatch")
            leaf = parts[-1]
            if leaf in {"store.lock", "records.sqlite3-wal", "records.sqlite3-shm", "records.sqlite3-journal"}:
                continue
            require(leaf in {"records.sqlite3", "MEMORY.md", "USER.md"}, "recovery_unknown_memory_file")
            files[name] = "individual_memory" if leaf == "records.sqlite3" else "memory_projection"
        required = {f"individual-memory/{scope.namespace_id}/{name}" for name in ("records.sqlite3", "MEMORY.md", "USER.md")}
        require(required <= files.keys(), "recovery_memory_incomplete")
    require(len(files) <= MAX_FILES, "recovery_inventory_limit")
    stamps = {name: _regular(home, name) for name in files}
    require(sum(item[2] for item in stamps.values()) <= _MAX_BYTES, "recovery_size_limit")
    included_roots = {name.split("/")[0] for name in files}
    excluded = sorted(name for name in root_entries if name not in included_roots
                      and not name.startswith(("state.db-", "projects.db-")) and name != ".backup.lock")
    return files, stamps, excluded


@contextmanager
def _database(path, *, lock=False):
    with closing(sqlite3.connect(path.as_uri() + ("?mode=rw" if lock else "?mode=ro"), uri=True, timeout=0.25)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA trusted_schema=OFF")
        if lock:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            if lock:
                conn.rollback()


def _schema(conn):
    return digest([tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")])


def _check_sqlite(conn):
    require([row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"], "recovery_integrity_failed")
    require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "recovery_foreign_key_failed")


def _state_contract(conn, context, schema):
    require(conn.execute("SELECT version FROM schema_version").fetchall()[0][0] == schema,
            "recovery_schema_mismatch")
    actor = artifact_actor(context)
    sessions = conn.execute("SELECT id,model_config,turn_owner_generation FROM sessions LIMIT ?", (MAX_SESSIONS + 1,)).fetchall()
    require(len(sessions) <= MAX_SESSIONS, "recovery_row_limit")
    for row in sessions:
        identity = json.loads(row["model_config"] or "{}").get("agent_identity", {})
        require(all(identity.get(key) == value for key, value in actor.items())
                and identity.get("profile_home_digest") == context.identity.profile_home_digest,
                "recovery_session_scope_mismatch")
    states = conn.execute("SELECT * FROM runtime_state LIMIT ?", (MAX_SESSIONS + 1,)).fetchall()
    require(len(states) <= MAX_SESSIONS and all(all(row[key] == value for key, value in actor.items()) for row in states),
            "recovery_runtime_scope_mismatch")
    generations = {row["id"]: row["turn_owner_generation"] for row in sessions}
    for table, column in (("session_turn_leases", "conversation_id"), ("runtime_events", "session_id"),
                          ("runtime_checkpoints", "session_id")):
        require(not conn.execute(f"SELECT 1 FROM {table} r JOIN sessions s ON s.id=r.{column} "
                "WHERE r.generation>s.turn_owner_generation LIMIT 1").fetchone(), "recovery_generation_invalid")
    for table in ("runtime_effects", "runtime_effect_approvals", "runtime_artifact_versions"):
        require(not conn.execute(f"SELECT 1 FROM {table} WHERE principal_id IS NOT ? OR profile_id IS NOT ? "
                "OR agent_id IS NOT ? LIMIT 1", tuple(actor.values())).fetchone(), "recovery_record_scope_mismatch")
    for table in ("runtime_effects", "runtime_effect_approvals"):
        require(not conn.execute(f"SELECT 1 FROM {table} WHERE schema_version != 1 LIMIT 1").fetchone(),
                "recovery_record_version_mismatch")
    require(not conn.execute("SELECT 1 FROM runtime_effects e LEFT JOIN sessions s ON s.id=e.session_id "
            "WHERE s.id IS NULL OR e.generation>s.turn_owner_generation OR e.prepared_generation>e.generation LIMIT 1").fetchone(),
            "recovery_generation_invalid")
    effects = conn.execute("SELECT effect_id,state,generation FROM runtime_effects ORDER BY effect_id LIMIT ?", (MAX_ROWS + 1,)).fetchall()
    approvals = conn.execute("SELECT approval_id,status,consumer_id,binding_json FROM runtime_effect_approvals ORDER BY approval_id LIMIT ?", (MAX_ROWS + 1,)).fetchall()
    require(len(effects) <= MAX_ROWS and len(approvals) <= MAX_ROWS, "recovery_row_limit")
    return {"sessions": sorted(generations), "generation_digest": digest(generations),
            "effects_digest": digest([tuple(row) for row in effects]),
            "approvals_digest": digest([tuple(row) for row in approvals]),
            "unresolved_effects": sum(row["state"] not in {"confirmed", "failed"} for row in effects),
            "consumed_approvals": sum(row["status"] == "consumed" for row in approvals)}


def _artifact_contract(conn, home, files, context):
    rows = conn.execute("SELECT descriptor_json,principal_id,profile_id,agent_id,project_id FROM runtime_artifact_versions "
                        "WHERE publication_state='committed' LIMIT ?", (MAX_FILES + 1,)).fetchall()
    require(len(rows) <= MAX_FILES, "recovery_inventory_limit")
    locators = set()
    project_ids = set()
    for row in rows:
        owner = {key: row[key] for key in ("principal_id", "profile_id", "agent_id")}
        require(owner == artifact_actor(context), "recovery_artifact_scope_mismatch")
        descriptor = _validate_descriptor(json.loads(row["descriptor_json"]), owner)
        locator = descriptor["locator"]
        require(files.get(locator) == "artifact_bytes", "recovery_artifact_missing")
        data = _read_bytes(home, locator)
        require(len(data) == descriptor["size"] and _sha(data) == descriptor["sha256"], "recovery_artifact_corrupt")
        locators.add(locator)
        if row["project_id"]:
            project_ids.add(row["project_id"])
    return locators, project_ids


def _memory_contract(conn, home, relative, context):
    from tools.individual_memory_store import IndividualMemoryStore
    store = IndividualMemoryStore(context)
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.row_factory = sqlite3.Row
        store._initialize(expected)
        require(_schema(conn) == _schema(expected), "recovery_memory_schema_mismatch")
    meta = store._meta(conn)
    require(meta["namespace_json"] == store._scope.canonical_json, "recovery_memory_scope_mismatch")
    require(meta["projection_revision"] == meta["revision"], "recovery_memory_projection_pending")
    require(conn.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0] <= 4096, "recovery_row_limit")
    for row in conn.execute("SELECT record_json FROM memory_records"):
        record = json.loads(row[0])
        require(record.get("namespace_id") == store.namespace_id
                and all(record.get("owner_" + key) == value for key, value in artifact_actor(context).items()),
                "recovery_memory_scope_mismatch")
    require(not conn.execute("SELECT 1 FROM memory_heads h LEFT JOIN memory_records r "
            "ON r.record_id=h.record_id AND r.version=h.version WHERE r.record_id IS NULL LIMIT 1").fetchone(),
            "recovery_memory_head_missing")
    records = store._heads(conn)
    hashes = json.loads(meta["projection_hashes"])
    for target, name in (("memory", "MEMORY.md"), ("user", "USER.md")):
        data = _read_bytes(home, str(Path(relative).parent / name))
        require(data == store._projection(records, target, meta["revision"])
                and _sha(data) == hashes.get(target), "recovery_memory_projection_corrupt")
    return {"namespace_id": store.namespace_id, "revision": meta["revision"], "format": "individual_memory.v1"}


def _inspect_restored(home, files, context, expected_schema):
    stores = {}
    for name, kind in files.items():
        if kind not in {"session_store", "project_store", "individual_memory"}:
            continue
        with _database(home / name) as conn:
            _check_sqlite(conn)
            stores[name] = {"kind": kind, "schema_digest": _schema(conn)}
            if kind == "individual_memory":
                stores[name].update(_memory_contract(conn, home, name, context))
            if kind == "project_store":
                from hermes_cli.projects_db import SCHEMA_SQL, _REVISION_TRIGGERS
                with closing(sqlite3.connect(":memory:")) as expected:
                    expected.executescript(SCHEMA_SQL + _REVISION_TRIGGERS)
                    require(_schema(conn) == _schema(expected), "recovery_project_schema_mismatch")
                require(not conn.execute("SELECT 1 FROM projects WHERE owner_principal_id IS NOT ? LIMIT 1",
                        (context.identity.principal_id,)).fetchone(), "recovery_project_scope_mismatch")
    with _database(home / "state.db") as conn:
        safety = _state_contract(conn, context, expected_schema)
        referenced, projects = _artifact_contract(conn, home, files, context)
        from hermes_state_opportunities import validate_opportunity_recovery
        projects.update(validate_opportunity_recovery(conn, artifact_actor(context), max_rows=MAX_ROWS))
    require(not projects or "projects.db" in stores, "recovery_project_store_missing")
    if projects:
        with _database(home / "projects.db") as conn:
            require(projects <= {row[0] for row in conn.execute("SELECT id FROM projects")}, "recovery_project_missing")
    from agent.operations_checkpoint_recovery import reconstruct_on_conn
    from hermes_state import SessionDB
    checkpoints = {}
    with closing(SessionDB(home / "state.db", read_only=True)) as reader:
        with reader._runtime_read() as conn:
            for row in conn.execute("SELECT session_id FROM runtime_state"):
                reader._runtime_snapshot_on_conn(conn, row[0])
            for row in conn.execute("SELECT session_id FROM runtime_checkpoints"):
                checkpoints[row[0]] = reconstruct_on_conn(reader, conn, row[0], context)[1]
    owners = {kind: sorted(name for name, value in files.items() if value == kind) for kind in
              ("session_store", "project_store", "artifact_bytes", "individual_memory", "memory_projection")}
    return {"stores": stores, "checkpoint_reconstruction": checkpoints, "owning_store_inventory": owners, "safety": safety, "artifact_count": len(referenced),
            "orphan_artifact_count": sum(kind == "artifact_bytes" and name not in referenced for name, kind in files.items())}


def snapshot_owning_stores(db, context, *, staging_directory, code_version):
    """Freeze supported writers, reuse updater SQLite snapshots, verify before return.

    No archive is persisted here. The caller must explicitly select custody and
    seal the returned sensitive ZIP if an encrypted archive is required.
    """
    from hermes_cli.backup import _backup_operation_lock, _zip_sqlite_snapshot
    from hermes_state_common import SCHEMA_VERSION
    import fcntl
    authority(db, context)
    require(isinstance(code_version, str) and 0 < len(code_version) <= 128, "recovery_code_version_required")
    home = Path(context.profile_home)
    with _backup_operation_lock(home), ExitStack() as stack:
        files, stamps, excluded = _inventory(home, context)
        # Match established project -> SessionDB lock order. Memory's lock covers
        # its catalog plus derived files; do not call its mutating read/repair API.
        for name, kind in files.items():
            if kind == "individual_memory":
                lock_path = str(Path(name).parent / "store.lock")
                _regular(home, lock_path)
                handle = stack.enter_context((home / lock_path).open("rb"))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        order = sorted((name for name, kind in files.items() if kind in {"project_store", "session_store", "individual_memory"}),
                       key=lambda name: (name != "projects.db", name != "state.db", name))
        locked = {name: stack.enter_context(_database(home / name, lock=True)) for name in order}
        require(sum(conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
                    for conn in locked.values()) <= _MAX_BYTES, "recovery_size_limit")
        require(not locked["state.db"].execute("SELECT 1 FROM session_turn_leases WHERE expires_at>strftime('%s','now') LIMIT 1").fetchone(),
                "recovery_profile_busy")
        output = io.BytesIO()
        with tempfile.TemporaryDirectory(prefix="operations-stage-", dir=staging_directory) as staging:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                total = 0
                for name, kind in files.items():
                    if name in locked:
                        size = _zip_sqlite_snapshot(archive, home / name, Path(name), Path(staging) / "drill.zip")
                        require(size is not None, "recovery_snapshot_failed")
                    else:
                        data = _read_bytes(home, name)
                        archive.writestr(name, data)
                        size = len(data)
                    total += size
                    require(total <= _MAX_BYTES, "recovery_size_limit")
            require(_inventory(home, context) == (files, stamps, excluded), "recovery_inventory_changed")
            payload = output.getvalue()
            validate_archive(payload)
            with _materialize(payload, staging) as restored:
                contract = _inspect_restored(restored, files, context, SCHEMA_VERSION)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                inventory = [{"path": name, "kind": kind, "size": archive.getinfo(name).file_size,
                              "sha256": _sha(archive.read(name))} for name, kind in sorted(files.items())]
            manifest = {"format": FORMAT, "code_version": code_version, "store_schema": SCHEMA_VERSION,
                "actor": artifact_actor(context), "profile_home_digest": context.identity.profile_home_digest,
                "config_digest": context.config_digest, "policy_digest": context.policy.digest,
                "files": inventory, "excluded_root_entries": excluded, "limitations": EXCLUSIONS,
                "source_memory_backend": context.policy.memory_backend, **contract}
            manifest_bytes = _json(manifest)
            require(len(manifest_bytes) <= 1024 * 1024, "recovery_manifest_limit")
            with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST, manifest_bytes)
            result = output.getvalue()
            validate_archive(result)
            return result


@contextmanager
def _materialize(archive, temporary_parent):
    names = validate_archive(archive)
    with tempfile.TemporaryDirectory(prefix="operations-restore-", dir=temporary_parent) as directory:
        home = Path(directory)
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            for name in names:
                if name == MANIFEST:
                    continue
                path = home / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(source.read(name))
        yield home


def verify_owning_store_restore(archive, context, *, expected_store_schema, expected_code_version, temporary_parent):
    """Validate an entire bundle in a fresh temporary home, never open a writer.

    Source identity remains source-bound: no credentials/config or identity
    remapping is installed and no restored runtime is started. Temporary copies
    are removed on success and Python exceptions. A host/process crash can leave
    plaintext staging behind; this module makes no secure-erasure claim.
    """
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == context, "operations_identity_required")
    names = validate_archive(archive)
    require(MANIFEST in names, "recovery_manifest_missing")
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        require(source.getinfo(MANIFEST).file_size <= 1024 * 1024, "recovery_manifest_limit")
        manifest = json.loads(source.read(MANIFEST))
        require(manifest.get("format") == FORMAT and manifest.get("code_version") == expected_code_version
                and type(expected_store_schema) is int and manifest.get("store_schema") == expected_store_schema,
                "recovery_version_mismatch")
        require(manifest.get("actor") == artifact_actor(context)
                and manifest.get("profile_home_digest") == context.identity.profile_home_digest
                and manifest.get("config_digest") == context.config_digest
                and manifest.get("policy_digest") == context.policy.digest, "recovery_scope_mismatch")
        items = manifest.get("files")
        require(isinstance(items, list) and 0 < len(items) <= MAX_FILES, "recovery_inventory_invalid")
        files = {item["path"]: item["kind"] for item in items}
        require(len(files) == len(items) and sorted([*files, MANIFEST]) == names, "recovery_inventory_mismatch")
        for item in items:
            data = source.read(item["path"])
            require(len(data) == item["size"] and _sha(data) == item["sha256"], "recovery_member_corrupt")
    with _materialize(archive, temporary_parent) as restored:
        # Independently rediscover, preventing a manifest from hiding local bytes
        # or relabeling a catalog as an unchecked blob.
        actual, _, _ = _inventory(restored, context)
        require(actual == files, "recovery_inventory_mismatch")
        contract = _inspect_restored(restored, files, context, expected_store_schema)
        require(all(contract[key] == manifest.get(key) for key in contract), "recovery_contract_mismatch")
        return {"restored": True, "scope": FORMAT, "all_supported_owning_stores_validated": True,
                "store_count": len(contract["stores"]), "checkpoint_count": len(contract["checkpoint_reconstruction"]),
                "artifact_count": contract["artifact_count"],
                "orphan_artifact_count": contract["orphan_artifact_count"], "safety": contract["safety"],
                "bundle_sha256": _sha(archive), "live_profile_modified": False,
                "full_profile_cutover_certified": False, "external_effects_replayed": 0,
                "limitations": list(EXCLUSIONS)}
