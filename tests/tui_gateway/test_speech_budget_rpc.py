"""Strict owned speech through real RPC/profile, ledger, and offline subprocesses."""
import json
import threading
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied  # noqa: F401
from tests.tui_gateway.test_media_rpc import media_result
from tests.agent.test_speech_local import local_speech_packages, configured_home, _wait_for_file  # noqa: F401
from tests.agent.test_budget_runtime import policy


def configure(rpc, label="a", *, stt_extra=None, tts_extra=None, budget=None):
    from agent.budget_account import parse_budget_policy
    agent, home = rpc.agents[label], rpc.homes[label]
    original = json.loads((home / "config.yaml").read_text())
    config = configured_home(home, stt_extra=stt_extra, tts_extra=tts_extra)
    config = {**original, **config, "runtime_budget": budget or policy()}
    (home / "config.yaml").write_text(json.dumps(config))
    agent._runtime_budget_policy = parse_budget_policy(config)
    return agent


def admit(agent, key="admitted", *, finish=True):
    from agent.budget_account import actor_for, create_run_budget
    db, context, sid = agent._session_db, agent.runtime_context, agent.session_id
    actor = actor_for(context)
    receipt = db.submit_runtime_command(sid, actor, dict(schema_version=1, command_id=key,
        idempotency_key=key, identity_binding=actor, operation="submit", payload={"text": "Existing admitted task"}),
        budget_policy_json=agent._runtime_budget_policy.snapshot)
    holder = "speech-fixture"
    assert db.try_acquire_session_turn_lease(sid, holder)
    generation = db.get_session_turn_lease(sid)["generation"]
    assert db.claim_runtime_command(sid, key, holder=holder, generation=generation)
    budget = create_run_budget(agent, db, context, receipt["run_id"], holder, generation, key)
    if finish:
        db.finish_runtime_command(sid, key, holder=holder, generation=generation, result={"completed": True})
    db.release_session_turn_lease(sid, holder, generation=generation)
    return budget


@pytest.mark.platforms("posix")
def test_strict_rpc_first_turn_failclosed_idle_account_and_cross_profile_replay(artifacts, local_speech_packages):
    from hermes_state import SessionDB
    from tui_gateway import server
    for label in ("a", "b"):
        configure(artifacts, label)
    capabilities = media_result(artifacts.call("runtime.media.capabilities"))["voice"]
    assert capabilities["budget"]["mode"] == "existing_run_tree"
    assert capabilities["budget"]["first_turn_without_account"] == "explicit_admission_required"
    denied(artifacts.call("runtime.voice.capture.start"), "speech_request_id_required")
    denied(artifacts.call("runtime.voice.capture.start", request_id="first"), "speech_budget_account_required")
    denied(artifacts.call("runtime.voice.speak", request_id="first", budget_account_id="missing", text="hello"), "budget_not_found")
    agent = artifacts.agents["a"]
    budget = admit(agent)
    capture = media_result(artifacts.call("runtime.voice.capture.start", request_id="capture", budget_account_id=budget.account_id))
    final = media_result(artifacts.call("runtime.voice.capture.feed", capture_id=capture["capture_id"], sequence=0,
                                      pcm_base64="AAA=", final=True))
    assert final["budget"]["actual"]["attempts"] == 1
    output = media_result(artifacts.call("runtime.voice.speak", request_id="speak", budget_account_id=budget.account_id, text="Ada at 12"))
    assert output["budget"]["state"] == "settled" and output["playback"] == "client"
    denied(artifacts.call("runtime.voice.speak", "b", request_id="foreign", budget_account_id=budget.account_id, text="private"), "budget_not_found")
    # Reopen the real ledger and replace the transport/local projection. Stable
    # request identity still rejects dispatch and never replays stored audio.
    agent._session_db.close()
    agent._session_db = SessionDB(artifacts.homes["a"] / "state.db")
    del agent._bounded_voice_ingress
    replacement = SimpleNamespace(write=lambda _frame: True)
    server._sessions["live-a"]["transport"] = replacement
    response = artifacts.call("runtime.voice.speak", via=replacement,
        request_id="speak", budget_account_id=budget.account_id, text="even changed text")
    denied(response, "speech_request_consumed")
    assert "pcm_base64" not in json.dumps(response)
    assert agent._session_db.get_budget_account(budget.account_id, budget.actor)["consumed"]["attempts"] == 2


