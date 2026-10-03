"""BE12 finite inbox preparation and explicit accepted-commitment authority.

Imported sources remain untrusted immutable evidence. Only owned human controls
can accept or revise obligations; review reads never revive terminal work. This
registry has no inbox connector, calendar mutation or communication dispatcher.
"""
from __future__ import annotations

import json
import re
import time

from agent.artifact_commands import ArtifactControlRun, assert_artifact_dispatch
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from hermes_state_commitment_sources import (
    calendar_preview, canonical, digest, due_record, identifier, parse_inbox, require, text, timestamp,
)

_TERMINAL = frozenset({"done", "cancelled", "superseded"})
_ACTIVE = frozenset({"ready", "waiting"})
MAX_PUBLIC_BYTES = 120000


def bounded_result(value):
    require(len(canonical(value).encode()) <= MAX_PUBLIC_BYTES,
            "commitment_result_bound", "Select fewer source messages or retrieve obligations individually")
    return value


def assert_commitment_control(run, method):
    from tui_gateway import server
    require(isinstance(run, ArtifactControlRun) and server._current_rpc_method.get() == method,
            "commitment_human_control_required", "Exact owned human commitment control required")
    return assert_artifact_dispatch(run)


class CommitmentRegistry:
    def __init__(self, context, db):
        self.context, self.db = context, db
        self.actor = artifact_actor(context)
        self.access = project_access(context)

    def _key(self, project_id, kind, value):
        return kind + "_" + digest({"actor": self.actor, "project_id": project_id, "value": value})

    def _fence(self, conn, run):
        require(run.db is self.db and run.context == self.context,
                "identity_mismatch", "Commitment owner changed")
        self.db._mission_owner_on_conn(conn, run.session_id, self.actor,
                                      holder=run.holder, generation=run.generation)
        self.db._effect_run_on_conn(conn, run.session_id, self.actor, run.run_id,
                                   run.holder, run.generation, dispatch=True)

    def _source(self, project_id, ref):
        from hermes_cli.domain_media import _read
        raw, mime = _read(self.context, self.db, project_id, ref)
        return raw, mime

    def _json_source(self, project_id, ref):
        raw, mime = self._source(project_id, ref)
        require(mime == "application/json", "invalid_inbox_snapshot", "Source must be an immutable JSON artifact")
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            from hermes_state_runtime import RuntimeStoreError
            raise RuntimeStoreError("invalid_inbox_snapshot", "Source is not valid JSON") from exc
        canonical(record)
        return record

    def _row(self, conn, project_id, commitment_id):
        identifier(commitment_id)
        row = conn.execute("SELECT * FROM accepted_commitments WHERE commitment_id=? AND project_id=? AND owner_json=?",
                           (commitment_id, project_id, canonical(self.actor))).fetchone()
        require(row is not None, "commitment_not_found", "Accepted commitment does not exist in this owner scope")
        return row

    @staticmethod
    def _public(row):
        return {**json.loads(row["record_json"]), "state": row["state"], "revision": row["revision"], "accepted": True}

    @staticmethod
    def _capacity(conn, table, addition=1):
        # Table names are internal constants, never caller-controlled.
        require(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + addition <= 16384,
                "commitment_capacity", "Retained commitment registry capacity reached")

    def prepare_inbox(self, run, project_id, source_ref, selection):
        assert_commitment_control(run, "runtime.inbox.prepare")
        with self.access.guard(project_id, self.actor, "write"):
            source = self._json_source(project_id, source_ref)
            groups = parse_inbox(source, selection)
            preview_id = self._key(project_id, "inbox", {"source_ref": source_ref, "selection": selection})
            candidates = []
            for group in groups:
                for message in group["messages"]:
                    if message["classification"] == "info":
                        continue
                    candidate_id = self._key(project_id, "candidate", {
                        "source_id": source["source_id"], "thread_id": group["thread_id"],
                        "message_id": message["message_id"], "message": {key: value for key, value in message.items()
                            if key not in {"classification", "date_mentions", "classification_status", "urgency", "owner", "accepted"}}})
                    message["candidate_id"] = candidate_id
                    candidates.append({"candidate_id": candidate_id, "project_id": project_id,
                        "source_id": source["source_id"], "thread_id": group["thread_id"],
                        "message_id": message["message_id"], "source_refs": [source_ref],
                        "outcome": message["body"], "owner": None, "classification": message["classification"],
                        "date_mentions": message["date_mentions"], "date_uncertainty": "unresolved",
                        "participants": message["participants"], "attachments": message["attachments"],
                        "source_captured_at": source["captured_at"], "accepted": False})
            preview = {"preview_id": preview_id, "project_id": project_id, "source_ref": source_ref,
                "source_id": source["source_id"], "source_captured_at": source["captured_at"], "selection": selection,
                "groups": groups, "candidate_ids": [row["candidate_id"] for row in candidates],
                "source_status": "imported_snapshot_only", "live_mailbox_checked": False,
                "classification_status": "english_lexical_proposals_review_required", "source_content_is_authority": False}
            bounded_result(preview)
            encoded, now = canonical(preview), time.time()
            def write(conn):
                self._fence(conn, run)
                old = conn.execute("SELECT record_json FROM commitment_inbox_previews WHERE preview_id=?", (preview_id,)).fetchone()
                if old:
                    require(old[0] == encoded, "inbox_preview_conflict", "Immutable preview identity differs")
                    return json.loads(old[0])
                self._capacity(conn, "commitment_inbox_previews")
                new_candidates = [candidate for candidate in candidates if conn.execute(
                    "SELECT 1 FROM commitment_candidates WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone() is None]
                self._capacity(conn, "commitment_candidates", len(new_candidates))
                for candidate in new_candidates:
                    # Stable message identity deduplicates overlapping snapshot/range imports.
                    conn.execute("INSERT OR IGNORE INTO commitment_candidates VALUES(?,?,?,?,1,NULL,?)",
                        (candidate["candidate_id"], project_id, canonical(self.actor), canonical(candidate), now))
                conn.execute("INSERT INTO commitment_inbox_previews VALUES(?,?,?,?,?)",
                             (preview_id, project_id, canonical(self.actor), encoded, now))
                return json.loads(encoded)
            return self.db._execute_write(write)

    def candidate(self, project_id, candidate_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            row = conn.execute("SELECT * FROM commitment_candidates WHERE candidate_id=? AND project_id=? AND owner_json=?",
                               (candidate_id, project_id, canonical(self.actor))).fetchone()
            require(row is not None, "commitment_candidate_missing", "Candidate does not exist in this owner scope")
            return {**json.loads(row["record_json"]), "revision": row["revision"], "commitment_id": row["commitment_id"],
                    "accepted": row["commitment_id"] is not None}

    def accept(self, run, project_id, candidate_id, expected_revision, owner, outcome, due_or_check_at=None):
        assert_commitment_control(run, "runtime.commitment.accept")
        identifier(owner, 320)
        text(outcome, 8000)
        require(type(expected_revision) is int and expected_revision > 0,
                "invalid_commitment", "Expected candidate revision is required")
        decision = {"owner": owner, "outcome": outcome, "due_or_check_at": due_record(due_or_check_at)}
        with self.access.guard(project_id, self.actor, "write"):
            candidate = self.candidate(project_id, candidate_id)
            for ref in candidate["source_refs"]:
                self._source(project_id, ref)
            commitment_id = self._key(project_id, "commitment", candidate_id)
            def write(conn):
                self._fence(conn, run)
                current = conn.execute("SELECT * FROM commitment_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
                require(current is not None and current["owner_json"] == canonical(self.actor)
                        and current["project_id"] == project_id, "identity_mismatch", "Candidate owner changed")
                if current["commitment_id"]:
                    old = self._row(conn, project_id, current["commitment_id"])
                    require(old["acceptance_json"] == canonical(decision), "commitment_already_accepted", "An accepted candidate cannot become a different obligation")
                    return self._public(old)
                require(current["revision"] == expected_revision, "commitment_revision_conflict", "Candidate revision changed")
                self._capacity(conn, "accepted_commitments")
                self._capacity(conn, "commitment_history")
                now = time.time()
                state = "waiting" if candidate["classification"] == "waiting" else "ready"
                record = {**json.loads(current["record_json"]), **decision, "commitment_id": commitment_id,
                    "accepted": True, "accepted_at": now, "accepted_by": self.actor,
                    "acceptance_run_id": run.run_id, "acceptance_command_id": run.command_id,
                    "date_uncertainty": "operator_resolved" if decision["due_or_check_at"] else "unresolved",
                    "superseded_by": None, "evidence_refs": candidate["source_refs"]}
                bounded_result({**record, "state": state, "revision": 1})
                conn.execute("INSERT INTO accepted_commitments VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (commitment_id, candidate_id, project_id, canonical(self.actor), canonical(record), canonical(decision), state, 1, now, now))
                conn.execute("UPDATE commitment_candidates SET commitment_id=?,revision=revision+1 WHERE candidate_id=?",
                             (commitment_id, candidate_id))
                public = self._public(self._row(conn, project_id, commitment_id))
                conn.execute("INSERT INTO commitment_history VALUES(?,1,?,?)", (commitment_id, canonical(public), now))
                return public
            return self.db._execute_write(write)

    def update(self, run, project_id, commitment_id, expected_revision, state, evidence_ref,
               superseded_by=None, due_or_check_at=None):
        assert_commitment_control(run, "runtime.commitment.update")
        require(isinstance(state, str) and state in _ACTIVE | _TERMINAL,
                "invalid_commitment", "Unsupported obligation state")
        require(type(expected_revision) is int and expected_revision > 0,
                "invalid_commitment", "Expected obligation revision is required")
        due = due_record(due_or_check_at)
        require((state == "superseded") == (superseded_by is not None),
                "invalid_commitment", "Superseded state requires its exact accepted successor")
        with self.access.guard(project_id, self.actor, "write"):
            self._source(project_id, evidence_ref)
            def write(conn):
                self._fence(conn, run)
                row = self._row(conn, project_id, commitment_id)
                require(row["revision"] == expected_revision, "commitment_revision_conflict", "Obligation revision changed")
                require(row["state"] not in _TERMINAL, "commitment_terminal", "Terminal or superseded obligations cannot reopen")
                if superseded_by:
                    replacement = self._row(conn, project_id, superseded_by)
                    require(superseded_by != commitment_id and replacement["state"] in _ACTIVE,
                            "invalid_commitment", "Successor must be a distinct active accepted obligation")
                record = json.loads(row["record_json"])
                if due is not None:
                    record.update(due_or_check_at=due, date_uncertainty="operator_resolved")
                if evidence_ref not in record["evidence_refs"]:
                    require(len(record["evidence_refs"]) < 100, "commitment_capacity", "Evidence reference bound reached")
                    record["evidence_refs"].append(evidence_ref)
                record.update(superseded_by=superseded_by, last_update_run_id=run.run_id)
                now, revision = time.time(), row["revision"] + 1
                self._capacity(conn, "commitment_history")
                bounded_result({**record, "state": state, "revision": revision})
                conn.execute("UPDATE accepted_commitments SET record_json=?,state=?,revision=?,updated_at=? WHERE commitment_id=?",
                             (canonical(record), state, revision, now, commitment_id))
                public = self._public(self._row(conn, project_id, commitment_id))
                conn.execute("INSERT INTO commitment_history VALUES(?,?,?,?)", (commitment_id, revision, canonical(public), now))
                return public
            return self.db._execute_write(write)

    def get(self, project_id, commitment_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            return self._public(self._row(conn, project_id, commitment_id))

    def list(self, project_id, *, active_only=False):
        require(type(active_only) is bool, "invalid_commitment", "Active-only selection must be boolean")
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            sql = "SELECT * FROM accepted_commitments WHERE project_id=? AND owner_json=?"
            if active_only:
                sql += " AND state IN ('ready','waiting')"
            return bounded_result([self._public(row) for row in conn.execute(sql + " ORDER BY created_at,commitment_id LIMIT 500",
                                                            (project_id, canonical(self.actor)))])

    def weekly_review(self, project_id, *, as_of=None):
        now = time.time() if as_of is None else timestamp(as_of)
        rows = self.list(project_id, active_only=True)
        for row in rows:
            due = row["due_or_check_at"]
            row["due_in_review_window"] = bool(due and timestamp(due["at"]) <= now + 7 * 86400)
            row["overdue"] = bool(due and timestamp(due["at"]) < now)
        return bounded_result({"source": "accepted_commitment_registry", "window_seconds": 7 * 86400,
                "as_of": now, "possibly_truncated": len(rows) == 500, "ready": [row for row in rows if row["state"] == "ready"],
                "waiting": [row for row in rows if row["state"] == "waiting"],
                "mutated": False, "automatic_followups_authorized": False})

    def preview_calendar(self, project_id, availability_ref, *, timezone, participants,
                         start_at, end_at, duration_minutes=30):
        with self.access.guard(project_id, self.actor, "read"):
            source = self._json_source(project_id, availability_ref)
            return {**calendar_preview(source, timezone=timezone, participants=participants, start_at=start_at,
                end_at=end_at, duration_minutes=duration_minutes, now=time.time()), "source_ref": availability_ref}

    def draft_correspondence(self, run, project_id, correspondence_id, recipients, content, source_refs):
        assert_commitment_control(run, "runtime.correspondence.draft")
        identifier(correspondence_id)
        text(content, 16000)
        require(isinstance(recipients, list) and 1 <= len(recipients) <= 50,
                "invalid_correspondence", "Exact recipients are required")
        for recipient in recipients:
            identifier(recipient, 320)
        require(len(set(recipients)) == len(recipients), "invalid_correspondence", "Duplicate recipient")
        require(isinstance(source_refs, list) and 1 <= len(source_refs) <= 20,
                "invalid_correspondence", "Bounded immutable source references are required")
        key = self._key(project_id, "correspondence", correspondence_id)
        record = {"correspondence_id": key, "project_id": project_id, "recipients": recipients,
            "content": content, "source_refs": source_refs, "state": "draft", "sent": False, "effect_receipt": None,
            "possible_new_promise": bool(re.search(r"\b(?:I(?:['’]ll| will)|we(?:['’]ll| will)|I promise|we promise|I commit|we commit)\b", content, re.I)),
            "promise_review_required": True, "commitments_created": False,
            "input_digest": digest({"recipients": recipients, "content": content})}
        bounded_result(record)
        with self.access.guard(project_id, self.actor, "write"):
            for ref in source_refs:
                self._source(project_id, ref)
            def write(conn):
                self._fence(conn, run)
                old = conn.execute("SELECT record_json FROM commitment_correspondence WHERE correspondence_id=?", (key,)).fetchone()
                if old:
                    previous = json.loads(old[0])
                    require(previous["input_digest"] == record["input_digest"] and previous["source_refs"] == source_refs,
                            "correspondence_immutable", "Changed draft needs a new correspondence identifier")
                    return previous
                self._capacity(conn, "commitment_correspondence")
                conn.execute("INSERT INTO commitment_correspondence VALUES(?,?,?,?,?,?)",
                             (key, project_id, canonical(self.actor), canonical(record), "draft", time.time()))
                return record
            return self.db._execute_write(write)

    def correspondence(self, project_id, correspondence_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            row = conn.execute("SELECT record_json FROM commitment_correspondence WHERE correspondence_id=? AND project_id=? AND owner_json=?",
                               (correspondence_id, project_id, canonical(self.actor))).fetchone()
            require(row is not None, "correspondence_not_found", "Exact draft is unavailable")
            return json.loads(row[0])

    def record_correspondence_receipt(self, run, project_id, correspondence_id, effect_id):
        """Attach existing BE06 proof only. This cannot dispatch or certify a provider."""
        assert_commitment_control(run, "runtime.correspondence.receipt")
        with self.access.guard(project_id, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                row = conn.execute("SELECT * FROM commitment_correspondence WHERE correspondence_id=? AND project_id=? AND owner_json=?",
                                   (correspondence_id, project_id, canonical(self.actor))).fetchone()
                require(row is not None, "correspondence_not_found", "Exact draft is unavailable")
                record = json.loads(row["record_json"])
                effect = self.db._effect_on_conn(conn, effect_id, self.actor)
                require(effect["operation_type"] == "correspondence_send" and effect["state"] == "confirmed"
                        and effect["target_ref"] == "correspondence:" + correspondence_id
                        and effect["input_digest"] == record["input_digest"],
                        "correspondence_receipt_mismatch", "A confirmed send effect must bind exact recipients and content")
                receipt = conn.execute("SELECT evidence_id,receipt_json FROM runtime_effect_evidence WHERE effect_id=? AND state='confirmed' AND receipt_json IS NOT NULL ORDER BY sequence DESC LIMIT 1",
                                       (effect_id,)).fetchone()
                require(receipt is not None, "correspondence_receipt_missing", "Confirmed send lacks a retained receipt")
                proof = {"effect_id": effect_id, "evidence_id": receipt["evidence_id"], "receipt": json.loads(receipt["receipt_json"])}
                require(record["effect_receipt"] is None or record["effect_receipt"] == proof,
                        "correspondence_receipt_mismatch", "Recorded sent receipt cannot be replaced")
                record.update(state="sent_receipt_recorded", sent=True, effect_receipt=proof)
                bounded_result(record)
                conn.execute("UPDATE commitment_correspondence SET record_json=?,state=? WHERE correspondence_id=?",
                             (canonical(record), record["state"], correspondence_id))
                return record
            return self.db._execute_write(write)
