"""Immutable host test observations on SessionDB's existing authoritative writer.

Only the concrete isolated adapter can supply an in-process one-use witness.
These receipts are distinct from model messages, runtime logs and legacy goals.
"""
from __future__ import annotations

import json

from agent.mission_contract import bounded_json, digest, require
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from hermes_state_effects import _actor, effect_digest

MAX_TEST_RECEIPTS = 65536


class SessionMissionTestsMixin:
    def _record_mission_test_execution(self, run, witness):
        from agent.mission_test_adapter import _consume_execution_witness
        from agent.runtime_commands import assert_runtime_finalization
        assert_runtime_finalization(run)
        require(run.db is self, "Test observation belongs to another store", "identity_mismatch")
        record = _consume_execution_witness(run, witness)
        actor, access = artifact_actor(run.context), project_access(run.context)
        require(record["session_id"] == run.session_id and record["run_id"] == run.run_id,
                "Test observation differs from admitted run", "identity_mismatch")
        encoded = bounded_json(record, 32768)
        with self._mission_guard(run.session_id, actor, access, "read"):
            def write(conn):
                _, mission = self._mission_row_on_conn(conn, run.session_id, actor,
                    holder=run.holder, generation=run.generation)
                require(record["mission_id"] == mission["mission_id"] and record["project_id"] == mission["project_id"],
                        "Test observation belongs to another mission", "identity_mismatch")
                self._effect_run_on_conn(conn, run.session_id, actor, run.run_id,
                                         run.holder, run.generation)
                effect = self._effect_on_conn(conn, record["effect_id"], actor)
                require(effect["state"] == "confirmed" and effect["run_id"] == run.run_id
                        and effect["operation_type"] == "mission_test_execution"
                        and effect["operation_id"] == record["operation_id"]
                        and effect["input_digest"] == record["inputs_digest"]
                        and effect["input_revision"] == record["workspace_manifest_digest"]
                        and effect["target_ref"] == "mission-test:" + record["criterion_digest"],
                        "Test requires exact confirmed execution intent", "test_observation_mismatch")
                proof = conn.execute("SELECT receipt_json FROM runtime_effect_evidence WHERE effect_id=? AND state='confirmed' ORDER BY sequence DESC LIMIT 1",
                                     (record["effect_id"],)).fetchone()
                require(proof is not None and json.loads(proof[0])["sha256"] == effect_digest(record),
                        "Execution receipt digest differs", "test_observation_mismatch")
                reservation = conn.execute("SELECT * FROM budget_reservations WHERE account_id=? AND operation_id=?",
                                           (record["budget_account_id"], record["operation_id"])).fetchone()
                account = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?",
                                       (record["budget_account_id"],)).fetchone()
                require(account is not None and account["run_id"] == run.run_id
                        and all(account[key] == actor[key] for key in actor)
                        and reservation is not None and reservation["settlement_state"] in {"dispatched", "settled"}
                        and json.loads(reservation["maxima_json"]).get("executor_slots") == 1
                        and 0 < json.loads(reservation["maxima_json"]).get("wall_ms", 0) <= 5000,
                        "Test requires its charged bounded executor reservation", "test_budget_required")
                for ref in record["artifact_refs"]:
                    row = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
                    require(row["project_id"] == record["project_id"]
                            and json.loads(row["descriptor_json"])["sha256"] == ref["digest"],
                            "Tested artifact differs from retained immutable input", "test_input_changed")
                require(conn.execute("SELECT COUNT(*) FROM runtime_mission_test_executions").fetchone()[0] < MAX_TEST_RECEIPTS,
                        "Test receipt capacity reached", "mission_capacity")
                conn.execute("INSERT INTO runtime_mission_test_executions(receipt_id,mission_id,session_id,principal_id,profile_id,agent_id,project_id,criterion_digest,run_id,effect_id,record_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record["receipt_id"], record["mission_id"], run.session_id,
                     *(actor[key] for key in ("principal_id", "profile_id", "agent_id")), record["project_id"],
                     record["criterion_digest"], run.run_id, record["effect_id"], encoded, record["observed_at"]))
                return record
            return self._execute_write(write)

    def _mission_test_receipt_current_on_conn(self, conn, record, criterion, receipt, actor, access):
        """Recheck immutable host proof in the same completion transaction."""
        from agent.mission_contract import criterion_digest
        from agent.mission_test_adapter import ISOLATION_PROFILE, test_inputs_digest, validate_test_criterion
        try:
            item = validate_test_criterion(criterion)
            prefix = "mission_test_execution:"
            reference = receipt.get("evidence_ref", "")
            if not reference.startswith(prefix):
                return False
            row = conn.execute("SELECT * FROM runtime_mission_test_executions WHERE receipt_id=?",
                               (reference[len(prefix):],)).fetchone()
            if row is None or any(row[key] != actor[key] for key in actor):
                return False
            observed = json.loads(row["record_json"])
            if (observed["mission_id"] != record["mission_id"]
                    or observed["session_id"] != record["session_id"]
                    or observed["project_id"] != record["project_id"]
                    or observed["criterion_id"] != item["criterion_id"]
                    or observed["criterion_digest"] != criterion_digest(item)
                    or observed["artifact_refs"] != item["artifact_refs"]
                    or observed["code_sha256"] != item["parameters"]["code_sha256"]
                    or observed["inputs_digest"] != test_inputs_digest(item)
                    or observed["status"] != "completed" or type(observed["exit_code"]) is not int
                    or observed["exit_code"] != 0 or observed["stdout_truncated"] or observed["stderr_truncated"]
                    or observed["isolation_profile"] != ISOLATION_PROFILE):
                return False
            effect = self._effect_on_conn(conn, observed["effect_id"], actor)
            if (effect["state"] != "confirmed" or effect["operation_type"] != "mission_test_execution"
                    or effect["run_id"] != observed["run_id"] or effect["operation_id"] != observed["operation_id"]
                    or effect["input_digest"] != observed["inputs_digest"]
                    or effect["input_revision"] != observed["workspace_manifest_digest"]):
                return False
            proof = conn.execute("SELECT receipt_json FROM runtime_effect_evidence WHERE effect_id=? AND state='confirmed' ORDER BY sequence DESC LIMIT 1",
                                 (observed["effect_id"],)).fetchone()
            if proof is None or json.loads(proof[0]).get("sha256") != effect_digest(observed):
                return False
            budget = conn.execute("SELECT * FROM budget_reservations WHERE account_id=? AND operation_id=?",
                                  (observed["budget_account_id"], observed["operation_id"])).fetchone()
            if (budget is None or budget["settlement_state"] != "settled" or budget["unknown_usage"]
                    or not budget["slots_released"]):
                return False
            for ref in item["artifact_refs"]:
                artifact = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
                if (artifact["project_id"] != record["project_id"]
                        or json.loads(artifact["descriptor_json"])["sha256"] != ref["digest"]):
                    return False
            return True
        except (ValueError, PermissionError, KeyError, TypeError):
            return False

    def get_mission_test_execution(self, session_id, actor, *, criterion_digest, mission_id=None, run_id=None, access=None):
        actor = _actor(actor)
        digest(criterion_digest)
        with self._mission_guard(session_id, actor, access, "read"):
            with self._runtime_read() as conn:
                _, mission = self._mission_row_on_conn(conn, session_id, actor)
                require(mission_id is None or mission_id == mission["mission_id"],
                        "Test receipt belongs to another mission", "identity_mismatch")
                query = ("SELECT record_json FROM runtime_mission_test_executions WHERE mission_id=? "
                         "AND principal_id=? AND profile_id=? AND agent_id=? AND criterion_digest=?")
                values = [mission["mission_id"], *(actor[key] for key in ("principal_id", "profile_id", "agent_id")), criterion_digest]
                if run_id is not None:
                    query += " AND run_id=?"
                    values.append(run_id)
                row = conn.execute(query + " ORDER BY observed_at DESC,receipt_id DESC LIMIT 1", values).fetchone()
                return json.loads(row[0]) if row is not None else None
