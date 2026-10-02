"""Real SQLite acceptance, scheduling, restart and terminal-state invariants."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.admission import AdmissionPolicy, AdmissionQueue
from agent.agent_identity import resolve_agent_context
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    agents = {}
    for sid, principal in (("a", "alice"), ("b", "bob"), ("a2", "alice")):
        config = {"agent_identity": {"schema_version": 1, "principal_id": principal,
            "profile_id": "test", "primary_agent_id": "primary", "active_agent_id": "primary",
            "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}
        ctx = resolve_agent_context(config, session_id=sid, profile_home=tmp_path)
        db.create_session(sid, source="tui")
        db.claim_session_agent_identity(sid, ctx.identity.to_record())
        agents[sid] = SimpleNamespace(runtime_context=ctx, _session_db=db, session_id=sid,
                                     api_mode="chat_completions", provider="fixture")
    yield db, agents
    db.close()


def command(cid, text="private prompt"):
    return {"schema_version": 1, "command_id": cid, "idempotency_key": cid,
            "expected_revision": None, "operation": "submit", "payload": {"text": text}}


def test_atomic_bounds_and_no_prompt_copy(store):
    db, agents = store
    policy = replace(AdmissionPolicy(), max_queued=2, max_per_principal=1)
    queue = AdmissionQueue(db, policy)
    first = queue.submit(agents["a"], command("one"))
    assert queue.submit(agents["a"], command("one")) == first
    with pytest.raises(RuntimeStoreError, match="principal queue full"):
        queue.submit(agents["a2"], command("noisy"))
    assert db.read_runtime_command("a2", "noisy") is None
    queue.submit(agents["b"], command("two"))
    with pytest.raises(RuntimeStoreError, match="queue full"):
        queue.submit(agents["b"], command("overflow"))
    assert db.read_runtime_command("b", "overflow") is None
    with db._runtime_read() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(runtime_admission_queue)")]
        assert not {"text", "prompt", "payload", "command_json"} & set(columns)
    for name, value in (("max_payload_bytes", 10), ("max_queue_bytes", 10), ("max_database_bytes", 10)):
        small = AdmissionQueue(db, replace(AdmissionPolicy(), **{name: value}))
        with pytest.raises(RuntimeStoreError):
            small.submit(agents["a"], command("refused-" + name, "☃" * 8))
        assert db.read_runtime_command("a", "refused-" + name) is None


def test_principal_fairness_priority_aging_and_cross_worker_slots(store, monkeypatch):
    db, agents = store
    now = [1000.0]
    monkeypatch.setattr("agent.admission.time.time", lambda: now[0])
    queue = AdmissionQueue(db, replace(AdmissionPolicy(), max_active=1))
    queue.submit(agents["a"], command("old-background"), workload="background")
    now[0] += 1
    queue.submit(agents["a2"], command("interactive"))
    first = queue.reserve_next("worker", {"a", "a2", "b"})
    assert first["command_id"] == "interactive"
    queue.reject_launch(first["session_id"], first["command_id"])
    queue.submit(agents["b"], command("bob"))
    second = queue.reserve_next("worker", {"a", "a2", "b"})
    assert second["principal_id"] == "bob"
    queue.reject_launch(second["session_id"], second["command_id"])
    now[0] += 20
    queue.submit(agents["a2"], command("new-interactive"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda n: AdmissionQueue(db, queue.policy).reserve_next(str(n), {"a", "a2", "b"}), range(8)))
    assert len([claim for claim in claims if claim]) == 1
    assert next(claim for claim in claims if claim)["command_id"] == "old-background"


def test_restart_unclaimed_recovery_expiry_and_drain_are_terminal(store, monkeypatch):
    db, agents = store
    now = [2000.0]
    monkeypatch.setattr("agent.admission.time.time", lambda: now[0])
    queue = AdmissionQueue(db, replace(AdmissionPolicy(), ttl_seconds=60, launch_lease_seconds=5))
    queue.submit(agents["a"], command("recover"))
    first = queue.reserve_next("crashed", {"a"})
    assert first
    path = db.db_path
    db.close()
    restored = SessionDB(path)
    try:
        recovered = AdmissionQueue(restored, queue.policy)
        now[0] += 6
        again = recovered.reserve_next("new-worker", {"a"})
        assert again["command_id"] == first["command_id"]
        now[0] += 60
        assert recovered.reserve_next("new-worker", {"a"}) is None
        record = restored.read_runtime_command("a", "recover")
        assert record["status"] == "blocked" and record["result"]["admission_state"] == "expired"
        agents["a"]._session_db = restored
        recovered.submit(agents["a"], command("drain"))
        recovered.set_draining(True, reject_queued=True)
        assert restored.read_runtime_command("a", "drain")["result"]["admission_state"] == "rejected"
        with pytest.raises(RuntimeStoreError, match="admission draining"):
            recovered.submit(agents["a"], command("after-drain"))
        assert restored.read_runtime_command("a", "after-drain") is None
    finally:
        restored.close()


def test_queue_cancel_is_idempotent_and_claimed_work_never_replays(store, monkeypatch):
    db, agents = store
    now = [3000.0]
    monkeypatch.setattr("agent.admission.time.time", lambda: now[0])
    queue = AdmissionQueue(db)
    queue.submit(agents["a"], command("running"))
    queue.reserve_next("worker", {"a"})
    assert db.try_acquire_session_turn_lease("a", "owner", ttl_seconds=600)
    generation = db.get_session_turn_lease("a")["generation"]
    assert db.claim_runtime_command("a", "running", holder="owner", generation=generation)
    queue.submit(agents["b"], command("waiting"))
    cancel = {**command("stop"), "operation": "cancel", "payload": {"reason": "stop"}}
    receipt = queue.cancel_command(agents["b"], cancel)
    assert queue.cancel_command(agents["b"], cancel) == receipt
    assert db.read_runtime_command("b", "waiting")["status"] == "cancelled"
    result = db.read_runtime_command("b", "stop")["result"]
    assert result["cancellation"]["upstream_ack"] is None
    assert result["cancellation"]["remote_effects_undone"] is False
    now[0] += 900
    assert queue.reserve_next("replacement", {"a", "b"}) is None
    assert db.read_runtime_command("a", "running")["status"] == "claimed"


def test_acceptance_pins_deadline_and_budget_snapshot(store):
    from agent.budget_account import parse_budget_policy
    from agent.runtime_commands import submit_command
    import time

    db, agents = store
    agent = agents["a"]
    config = {"schema_version": 1, "mode": "tokens",
        "limits": {"tokens": 10000, "attempts": 2, "cost_micros": None,
                   "wall_ms": 30000, "provider_slots": 1, "executor_slots": 1},
        "deadline_seconds": 30, "request_timeout_ms": 1000,
        "routes": [{"model": "fixture", "base_url": "https://fixture.invalid/v1",
                    "max_input_tokens": 1000, "max_output_tokens": 64,
                    "input_overhead_tokens": 128, "output_token_parameter": "max_tokens",
                    "input_cost_micros_per_million": None, "output_cost_micros_per_million": None,
                    "bounds_verified": True}]}
    original = parse_budget_policy({"runtime_budget": config})
    agent._runtime_budget_policy = original
    queue = AdmissionQueue(db)
    deadline = time.time() + 30
    receipt = queue.submit(agent, command("pinned"), deadline=deadline)
    # A command accepted through a non-queue ingress also keeps its original
    # policy when an explicit retry supplies its first durable queue reference.
    unqueued = submit_command(agent, command("accepted-before-queue"))
    agent._runtime_budget_policy = parse_budget_policy({"runtime_budget": {
        **config, "limits": {**config["limits"], "attempts": 20}}})
    assert queue.submit(agent, command("accepted-before-queue")) == unqueued
    for cid in ("pinned", "accepted-before-queue"):
        assert queue.budget_policy("a", cid) == (True, original.snapshot)
        assert db.read_runtime_command("a", cid)["budget_policy_json"] == original.snapshot
    agent._runtime_budget_policy = None
    assert queue.submit(agent, command("pinned")) == receipt
    assert queue.deadline("a", "pinned") == deadline
    assert db.read_runtime_run_accepted_at("a", receipt["run_id"]) <= deadline
