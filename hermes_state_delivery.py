"""Result commits and delivery receipts on the existing SessionDB writer.

``delivery_obligations`` is the only delivery authority. Legacy gateway rows are
explicitly disjoint from recipient-bound runtime rows, never competing queues.
Artifacts and command completion become visible in the same transaction as intent.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from hermes_state_runtime import _FINISH_STATES, _identifier, _json, _require

_AUTHORITY = "runtime.v1"
_ACTOR_FIELDS = ("principal_id", "profile_id", "agent_id")
_MAX_ATTEMPTS = 3
_DELIVERY_DEADLINE_SECONDS = 86400
_RETENTION_SECONDS = 30 * 86400


def _actor(actor):
    _require(isinstance(actor, dict) and set(actor) == set(_ACTOR_FIELDS),
             "identity_required", "An exact authenticated actor is required")
    return {key: _identifier(actor.get(key), key) for key in _ACTOR_FIELDS}


def _descriptor(descriptor):
    fields = {"artifact_id", "version", "locator", "sha256", "size", "mime", "producing_run"}
    _require(isinstance(descriptor, dict) and set(descriptor) == fields,
             "invalid_artifact", "An immutable artifact descriptor is required")
    for field in ("artifact_id", "producing_run", "mime"):
        _identifier(descriptor[field], field)
    _require(type(descriptor["version"]) is int and descriptor["version"] > 0
             and type(descriptor["size"]) is int and descriptor["size"] >= 0,
             "invalid_artifact", "Artifact version and byte size are invalid")
    digest = descriptor["sha256"]
    _require(isinstance(digest, str) and len(digest) == 64
             and all(c in "0123456789abcdef" for c in digest),
             "invalid_artifact", "Artifact content digest is invalid")
    _require(isinstance(descriptor["locator"], str) and 0 < len(descriptor["locator"]) <= 2048,
             "invalid_artifact", "Artifact locator is invalid")
    return dict(descriptor)


def result_artifact_id(actor, run_id, command_id):
    """Stable result identity independent of compression's rotating transcript key."""
    return "result_" + hashlib.sha256(
        json.dumps([actor, run_id, command_id], sort_keys=True).encode()).hexdigest()


def _receipt(row):
    metadata = json.loads(row["metadata_json"])
    ack = json.loads(row["acknowledgement_json"])
    descriptor = metadata["artifact"]
    return {"delivery_id": row["obligation_id"], "artifact_id": descriptor["artifact_id"],
            "version": descriptor["version"], "sha256": descriptor["sha256"],
            "destination": metadata["destination"], "state": row["state"],
            "acknowledgment_level": ack.get("level", "none"),
            "components": ack.get("components", {"text": "not_sent", "artifact": "not_sent"}),
            "platform_ids": ack.get("platform_ids", []), "attempt_count": row["attempts"],
            "max_attempts": row["max_attempts"], "next_attempt_at": row["next_attempt_at"],
            "deadline_at": row["deadline_at"], "retention_until": row["retention_until"],
            "last_error": row["last_error"], "result_available": True}