@pytest.mark.platforms("posix")
def test_strict_reconnect_during_worker_stops_disclosure_and_stale_fence_retains_hold(artifacts, local_speech_packages):
    from tui_gateway import server
    agent = configure(artifacts, tts_extra={"block": True})
    budget = admit(agent)
    replies = []
    def work():
        replies.append(artifacts.call("runtime.voice.speak", request_id="reconnect",
                                    budget_account_id=budget.account_id, text="private"))
    thread = threading.Thread(target=work)
    thread.start()
    _wait_for_file(artifacts.homes["a"] / "tts-started")
    old = agent._session_db.get_session_turn_lease(agent.session_id)
    # Retire the physical transport and the writer while native work is in flight.
    replacement = SimpleNamespace(write=lambda _frame: True)
    server._sessions["live-a"]["transport"] = replacement
    agent._session_db.release_session_turn_lease(agent.session_id, old["holder"], generation=old["generation"])
    assert agent._session_db.try_acquire_session_turn_lease(agent.session_id, "new-owner")
    thread.join(5)
    assert not thread.is_alive() and len(replies) == 1
    denied(replies[0])
    ledger = agent._session_db.get_budget_account(budget.account_id, budget.actor)
    assert ledger["unknown_usage"] and ledger["reserved"]["executor_slots"] == 1
    assert "pcm_base64" not in json.dumps(replies[0])
    import psutil
    assert not psutil.pid_exists(int((artifacts.homes["a"] / "tts-started").read_text()))
    lease = agent._session_db.get_session_turn_lease(agent.session_id)
    assert lease["holder"] == "new-owner" and lease["generation"] > old["generation"]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("existing_mission", [False, True])
def test_explicit_first_voice_control_is_real_bounded_and_never_renews(artifacts, local_speech_packages, monkeypatch, existing_mission):
    from agent.budget_account import actor_for
    if existing_mission:
        project = artifacts.project()["id"]
        media = artifacts.call("runtime.mission.create", mission_id="voice-mission",
            contract={"outcome": "An explicitly defined task", "project_id": project})
        assert "error" not in media
    agent = configure(artifacts)
    initial = media_result(artifacts.call("runtime.voice.admit", request_id="first-voice"))
    assert initial["status"] == "admitted" and not initial["capture_started"]
    assert not initial["model_dispatched"] and not initial["mission_accepted"]
    assert not initial["reservation_created"] and not hasattr(agent, "client")
    assert getattr(agent, "_active_runtime_run", None) is None
    account = initial["budget_account_id"]
    db = agent._session_db
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT command_json,status,result_json FROM runtime_commands WHERE run_id=?", (account,)).fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "completed"
        command = json.loads(rows[0]["command_json"])
        assert command["operation"] == "artifact" and command["payload"]["purpose"] == "offline_speech"
        assert conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
        mission_before = conn.execute("SELECT record_json FROM runtime_missions").fetchall()
    for request in ("first-voice", "another-click"):
        assert media_result(artifacts.call("runtime.voice.admit", request_id=request)) == initial
    capture = media_result(artifacts.call("runtime.voice.capture.start", request_id="first-audio", budget_account_id=account))
    final = media_result(artifacts.call("runtime.voice.capture.feed", capture_id=capture["capture_id"],
        sequence=0, pcm_base64="AAA=", final=True))
    assert final["text"] == "Ada at 12" and not final["accepted_as_task"]
    assert final["budget"]["root_id"] == initial["root_id"]
    with db._runtime_read() as conn:
        assert conn.execute("SELECT record_json FROM runtime_missions").fetchall() == mission_before
        assert conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0] == 1
    # A later real task inherits the first ceiling, while its exact current
    # account replaces the speech control for subsequent physical operations.
    newer = admit(agent, "real-task")
    current = media_result(artifacts.call("runtime.voice.admit", request_id="after-task"))
    assert current["budget_account_id"] == newer.account_id != account
    assert current["root_id"] == initial["root_id"] and current["deadline"] == initial["deadline"]
    denied(artifacts.call("runtime.voice.speak", request_id="old-root", budget_account_id=account, text="No"),
           "speech_budget_owner_changed")
    # Neither retry nor a fresh click silently renews an expired allocation.
    monkeypatch.setattr("agent.speech_budget.time", SimpleNamespace(time=lambda: initial["deadline"] + 1))
    denied(artifacts.call("runtime.voice.admit", request_id="first-voice"), "budget_expired")
    denied(artifacts.call("runtime.voice.admit", request_id="new-after-expiry"), "budget_expired")
    ledger = db.get_budget_account(account, actor_for(agent.runtime_context))
    assert ledger["deadline"] == initial["deadline"] and ledger["consumed"]["attempts"] == 1
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0] == 2


@pytest.mark.platforms("posix")
def test_first_voice_exhaustion_and_cancelled_capture_never_buy_a_new_root(artifacts, local_speech_packages):
    configured = policy(attempts=1)
    agent = configure(artifacts, budget=configured)
    initial = media_result(artifacts.call("runtime.voice.admit", request_id="one-finite-budget"))
    account = initial["budget_account_id"]
    for index in range(10):
        media_result(artifacts.call("runtime.voice.capture.start", request_id=f"cancel-{index}", budget_account_id=account))
        media_result(artifacts.call("runtime.voice.capture.cancel"))
    with agent._session_db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0] == 1
    audio = media_result(artifacts.call("runtime.voice.speak", request_id="only-attempt", budget_account_id=account, text="Hello"))
    assert audio["budget"]["actual"]["attempts"] == 1
    for request in ("one-finite-budget", "another-admission"):
        denied(artifacts.call("runtime.voice.admit", request_id=request), "budget_exhausted")
    denied(artifacts.call("runtime.voice.speak", request_id="second-attempt", budget_account_id=account, text="No"), "budget_exhausted")
    with agent._session_db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
