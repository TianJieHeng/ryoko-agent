"""Structured individual memory with a derived, checked Markdown projection.

SQLite is the only authority. The inherited entry operations keep the established
MemoryStore contract, but all reads/writes bind to the constructor's identity and
commit versioned records first. A projection crash is recoverable; external edits
are detected and never silently imported or overwritten.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from uuid import uuid4

from agent.individual_memory_scope import IndividualMemoryScope, checked_file
from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore, _scan_memory_content

_KINDS = {"stated_fact", "inference", "preference", "decision", "procedure_reference"}
_VALIDITY = {"valid", "uncertain", "invalid"}
_FILES = {"memory": "MEMORY.md", "user": "USER.md"}
_MAX_TEXT = 65536


class IndividualMemoryError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _text(value, field, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise IndividualMemoryError("invalid_record", f"A bounded {field} is required")
    return value


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise IndividualMemoryError("invalid_record", f"Invalid {field}")
    return value


class IndividualMemoryStore(MemoryStore):
    def __init__(self, context, memory_char_limit=2200, user_char_limit=1375, *,
                 memory_enabled=True, user_profile_enabled=True):
        self._scope = IndividualMemoryScope.from_context(context)
        self._scope.assert_current()
        for value in (memory_char_limit, user_char_limit):
            if type(value) is not int or not 0 <= value <= 65536:
                raise IndividualMemoryError("invalid_capacity", "Individual memory limits must be bounded integers")
        super().__init__(memory_char_limit, user_char_limit, memory_enabled=memory_enabled,
                         user_profile_enabled=user_profile_enabled)
        self.snapshot_revision = None
        self._snapshot_loaded = False

    @property
    def memory_entries(self):
        self._check()
        return list(self._memory_entries) if self.target_enabled("memory") else []

    @memory_entries.setter
    def memory_entries(self, entries):
        self._check()
        self._memory_entries = list(entries)

    @property
    def user_entries(self):
        self._check()
        return list(self._user_entries) if self.target_enabled("user") else []

    @user_entries.setter
    def user_entries(self, entries):
        self._check()
        self._user_entries = list(entries)

    def target_enabled(self, target):
        self._check(target)
        return super().target_enabled(target)

    def _project_guard(self, scope, permission="read"):
        context = self._check()
        if scope == "individual":
            return nullcontext()
        if not isinstance(scope, str) or not scope.startswith("project:") or not scope[8:]:
            raise IndividualMemoryError("invalid_scope", "Memory scope must be individual or an exact project reference")
        from agent.project_context import project_access
        actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
        return project_access(context).guard(scope[8:], actor, permission)

    @property
    def namespace_id(self):
        self._check()
        return self._scope.namespace_id

    def _path_for(self, target):
        self._check(target)
        return self._scope.directory / _FILES[target]

    def _check(self, target=None):
        context = self._scope.assert_current()
        if target is not None and target not in _FILES:
            raise IndividualMemoryError("invalid_target", "Memory target must be memory or user")
        return context

    @contextmanager
    def _transaction(self, *, write=False):
        self._check()
        # fcntl serializes the catalog + derived file update as one recoverable
        # sequence across processes. Hosts lacking checked dirfd I/O fail closed.
        import fcntl
        with self._scope.open_directory() as directory:
            lock = checked_file(directory, "store.lock", create=True)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._check()
                state_fd = checked_file(directory, "records.sqlite3", create=True)
                try:
                    for suffix in ("-journal", "-wal", "-shm"):
                        try:
                            other = checked_file(directory, "records.sqlite3" + suffix)
                        except FileNotFoundError:
                            continue
                        os.close(other)
                    db = sqlite3.connect(f"/proc/self/fd/{directory}/records.sqlite3", timeout=15)
                    db.row_factory = sqlite3.Row
                    try:
                        original = os.fstat(state_fd)
                        current = os.stat("records.sqlite3", dir_fd=directory, follow_symlinks=False)
                        if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                            raise IndividualMemoryError("state_changed", "Memory state changed during open")
                        db.execute("PRAGMA synchronous=FULL")
                        db.execute("PRAGMA trusted_schema=OFF")
                        page_size = db.execute("PRAGMA page_size").fetchone()[0]
                        db.execute(f"PRAGMA max_page_count={32 * 1024 * 1024 // page_size}")
                        self._initialize(db)
                        self._sync_projection(db, directory)
                        db.execute("BEGIN IMMEDIATE")
                        if write and os.fstat(state_fd).st_size > 32 * 1024 * 1024:
                            raise IndividualMemoryError("history_capacity", "Memory catalog retention byte limit reached")
                        if write and self._meta(db)["retention_state"] == "archived":
                            raise IndividualMemoryError("memory_archived", "Archived individual memory is read-only")
                        try:
                            yield db
                            db.commit()
                        except BaseException:
                            db.rollback()
                            raise
                        self._sync_projection(db, directory)
                    finally:
                        db.close()
                finally:
                    os.close(state_fd)
            finally:
                os.close(lock)

    def _initialize(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_meta (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), namespace_json TEXT NOT NULL,
              revision INTEGER NOT NULL, retention_state TEXT NOT NULL,
              projection_revision INTEGER NOT NULL, projection_hashes TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_records (
              record_id TEXT NOT NULL, version INTEGER NOT NULL, revision INTEGER NOT NULL,
              target TEXT NOT NULL, record_json TEXT NOT NULL,
              PRIMARY KEY(record_id, version));
            CREATE TABLE IF NOT EXISTS memory_heads (
              record_id TEXT PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_conflicts (
              conflict_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, expected_version INTEGER NOT NULL,
              actual_version INTEGER NOT NULL, proposed_json TEXT NOT NULL, created_at REAL NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO memory_meta VALUES(1,?,0,'active',-1,'{}')",
                   (self._scope.canonical_json,))
        db.commit()
        if self._meta(db)["namespace_json"] != self._scope.canonical_json:
            raise IndividualMemoryError("namespace_mismatch", "Memory catalog belongs to a different identity")

    @staticmethod
    def _meta(db):
        return dict(db.execute("SELECT * FROM memory_meta WHERE singleton=1").fetchone())

    def _heads(self, db, target=None):
        rows = db.execute("SELECT r.record_json FROM memory_records r JOIN memory_heads h "
                          "ON r.record_id=h.record_id AND r.version=h.version ORDER BY r.rowid").fetchall()
        records = sorted((json.loads(row[0]) for row in rows), key=lambda row: (row["created_at"], row["record_id"]))
        for record in records:
            if (record.get("namespace_id") != self.namespace_id
                    or record.get("owner_agent_id") != self._scope.agent_id
                    or record.get("owner_principal_id") != self._scope.principal_id
                    or record.get("owner_profile_id") != self._scope.profile_id
                    or record.get("target") not in _FILES
                    or record.get("kind") not in _KINDS
                    or record.get("validity") not in _VALIDITY
                    or record.get("deletion_state") not in {"present", "deleted"}):
                raise IndividualMemoryError("catalog_invalid", "Structured memory ownership or record schema is invalid")
        return [record for record in records if target is None or record["target"] == target]

    def _visible(self, record, now=None):
        now = time.time() if now is None else now
        return (self.target_enabled(record["target"])
                and record["deletion_state"] == "present" and record["validity"] != "invalid"
                and record["valid_from"] <= now
                and (record["valid_to"] is None or record["valid_to"] > now))

    def _projection(self, records, target, revision):
        lines = ["# Individual memory projection", "",
                 f"Namespace: {self.namespace_id}", f"Catalog revision: {revision}",
                 "Generated from the structured catalog. Edit through the memory API.", ""]
        for record in records:
            if record["target"] == target and record["deletion_state"] == "present":
                lines.extend([f"## {record['record_id']} v{record['version']} ({record['kind']})",
                              f"Validity: {record['validity']}; source: {record['source_ref']}",
                              record["content"], ""])
        return ("\n".join(lines) + "\n").encode()

    @staticmethod
    def _read_projection(directory, name):
        try:
            fd = checked_file(directory, name)
        except FileNotFoundError:
            return None
        try:
            if os.fstat(fd).st_size > 4 * 1024 * 1024:
                raise IndividualMemoryError("projection_drift", "Memory projection exceeds its bound")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(4 * 1024 * 1024 + 1)
        finally:
            os.close(fd)

    @staticmethod
    def _write_projection(directory, name, data):
        temporary = ".projection-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _sync_projection(self, db, directory):
        meta = self._meta(db)
        records = self._heads(db)
        expected = {target: self._projection(records, target, meta["revision"]) for target in _FILES}
        previous = json.loads(meta["projection_hashes"])
        observed = {target: self._read_projection(directory, name) for target, name in _FILES.items()}
        for target, raw in observed.items():
            if raw is None:
                continue
            allowed = {_hash(expected[target])}
            if meta["projection_revision"] != meta["revision"] and target in previous:
                allowed.add(previous[target])
            if _hash(raw) not in allowed:
                raise IndividualMemoryError("projection_drift", "Memory projection changed outside the catalog; preserve and review it")
        for target, raw in observed.items():
            if raw != expected[target]:
                self._write_projection(directory, _FILES[target], expected[target])
        if meta["projection_revision"] != meta["revision"]:
            db.execute("UPDATE memory_meta SET projection_revision=revision,projection_hashes=? WHERE singleton=1",
                       (_json({key: _hash(value) for key, value in expected.items()}),))
            db.commit()

    def _record(self, content, *, target="memory", kind="stated_fact", source_ref=None, author=None,
                valid_from=None, valid_to=None, confidence=None, validity="valid", scope="individual"):
        context = self._check(target)
        _text(content, "content", _MAX_TEXT)
        if not self.target_enabled(target):
            raise IndividualMemoryError("memory_disabled", "This individual memory target is disabled")
        if scan_error := _scan_memory_content(content):
            raise IndividualMemoryError("unsafe_content", scan_error)
        if kind not in _KINDS or validity not in _VALIDITY:
            raise IndividualMemoryError("invalid_record", "Unsupported memory kind, validity, or scope")
        with self._project_guard(scope, "write"):
            pass
        source_ref = source_ref or f"session:{context.identity.session_id}"
        author = author or context.identity.agent_id
        _text(source_ref, "source reference")
        _text(author, "author", 256)
        if kind == "procedure_reference":
            if re.fullmatch(r"workflow:[A-Za-z0-9][A-Za-z0-9_-]{0,63}:[1-9][0-9]{0,9}", source_ref) is None:
                raise IndividualMemoryError("invalid_record", "Procedure references require workflow:<id>:<positive-version>")
            # BE11 owns workflow resolution. A well-formed reference is not
            # evidence that its canonical procedure exists or was validated.
            if validity == "valid":
                validity = "uncertain"
        now = time.time()
        valid_from = now if valid_from is None else valid_from
        for value in (valid_from, valid_to, confidence):
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
                raise IndividualMemoryError("invalid_record", "Invalid memory time or confidence")
        if valid_to is not None and valid_to <= valid_from:
            raise IndividualMemoryError("invalid_record", "valid_to must follow valid_from")
        if confidence is not None and not 0 <= confidence <= 1:
            raise IndividualMemoryError("invalid_record", "Confidence must be between zero and one")
        return {"owner_agent_id": self._scope.agent_id, "owner_principal_id": self._scope.principal_id,
                "owner_profile_id": self._scope.profile_id, "namespace_id": self.namespace_id,
                "target": target, "kind": kind, "content": content, "source_ref": source_ref,
                "author": author, "created_at": now, "updated_at": now, "valid_from": valid_from,
                "valid_to": valid_to, "confidence": confidence, "validity": validity,
                "scope": scope, "deletion_state": "present", "deleted_at": None}

    def _put(self, db, record, record_id, expected_version, revision):
        _text(record_id, "record ID", 256)
        _integer(expected_version, "expected version")
        row = db.execute("SELECT version FROM memory_heads WHERE record_id=?", (record_id,)).fetchone()
        actual = row[0] if row else 0
        if actual != expected_version:
            if db.execute("SELECT count(*) FROM memory_conflicts").fetchone()[0] >= 256:
                raise IndividualMemoryError("history_capacity", "Memory conflict retention limit reached")
            conflict_id = "conflict_" + uuid4().hex
            db.execute("INSERT INTO memory_conflicts VALUES(?,?,?,?,?,?)",
                       (conflict_id, record_id, expected_version, actual, _json(record), time.time()))
            return {"success": False, "code": "version_conflict", "conflict_id": conflict_id,
                    "record_id": record_id, "expected_version": expected_version, "current_version": actual}
        previous = None if not row else json.loads(db.execute(
            "SELECT record_json FROM memory_records WHERE record_id=? AND version=?", (record_id, actual)).fetchone()[0])
        if previous:
            if previous["deletion_state"] == "deleted":
                raise IndividualMemoryError("record_deleted", "Deleted memory cannot be resurrected; create a new record")
            if (record["target"], record["scope"]) != (previous["target"], previous["scope"]):
                raise IndividualMemoryError("scope_changed", "A memory correction cannot move its scope or target")
            record["created_at"] = previous["created_at"]
        if db.execute("SELECT count(*) FROM memory_records").fetchone()[0] >= 4096:
            raise IndividualMemoryError("history_capacity", "Memory version retention limit reached")
        if not row and db.execute("SELECT count(*) FROM memory_heads").fetchone()[0] >= 1024:
            raise IndividualMemoryError("history_capacity", "Memory record retention limit reached")
        record.update(record_id=record_id, version=actual + 1, revision=revision,
                      supersedes_version=actual or None)
        db.execute("INSERT INTO memory_records VALUES(?,?,?,?,?)",
                   (record_id, actual + 1, revision, record["target"], _json(record)))
        db.execute("INSERT INTO memory_heads VALUES(?,?) ON CONFLICT(record_id) DO UPDATE SET version=excluded.version",
                   (record_id, actual + 1))
        db.execute("UPDATE memory_meta SET revision=? WHERE singleton=1", (revision,))
        return {"success": True, "record": record, "acknowledged_version": actual + 1, "revision": revision}

    def write_record(self, content, *, record_id=None, expected_version=0, **metadata):
        if record_id is not None:
            try:
                self.read_record(record_id)
            except IndividualMemoryError as exc:
                if exc.code != "record_not_found":
                    raise
        record = self._record(content, **metadata)
        with self._project_guard(record["scope"], "write"), self._transaction(write=True) as db:
            current = self._heads(db, record["target"])
            entries = [row["content"] for row in current if self._visible(row) and row["record_id"] != record_id]
            if len(ENTRY_DELIMITER.join(entries + [content])) > self._char_limit(record["target"]):
                raise IndividualMemoryError("memory_capacity", "Individual memory exceeds its configured character budget")
            result = self._put(db, record, record_id if record_id is not None else "memory_" + uuid4().hex,
                               expected_version, self._meta(db)["revision"] + 1)
        return result

    def delete_record(self, record_id, *, expected_version):
        prior = self.read_record(record_id)
        with self._project_guard(prior["scope"], "write"), self._transaction(write=True) as db:
            record = next((row for row in self._heads(db) if row["record_id"] == record_id), None)
            if record is None:
                raise IndividualMemoryError("record_not_found", "Memory record does not exist")
            record.update(content=None, deletion_state="deleted", deleted_at=time.time(), updated_at=time.time())
            result = self._put(db, record, record_id, expected_version, self._meta(db)["revision"] + 1)
        return result

    def read_record(self, record_id, *, version=None):
        record = self._read_record(record_id, version=version)
        with self._project_guard(record["scope"]):
            return record

    def _read_record(self, record_id, *, version=None):
        with self._transaction() as db:
            head = next((row for row in self._heads(db) if row["record_id"] == record_id), None)
            if head is None:
                raise IndividualMemoryError("record_not_found", "Memory record does not exist")
            if not self.target_enabled(head["target"]):
                raise IndividualMemoryError("memory_disabled", "This individual memory target is disabled")
            if version is None or version == head["version"]:
                return head
            _integer(version, "version", 1)
            row = db.execute("SELECT record_json FROM memory_records WHERE record_id=? AND version=?",
                             (record_id, version)).fetchone()
            if row is None:
                raise IndividualMemoryError("record_not_found", "Memory record version does not exist")
            record = json.loads(row[0])
            record.update(validity="superseded", superseded_by_version=head["version"])
            if head["deletion_state"] == "deleted":
                record.update(content=None, deletion_state="deleted", deleted_at=head["deleted_at"])
            return record

    def export_records(self, *, include_deleted=False, project_id=None):
        return self.export_snapshot(include_deleted=include_deleted, project_id=project_id)["records"]

    def export_snapshot(self, *, include_deleted=False, project_id=None):
        scope = "individual" if project_id is None else "project:" + project_id
        with self._project_guard(scope), self._transaction() as db:
            return {"revision": self._meta(db)["revision"],
                    "records": [row for row in self._heads(db) if self.target_enabled(row["target"])
                                and row["scope"] in {"individual", scope} and (include_deleted or row["deletion_state"] == "present")]}

    def read_conflict(self, conflict_id):
        _text(conflict_id, "conflict ID", 256)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM memory_conflicts WHERE conflict_id=?", (conflict_id,)).fetchone()
            if row is None:
                raise IndividualMemoryError("conflict_not_found", "Memory conflict does not exist")
            conflict = dict(row)
            proposed = json.loads(conflict.pop("proposed_json"))
            if not self.target_enabled(proposed["target"]):
                raise IndividualMemoryError("memory_disabled", "This individual memory target is disabled")
            current = next((item for item in self._heads(db) if item["record_id"] == conflict["record_id"]), None)
            if current is not None and current["deletion_state"] == "deleted":
                proposed["content"] = None
        with self._project_guard(proposed["scope"]):
            return {**conflict, "proposed_record": proposed, "status": "unresolved"}

    def recall(self, query="", *, limit=32, project_id=None):
        self._check()
        if not isinstance(query, str) or len(query) > 4096 or type(limit) is not int or not 1 <= limit <= 100:
            raise IndividualMemoryError("invalid_query", "Memory recall requires bounded text and limit")
        scope = "individual" if project_id is None else "project:" + project_id
        with self._project_guard(scope), self._transaction() as db:
            return [row for row in self._heads(db) if row["scope"] in {"individual", scope} and self._visible(row)
                    and (not query or query.casefold() in row["content"].casefold())][:limit]

    def current_revision(self):
        with self._transaction() as db:
            return self._meta(db)["revision"]

    def changes_since(self, revision, *, limit=32, max_chars=8192, project_id=None):
        _integer(revision, "revision")
        if type(limit) is not int or not 1 <= limit <= 100 or type(max_chars) is not int or not 128 <= max_chars <= 65536:
            raise IndividualMemoryError("invalid_query", "Invalid fresh memory bounds")
        scope = "individual" if project_id is None else "project:" + project_id
        with self._project_guard(scope), self._transaction() as db:
            latest = self._meta(db)["revision"]
            if revision > latest:
                raise IndividualMemoryError("invalid_cursor", "Memory cursor is ahead of its catalog")
            pending = sorted((row for row in self._heads(db) if self.target_enabled(row["target"])
                              and row["revision"] > revision and row["scope"] in {"individual", scope}),
                             key=lambda row: (row["revision"], row["record_id"]))
            records, cursor, used = [], revision, 0
            for group_revision in sorted({row["revision"] for row in pending}):
                group = [dict(row) for row in pending if row["revision"] == group_revision]
                for row in group:
                    if not self._visible(row):
                        row["content"] = None
                        if row["deletion_state"] == "present":
                            row["validity"] = "invalid"
                cost = len(_json(group))
                if len(records) + len(group) > limit or used + cost > max_chars:
                    break
                records.extend(group)
                used += cost
                cursor = group_revision
            truncated = len(records) != len(pending)
            if not truncated:
                # Time can invalidate an unchanged version already in a frozen
                # prefix. Recheck explicit windows without pretending a new
                # catalog mutation happened or advancing a skipped cursor.
                temporal = [dict(row) for row in self._heads(db)
                            if self.target_enabled(row["target"]) and row["revision"] <= revision
                            and row["scope"] in {"individual", scope} and row["deletion_state"] == "present"
                            and (row["valid_to"] is not None or row["valid_from"] > row["updated_at"])]
                for row in temporal:
                    if not self._visible(row):
                        row.update(content=None, validity="invalid")
                    cost = len(_json(row))
                    if len(records) == limit or used + cost > max_chars:
                        truncated = True
                        break
                    records.append(row)
                    used += cost
            return {"namespace_id": self.namespace_id, "revision": cursor if truncated else latest,
                    "records": records, "truncated": truncated}

    def retention(self, state=None):
        if state is not None and state not in {"active", "retained", "archived"}:
            raise IndividualMemoryError("invalid_retention", "Unknown memory retention state")
        with self._transaction() as db:
            if state is not None:
                db.execute("UPDATE memory_meta SET retention_state=? WHERE singleton=1", (state,))
            return {"lifecycle": self._scope.lifecycle, "retention_state": self._meta(db)["retention_state"],
                    "automatic_deletion": False}

    def close(self):
        self._check()  # Close releases no persistent namespace or resume authority.

    def load_from_disk(self):
        self._load(freeze=not self._snapshot_loaded)

    def refresh_snapshot(self):
        """Only the caller's explicit cache-aware boundary may replace this snapshot."""
        self._load(freeze=True)

    def _load(self, *, freeze):
        from tools.threat_patterns import scan_for_threats
        with self._transaction() as db:
            records = self._heads(db)
            for target in _FILES:
                entries = [row["content"] for row in records if row["target"] == target and row["scope"] == "individual" and self._visible(row)]
                self._set_entries(target, entries)
                if freeze:
                    safe = [entry if not scan_for_threats(entry, scope="strict")
                            else "[BLOCKED: unsafe individual memory entry]" for entry in entries]
                    self._system_prompt_snapshot[target] = self._render_block(target, safe)
            if freeze:
                self.snapshot_revision = self._meta(db)["revision"]
                self._snapshot_loaded = True

    def format_for_system_prompt(self, target):
        self._check(target)
        if not self.target_enabled(target):
            return None
        return super().format_for_system_prompt(target)

    def _mutate(self, target, mutate, *, skip_drift=False):
        self._check(target)
        if not self.target_enabled(target):
            raise IndividualMemoryError("memory_disabled", "This individual memory target is disabled")
        with self._transaction(write=True) as db:
            heads = [row for row in self._heads(db, target) if row["scope"] == "individual" and self._visible(row)]
            old = [row["content"] for row in heads]
            self._set_entries(target, old)
            result = mutate(list(old), self._char_limit(target))
            if isinstance(result, dict):
                return result
            new = result[0]
            revision = self._meta(db)["revision"] + 1
            # Preserve exact unchanged records; pair replacement positions with
            # removed records, making supersession explicit rather than an overwrite.
            removed = [row for row in heads if row["content"] not in new]
            added = [entry for entry in new if entry not in old]
            for index, row in enumerate(removed):
                if index < len(added):
                    record = self._record(added[index], target=target, kind=row["kind"], source_ref=row["source_ref"])
                else:
                    record = {**row, "content": None, "deletion_state": "deleted",
                              "deleted_at": time.time(), "updated_at": time.time()}
                self._put(db, record, row["record_id"], row["version"], revision)
            for entry in added[len(removed):]:
                self._put(db, self._record(entry, target=target), "memory_" + uuid4().hex, 0, revision)
            self._set_entries(target, new)
            response = self._success_response(target, result[1], **(result[2] if len(result) > 2 else {}))
            response.update(namespace_id=self.namespace_id, revision=self._meta(db)["revision"])
        return response


def create_individual_memory_store(context, memory_char_limit=2200, user_char_limit=1375, *,
                                   memory_enabled=True, user_profile_enabled=True):
    return IndividualMemoryStore(context, memory_char_limit, user_char_limit,
                                 memory_enabled=memory_enabled, user_profile_enabled=user_profile_enabled)
