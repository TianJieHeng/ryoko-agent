"""Fenced durable delegation atop the canonical runtime and async completion ledgers.

A recorded completion is recoverable. A running Python thread is not resumable.
No recovery operation here constructs a child or dispatches a provider request.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
import uuid

from agent.delegation_contract import ImmutableHandoff, DelegationLimits, canonical, require
from agent.budget_account import actor_for


@dataclass(frozen=True)
class DelegationTicket:
    child_id: str
    handoff_sha256: str
    holder: str
    generation: int


class DelegationRegistry:
    def __init__(self, context, db):
        self.context, self.db = context, db
        self.actor = actor_for(context)

    def _fence(self, conn, run, *, recovery=False):
        require(run.db is self.db and run.context == self.context,
                "delegation_identity_mismatch", "Delegation requires the admitted owner")
        self.db._mission_owner_on_conn(conn, run.session_id, self.actor, holder=run.holder, generation=run.generation)
        record = self.db._runtime_command_on_conn(conn, run.session_id, run.command_id)
        require(record is not None and record["status"] == "claimed" and record["run_id"] == run.run_id
                and (recovery or record["claimed_holder"] == run.holder and record["claimed_generation"] == run.generation),
                "delegation_stale_owner", "Delegation requires its live claimed command")

    @staticmethod
    def _handoff(row):
        require(row is not None, "delegation_not_found", "No exact durable handoff")
        require(hashlib.sha256(row["handoff_json"].encode()).hexdigest() == row["handoff_sha256"],
                "handoff_digest_mismatch", "Immutable handoff bytes changed")
        return ImmutableHandoff(json.loads(row["handoff_json"]))

    def admit(self, run, handoffs, limits):
        require(isinstance(limits, DelegationLimits) and handoffs and all(isinstance(h, ImmutableHandoff) for h in handoffs),
                "invalid_handoff", "Typed handoffs and limits required")
        require(run.budget is not None, "delegation_budget_required", "A reserved finite run budget is required")
        now = time.time()
        def write(conn):
            self._fence(conn, run)
            root = run.budget.root_id
            existing = conn.execute("SELECT * FROM delegation_roots WHERE root_run_id=?", (root,)).fetchone()
            encoded_limits = canonical(limits.to_record())
            if existing:
                require(existing["principal_id"] == self.actor["principal_id"] and existing["profile_id"] == self.actor["profile_id"]
                        and existing["limits_json"] == encoded_limits,
                        "delegation_limits_changed", "Root limits cannot reset or widen on continuation")
            else:
                require(conn.execute("SELECT COUNT(*) FROM delegation_roots").fetchone()[0] < 16384,
                        "delegation_storage_limit", "Root audit capacity reached")
                conn.execute("INSERT INTO delegation_roots VALUES(?,?,?,?,?)", (root, self.actor["principal_id"],
                    self.actor["profile_id"], encoded_limits, now))
            rows = conn.execute("SELECT state FROM delegation_handoffs WHERE root_run_id=?", (root,)).fetchall()
            require(len(rows) + len(handoffs) <= limits.max_total_children,
                    "delegation_fanout_limit", "Aggregate run-tree child limit reached")
            active = sum(row["state"] in {"accepted", "running", "unknown", "partial"} for row in rows)
            require(active + len(handoffs) <= limits.max_concurrent_children,
                    "delegation_concurrency_limit", "Aggregate active child limit reached")
            ancestor = conn.execute("SELECT handoff_json FROM delegation_handoffs WHERE child_session_id=?", (run.session_id,)).fetchone()
            depth = json.loads(ancestor[0])["depth"] + 1 if ancestor else 1
            for handoff in handoffs:
                data = handoff.to_record()
                require(data["root_run_id"] == root and data["parent_run_id"] == run.run_id
                        and data["parent_session_id"] == run.session_id and data["parent_identity"] == self.context.identity.to_record()
                        and data["depth"] == depth and depth <= limits.max_depth,
                        "delegation_depth_limit", "Exact run lineage and bounded depth required")
                require(data["budget"]["account_id"] == run.budget.account_id and data["budget"]["root_id"] == root
                        and data["deadline"] <= run.budget.deadline and data["deadline"] > now,
                        "delegation_budget_mismatch", "Handoff cannot replace or extend the reserved budget")
                stored = conn.execute("SELECT model_config FROM sessions WHERE id=?", (data["child_identity"]["session_id"],)).fetchone()
                require(stored is not None and (json.loads(stored[0] or "{}") or {}).get("agent_identity") == data["child_identity"],
                        "delegation_identity_mismatch", "Child identity must be constructor-bound in the canonical store")
                grants = data["grants"]
                parent_policy = run.context.policy
                require(set(grants["allowed_tools"]) <= parent_policy.allowed_tools
                        and set(grants["secret_refs"]) <= parent_policy.secret_refs
                        and not set(grants["secret_refs"]) & parent_policy.personal_secret_refs
                        and set(grants["project_grants"]) <= parent_policy.project_grants
                        and not grants["mcp_grants"].keys() & parent_policy.personal_mcp_servers
                        and all(set(tools) <= parent_policy.mcp_grants.get(server, frozenset())
                                for server, tools in grants["mcp_grants"].items()),
                        "delegation_grant_expansion", "Handoff grants cannot expand the parent or include personal authority")
                reservation = conn.execute("SELECT * FROM budget_reservations WHERE root_id=? AND operation_id=?",
                    (root, data["budget"]["reservation_id"])).fetchone()
                require(reservation is not None and reservation["account_id"] == run.budget.account_id
                        and reservation["settlement_state"] == "reserved",
                        "delegation_reservation_missing", "Exact unspent reservation required")
                binding = conn.execute("SELECT * FROM budget_child_bindings WHERE child_session_id=?",
                    (data["child_identity"]["session_id"],)).fetchone()
                require(binding is not None and binding["parent_id"] == run.budget.account_id,
                        "delegation_budget_mismatch", "Child must inherit the original budget account")
                conn.execute("INSERT INTO delegation_handoffs(child_id,root_run_id,parent_run_id,parent_session_id,child_session_id,"
                    "parent_agent_id,child_agent_id,handoff_json,handoff_sha256,state,owner_holder,owner_generation,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'accepted',?,?,?,?)", (data["child_id"], root, run.run_id, run.session_id,
                    data["child_identity"]["session_id"], self.context.identity.agent_id, data["child_identity"]["agent_id"],
                    handoff.snapshot, handoff.sha256, run.holder, run.generation, now, now))
            return [DelegationTicket(h.to_record()["child_id"], h.sha256, run.holder, run.generation) for h in handoffs]
        return self.db._execute_write(write)

    def start(self, run, ticket):
        def write(conn):
            self._fence(conn, run)
            row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (ticket.child_id,)).fetchone()
            handoff = self._handoff(row)
            require(row["state"] == "accepted" and row["owner_holder"] == ticket.holder == run.holder
                    and row["owner_generation"] == ticket.generation == run.generation and handoff.sha256 == ticket.handoff_sha256,
                    "delegation_already_started", "A child may start only once under its accepted owner")
            require(handoff.to_record()["deadline"] > time.time(), "delegation_expired", "Child deadline expired")
            conn.execute("UPDATE delegation_handoffs SET state='running',updated_at=? WHERE child_id=?", (time.time(), ticket.child_id))
        self.db._execute_write(write)

    def complete(self, ticket, result):
        """Parent-owned finite worker records output separately from parent delivery."""
        encoded = canonical(result)
        state = "completed" if result.get("status") == "completed" else (
            "cancelled" if result.get("status") == "interrupted" else "failed")
        def write(conn):
            row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (ticket.child_id,)).fetchone()
            handoff = self._handoff(row)
            require(row["owner_holder"] == ticket.holder and row["owner_generation"] == ticket.generation
                    and handoff.to_record()["parent_identity"] == self.context.identity.to_record()
                    and handoff.sha256 == ticket.handoff_sha256,
                    "delegation_stale_owner", "Completion owner was replaced")
            if row["completion_json"] is not None:
                require(row["completion_json"] == encoded, "delegation_completion_conflict", "Completion is immutable")
                return
            require(row["state"] == "running", "delegation_stale_owner", "Recovered/orphaned work cannot complete as live")
            conn.execute("UPDATE delegation_handoffs SET state=?,completion_json=?,updated_at=? WHERE child_id=?",
                         (state, encoded, time.time(), ticket.child_id))
        self.db._execute_write(write)

    def link_async(self, tickets, delegation_id):
        def write(conn):
            for ticket in tickets:
                row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (ticket.child_id,)).fetchone()
                handoff = self._handoff(row)
                require(row["state"] == "accepted" and row["async_delegation_id"] in (None, delegation_id)
                        and handoff.to_record()["parent_identity"] == self.context.identity.to_record()
                        and handoff.sha256 == ticket.handoff_sha256,
                        "delegation_async_conflict", "Async unit must retain its exact original handoffs")
                conn.execute("UPDATE delegation_handoffs SET async_delegation_id=? WHERE child_id=?", (delegation_id, ticket.child_id))
        self.db._execute_write(write)

    def read(self, child_id):
        with self.db._runtime_read() as conn:
            row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (child_id,)).fetchone()
            handoff = self._handoff(row)
            require(handoff.to_record()["parent_identity"] == self.context.identity.to_record(),
                    "delegation_identity_mismatch", "Only the exact parent session may read this handoff")
            return {"handoff": handoff.to_record(), "handoff_sha256": handoff.sha256, "state": row["state"],
                    "completion": json.loads(row["completion_json"]) if row["completion_json"] else None,
                    "delivery_state": row["delivery_state"], "execution_resumed": False}

    def recover(self, run):
        """A fenced parent classifies lost workers; it never blindly spawns replacements."""
        def write(conn):
            self._fence(conn, run, recovery=True)
            rows = conn.execute("SELECT * FROM delegation_handoffs WHERE parent_session_id=? AND parent_agent_id=? "
                "AND state IN ('accepted','running')", (run.session_id, self.actor["agent_id"])).fetchall()
            count = 0
            for row in rows:
                if row["owner_generation"] == run.generation and row["owner_holder"] == run.holder:
                    continue
                state = "orphaned" if row["state"] == "accepted" else "unknown"
                conn.execute("UPDATE delegation_handoffs SET state=?,updated_at=? WHERE child_id=?", (state, time.time(), row["child_id"]))
                count += 1
            return {"classified": count, "execution_resumed": False, "respawned": False}
        return self.db._execute_write(write)

    def claim_delivery(self, run, child_id):
        claim = uuid.uuid4().hex
        def write(conn):
            self._fence(conn, run)
            row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (child_id,)).fetchone()
            handoff = self._handoff(row)
            require(handoff.to_record()["parent_identity"] == self.context.identity.to_record()
                    and row["completion_json"] is not None, "delegation_completion_unavailable", "Owned completion required")
            if row["delivery_state"] == "delivered":
                return None
            require(row["delivery_state"] == "pending" or row["delivery_generation"] < run.generation,
                    "delegation_delivery_claimed", "Another current owner has claimed delivery")
            conn.execute("UPDATE delegation_handoffs SET delivery_state='claimed',delivery_claim=?,delivery_generation=?,updated_at=? "
                         "WHERE child_id=?", (claim, run.generation, time.time(), child_id))
            return {"claim_id": claim, "completion": json.loads(row["completion_json"]), "handoff_sha256": handoff.sha256}
        return self.db._execute_write(write)

    def finish_delivery(self, run, child_id, claim):
        def write(conn):
            self._fence(conn, run)
            row = conn.execute("SELECT * FROM delegation_handoffs WHERE child_id=?", (child_id,)).fetchone()
            handoff = self._handoff(row)
            require(handoff.to_record()["parent_identity"] == self.context.identity.to_record()
                    and row["delivery_state"] == "claimed" and row["delivery_claim"] == claim
                    and row["delivery_generation"] == run.generation,
                    "delegation_delivery_stale", "Delivery requires the exact live parent claim")
            conn.execute("UPDATE delegation_handoffs SET delivery_state='delivered',updated_at=? WHERE child_id=?", (time.time(), child_id))
        self.db._execute_write(write)


def authorize_async_delivery(conn, delegation_id, owner_context):
    """Existing completion queues must supply their already verified parent object.

    Event/session labels are not credentials. Legacy rows have no strict handoff.
    """
    rows = conn.execute("SELECT handoff_json,handoff_sha256 FROM delegation_handoffs WHERE async_delegation_id=?",
                        (delegation_id,)).fetchall()
    if not rows:
        return True
    from agent.runtime_context import AgentContext
    if not isinstance(owner_context, AgentContext):
        return False
    from agent.identity_lifecycle import agent_runtime_scope
    from tools.capability_broker import require_live_policy
    with agent_runtime_scope(owner_context):
        if require_live_policy(require_run=False) != owner_context:
            return False
    for row in rows:
        handoff = ImmutableHandoff(json.loads(row[0]))
        if handoff.sha256 != row[1] or handoff.to_record()["parent_identity"] != owner_context.identity.to_record():
            return False
    return True


def validate_async_handoffs(conn, record):
    refs = record.get("durable_handoffs")
    if not refs:
        return
    from agent.runtime_context import current_agent_context
    from tools.capability_broker import require_live_policy
    context = current_agent_context()
    require(context is not None and require_live_policy(require_run=False) == context,
            "delegation_owner_required", "Strict async dispatch requires the current parent identity")
    require(isinstance(refs, list) and len(refs) <= 32, "invalid_handoff", "Bounded async references required")
    require(conn.execute("SELECT 1 FROM async_delegations WHERE delegation_id=?", (record["delegation_id"],)).fetchone() is None,
            "delegation_already_started", "A durable async unit cannot be replaced or replayed")
    for ref in refs:
        require(isinstance(ref, dict) and set(ref) == {"child_id", "sha256"}, "invalid_handoff", "Exact async handoff required")
        row = conn.execute("SELECT handoff_json,handoff_sha256,state,async_delegation_id FROM delegation_handoffs WHERE child_id=?",
                           (ref["child_id"],)).fetchone()
        require(row is not None and row[1] == ref["sha256"] and row[2] == "accepted"
                and row[3] == record["delegation_id"], "delegation_async_conflict", "Async dispatch differs from its admitted handoff")
        handoff = ImmutableHandoff(json.loads(row[0]))
        require(handoff.sha256 == row[1] and handoff.to_record()["parent_identity"] == context.identity.to_record()
                and handoff.to_record()["parent_session_id"] == record.get("parent_session_id"),
                "delegation_identity_mismatch", "Async destination differs from its exact parent")
