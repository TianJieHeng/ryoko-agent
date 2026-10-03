"""Existing run-tree authority for finite offline speech, including idle controls.

A media request never creates a mission, run, account, or allocation. It names an
already admitted account and a stable request identity. Idle controls borrow only
the session writer lease; they do not impersonate an active runtime run.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import time
import uuid

from agent.bounded_services import require
from agent.budget_account import BudgetPolicyError, actor_for, budget_enabled, parse_budget_policy
from hermes_state_budgets import BudgetStoreError


def speech_policy(agent):
    from hermes_cli.config import load_config
    config = load_config()
    try:
        configured = parse_budget_policy(config)
    except BudgetPolicyError:
        require(False, "speech_budget_unsupported")
    policy = getattr(agent, "_runtime_budget_policy", None)
    require(policy == configured, "speech_budget_unsupported")
    return policy


def speech_budget_blocked(agent):
    try:
        speech_policy(agent)
    except (ValueError, TypeError):
        return True
    return False


def voice_admission_record(agent):
    db = agent._session_db
    with db._runtime_read() as conn:
        sid = db._runtime_session_on_conn(conn, str(agent.session_id))
        row = conn.execute("SELECT command_id FROM runtime_commands WHERE session_id=? "
            "AND json_extract(command_json,'$.operation')='artifact' "
            "AND json_extract(command_json,'$.payload.mode')='runtime.voice.admit' "
            "ORDER BY accepted_revision DESC LIMIT 1", (sid,)).fetchone()
    return db.read_runtime_command(sid, row["command_id"]) if row else None


def current_speech_account(agent):
    # Never choose a client-supplied sibling. Once a real task is current, its
    # exact run supersedes the explicit first-voice control.
    state = agent._session_db.read_runtime_snapshot(str(agent.session_id))["state"]
    if state["run_id"] is not None:
        return state["run_id"]
    record = voice_admission_record(agent)
    return record["receipt"]["run_id"] if record else None


@dataclass(frozen=True)
class SpeechBudgetBinding:
    agent: object
    context: object
    account_id: str
    request_id: str
    kind: str
    deadline: float
    mission_id: str | None = None
    mission_deadline: float | None = None
    owner_check: object = None

    def account(self):
        from tools.capability_broker import require_live_policy
        require(self.agent.runtime_context is self.context
                and require_live_policy(require_run=False) == self.context, "identity_mismatch")
        policy = speech_policy(self.agent)
        require(policy is not None, "speech_budget_unsupported")
        if self.owner_check is not None:
            require(self.owner_check(), "speech_owner_changed")
        db, actor = self.agent._session_db, actor_for(self.context)
        account = db.get_budget_account(self.account_id, actor)
        with db._runtime_read() as conn:
            sid = db._runtime_session_on_conn(conn, str(self.agent.session_id))
        require(account["session_id"] == sid, "speech_budget_session_mismatch")
        require(current_speech_account(self.agent) == account["run_id"], "speech_budget_owner_changed")
        from agent.project_context import project_access
        mission = db.get_mission(sid, actor, access=project_access(self.context))
        require((mission["mission_id"] if mission else None) == self.mission_id,
                "speech_budget_mission_changed")
        if mission is not None:
            with db._runtime_read() as conn:
                root, _ = db._mission_budget_on_conn(conn, sid, actor, mission["budget_ref"])
            require(root == account["root_id"] and mission["state"] not in {"paused", "cancelled"},
                    "speech_budget_mission_changed")
            require(mission.get("deadline") is None or mission["deadline"] > time.time(), "budget_expired")
        require(account["policy_snapshot"] == policy.record and account["deadline"] == self.deadline,
                "speech_budget_policy_changed")
        require(account["state"] == "open", "budget_closed")
        require(not account["debt"], "budget_debt")
        require(time.time() < self.effective_deadline, "budget_expired")
        return db, actor, policy, account

    @property
    def effective_deadline(self):
        return min(self.deadline, self.mission_deadline) if self.mission_deadline is not None else self.deadline

    @property
    def operation_id(self):
        # Exclude account_id: moving a retry to a sibling must not buy another
        # dispatch in the same tree. No text, audio, or transport secret is stored.
        key = f"{self.context.identity.session_id}\0{self.kind}\0{self.request_id}"
        return "speech:" + self.kind + ":" + hashlib.sha256(key.encode()).hexdigest()

    def receipt(self):
        return self.agent._session_db.get_budget_reservation(
            self.account_id, actor_for(self.context), self.operation_id)

    @contextmanager
    def execution(self, wall_seconds):
        from agent.runtime_commands import RuntimeRun, _check_run
        db, actor, policy, account = self.account()
        run = getattr(self.agent, "_active_runtime_run", None)
        owned = False
        if run is not None:
            require(isinstance(run, RuntimeRun) and run.agent is self.agent
                    and run.context is self.context and run.db is db
                    and run.budget is not None and run.budget.account_id == self.account_id,
                    "speech_budget_owner_changed")
            _check_run(run)
            holder, generation = run.holder, run.generation
        else:
            holder = "speech-control:" + uuid.uuid4().hex
            require(db.try_acquire_session_turn_lease(str(self.agent.session_id), holder,
                ttl_seconds=wall_seconds + 5, patience_s=0.5), "speech_budget_owner_busy")
            owned = True
            generation = None
        try:
            if owned:
                lease = db.get_session_turn_lease(str(self.agent.session_id))
                require(lease is not None and lease["holder"] == holder, "stale_owner")
                generation = lease["generation"]
                # A crashed/claimed or queued run is not an idle speech grant.
                # Only an actual terminal run can supply an off-turn account.
                with db._runtime_read() as conn:
                    rows = conn.execute("SELECT status FROM runtime_commands WHERE session_id=? AND run_id=?",
                                        (account["session_id"], account["run_id"])).fetchall()
                require(rows and all(row["status"] in {"completed", "failed", "blocked"} for row in rows),
                        "speech_budget_run_unfinished")
            bound = SpeechBudgetExecution(self, db, actor, holder, generation, run)
            bound.check()
            try:
                db.get_budget_reservation(self.account_id, actor, self.operation_id)
            except BudgetStoreError as exc:
                if exc.code != "budget_reservation_not_found":
                    raise
            else:
                require(False, "speech_request_consumed")
            maximum = min(math.floor(wall_seconds * 1000), policy.record["request_timeout_ms"],
                          math.floor((self.effective_deadline - time.time()) * 1000))
            # Leave room for process startup, one second of OS CPU allowance and
            # bounded kill/reap. Too-short requests never reserve or spawn.
            require(maximum >= 1500, "speech_budget_deadline_insufficient")
            bound.maximum_ms = maximum
            bound.deadline = min(self.effective_deadline, time.time() + maximum / 1000)
            row = db.reserve_budget(self.account_id, actor, self.operation_id,
                {"executor_slots": 1, "wall_ms": maximum, "attempts": 1},
                holder=holder, generation=generation, deadline=bound.deadline)
            require(row["state"] == "reserved", "speech_request_consumed")
            yield bound
        finally:
            if owned:
                db.release_session_turn_lease(str(self.agent.session_id), holder, generation=generation)


@dataclass
class SpeechBudgetExecution:
    binding: SpeechBudgetBinding
    db: object
    actor: dict
    holder: str
    generation: int
    run: object
    maximum_ms: int = 0
    deadline: float = 0
    dispatched: bool = False

    @property
    def fence(self):
        return {"holder": self.holder, "generation": self.generation}

    def check(self):
        self.binding.account()
        lease = self.db.get_session_turn_lease(str(self.binding.agent.session_id))
        require(lease is not None and lease["holder"] == self.holder
                and lease["generation"] == self.generation, "stale_owner")
        if self.run is not None:
            from agent.runtime_commands import _check_run
            require(getattr(self.binding.agent, "_active_runtime_run", None) is self.run,
                    "speech_budget_owner_changed")
            _check_run(self.run)
        if self.deadline:
            require(time.time() < self.deadline, "speech_deadline_exceeded")

    def dispatch(self):
        self.check()
        result = self.db.mark_budget_dispatched(self.binding.account_id, self.actor,
            self.binding.operation_id, **self.fence)
        require(result["dispatch_granted"] is True, "speech_request_consumed")
        self.dispatched = True

    def settle(self, elapsed_ms, *, spawned, uncertain):
        if not self.dispatched:
            return self.db.release_budget_reservation(self.binding.account_id, self.actor,
                self.binding.operation_id, **self.fence)
        return self.db.settle_budget(self.binding.account_id, self.actor, self.binding.operation_id,
            {"wall_ms": elapsed_ms, "attempts": int(spawned)}, unknown_usage=uncertain,
            slots_released=not uncertain, **self.fence)


def admit_speech(agent, kind, request_id, account_id, owner_check=None):
    policy = speech_policy(agent)
    if policy is None:
        return None
    require(budget_enabled(agent), "speech_budget_unsupported")
    require(isinstance(request_id, str) and 0 < len(request_id.encode()) <= 256
            and request_id.strip() == request_id, "speech_request_id_required")
    require(isinstance(account_id, str) and 0 < len(account_id.encode()) <= 256,
            "speech_budget_account_required")
    context = agent.runtime_context
    account = agent._session_db.get_budget_account(account_id, actor_for(context))
    from agent.project_context import project_access
    mission = agent._session_db.get_mission(str(agent.session_id), actor_for(context), access=project_access(context))
    binding = SpeechBudgetBinding(agent, context, account_id, request_id, kind, account["deadline"],
                                 mission["mission_id"] if mission else None, mission.get("deadline") if mission else None, owner_check)
    binding.account()
    try:
        agent._session_db.get_budget_reservation(account_id, actor_for(context), binding.operation_id)
    except BudgetStoreError as exc:
        if exc.code != "budget_reservation_not_found":
            raise
    else:
        require(False, "speech_request_consumed")
    return binding
