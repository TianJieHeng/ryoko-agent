"""Owned opportunity review metadata on the canonical SessionDB writer.

Review choices do not create missions, commitments, approvals or runtime commands.
Every acceptance rechecks the current source in the same transaction as its CAS.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import time

from agent.project_opportunities import changed_reason, current_evidence, scan_evidence
from hermes_cli.project_sources import source_authority
from hermes_state_workflows import canonical, digest, require

_MAX_ROWS = 16384
_MAX_HISTORY = 65536
_MAX_RESULT_BYTES = 128 * 1024
_DISPOSITIONS = frozenset({"proposed", "saved", "dismissed", "accepted"})


def _bounded(value):
    require(len(canonical(value).encode()) <= _MAX_RESULT_BYTES,
            "opportunity_result_bound", "Select fewer projects or a smaller result limit")
    return value


def _limit(value):
    require(type(value) is int and 1 <= value <= 50, "invalid_opportunity", "Review limits must be between one and fifty")
    return value


def _identifier(value):
    require(isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value
            and not any(ord(char) < 32 for char in value), "invalid_opportunity", "An exact bounded identifier is required")
    return value


class OpportunityRegistry:
    def __init__(self, context, db):
        self.context, self.db = context, db
        self.actor, self.access = source_authority(context, db)
        self.owner = canonical(self.actor)

    @contextmanager
    def _scope(self, projects):
        require(isinstance(projects, list) and 1 <= len(projects) <= 8,
                "invalid_opportunity", "Select one to eight projects explicitly")
        for project in projects:
            _identifier(project)
        require(len(projects) == len(set(projects)), "invalid_opportunity", "Selected projects must be distinct")
        with ExitStack() as stack:
            # All selected grants are checked before enumerating any source rows.
            for project in sorted(projects):
                stack.enter_context(self.access.guard(project, self.actor, "read"))
            yield

    def _control(self, control, operation):
        from agent.project_opportunities import OpportunityControl
        require(isinstance(control, OpportunityControl) and control.context == self.context and control.db is self.db,
                "opportunity_human_control_required", "An owned review control is required")
        control.assert_current("runtime.opportunity." + operation)

    @staticmethod
    def _capacity(conn, table, maximum):
        require(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] < maximum,
                "opportunity_capacity", "Retained review history capacity reached")

    def _key(self, candidate):
        return "opportunity_" + digest({"owner": self.actor, **{key: candidate[key] for key in ("project_id", "kind", "source_id")}})

    def _row(self, conn, project, candidate_id):
        _identifier(candidate_id)
        row = conn.execute("SELECT * FROM opportunity_candidates WHERE candidate_id=? AND project_id=? AND owner_json=?",
                           (candidate_id, project, self.owner)).fetchone()
        require(row is not None, "opportunity_not_found", "Candidate does not exist in this owner/project scope")
        return row

    def _public(self, conn, record, *, now, refresh_refs=True):
        source = current_evidence(conn, self.actor, record, now)
        current = source is not None and source["evidence_digest"] == record["evidence_digest"]
        public = {key: value for key, value in record.items() if key != "source_id"}
        # Non-material source revisions do not revive dismissed candidates. Current
        # views still cite the exact present canonical revision instead of old metadata.
        if current and refresh_refs:
            public["evidence_refs"] = source["evidence_refs"]
        public["evidence_current"] = current and (refresh_refs or source["evidence_refs"] == record["evidence_refs"])
        return public

    def _history(self, conn, event, record, now):
        self._capacity(conn, "opportunity_history", _MAX_HISTORY)
        conn.execute("INSERT INTO opportunity_history VALUES(?,?,?,?,?,?,?)",
            (record["candidate_id"], record["revision"], record["project_id"], self.owner, event, canonical(record), now))

    def _retain(self, conn, candidate, now):
        identifier = self._key(candidate)
        row = conn.execute("SELECT * FROM opportunity_candidates WHERE candidate_id=? AND owner_json=?",
                           (identifier, self.owner)).fetchone()
        if row and row["evidence_digest"] == candidate["evidence_digest"]:
            return json.loads(row["record_json"])
        old = json.loads(row["record_json"]) if row else None
        record = {**candidate, "candidate_id": identifier, "disposition": "proposed",
                  "revision": row["revision"] + 1 if row else 1,
                  "changed_source_reason": changed_reason(old, candidate) if old else None,
                  "created_at": row["created_at"] if row else now, "updated_at": now, "evidence_current": True}
        if row:
            conn.execute("UPDATE opportunity_candidates SET evidence_digest=?,record_json=?,revision=?,disposition='proposed',updated_at=? "
                         "WHERE candidate_id=? AND revision=?",
                         (record["evidence_digest"], canonical(record), record["revision"], now, identifier, row["revision"]))
        else:
            self._capacity(conn, "opportunity_candidates", _MAX_ROWS)
            conn.execute("INSERT INTO opportunity_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, record["project_id"], self.owner, record["kind"], record["source_id"], record["evidence_digest"],
                 canonical(record), record["revision"], record["disposition"], now, now))
        self._history(conn, "source_changed" if old else "discovered", record, now)
        return record

    def _request(self, conn, operation, request_id, payload, callback):
        _identifier(request_id)
        key = digest({"owner": self.actor, "operation": operation, "request_id": request_id})
        request_digest = digest(payload)
        row = conn.execute("SELECT * FROM opportunity_requests WHERE request_key=? AND owner_json=?", (key, self.owner)).fetchone()
        if row:
            require(row["request_digest"] == request_digest, "idempotency_conflict", "Request identifier is already bound to different review input")
            response = json.loads(row["response_json"])
            now = time.time()
            if operation == "discover":
                visible = []
                for item in response["candidates"]:
                    latest = json.loads(self._row(conn, item["project_id"], item["candidate_id"])["record_json"])
                    if latest["disposition"] in {"dismissed", "accepted"}:
                        response["suppressed_count"] += 1
                        continue
                    projected = self._public(conn, latest, now=now)
                    if projected["evidence_current"]:
                        visible.append(projected)
                response["candidates"] = visible
            else:
                # A disposition retry is a historical receipt, never new approval.
                item = response["candidate"]
                retained = {**item, "source_id": item["suggested_action"]["target_id"]}
                response["candidate"] = self._public(conn, retained, now=now)
            return response
        response = _bounded(callback())
        self._capacity(conn, "opportunity_requests", _MAX_HISTORY)
        conn.execute("INSERT INTO opportunity_requests VALUES(?,?,?,?,?,?)",
                     (key, self.owner, operation, request_digest, canonical(response), time.time()))
        return response

    def discover(self, control, *, project_ids, request_id, limit=20, scan_limit_per_source=20):
        self._control(control, "discover")
        _limit(limit), _limit(scan_limit_per_source)
        with self._scope(project_ids):
            def write(conn):
                self._control(control, "discover")
                def scan():
                    now, candidates, bounds, suppressed = time.time(), [], [], 0
                    for project in project_ids:
                        found, scanned = scan_evidence(conn, self.actor, project, scan_limit_per_source, now)
                        bounds.extend(scanned)
                        for source in found:
                            record = self._retain(conn, source, now)
                            if record["disposition"] in {"dismissed", "accepted"}:
                                suppressed += 1
                            else:
                                candidates.append(self._public(conn, record, now=now))
                    return {"candidates": candidates[:limit], "project_ids": project_ids, "scanned": bounds,
                            "suppressed_count": suppressed, "limit": limit, "result_limit_reached": len(candidates) > limit,
                            "complete": False, "discovery_mode": "bounded_local_rules", "observed_at": now, "tasks_created": False}
                return self._request(conn, "discover", request_id,
                    {"project_ids": project_ids, "limit": limit, "scan_limit_per_source": scan_limit_per_source}, scan)
            return self.db._execute_write(write)

    def list(self, *, project_ids, limit=20, dispositions=None):
        _limit(limit)
        dispositions = ["proposed", "saved"] if dispositions is None else dispositions
        require(isinstance(dispositions, list) and 1 <= len(dispositions) <= 4
                and all(item in _DISPOSITIONS for item in dispositions) and len(set(dispositions)) == len(dispositions),
                "invalid_opportunity", "Select distinct supported review states")
        with self._scope(project_ids), self.db._runtime_read() as conn:
            placeholders = lambda values: ",".join("?" for _ in values)
            rows = conn.execute(f"SELECT record_json FROM opportunity_candidates WHERE owner_json=? "
                f"AND project_id IN ({placeholders(project_ids)}) AND disposition IN ({placeholders(dispositions)}) "
                "ORDER BY updated_at DESC,candidate_id LIMIT ?", (self.owner, *project_ids, *dispositions, limit + 1)).fetchall()
            now = time.time()
            return _bounded({"candidates": [self._public(conn, json.loads(row[0]), now=now) for row in rows[:limit]],
                "project_ids": project_ids, "limit": limit, "result_limit_reached": len(rows) > limit, "complete": False})

    def disposition(self, control, *, project_id, candidate_id, request_id, expected_revision,
                    expected_evidence_digest, disposition):
        self._control(control, "disposition")
        require(type(expected_revision) is int and expected_revision > 0 and disposition in {"saved", "dismissed", "accepted"},
                "invalid_opportunity", "An exact revision and supported human disposition are required")
        payload = {"project_id": project_id, "candidate_id": candidate_id, "expected_revision": expected_revision,
                   "expected_evidence_digest": expected_evidence_digest, "disposition": disposition}
        with self._scope([project_id]):
            def write(conn):
                self._control(control, "disposition")
                def change():
                    row = self._row(conn, project_id, candidate_id)
                    require(row["revision"] == expected_revision and row["evidence_digest"] == expected_evidence_digest,
                            "opportunity_revision_conflict", "Candidate changed; review the latest evidence")
                    record, now = json.loads(row["record_json"]), time.time()
                    fresh = self._public(conn, record, now=now)
                    if disposition in {"saved", "accepted"}:
                        require(fresh["evidence_current"], "opportunity_evidence_changed", "The current source no longer matches this candidate; discover again")
                    record.update(disposition=disposition, revision=row["revision"] + 1, updated_at=now,
                                  evidence_refs=fresh["evidence_refs"], evidence_current=fresh["evidence_current"])
                    conn.execute("UPDATE opportunity_candidates SET disposition=?,revision=?,record_json=?,updated_at=? "
                                 "WHERE candidate_id=? AND revision=?",
                        (disposition, record["revision"], canonical(record), now, candidate_id, expected_revision))
                    self._history(conn, disposition, record, now)
                    return {"candidate": self._public(conn, record, now=now), "tasks_created": False,
                            "execution_authorized": False, "next_step": "open_existing_review_control" if disposition == "accepted" else "review_recorded"}
                return self._request(conn, "disposition", request_id, payload, change)
            return self.db._execute_write(write)

    def history(self, *, project_id, candidate_id, limit=20):
        _limit(limit)
        with self._scope([project_id]), self.db._runtime_read() as conn:
            self._row(conn, project_id, candidate_id)
            rows = conn.execute("SELECT * FROM opportunity_history WHERE candidate_id=? AND project_id=? AND owner_json=? "
                                "ORDER BY revision DESC LIMIT ?", (candidate_id, project_id, self.owner, limit + 1)).fetchall()
            now = time.time()
            return _bounded({"history": [{"event": row["event"], "recorded_at": row["created_at"],
                "candidate": self._public(conn, json.loads(row["record_json"]), now=now, refresh_refs=False)} for row in rows[:limit]],
                "limit": limit, "result_limit_reached": len(rows) > limit, "complete": False})


def validate_opportunity_recovery(conn, actor, *, max_rows):
    """Validate review ownership/history without resolving sources or executing work."""
    from agent.operations_control import require as recovery_require
    from pydantic import ValidationError
    from tui_gateway.contracts.opportunities import (
        OpportunityCandidate, OpportunityDiscoverResult, OpportunityDispositionResult,
    )
    owner, projects, candidates, revisions = canonical(actor), set(), {}, {}
    rows_by_table = {}
    for table in ("opportunity_candidates", "opportunity_history", "opportunity_requests"):
        rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (max_rows + 1,)).fetchall()
        recovery_require(len(rows) <= max_rows, "recovery_row_limit")
        recovery_require(all(row["owner_json"] == owner for row in rows), "recovery_opportunity_scope_mismatch")
        rows_by_table[table] = rows

    def candidate_record(row):
        value = json.loads(row["record_json"])
        public = {key: item for key, item in value.items() if key != "source_id"}
        OpportunityCandidate.model_validate(public)
        recovery_require(value["candidate_id"] == row["candidate_id"] and value["project_id"] == row["project_id"]
                         and value["revision"] == row["revision"] and value["authorized_project_refs"] == [row["project_id"]]
                         and all(ref["project_id"] == row["project_id"] for ref in value["evidence_refs"]),
                         "recovery_opportunity_scope_mismatch")
        projects.add(row["project_id"])
        return value

    try:
        for row in rows_by_table["opportunity_candidates"]:
            value = candidate_record(row)
            recovery_require(row["candidate_id"] == "opportunity_" + digest({"owner": actor,
                **{key: value[key] for key in ("project_id", "kind", "source_id")}})
                and all(row[key] == value[key] for key in ("kind", "source_id", "evidence_digest", "disposition")),
                "recovery_opportunity_record_invalid")
            candidates[row["candidate_id"]] = value
        for row in rows_by_table["opportunity_history"]:
            value = candidate_record(row)
            latest = candidates.get(row["candidate_id"])
            recovery_require(latest is not None and latest["project_id"] == value["project_id"]
                and row["event"] in {"discovered", "source_changed", "saved", "dismissed", "accepted"},
                "recovery_opportunity_history_invalid")
            revisions.setdefault(row["candidate_id"], {})[row["revision"]] = value
        for identifier, value in candidates.items():
            history = revisions.get(identifier, {})
            recovery_require(len(history) == value["revision"] and all(index == revision for index, revision in enumerate(sorted(history), 1))
                             and history[value["revision"]] == value,
                             "recovery_opportunity_history_invalid")
        models = {"discover": OpportunityDiscoverResult, "disposition": OpportunityDispositionResult}
        for row in rows_by_table["opportunity_requests"]:
            recovery_require(row["operation"] in models, "recovery_opportunity_record_invalid")
            value = json.loads(row["response_json"])
            models[row["operation"]].model_validate(value)
            projects.update(value.get("project_ids", []))
            for item in value.get("candidates", []) + ([value["candidate"]] if "candidate" in value else []):
                known = candidates.get(item["candidate_id"])
                recovery_require(known is not None and known["project_id"] == item["project_id"]
                    and item["authorized_project_refs"] == [item["project_id"]]
                    and all(ref["project_id"] == item["project_id"] for ref in item["evidence_refs"]),
                    "recovery_opportunity_scope_mismatch")
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        from agent.operations_control import OperationsError
        raise OperationsError("recovery_opportunity_record_invalid", "Recovery opportunity record invalid") from exc
    return projects
