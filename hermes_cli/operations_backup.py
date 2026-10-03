"""Authenticated backup envelopes and isolated SessionDB restore drills.

This does NOT encrypt the live database, filesystem, logs, temporary staging, or
the existing updater's snapshots. Keys are supplied in memory by an authorized
custody layer; key generation, persistence and deployment are deliberately absent.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import stat
import tempfile
import zipfile

_MAX_BYTES = 64 * 1024 * 1024
_FORMAT = "ryoko-authenticated-backup-v1"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _key(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("external_256_bit_key_required")
    return key


def validate_archive(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_BYTES:
        raise ValueError("backup_size_limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > 4096 or sum(row.file_size for row in entries) > _MAX_BYTES:
            raise ValueError("backup_expansion_limit")
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (entry.filename in names or path.is_absolute() or ".." in path.parts
                    or path.as_posix() != entry.filename or not path.parts or entry.is_dir()
                    or ":" in entry.filename or "\\" in entry.filename
                    or (stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG})
                    or entry.flag_bits & 1):
                raise ValueError("unsafe_backup_member")
            names.add(entry.filename)
        if archive.testzip() is not None:
            raise ValueError("backup_crc_failed")
    return sorted(names)


def seal_backup(archive, *, key, key_id, store_schema, code_version):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    for value in (key_id, code_version):
        if not isinstance(value, str) or not 0 < len(value) <= 128 or any(ord(c) < 32 for c in value):
            raise ValueError("invalid_backup_version")
    if type(store_schema) is not int or store_schema < 1:
        raise ValueError("invalid_backup_schema")
    validate_archive(archive)
    header = {"format": _FORMAT, "key_id": key_id, "store_schema": store_schema,
              "code_version": code_version, "sha256": hashlib.sha256(archive).hexdigest(),
              "size": len(archive), "scope": "archive_only"}
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, archive, _json(header))
    return _json({"header": header, "nonce": base64.b64encode(nonce).decode(),
                  "ciphertext": base64.b64encode(ciphertext).decode()})


def open_backup(envelope, *, key, expected_key_id, supported_schema, expected_code_version):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    if not isinstance(envelope, bytes) or len(envelope) > 2 * _MAX_BYTES:
        raise ValueError("backup_size_limit")
    value = json.loads(envelope)
    if not isinstance(value, dict) or set(value) != {"header", "nonce", "ciphertext"}:
        raise ValueError("invalid_backup_envelope")
    header = value["header"]
    if (not isinstance(header, dict) or set(header) != {"format", "key_id", "store_schema", "code_version", "sha256", "size", "scope"}
            or header["format"] != _FORMAT or header["scope"] != "archive_only"
            or header["key_id"] != expected_key_id or header["store_schema"] != supported_schema
            or header["code_version"] != expected_code_version):
        raise ValueError("backup_key_or_version_mismatch")
    nonce = base64.b64decode(value["nonce"], validate=True)
    if len(nonce) != 12:
        raise ValueError("invalid_backup_nonce")
    plaintext = AESGCM(key).decrypt(nonce, base64.b64decode(value["ciphertext"], validate=True), _json(header))
    if len(plaintext) != header["size"] or hashlib.sha256(plaintext).hexdigest() != header["sha256"]:
        raise ValueError("backup_integrity_failed")
    validate_archive(plaintext)
    return plaintext


def rotate_backup(envelope, *, old_key, old_key_id, new_key, new_key_id, store_schema, code_version):
    if old_key_id == new_key_id or old_key == new_key:
        raise ValueError("rotation_requires_distinct_external_key")
    archive = open_backup(envelope, key=old_key, expected_key_id=old_key_id,
                          supported_schema=store_schema, expected_code_version=code_version)
    return seal_backup(archive, key=new_key, key_id=new_key_id, store_schema=store_schema, code_version=code_version)


def snapshot_session_store(db, context, *, staging_directory):
    """Reuse the updater's WAL-safe copier, with explicit plaintext staging.

    This is a SessionDB fixture/drill component, not a replacement/tier of the
    updater's full-profile snapshots. Callers must classify staging as sensitive.
    """
    from agent.operations_control import authority
    from hermes_cli.backup import _zip_sqlite_snapshot
    authority(db, context)
    if Path(db.db_path).stat().st_size > _MAX_BYTES:
        raise ValueError("backup_size_limit")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        size = _zip_sqlite_snapshot(archive, Path(db.db_path), Path("state.db"),
                                    Path(staging_directory) / "drill.zip")
        if size is None or size > _MAX_BYTES:
            raise ValueError("backup_snapshot_failed")
    data = output.getvalue()
    validate_archive(data)
    return data


def verify_session_store_restore(archive, *, expected_sessions, expected_store_schema, temporary_parent):
    """Restore to a fresh temporary directory, never to a live profile."""
    names = validate_archive(archive)
    if names != ["state.db"]:
        raise ValueError("restore_drill_requires_session_store_only")
    with tempfile.TemporaryDirectory(prefix="operations-restore-", dir=temporary_parent) as directory:
        path = Path(directory) / "state.db"
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            path.write_bytes(source.read("state.db"))
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            version = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if version is None or type(expected_store_schema) is not int or version[0] != expected_store_schema:
                raise ValueError("restore_schema_mismatch")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("restore_integrity_failed")
            actual = {row[0] for row in connection.execute("SELECT id FROM sessions")}
            if actual != set(expected_sessions):
                raise ValueError("restore_scope_mismatch")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise ValueError("restore_foreign_key_failed")
        return {"restored": True, "integrity": "ok", "session_count": len(actual),
                "scope": "session_store_only", "full_profile_recovery_certified": False,
                "live_profile_modified": False}