class SessionDeliveryMixin:
    def _delivery_actor_on_conn(self, conn, session_id, actor):
        actor = _actor(actor)
        sid = self._runtime_session_on_conn(conn, session_id)
        state = self._runtime_state_on_conn(conn, sid)
        _require(state is not None and all(state[key] == actor[key] for key in _ACTOR_FIELDS),
                 "identity_mismatch", "Runtime session belongs to a different actor")
        return sid, actor

    def _delivery_on_conn(self, conn, session_id, actor, delivery_id):
        sid, actor = self._delivery_actor_on_conn(conn, session_id, actor)
        _identifier(delivery_id, "delivery_id")
        row = conn.execute("SELECT * FROM delivery_obligations WHERE obligation_id=? AND authority=? "
                           "AND session_key=?", (delivery_id, _AUTHORITY, sid)).fetchone()
        _require(row is not None, "delivery_not_found", "Delivery does not exist in this session")
        metadata = json.loads(row["metadata_json"])
        _require(metadata["actor"] == actor, "identity_mismatch", "Delivery belongs to a different actor")
        return row

    def commit_runtime_result(self, session_id, command_id, *, actor, descriptor,
                              holder, generation, status="completed", result_summary=None):
        """Atomically publish an immutable full result, completion and one delivery intent."""
        descriptor = _descriptor(descriptor)
        actor = _actor(actor)
        _require(status in _FINISH_STATES, "invalid_command", "Unsupported completion status")
        _require(isinstance(result_summary, dict), "invalid_command", "Result summary must be an object")
        now = time.time()
        def write(conn):
            sid, _ = self._delivery_actor_on_conn(conn, session_id, actor)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            command = self._runtime_command_on_conn(conn, sid, command_id)
            _require(command is not None, "command_not_found", "Runtime command does not exist")
            _require(command["status"] == "claimed" and command["claimed_holder"] == holder
                     and command["claimed_generation"] == generation,
                     "claim_conflict", "Command is not owned by this claim")
            _require(descriptor["producing_run"] == command["run_id"],
                     "invalid_artifact", "Artifact was produced by another run")
            from hermes_state_effects import effect_digest
            evidence = conn.execute("SELECT input_ref_json FROM runtime_effects WHERE session_id=? AND run_id=? "
                "AND principal_id=? AND profile_id=? AND agent_id=? AND operation_type='artifact_publish' "
                "AND state='confirmed' AND target_ref=? AND input_digest=?",
                (sid, command["run_id"], *(actor[key] for key in _ACTOR_FIELDS),
                 f"artifact:{descriptor['artifact_id']}:{descriptor['version']}", effect_digest(descriptor))).fetchall()
            _require(any(json.loads(row[0]) == descriptor for row in evidence),
                     "artifact_not_confirmed", "Artifact publication lacks a matching confirmed receipt")
            delivery_id = hashlib.sha256(f"{sid}\0{command_id}\0result".encode()).hexdigest()
            reference = {"artifact_id": descriptor["artifact_id"], "version": descriptor["version"],
                         "sha256": descriptor["sha256"], "size": descriptor["size"],
                         "delivery_id": delivery_id}
            # Client addressing is a durable conversation/actor, never a supplied path or URL.
            destination = {"kind": "local_runtime", "session_id": sid, **actor}
            metadata = {"actor": actor, "artifact": descriptor, "destination": destination,
                        "command_id": command_id, "run_id": command["run_id"],
                        "generation": generation}
            conn.execute("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,"
                "run_id,principal_id,profile_id,agent_id,descriptor_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (descriptor["artifact_id"], descriptor["version"], sid, command_id, command["run_id"],
                 *(actor[key] for key in _ACTOR_FIELDS), _json(descriptor), now))
            conn.execute("INSERT INTO delivery_obligations(obligation_id,session_key,platform,chat_id,content,"
                "state,attempts,created_at,updated_at,authority,metadata_json,deadline_at,retention_until,"
                "next_attempt_at,max_attempts,acknowledgement_json) VALUES(?,?,?,?,?,'pending',0,?,?,?,?,?,?,?,?,?)",
                (delivery_id, sid, "local_runtime", actor["principal_id"], "", now, now, _AUTHORITY,
                 _json(metadata), now + _DELIVERY_DEADLINE_SECONDS, now + _RETENTION_SECONDS,
                 now, _MAX_ATTEMPTS, _json({"level": "none", "components": {
                     "text": "not_sent", "artifact": "not_sent"}, "platform_ids": []})))
            mission = self._settle_mission_delivery_on_conn(conn, sid, actor, command["run_id"], generation)
            stored = {**result_summary, "runtime_result": reference}
            if mission is not None and "mission" in stored:
                from agent.mission_runtime import _summary
                stored["mission"] = _summary(mission)
            result_json = _json(stored)
            conn.execute("UPDATE runtime_commands SET status=?,result_json=? WHERE session_id=? AND command_id=?",
                         (status, result_json, sid, command_id))
            self._append_runtime_event_on_conn(conn, sid, f"command.{status}",
                {"command_id": command_id, "result": stored}, generation,
                run_id=command["run_id"], delivery_id=delivery_id)
            state = self._runtime_state_on_conn(conn, sid)
            projection = json.loads(state["snapshot_json"])
            # The snapshot is a bounded discoverability window; all versions remain addressable.
            projection["artifacts"] = (projection["artifacts"] + [{
                "artifact_id": descriptor["artifact_id"], "version": str(descriptor["version"])}])[-100:]
            conn.execute("UPDATE runtime_state SET snapshot_json=? WHERE session_id=?", (_json(projection), sid))
            return reference
        return self._execute_write(write)

    def read_runtime_result_artifact(self, session_id, actor, command_id):
        with self._runtime_read() as conn:
            sid, actor = self._delivery_actor_on_conn(conn, session_id, actor)
            row = conn.execute("SELECT * FROM runtime_artifact_versions WHERE session_id=? AND command_id=? "
                               "AND artifact_kind='runtime_result' AND publication_state='committed'",
                               (sid, _identifier(command_id, "command_id"))).fetchone()
            _require(row is not None, "result_not_found", "No committed result exists")
            _require(all(row[key] == actor[key] for key in _ACTOR_FIELDS),
                     "identity_mismatch", "Artifact belongs to a different actor")
            return json.loads(row["descriptor_json"])

    def locate_runtime_result(self, session_id, actor, command_id):
        """Read committed output or exact confirmed publication left before final commit.

        The recovery branch does not finalize a command, create delivery intent,
        dispatch a provider, or turn uncertain execution into completion.
        """
        from hermes_state_effects import effect_digest
        with self._runtime_read() as conn:
            sid, actor = self._delivery_actor_on_conn(conn, session_id, actor)
            command = self._runtime_command_on_conn(conn, sid, command_id)
            _require(command is not None, "result_not_found", "No result exists for this command")
            row = conn.execute("SELECT * FROM runtime_artifact_versions WHERE session_id=? AND command_id=? "
                               "AND artifact_kind='runtime_result' AND publication_state='committed'",
                               (sid, command_id)).fetchone()
            if row is not None:
                _require(all(row[key] == actor[key] for key in _ACTOR_FIELDS),
                         "identity_mismatch", "Artifact belongs to a different actor")
                reference = (json.loads(command["result_json"] or "{}"))["runtime_result"]
                return {"descriptor": json.loads(row["descriptor_json"]), "publication_state": "committed",
                        "delivery_id": reference["delivery_id"]}
            _require(command["status"] == "claimed", "result_not_found", "No committed result exists")
            artifact_id = result_artifact_id(actor, command["run_id"], command_id)
            rows = conn.execute("SELECT input_ref_json,input_digest FROM runtime_effects WHERE session_id=? "
                "AND run_id=? AND principal_id=? AND profile_id=? AND agent_id=? "
                "AND operation_type='artifact_publish' AND state='confirmed' AND target_ref=?",
                (sid, command["run_id"], *(actor[key] for key in _ACTOR_FIELDS),
                 f"artifact:{artifact_id}:1")).fetchall()
            _require(len(rows) == 1, "result_not_found", "No exact confirmed result publication exists")
            descriptor = _descriptor(json.loads(rows[0]["input_ref_json"]))
            _require(descriptor["artifact_id"] == artifact_id and descriptor["version"] == 1
                     and descriptor["producing_run"] == command["run_id"]
                     and effect_digest(descriptor) == rows[0]["input_digest"],
                     "invalid_artifact", "Result publication descriptor does not match its intent")
            return {"descriptor": descriptor, "publication_state": "published_uncommitted", "delivery_id": None}

    def read_runtime_delivery(self, session_id, actor, delivery_id):
        with self._runtime_read() as conn:
            return _receipt(self._delivery_on_conn(conn, session_id, actor, delivery_id))

    def claim_runtime_delivery(self, session_id, actor, delivery_id):
        """Claim one physical local-transport attempt; never dispatch inference or effects."""
        now, token = time.time(), secrets.token_hex(24)
        def write(conn):
            row = self._delivery_on_conn(conn, session_id, actor, delivery_id)
            if row["state"] not in {"pending", "failed"}:
                return None
            if row["attempts"] >= row["max_attempts"] or now >= row["deadline_at"]:
                conn.execute("UPDATE delivery_obligations SET state='dead_letter',updated_at=?,last_error=? "
                             "WHERE obligation_id=?", (now, "delivery_budget_exhausted", delivery_id))
                return None
            if row["next_attempt_at"] is not None and now < row["next_attempt_at"]:
                return None
            conn.execute("UPDATE delivery_obligations SET state='attempting',attempts=attempts+1,updated_at=?,"
                         "attempt_token=?,last_error=NULL WHERE obligation_id=?", (now, token, delivery_id))
            metadata = json.loads(row["metadata_json"])
            return {"attempt_token": token, "delivery_id": delivery_id, "artifact": metadata["artifact"],
                    "destination": metadata["destination"], "command_id": metadata["command_id"]}
        return self._execute_write(write)

    def finish_runtime_delivery_attempt(self, session_id, actor, delivery_id, attempt_token, *,
                                        accepted, definitely_not_sent=False):
        """A write receipt proves transport acceptance only; disconnects stay uncertain."""
        _require(type(accepted) is bool and type(definitely_not_sent) is bool,
                 "invalid_delivery", "Delivery disposition must be explicit")
        _require(not (accepted and definitely_not_sent), "invalid_delivery", "Conflicting disposition")
        now = time.time()
        def write(conn):
            row = self._delivery_on_conn(conn, session_id, actor, delivery_id)
            _require(row["attempt_token"] == attempt_token, "stale_delivery_attempt", "Delivery claim changed")
            # An explicit client receipt can race the physical write's return.
            if row["state"] in {"delivered", "partial"}:
                return _receipt(row)
            _require(row["state"] == "attempting", "stale_delivery_attempt", "Delivery claim ended")
            ack = json.loads(row["acknowledgement_json"])
            state, error, due = "outcome_unknown", "transport_outcome_unknown", None
            if accepted:
                state, error = ("partial" if "client_received" in ack["components"].values() else "awaiting_ack"), None
                if ack["level"] != "client_received":
                    ack["level"] = "transport_accepted"
            elif definitely_not_sent:
                state, error = "failed", "transport_not_sent"
            if accepted or definitely_not_sent:
                # Stable jitter is persisted once; reloads cannot shift the backoff deadline.
                jitter = int(hashlib.sha256(attempt_token.encode("ascii")).hexdigest()[:8], 16) / 0xffffffff
                due = now + min(300, 5 * (2 ** (row["attempts"] - 1))) * (0.8 + 0.4 * jitter)
            conn.execute("UPDATE delivery_obligations SET state=?,updated_at=?,last_error=?,next_attempt_at=?,"
                         "acknowledgement_json=? WHERE obligation_id=?",
                         (state, now, error, due, _json(ack), delivery_id))
            return _receipt(self._delivery_on_conn(conn, session_id, actor, delivery_id))
        return self._execute_write(write)

    def acknowledge_runtime_delivery(self, session_id, actor, delivery_id, *, attempt_token,
                                     sha256, text_received=False, artifact_received=False):
        """Authenticated client receipt, never a claim that a human read the output."""
        _require(type(text_received) is bool and type(artifact_received) is bool
                 and (text_received or artifact_received), "invalid_delivery", "A component receipt is required")
        now = time.time()
        def write(conn):
            row = self._delivery_on_conn(conn, session_id, actor, delivery_id)
            metadata = json.loads(row["metadata_json"])
            _require(now <= row["retention_until"], "delivery_receipt_expired", "Delivery receipt retention expired")
            _require(isinstance(attempt_token, str) and row["attempt_token"] is not None
                     and secrets.compare_digest(row["attempt_token"], attempt_token)
                     and sha256 == metadata["artifact"]["sha256"],
                     "invalid_delivery_receipt", "Receipt is not bound to this result and attempt")
            ack = json.loads(row["acknowledgement_json"])
            components = ack["components"]
            if text_received:
                components["text"] = "client_received"
            if artifact_received:
                components["artifact"] = "client_received"
            ack["level"] = "client_received"
            state = "delivered" if all(v == "client_received" for v in components.values()) else "partial"
            conn.execute("UPDATE delivery_obligations SET state=?,updated_at=?,last_error=NULL,"
                         "acknowledgement_json=? WHERE obligation_id=?", (state, now, _json(ack), delivery_id))
            return _receipt(self._delivery_on_conn(conn, session_id, actor, delivery_id))
        return self._execute_write(write)

    def repair_runtime_delivery(self, session_id, actor, delivery_id, *, holder=None, generation=None,
                                expected_attempt_count=None, expected_state=None):
        """Explicit authorized local replay, retaining the same result, actor and attempt budget.

        Local clients can deduplicate by delivery_id, unlike non-idempotent legacy
        external sends. This is the only path out of an ambiguous local write.
        """
        now = time.time()
        guarded = (holder, generation, expected_attempt_count, expected_state)
        _require(all(value is None for value in guarded) or all(value is not None for value in guarded),
                 "invalid_delivery_repair_fence", "A strict repair requires its complete owner and target fence")
        def write(conn):
            row = self._delivery_on_conn(conn, session_id, actor, delivery_id)
            if holder is not None:
                self._runtime_fence_on_conn(conn, row["session_key"], holder, generation)
                _require(type(expected_attempt_count) is int and row["attempts"] == expected_attempt_count
                         and row["state"] == expected_state,
                         "delivery_repair_conflict", "Delivery state changed after its repair preview")
            if row["state"] == "delivered":
                return _receipt(row)
            if row["attempts"] >= row["max_attempts"] or now >= row["deadline_at"]:
                conn.execute("UPDATE delivery_obligations SET state='dead_letter',updated_at=?,last_error=? "
                             "WHERE obligation_id=?", (now, "delivery_budget_exhausted", delivery_id))
            else:
                _require(row["state"] != "attempting" or now - row["updated_at"] >= 60,
                         "delivery_in_flight", "A transport write is still in progress")
                conn.execute("UPDATE delivery_obligations SET state='pending',updated_at=?,last_error=NULL "
                             "WHERE obligation_id=?", (now, delivery_id))
            return _receipt(self._delivery_on_conn(conn, session_id, actor, delivery_id))
        return self._execute_write(write)
