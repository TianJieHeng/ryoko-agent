"""Redacted receipts on the existing fenced journal; no separate payload log."""
from __future__ import annotations

from dataclasses import asdict
import time

from agent.decisions.contracts import digest, require, sha256


def receipt_for(request, result, contract, bundle, policy, *, route, reason, elapsed_ms):
    body = {"schema_version": 1, "point_id": request.point_id, "contract_version": request.contract_version,
        "contract_digest": request.contract_digest, "question_id": request.question_id,
        "request_id": request.request_id, "input_digest": request.state_packet.input_digest,
        "scope_digest": request.state_packet.scope_digest, "classification": request.state_packet.classification,
        "model_digest": bundle.model_digest, "calibration_digest": bundle.calibration_digest,
        "service_digest": bundle.service_digest, "mode": policy.mode,
        "thresholds": {"select": policy.threshold, **dict(policy.effect_thresholds)}, "point_gate_digest": policy.gate_digest,
        "live_options": list(request.live_options), "distribution": dict(result.distribution) if result else None,
        "selected": result.selected if result else None, "unclear": result.unclear if result else True,
        "actual_route": route, "fallback": reason, "incumbent": contract.fallback,
        "latency_ms": elapsed_ms, "node_latency_ms": result.latency_ms if result else None,
        "recorded_at": time.time(), "outcome": None, "raw_state_retained": False}
    return {**body, "receipt_id": digest(body)}


class JournalSink:
    """Construction does not capture authority; each write rechecks the live run."""
    durable = True

    def __init__(self, run):
        self.run = run

    def __call__(self, receipt):
        from agent.runtime_commands import assert_runtime_dispatch
        from tools.capability_broker import require_live_policy
        run = assert_runtime_dispatch()
        require(run is self.run and require_live_policy() == run.context, "receipt_authority_mismatch")
        expected_scope = scope_digest(run.context)
        require(receipt["scope_digest"] == expected_scope, "receipt_scope_mismatch")
        return run.db.append_runtime_event(run.session_id, "decision.observed", receipt,
            holder=run.holder, generation=run.generation, run_id=run.run_id)

    def annotate(self, receipt_id, *, label, outcome, source_digest):
        from agent.runtime_commands import assert_runtime_dispatch
        from agent.decisions.contracts import label as valid_label
        from tools.capability_broker import require_live_policy
        sha256(receipt_id)
        sha256(source_digest)
        valid_label(label)
        require(outcome in {"correct", "incorrect", "unresolved", "recovered"}, "invalid_outcome")
        run = assert_runtime_dispatch()
        require(run is self.run and require_live_policy() == run.context, "receipt_authority_mismatch")
        with run.db._runtime_read() as conn:
            rows = conn.execute("SELECT payload_json FROM runtime_events WHERE session_id=? AND type='decision.observed' "
                                "ORDER BY seq DESC LIMIT 1000", (run.session_id,)).fetchall()
        import json
        found = next((json.loads(row[0]) for row in rows if json.loads(row[0]).get("receipt_id") == receipt_id), None)
        require(found is not None and found["scope_digest"] == scope_digest(run.context)
                and label in found["live_options"], "unknown_receipt_or_label")
        return run.db.append_runtime_event(run.session_id, "decision.outcome",
            {"receipt_id": receipt_id, "label": label, "outcome": outcome, "source_digest": source_digest},
            holder=run.holder, generation=run.generation, run_id=run.run_id)


def scope_digest(context):
    return digest({"identity": context.identity.to_record(), "policy_digest": context.policy.digest,
                   "profile_home_digest": digest(context.profile_home)})
