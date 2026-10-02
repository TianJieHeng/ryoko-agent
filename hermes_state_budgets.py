"""Durable, fenced tree budgets. Amounts are integer units, never invoice promises.

Every reserve updates all ancestors in SessionDB's BEGIN IMMEDIATE transaction.
Provider uncertainty retains the worst-case units; a local stop is not a refund.
Bounded audit rows survive transcript deletion and there is no reset/prune API.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import time

from hermes_state_runtime import RuntimeStoreError, _identifier

BUDGET_RESOURCES = ("tokens", "attempts", "cost_micros", "wall_ms", "provider_slots", "executor_slots")
BUDGET_SLOTS = frozenset({"provider_slots", "executor_slots"})
MAX_BUDGET_DEPTH = 16
MAX_BUDGET_ACCOUNTS = 16_384
MAX_BUDGET_RESERVATIONS = 65_536
MAX_BUDGET_BINDINGS = 16_384
MAX_BUDGET_POLICY_BYTES = 16_384
MAX_BUDGET_INTEGER = (1 << 63) - 1
_ACTOR_KEYS = ("principal_id", "profile_id", "agent_id")


class BudgetStoreError(RuntimeStoreError):
    """Transport-independent budget rejection; no denied operation may dispatch."""


def _check(condition, code, message):
    if not condition:
        raise BudgetStoreError(code, message)


def _actor(value):
    _check(isinstance(value, Mapping), "identity_mismatch", "A trusted actor is required")
    for key in _ACTOR_KEYS:
        _identifier(value.get(key), key)
    return {key: value[key] for key in _ACTOR_KEYS}


def _amounts(value, *, limits=False):
    _check(isinstance(value, Mapping) and set(value) <= set(BUDGET_RESOURCES),
           "invalid_budget", "Budget amounts must use known integer resources")
    _check(not limits or set(value) == set(BUDGET_RESOURCES),
           "invalid_budget", "Every resource limit must be declared")
    result = {}
    for key in BUDGET_RESOURCES:
        amount = value.get(key, 0)
        if limits and key == "cost_micros" and amount is None:
            result[key] = None
            continue
        _check(type(amount) is int and 0 <= amount <= MAX_BUDGET_INTEGER,
               "invalid_budget", f"{key} must be a bounded nonnegative integer")
        result[key] = amount
    return result


def _json(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise BudgetStoreError("invalid_budget", "Policy snapshot must be finite JSON") from exc
    _check(len(result.encode("utf-8")) <= MAX_BUDGET_POLICY_BYTES,
           "budget_payload_limit", "Budget policy exceeds the inline bound")
    return result


def _deadline(value):
    _check(type(value) in (int, float) and 0 < value <= 253402300799 and math.isfinite(value),
           "invalid_budget", "Deadline must be finite UTC epoch seconds")
    return float(value)


def _identifier_bytes(value, name):
    _identifier(value, name)
    _check(len(value.encode("utf-8")) <= 256, "invalid_budget", f"{name} exceeds its byte bound")
    return value


def _zero():
    return dict.fromkeys(BUDGET_RESOURCES, 0)


def _account_result(row):
    limits = json.loads(row["limits_json"])
    return {key: row[key] for key in ("account_id", "root_id", "parent_id", "run_id", "session_id", "depth",
                                    "deadline", "state")} | {
        "limits": limits, "reserved": json.loads(row["reserved_json"]),
        "consumed": json.loads(row["consumed_json"]), "policy_snapshot": json.loads(row["policy_json"]),
        "unknown_usage": row["unknown_count"] > 0, "unknown_operations": row["unknown_count"],
        "debt": bool(row["debt"]), "cost_tracking": "untracked" if limits["cost_micros"] is None else "bounded",
        "expired": row["deadline"] <= time.time(), "invoice_guarantee": False,
        "actor": {key: row[key] for key in _ACTOR_KEYS},
    }


def _reservation_result(row, account):
    return {key: row[key] for key in ("account_id", "root_id", "operation_id", "deadline", "settlement_state")} | {
        "parent_id": account["parent_id"], "run_id": account["run_id"], "session_id": account["session_id"],
        "state": row["settlement_state"], "maxima": json.loads(row["maxima_json"]),
        "reserved": json.loads(row["held_json"]), "consumed": json.loads(row["consumed_json"]),
        "actual": json.loads(row["actual_json"]) if row["actual_json"] is not None else None,
        "unknown_usage": bool(row["unknown_usage"]), "slots_released": bool(row["slots_released"]),
        "debt": bool(row["debt"]), "dispatched": row["dispatched_at"] is not None, "invoice_guarantee": False,
        "cost_tracking": "untracked" if json.loads(account["limits_json"])["cost_micros"] is None else "bounded",
    }


class SessionBudgetsMixin:
    """Ledger only: runtime policy supplies defensible maxima and dispatches afterward."""

    def _budget_account_on_conn(self, conn, account_id, actor, *, holder=None, generation=None, mutate=False):
        _identifier_bytes(account_id, "account_id")
        actor = _actor(actor)
        row = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (account_id,)).fetchone()
        _check(row is not None, "budget_not_found", "Budget account does not exist")
        _check(all(row[key] == actor[key] for key in _ACTOR_KEYS),
               "identity_mismatch", "Budget belongs to a different actor")
        if mutate:
            self._runtime_fence_on_conn(conn, row["session_id"], holder, generation)
        return row

    def _budget_ancestors_on_conn(self, conn, row):
        ancestors, seen = [], set()
        while row is not None:
            _check(row["account_id"] not in seen and len(ancestors) <= MAX_BUDGET_DEPTH,
                   "budget_lineage_invalid", "Budget ancestry is cyclic or too deep")
            ancestors.append(row)
            seen.add(row["account_id"])
            if row["parent_id"] is None:
                _check(row["account_id"] == ancestors[0]["root_id"],
                       "budget_lineage_invalid", "Budget root reference does not match ancestry")
                return ancestors
            parent = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (row["parent_id"],)).fetchone()
            _check(parent is not None and parent["root_id"] == row["root_id"],
                   "budget_lineage_invalid", "Budget parent reference is missing")
            row = parent
        raise BudgetStoreError("budget_lineage_invalid", "Budget root is missing")

    @staticmethod
    def _budget_check_open(ancestors, *, deadline=None):
        now = time.time()
        for row in ancestors:
            _check(row["state"] == "open", "budget_closed", "Budget ancestor is closed or cancelled")
            _check(row["deadline"] > now, "budget_expired", "Budget ancestor deadline expired")
            _check(not row["debt"], "budget_debt", "Budget has an unresolved bound overrun")
        _check(deadline is None or deadline > now, "budget_expired", "Operation deadline expired")

    @staticmethod
    def _budget_check_quota(conn, table, maximum):
        # Table names are internal constants, never caller-supplied SQL.
        _check(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] < maximum,
               "budget_storage_limit", "Budget audit storage limit reached; retain records for reconciliation")

    def _budget_run_on_conn(self, conn, session_id, actor, run_id, holder, generation):
        sid = self._runtime_session_on_conn(conn, session_id)
        self._runtime_fence_on_conn(conn, sid, holder, generation)
        state = self._runtime_state_on_conn(conn, sid)
        _check(state is not None and all(state[key] == actor[key] for key in _ACTOR_KEYS),
               "identity_mismatch", "Budget actor differs from the accepted runtime actor")
        command = conn.execute("SELECT command_json FROM runtime_commands WHERE session_id=? "
                               "AND principal_id=? AND run_id=?",
                               (sid, actor["principal_id"], run_id)).fetchall()
        _check(any(json.loads(item[0])["operation"] in {"submit", "artifact"} for item in command),
               "budget_run_not_found", "Root and child accounts require an actual accepted runtime run")
        return sid

    def bind_budget_child(self, parent_id, actor, child_session_id, *, holder, generation):
        """Parent's trusted delegation handoff, persisted before child execution."""
        _identifier_bytes(child_session_id, "child_session_id")
        def write(conn):
            parent = self._budget_account_on_conn(conn, parent_id, actor, holder=holder, generation=generation, mutate=True)
            sid = self._runtime_session_on_conn(conn, child_session_id)
            _check(sid != parent["session_id"] or parent["parent_id"] is None,
                   "budget_lineage_invalid", "Only the root may bind its own session continuations")
            child_actor = self._runtime_state_on_conn(conn, sid)
            _check(child_actor is None or child_actor["principal_id"] is None or all(
                child_actor[key] == parent[key] for key in ("principal_id", "profile_id")),
                "identity_mismatch", "Child session belongs to another principal/profile")
            existing = conn.execute("SELECT parent_id FROM budget_child_bindings WHERE child_session_id=?", (sid,)).fetchone()
            if existing:
                _check(existing[0] == parent_id, "idempotency_conflict", "Child already belongs to another parent")
                return {"child_session_id": sid, "parent_id": parent_id, "root_id": parent["root_id"]}
            _check(sid == parent["session_id"] or conn.execute("SELECT 1 FROM budget_accounts WHERE session_id=?", (sid,)).fetchone() is None,
                   "budget_lineage_invalid", "An existing root session cannot become a child")
            self._budget_check_open(self._budget_ancestors_on_conn(conn, parent))
            self._budget_check_quota(conn, "budget_child_bindings", MAX_BUDGET_BINDINGS)
            conn.execute("INSERT INTO budget_child_bindings(child_session_id,parent_id,created_at) VALUES(?,?,?)",
                         (sid, parent_id, time.time()))
            return {"child_session_id": sid, "parent_id": parent_id, "root_id": parent["root_id"]}
        return self._execute_write(write)

    def get_budget_child_binding(self, child_session_id, actor):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, child_session_id)
            row = conn.execute("SELECT b.child_session_id,b.parent_id,a.root_id,a.principal_id,a.profile_id "
                "FROM budget_child_bindings b JOIN budget_accounts a ON a.account_id=b.parent_id "
                "WHERE b.child_session_id=?", (sid,)).fetchone()
            if row is None:
                return None
            _check(all(row[key] == actor[key] for key in ("principal_id", "profile_id")),
                   "identity_mismatch", "Child binding belongs to a different principal/profile")
            parent = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (row["parent_id"],)).fetchone()
            return {key: row[key] for key in ("child_session_id", "parent_id", "root_id")} | {"parent_account": _account_result(parent)}

    def create_budget_account(self, session_id, actor, run_id, limits, *, deadline, parent_id=None,
                              account_id=None, holder, generation, policy_snapshot=None):
        """Create once from an accepted run. Retries never reset limits or counters."""
        actor, limits, deadline = _actor(actor), _amounts(limits, limits=True), _deadline(deadline)
        _identifier_bytes(run_id, "run_id")
        account_id = run_id if account_id is None else _identifier_bytes(account_id, "account_id")
        _check(account_id == run_id, "invalid_budget", "Budget account identity must equal its accepted run identity")
        if parent_id is not None:
            _identifier_bytes(parent_id, "parent_id")
        policy_json, limits_json = _json(policy_snapshot), _json(limits)
        def write(conn):
            sid = self._budget_run_on_conn(conn, session_id, actor, run_id, holder, generation)
            existing = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (account_id,)).fetchone()
            if existing:
                _check(existing["session_id"] == sid and existing["run_id"] == run_id
                       and existing["parent_id"] == parent_id and existing["limits_json"] == limits_json
                       and existing["policy_json"] == policy_json and existing["deadline"] == deadline
                       and all(existing[key] == actor[key] for key in _ACTOR_KEYS),
                       "idempotency_conflict", "Account already exists with different immutable policy or lineage")
                return _account_result(existing)
            binding = conn.execute("SELECT parent_id FROM budget_child_bindings WHERE child_session_id=?", (sid,)).fetchone()
            root_id, depth = account_id, 0
            if parent_id is None:
                source = conn.execute("SELECT source FROM sessions WHERE id=?", (sid,)).fetchone()[0]
                _check(binding is None and source != "subagent" and conn.execute(
                    "SELECT 1 FROM budget_accounts WHERE session_id=?", (sid,)).fetchone() is None, "budget_parent_required",
                       "Child sessions must inherit their trusted parent budget")
            else:
                _check(binding is not None and binding[0] == parent_id, "budget_parent_required",
                       "Child budget requires a durable trusted parent handoff")
                parent = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (parent_id,)).fetchone()
                _check(parent is not None and all(parent[key] == actor[key] for key in ("principal_id", "profile_id")),
                       "identity_mismatch", "Child budget must share its parent's principal and profile")
                self._budget_check_open(self._budget_ancestors_on_conn(conn, parent))
                parent_limits = json.loads(parent["limits_json"])
                for key, ceiling in parent_limits.items():
                    _check(ceiling is None or (limits[key] is not None and limits[key] <= ceiling),
                           "budget_limit_enlarged", "Child cannot enlarge an ancestor ceiling")
                _check(deadline <= parent["deadline"], "budget_limit_enlarged", "Child cannot extend its parent's deadline")
                root_id, depth = parent["root_id"], parent["depth"] + 1
            _check(depth <= MAX_BUDGET_DEPTH, "budget_depth_limit", "Budget child depth exceeds the bound")
            _check(deadline > time.time(), "budget_expired", "Budget deadline expired")
            self._budget_check_quota(conn, "budget_accounts", MAX_BUDGET_ACCOUNTS)
            conn.execute("INSERT INTO budget_accounts(account_id,root_id,parent_id,session_id,run_id,principal_id,"
                "profile_id,agent_id,depth,limits_json,policy_json,reserved_json,consumed_json,deadline,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (account_id,root_id,parent_id,sid,run_id,
                *(actor[key] for key in _ACTOR_KEYS),depth,limits_json,policy_json,_json(_zero()),_json(_zero()),deadline,time.time()))
            if parent_id is None:
                self._budget_check_quota(conn, "budget_child_bindings", MAX_BUDGET_BINDINGS)
                conn.execute("INSERT INTO budget_child_bindings(child_session_id,parent_id,created_at) VALUES(?,?,?)",
                             (sid,account_id,time.time()))
            return _account_result(conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (account_id,)).fetchone())
        return self._execute_write(write)

    def get_budget_account(self, account_id, actor):
        with self._runtime_read() as conn:
            return _account_result(self._budget_account_on_conn(conn, account_id, actor))

    def _budget_reservation_on_conn(self, conn, account, operation_id):
        _identifier_bytes(operation_id, "operation_id")
        row = conn.execute("SELECT * FROM budget_reservations WHERE root_id=? AND operation_id=?",
                           (account["root_id"], operation_id)).fetchone()
        _check(row is not None, "budget_reservation_not_found", "Budget operation does not exist")
        _check(row["account_id"] == account["account_id"], "idempotency_conflict", "Operation belongs to another tree account")
        return row

    def get_budget_reservation(self, account_id, actor, operation_id):
        with self._runtime_read() as conn:
            account = self._budget_account_on_conn(conn, account_id, actor)
            return _reservation_result(self._budget_reservation_on_conn(conn, account, operation_id), account)

    def reserve_budget(self, account_id, actor, operation_id, maxima, *, holder, generation, deadline=None):
        maxima = _amounts(maxima)
        _identifier_bytes(operation_id, "operation_id")
        if deadline is not None:
            deadline = _deadline(deadline)
        def write(conn):
            account = self._budget_account_on_conn(conn, account_id, actor, holder=holder,generation=generation,mutate=True)
            effective_deadline = account["deadline"] if deadline is None else min(deadline, account["deadline"])
            prior = conn.execute("SELECT * FROM budget_reservations WHERE root_id=? AND operation_id=?",
                                 (account["root_id"], operation_id)).fetchone()
            if prior:
                _check(prior["account_id"] == account_id and prior["maxima_json"] == _json(maxima)
                       and prior["deadline"] == effective_deadline, "idempotency_conflict",
                       "Operation ID was already used with different maxima or deadline")
                return _reservation_result(prior, account)
            ancestors = self._budget_ancestors_on_conn(conn, account)
            self._budget_check_open(ancestors, deadline=effective_deadline)
            self._budget_check_quota(conn, "budget_reservations", MAX_BUDGET_RESERVATIONS)
            for ancestor in ancestors:
                limits, held, used = (json.loads(ancestor[key]) for key in ("limits_json","reserved_json","consumed_json"))
                for key, amount in maxima.items():
                    _check(limits[key] is None or used[key] + held[key] + amount <= limits[key],
                           "budget_exhausted", f"Ancestor {key} limit would be exceeded")
                    held[key] += amount
                conn.execute("UPDATE budget_accounts SET reserved_json=? WHERE account_id=?", (_json(held),ancestor["account_id"]))
            conn.execute("INSERT INTO budget_reservations(root_id,operation_id,account_id,maxima_json,held_json,"
                         "consumed_json,deadline,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (account["root_id"],operation_id,account_id,_json(maxima),_json(maxima),_json(_zero()),effective_deadline,time.time()))
            return _reservation_result(self._budget_reservation_on_conn(conn, account, operation_id),account)
        return self._execute_write(write)

    def mark_budget_dispatched(self, account_id, actor, operation_id, *, holder, generation):
        """Only dispatch_granted=True permits a new external invocation."""
        def write(conn):
            account = self._budget_account_on_conn(conn, account_id, actor,holder=holder,generation=generation,mutate=True)
            row = self._budget_reservation_on_conn(conn, account, operation_id)
            if row["settlement_state"] != "reserved":
                return _reservation_result(row,account) | {"dispatch_granted": False}
            self._budget_check_open(self._budget_ancestors_on_conn(conn,account),deadline=row["deadline"])
            self._budget_update_totals(conn,account,row,json.loads(row["held_json"]),_zero(),True,False)
            conn.execute("UPDATE budget_reservations SET settlement_state='dispatched',unknown_usage=1,dispatched_at=? "
                         "WHERE root_id=? AND operation_id=?", (time.time(),account["root_id"],operation_id))
            return _reservation_result(self._budget_reservation_on_conn(conn,account,operation_id),account) | {"dispatch_granted": True}
        return self._execute_write(write)

    def _budget_update_totals(self, conn, account, row, held, consumed, unknown, debt):
        old_held, old_used = json.loads(row["held_json"]),json.loads(row["consumed_json"])
        for ancestor in self._budget_ancestors_on_conn(conn,account):
            reserved, used = json.loads(ancestor["reserved_json"]),json.loads(ancestor["consumed_json"])
            for key in BUDGET_RESOURCES:
                reserved[key] += held[key] - old_held[key]
                used[key] += consumed[key] - old_used[key]
                _check(reserved[key] >= 0 and used[key] >= 0,"budget_lineage_invalid","Budget counters became negative")
            conn.execute("UPDATE budget_accounts SET reserved_json=?,consumed_json=?,unknown_count=unknown_count+?,"
                         "debt=MAX(debt,?) WHERE account_id=?", (_json(reserved),_json(used),
                         int(unknown)-row["unknown_usage"],int(debt),ancestor["account_id"]))

    def settle_budget(self, account_id, actor, operation_id, actual=None, *, holder, generation,
                      unknown_usage=False, slots_released=True):
        """Release slots only when known finished; unknown remote units remain reserved.

        A later known settlement can reconcile uncertainty, but final known amounts
        are immutable. Bound overruns remain a sticky debt flag and block new work.
        """
        _check(type(unknown_usage) is bool and type(slots_released) is bool,"invalid_budget","Settlement flags must be booleans")
        _check(actual is not None or unknown_usage,"invalid_budget","Known settlement requires measured actual usage")
        actual = None if actual is None else _amounts(actual)
        _check(actual is None or all(actual[key] == 0 for key in BUDGET_SLOTS),
               "invalid_budget","Concurrency slots are released, never cumulatively consumed")
        def write(conn):
            account = self._budget_account_on_conn(conn,account_id,actor,holder=holder,generation=generation,mutate=True)
            row = self._budget_reservation_on_conn(conn,account,operation_id)
            _check(row["settlement_state"] in {"dispatched","unknown","settled"},"budget_not_dispatched",
                   "Only dispatched operations may settle; release proven undispatched work")
            actual_json = None if actual is None else _json(actual)
            if row["settlement_state"] == "settled":
                _check(not unknown_usage and row["actual_json"] == actual_json and row["slots_released"] == slots_released,
                       "idempotency_conflict","Final settlement is immutable")
                return _reservation_result(row,account)
            _check(not row["slots_released"] or slots_released,"idempotency_conflict","Released slots cannot be reacquired")
            maxima, held, consumed = json.loads(row["maxima_json"]),_zero(),_zero()
            measured = actual or _zero()
            debt = any(measured[key] > maxima[key] for key in BUDGET_RESOURCES)
            for key in BUDGET_RESOURCES:
                if key in BUDGET_SLOTS:
                    held[key] = 0 if slots_released else maxima[key]
                elif unknown_usage:
                    held[key] = max(maxima[key],measured[key],json.loads(row["held_json"])[key])
                else:
                    consumed[key] = measured[key]
            # Known units with retained live slots remain reconcilable as an unknown
            # reservation until the external work is definitely finished.
            unknown = unknown_usage or not slots_released
            state = "unknown" if unknown else "settled"
            self._budget_update_totals(conn,account,row,held,consumed,unknown,debt)
            conn.execute("UPDATE budget_reservations SET actual_json=?,held_json=?,consumed_json=?,settlement_state=?,"
                         "unknown_usage=?,slots_released=?,debt=MAX(debt,?),settled_at=? WHERE root_id=? AND operation_id=?",
                         (actual_json,_json(held),_json(consumed),state,int(unknown),int(slots_released),int(debt),time.time(),
                          account["root_id"],operation_id))
            return _reservation_result(self._budget_reservation_on_conn(conn,account,operation_id),account)
        return self._execute_write(write)

    def release_budget_reservation(self, account_id, actor, operation_id, *, holder, generation):
        """Refund only durable reserved work that never crossed dispatch admission."""
        def write(conn):
            account = self._budget_account_on_conn(conn,account_id,actor,holder=holder,generation=generation,mutate=True)
            row = self._budget_reservation_on_conn(conn,account,operation_id)
            if row["settlement_state"] == "released":
                return _reservation_result(row,account)
            _check(row["settlement_state"] == "reserved" and row["dispatched_at"] is None,
                   "budget_dispatch_uncertain","Dispatched work cannot be refunded without a measured settlement")
            self._budget_update_totals(conn,account,row,_zero(),_zero(),False,False)
            conn.execute("UPDATE budget_reservations SET held_json=?,settlement_state='released',slots_released=1,settled_at=? "
                         "WHERE root_id=? AND operation_id=?",(_json(_zero()),time.time(),account["root_id"],operation_id))
            return _reservation_result(self._budget_reservation_on_conn(conn,account,operation_id),account)
        return self._execute_write(write)

    def close_budget_account(self, account_id, actor, *, holder, generation, state="closed"):
        _check(state in {"closed","cancelled"},"invalid_budget","Invalid terminal budget state")
        def write(conn):
            row = self._budget_account_on_conn(conn,account_id,actor,holder=holder,generation=generation,mutate=True)
            _check(row["state"] in {"open",state},"idempotency_conflict","Budget already has a different terminal state")
            conn.execute("UPDATE budget_accounts SET state=? WHERE account_id=?",(state,account_id))
            return _account_result(conn.execute("SELECT * FROM budget_accounts WHERE account_id=?",(account_id,)).fetchone())
        return self._execute_write(write)
