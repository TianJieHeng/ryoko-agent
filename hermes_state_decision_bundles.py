"""Fenced request-prefix pins in the existing session store, never a tool registry.

The canonical discovery pin remains owned by MCP/tool discovery. This separate
request pin is necessary because discovery intentionally merges newly available
schemas on restart; doing that to an already-sent reduced prefix breaks caching.
"""
from __future__ import annotations

import json

from agent.decisions.contracts import canonical, digest, require, sha256

_KEY = "decision_request_bundle"
_MAX_BYTES = 2 * 1024 * 1024
_FIELDS = {"schema_version", "actor", "owner_digest", "session_id", "run_id", "turn_id",
           "generation", "context_id", "boundary", "boundary_witness", "scope_digest",
           "catalog_version", "schemas_json", "prefix_digest", "plan_bundle_id",
           "receipt_ids", "release", "record_digest"}


def actor_for(run):
    return {key: getattr(run.context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def owner_digest(run):
    return digest({"actor": actor_for(run), "profile_home": digest(run.context.profile_home)})


def _fence(conn, run):
    return run.db._effect_run_on_conn(conn, run.agent.session_id, actor_for(run), run.run_id,
        run.holder, run.generation, dispatch=True)


def _read_on_conn(conn, run):
    row = conn.execute("SELECT model_config FROM sessions WHERE id=?", (run.agent.session_id,)).fetchone()
    require(row is not None, "bundle_session_missing")
    config = json.loads(row[0] or "{}")
    return config.get(_KEY)


def validate_record(record, run):
    require(isinstance(record, dict) and set(record) == _FIELDS
            and type(record["schema_version"]) is int and record["schema_version"] == 1,
            "unsupported_bundle_pin")
    require(record["actor"] == actor_for(run) and record["owner_digest"] == owner_digest(run),
            "planner_owner_mismatch")
    require(record["session_id"] == run.agent.session_id, "bundle_session_mismatch")
    require(record["record_digest"] == digest({k: v for k, v in record.items() if k != "record_digest"}),
            "bundle_pin_digest_mismatch")
    require(type(record["schemas_json"]) is str and len(record["schemas_json"].encode()) <= _MAX_BYTES,
            "bundle_pin_too_large")
    for key in ("owner_digest", "context_id", "scope_digest", "catalog_version", "prefix_digest", "record_digest"):
        sha256(record[key])
    require(type(record["generation"]) is int and record["generation"] > 0,
            "invalid_bundle_pin")
    require(all(type(record[key]) is str and 0 < len(record[key]) <= 256
                for key in ("session_id", "run_id", "turn_id")), "invalid_bundle_pin")
    require(type(record["receipt_ids"]) is list and len(record["receipt_ids"]) <= 62
            and all(type(value) is str for value in record["receipt_ids"])
            and len(set(record["receipt_ids"])) == len(record["receipt_ids"]), "invalid_bundle_pin")
    for receipt_id in record["receipt_ids"]:
        sha256(receipt_id)
    if record["plan_bundle_id"] is not None:
        sha256(record["plan_bundle_id"])
    require(record["release"] is None or (type(record["release"]) is dict
            and set(record["release"]) == {"model_digest", "calibration_digest", "service_digest", "gate_digest", "contract_version", "policy_digest"}
            and type(record["release"]["contract_version"]) is int
            and record["release"]["contract_version"] == 2), "invalid_bundle_release")
    if record["release"]:
        for key, value in record["release"].items():
            if key != "contract_version":
                sha256(value)
    schemas = json.loads(record["schemas_json"])
    require(isinstance(schemas, list) and canonical(schemas) == record["schemas_json"]
            and record["prefix_digest"] == digest(record["schemas_json"]), "bundle_pin_digest_mismatch")
    require(all(isinstance(item, dict) and isinstance(item.get("function"), dict)
                and isinstance(item["function"].get("name"), str) for item in schemas), "invalid_bundle_schema")
    require(record["boundary"] in {"new_context", "compression"}, "invalid_cache_boundary")
    witness = record["boundary_witness"]
    if record["boundary"] == "new_context":
        require(type(witness) is dict and set(witness) == {"first_request"}
                and witness["first_request"] is True, "invalid_boundary_witness")
    else:
        require(type(witness) is dict and set(witness) == {"projection_id", "checkpoint_id", "generation"}
                and type(witness["generation"]) is int and witness["generation"] > 0,
                "invalid_boundary_witness")
        require(all(type(witness[key]) is str and 0 < len(witness[key]) <= 256
                    for key in ("projection_id", "checkpoint_id")), "invalid_boundary_witness")
    require((record["plan_bundle_id"] is None and record["release"] is None and not record["receipt_ids"])
            or (record["plan_bundle_id"] is not None and record["release"] is not None and bool(record["receipt_ids"])),
            "invalid_bundle_release")
    return record


def read_bundle_state(run):
    """Only an admitted live owner can restore a pin or claim a first freeze."""
    with run.db._runtime_read() as conn:
        sid = _fence(conn, run)
        record = _read_on_conn(conn, run)
        if record is not None:
            return validate_record(record, run), False
        # A recreated object/task id/prompt cache is not evidence of a new context.
        # Legacy, seeded, resumed, and previously dispatched sessions fail closed.
        messages = conn.execute("SELECT 1 FROM messages WHERE session_id=? LIMIT 1", (run.agent.session_id,)).fetchone()
        calls = conn.execute("SELECT 1 FROM runtime_events WHERE session_id=? AND type='model.started' LIMIT 1", (sid,)).fetchone()
        submits = len(conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? "
                                   "AND json_extract(command_json, '$.operation')='submit' LIMIT 2", (sid,)).fetchall())
        projection = conn.execute("SELECT 1 FROM runtime_context_projections WHERE session_id=?", (run.agent.session_id,)).fetchone()
        prefix = conn.execute("SELECT system_prompt_hash,system_prompt,tool_names FROM sessions WHERE id=?",
                              (run.agent.session_id,)).fetchone()
        # Unknown persisted legacy pins are already a freeze, even if someone
        # removed the old transcript or its dispatch events were never journaled.
        unpinned = prefix is not None and all(value is None for value in prefix)
        return None, unpinned and not messages and not calls and submits == 1 and not projection


def read_plan_receipts(run, receipt_ids, *, plan_record=None):
    """Recover exact retained receipts, rather than trusting an in-memory flag."""
    require(0 < len(receipt_ids) <= 62 and len(set(receipt_ids)) == len(receipt_ids), "durable_receipt_required")
    with run.db._runtime_read() as conn:
        sid = _fence(conn, run)
        rows = conn.execute("SELECT payload_json FROM runtime_events WHERE session_id=? "
                            "AND run_id=? AND type='decision.observed' "
                            "AND json_extract(payload_json, '$.point_id')='DP16' LIMIT 63", (sid, run.run_id)).fetchall()
        if plan_record is not None:
            from tui_gateway.contracts.decision_plans import DecisionToolPlan
            expected = DecisionToolPlan.model_validate(plan_record).model_dump(mode="json")
            plans = conn.execute("SELECT payload_json FROM runtime_events WHERE session_id=? "
                                 "AND run_id=? AND type='decision.tool_plan'", (sid, run.run_id)).fetchall()
            require(any(json.loads(row[0]) == expected for row in plans), "durable_plan_required")
        wanted = set(receipt_ids)
        found = {}
        for row in rows:
            receipt = json.loads(row[0])
            if receipt.get("receipt_id") in wanted:
                require(receipt["receipt_id"] not in found, "duplicate_durable_receipt")
                require(receipt["receipt_id"] == digest({key: value for key, value in receipt.items() if key != "receipt_id"}),
                        "receipt_digest_mismatch")
                found[receipt["receipt_id"]] = receipt
        require(set(found) == wanted, "durable_receipt_required")
        return tuple(found[key] for key in receipt_ids)


def persist_bundle(run, record, *, expected_record_digest):
    """Install exact bytes and metadata atomically under the existing owner fence."""
    record = validate_record(record, run)
    from agent.decisions.receipts import scope_digest
    require(record["run_id"] == run.run_id and record["generation"] == run.generation
            and record["turn_id"] == getattr(run.agent, "_current_turn_id", "")
            and record["scope_digest"] == scope_digest(run.context), "stale_owner_bundle")
    def write(conn):
        sid = _fence(conn, run)
        current = _read_on_conn(conn, run)
        require((current or {}).get("record_digest") == expected_record_digest, "bundle_pin_changed")
        if record["boundary"] == "compression":
            row = conn.execute("SELECT projection_json FROM runtime_context_projections WHERE session_id=?",
                               (run.agent.session_id,)).fetchone()
            projection = json.loads(row[0]) if row else {}
            require(all(projection.get(key) == value for key, value in record["boundary_witness"].items())
                    and projection.get("generation") == run.generation, "committed_boundary_required")
        else:
            require(current is None and record["boundary_witness"] == {"first_request": True}, "new_context_boundary_required")
            require(not conn.execute("SELECT 1 FROM runtime_events WHERE session_id=? AND type='model.started' LIMIT 1", (sid,)).fetchone(),
                    "new_context_boundary_required")
        merged = run.db._merge_model_config_json(conn, run.agent.session_id, {_KEY: record}, on_missing="raise")
        conn.execute("UPDATE sessions SET model_config=? WHERE id=?", (merged, run.agent.session_id))
        metadata = {key: record[key] for key in ("schema_version", "context_id", "boundary", "scope_digest",
                    "catalog_version", "prefix_digest", "plan_bundle_id", "receipt_ids", "turn_id", "generation")}
        from tui_gateway.contracts.decision_plans import DecisionBundleRecord
        metadata = DecisionBundleRecord.model_validate(metadata).model_dump(mode="json")
        run.db._append_runtime_event_on_conn(conn, sid, "decision.bundle", metadata, run.generation, run_id=run.run_id)
    run.db._execute_write(write)
