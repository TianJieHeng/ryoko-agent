"""Actual RPC/broker/journal/pinned wire boundary, with a local durable native peer.

This fixture owns a separate SQLite page/execution store. It is not a live Dots
supervisor qualification; the actual BFF pair has its own integration gate.
"""
import copy
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


class NativePeer:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS pages(page TEXT PRIMARY KEY,version INT,body TEXT);"
                "CREATE TABLE IF NOT EXISTS receipts(effect TEXT PRIMARY KEY,identity TEXT,receipt TEXT);"
                "CREATE TABLE IF NOT EXISTS calls(effect TEXT PRIMARY KEY);")
        self.frames = []
        self.changed = threading.Condition()
        self.transport = None
        self.reject = None
        self.after_commit = None
        self.forge = None
        self.dispatch_count = 0

    def write(self, text):
        from tui_gateway import server
        frame = json.loads(text)
        self.frames.append(frame)
        if frame.get("method") in {"dots.effect.dispatch", "dots.effect.inspect"}:
            params = frame["params"]
            identity = params["identity"]
            with sqlite3.connect(self.path) as db:
                old = db.execute("SELECT receipt FROM receipts WHERE effect=?", (identity["effect_id"],)).fetchone()
                if frame["method"] == "dots.effect.inspect":
                    receipt = json.loads(old[0]) if old else {"identity": identity, "state": "outcome_unknown", "reason": "unknown"}
                elif old:
                    receipt = json.loads(old[0])
                else:
                    self.dispatch_count += 1
                    proposal = params["proposal"]
                    assert json.loads(params["content_json"]) == proposal.get("document", proposal.get("input"))
                    assert hashlib.sha256(params["content_json"].encode()).hexdigest() == identity["content_sha256"]
                    rejection = self.reject
                    if proposal["kind"] == "page":
                        head = db.execute("SELECT version FROM pages WHERE page=?", (proposal["page_id"],)).fetchone()
                        if (head[0] if head else 0) != proposal["expected_head_version"]:
                            rejection = "conflict"
                    receipt = {"identity": identity, "state": "not_applied" if rejection else "committed",
                        "receipt_id": "receipt-" + identity["effect_id"], "reason": rejection or "committed",
                        "content_sha256": identity["content_sha256"], "result_sha256": getattr(self, "result_sha256", "a" * 64),
                        "version": proposal.get("expected_head_version", 0) + 1 if proposal["kind"] == "page" else None}
                    if not rejection:
                        if proposal["kind"] == "page":
                            db.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?)", (proposal["page_id"], receipt["version"], params["content_json"]))
                        else:
                            db.execute("INSERT INTO calls VALUES(?)", (identity["effect_id"],))
                    db.execute("INSERT INTO receipts VALUES(?,?,?)", (identity["effect_id"], json.dumps(identity), json.dumps(receipt)))
            if self.after_commit is not None and frame["method"] == "dots.effect.dispatch":
                self.after_commit(frame)
                return len(text)
            if self.forge:
                receipt = self.forge(copy.deepcopy(receipt))
            server.dispatch({"jsonrpc": "2.0", "id": frame["id"], "result": receipt}, transport=self.transport)
        with self.changed:
            self.changed.notify_all()
        return len(text)

    def flush(self):
        pass


@pytest.fixture
def native(tmp_path, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from agent import dots_adapter
    from hermes_state import SessionDB
    from tui_gateway import server, server_requests
    from tui_gateway.transport import StdioTransport
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(dots_adapter, "_REGISTRATIONS", {})
    monkeypatch.setattr(server_requests, "_open", {})
    homes, agents, peers, sessions, configs = {}, {}, {}, {}, {}
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        config = {"agent_identity": {"schema_version": 1, "principal_id": "owner",
            "profile_id": "profile-" + label, "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
            "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                "allowed_tools": ["dots_computer_click", "dots_computer_exec", "dots_computer_files_write"], "project_grants": []}}}}
        (home / "config.yaml").write_text(json.dumps(config))
        context = resolve_agent_context(config, session_id="stored-session", profile_home=home)
        db = SessionDB(home / "state.db")
        db.create_session("stored-session", source="tui")
        db.claim_session_agent_identity("stored-session", context.identity.to_record())
        agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id="stored-session")
        peer = NativePeer(home / "native.db")
        peer.transport = StdioTransport(lambda p=peer: p, threading.RLock())
        sessions["live-" + label] = {"agent": agent, "profile_home": str(home), "transport": peer.transport,
            "session_key": agent.session_id, "history": [], "history_lock": threading.RLock()}
        homes[label], agents[label], peers[label], configs[label] = home, agent, peer, config
    monkeypatch.setenv("HERMES_HOME", str(homes["a"]))
    monkeypatch.setattr(server, "_sessions", sessions)
    seq = 0

    def call(method, label="a", via=None, **params):
        nonlocal seq
        seq += 1
        rid = "dots-test-" + str(seq)
        response = server.dispatch({"jsonrpc": "2.0", "id": rid, "method": method,
            "params": {"session_id": "live-" + label, "schema_version": 1, **params}},
            transport=via or peers[label].transport)
        if response is not None:
            return response
        peer = peers[label]
        with peer.changed:
            assert peer.changed.wait_for(lambda: any(f.get("id") == rid for f in peer.frames), timeout=15), peer.frames
        return next(f for f in peer.frames if f.get("id") == rid)

    def page(label="a"):
        record = ok(call("runtime.project.create", label, name="Research",
            grants=[{"principal_id": "owner", "agent_id": "ryoko", "permissions": ["read", "write", "share"]}]))["project"]
        config = configs[label]
        config["agent_identity"]["agents"]["ryoko"]["project_grants"].append(record["id"])
        (homes[label] / "config.yaml").write_text(json.dumps(config))
        agent = agents[label]
        agent.session_id = "configured-session"
        agent.runtime_context = resolve_agent_context(config, session_id=agent.session_id, profile_home=homes[label])
        agent._session_db.create_session(agent.session_id, source="tui")
        agent._session_db.claim_session_agent_identity(agent.session_id, agent.runtime_context.identity.to_record())
        sessions["live-" + label]["session_key"] = agent.session_id
        ok(call("runtime.dots.register", label, adapter_id="native-pages", kind="page", revision=1,
            enabled=True, project_ids=[record["id"]], space_ids=["space"]))
        return {"kind": "page", "store_id": "native-pages", "project_id": record["id"], "space_id": "space",
            "page_id": "page", "expected_head_version": 0, "expected_grant_revision": 1,
            "document": {"title": "Native page", "content": "# Exact café 🦊", "parent_id": None, "archived": False}}

    def computer(label="a"):
        ok(call("runtime.dots.register", label, adapter_id="native-computer", kind="computer", revision=1,
            enabled=True, actions=["click", "exec", "files_write"]))
        return {"kind": "computer", "executor_id": "native-computer", "expected_grant_revision": 1,
            "expected_control_revision": 3, "snapshot_id": 7, "snapshot_sha256": "d" * 64,
            "action": "click", "input": {"ref": "e1", "snapshotId": 7}}

    yield SimpleNamespace(call=call, page=page, computer=computer, peers=peers, agents=agents,
        homes=homes, sessions=sessions, configs=configs)
    for agent in agents.values():
        agent._session_db.close()


def ok(response):
    assert response and "error" not in response, response
    return response["result"]


def denied(response, code=None):
    assert response and "error" in response, response
    if code:
        assert response["error"]["data"]["code"] == code, response


def prepare(native, proposal, command="operation", label="a"):
    return ok(native.call("runtime.dots." + proposal["kind"] + ".prepare", label, command_id=command, proposal=proposal))


def publish(native, proposal, preview, command="operation", label="a"):
    verb = "publish" if proposal["kind"] == "page" else "execute"
    return native.call("runtime.dots." + proposal["kind"] + "." + verb, label, command_id=command, proposal=proposal,
        approval_id=preview["approval_id"], approval_digest=preview["approval_digest"])


def test_native_page_is_only_byte_authority_and_conflicting_retry_never_writes(native):
    proposal = native.page()
    preview = prepare(native, proposal)
    assert prepare(native, proposal) == preview
    altered = copy.deepcopy(proposal)
    altered["document"]["content"] = "unapproved"
    denied(publish(native, altered, preview), "idempotency_conflict")
    done = ok(publish(native, proposal, preview))
    assert done["state"] == "confirmed" and done["receipt"]["version"] == 1
    assert ok(publish(native, proposal, preview)) == done
    assert native.peers["a"].dispatch_count == 1
    with sqlite3.connect(native.peers["a"].path) as db:
        body = json.loads(db.execute("SELECT body FROM pages").fetchone()[0])
    assert body == proposal["document"]
    assert not list(native.homes["a"].glob("artifacts/**/*"))
    stale = prepare(native, proposal, "stale")
    conflict = ok(publish(native, proposal, stale, "stale"))
    assert conflict["state"] == "failed" and conflict["receipt"]["reason"] == "conflict"


def test_lost_native_commit_recovers_original_receipt_without_replay(native):
    from tui_gateway import server_requests
    proposal = native.page()
    preview = prepare(native, proposal)
    native.peers["a"].after_commit = lambda frame: server_requests.cancel(frame["params"]["session_id"], "disconnected")
    unknown = ok(publish(native, proposal, preview))
    assert unknown["state"] == "outcome_unknown"
    assert ok(publish(native, proposal, preview))["state"] == "outcome_unknown"
    native.peers["a"].after_commit = None
    recovered = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert recovered["state"] == "confirmed" and recovered["receipt"]["version"] == 1
    assert native.peers["a"].dispatch_count == 1
    assert ok(publish(native, proposal, preview))["state"] == "confirmed"


@pytest.mark.parametrize("reason", ["takeover", "stale_snapshot", "grant_revoked"])
def test_computer_exact_boundary_refusal_is_terminal_no_replay(native, reason):
    proposal = native.computer()
    preview = prepare(native, proposal)
    native.peers["a"].reject = reason
    result = ok(publish(native, proposal, preview))
    assert result["state"] == "failed" and result["receipt"]["reason"] == reason
    native.peers["a"].reject = None
    assert ok(publish(native, proposal, preview))["state"] == "failed"
    assert native.peers["a"].dispatch_count == 1
    with sqlite3.connect(native.peers["a"].path) as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_foreign_transport_profile_and_changed_registration_are_denied(native):
    proposal = native.computer()
    preview = prepare(native, proposal)
    denied(native.call("runtime.dots.register", adapter_id="native-computer", kind="computer", revision=2,
        expected_revision=1, enabled=False, via=native.peers["b"].transport))
    ok(native.call("runtime.dots.register", adapter_id="native-computer", kind="computer", revision=2,
        expected_revision=1, enabled=False))
    denied(publish(native, proposal, preview), "dots_adapter_unavailable")
    assert native.peers["a"].dispatch_count == 0
    denied(native.call("runtime.dots.computer.prepare", "b", command_id="foreign", proposal=proposal), "dots_adapter_unavailable")


def test_computer_wrong_snapshot_path_and_action_are_rejected_before_dispatch(native):
    proposal = native.computer()
    bad = copy.deepcopy(proposal)
    bad["input"]["snapshotId"] = 8
    denied(native.call("runtime.dots.computer.prepare", command_id="bad", proposal=bad), "dots_snapshot_mismatch")
    bad.update(action="files_write", input={"path": "../escape", "contents": "bad", "append": False})
    denied(native.call("runtime.dots.computer.prepare", command_id="bad-path", proposal=bad), "dots_path_denied")
    bad.update(action="human_click", input={"x": 1, "y": 1})
    denied(native.call("runtime.dots.computer.prepare", command_id="bad-action", proposal=bad))
    assert native.peers["a"].dispatch_count == 0


def test_forged_matching_state_without_exact_identity_stays_unknown(native):
    proposal = native.computer()
    preview = prepare(native, proposal)
    def forge(receipt):
        receipt["identity"]["agent_id"] = "different-agent"
        return receipt
    native.peers["a"].forge = forge
    unknown = ok(publish(native, proposal, preview))
    assert unknown["state"] == "outcome_unknown"
    pending = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert pending["state"] == "reconciliation_required"
    native.peers["a"].forge = None
    recovered = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert recovered["state"] == "confirmed" and native.peers["a"].dispatch_count == 1


def test_pinned_callback_foreign_answers_and_reconnect_replay_cannot_authorize(native):
    from tui_gateway import server_requests
    proposal = native.computer()
    preview = prepare(native, proposal)
    seen = []
    def examine(frame):
        assert server_requests.open_requests(frame["params"]["session_id"]) == []
        with sqlite3.connect(native.peers["a"].path) as db:
            receipt = json.loads(db.execute("SELECT receipt FROM receipts").fetchone()[0])
        reply = {"jsonrpc": "2.0", "id": frame["id"], "result": receipt}
        assert not server_requests.resolve_response(reply, native.peers["b"].transport)
        assert not server_requests.resolve_response(reply)
        assert server_requests.resolve_response(reply, native.peers["a"].transport)
        seen.append(True)
    native.peers["a"].after_commit = examine
    assert ok(publish(native, proposal, preview))["state"] == "confirmed"
    assert seen == [True]


def test_producer_restart_and_reregistration_inspects_without_redispatch(native, monkeypatch):
    from agent import dots_adapter
    from hermes_state import SessionDB
    from tui_gateway import server_requests
    proposal = native.computer()
    preview = prepare(native, proposal)
    native.peers["a"].after_commit = lambda frame: server_requests.cancel(frame["params"]["session_id"], "disconnected")
    unknown = ok(publish(native, proposal, preview))
    assert unknown["state"] == "outcome_unknown"
    agent = native.agents["a"]
    agent._session_db.close()
    agent._session_db = SessionDB(native.homes["a"] / "state.db")
    monkeypatch.setattr(dots_adapter, "_REGISTRATIONS", {})
    denied(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]), "dots_adapter_unavailable")
    native.computer()
    native.peers["a"].after_commit = None
    recovered = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert recovered["state"] == "confirmed" and native.peers["a"].dispatch_count == 1
    assert recovered["receipt"]["identity"]["generation"] == 1


def test_unknown_computer_without_native_proof_cannot_be_replayed(native):
    from tui_gateway import server_requests
    proposal = native.computer()
    preview = prepare(native, proposal)
    def lose_receipt(frame):
        with sqlite3.connect(native.peers["a"].path) as db:
            db.execute("DELETE FROM receipts")
        server_requests.cancel(frame["params"]["session_id"], "disconnected")
    native.peers["a"].after_commit = lose_receipt
    unknown = ok(publish(native, proposal, preview))
    native.peers["a"].after_commit = None
    pending = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert pending["state"] == "reconciliation_required"
    assert ok(publish(native, proposal, preview))["state"] == "reconciliation_required"
    assert native.peers["a"].dispatch_count == 1


def test_registration_revoked_while_native_action_returns_stays_unknown(native):
    from tui_gateway import server_requests
    proposal = native.computer()
    preview = prepare(native, proposal)
    def revoke(frame):
        ok(native.call("runtime.dots.register", adapter_id="native-computer", kind="computer", revision=2,
            expected_revision=1, enabled=True, actions=["click"]))
        with sqlite3.connect(native.peers["a"].path) as db:
            receipt = json.loads(db.execute("SELECT receipt FROM receipts").fetchone()[0])
        assert server_requests.resolve_response({"id": frame["id"], "result": receipt}, native.peers["a"].transport)
    native.peers["a"].after_commit = revoke
    unknown = ok(publish(native, proposal, preview))
    assert unknown["state"] == "outcome_unknown"
    native.peers["a"].after_commit = None
    recovered = ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))
    assert recovered["state"] == "confirmed" and native.peers["a"].dispatch_count == 1


def test_nonstdio_registration_and_grant_broadening_are_not_capabilities(native):
    peer = SimpleNamespace(write=lambda _frame: True)
    native.sessions["live-a"]["transport"] = peer
    denied(native.call("runtime.dots.register", adapter_id="remote", kind="computer", revision=1,
        enabled=True, actions=["click"], via=peer), "dots_transport_required")
    native.sessions["live-a"]["transport"] = native.peers["a"].transport
    denied(native.call("runtime.dots.register", adapter_id="ungranted", kind="computer", revision=1,
        enabled=True, actions=["navigate"]), "dots_grant_denied")
    denied(native.call("runtime.dots.register", adapter_id="ungranted", kind="page", revision=1,
        enabled=True, project_ids=["foreign-project"], space_ids=["space"]), "dots_grant_denied")


def test_native_scope_isolation_a_b_a_and_page_grant_revocation(native):
    first, second = native.page("a"), native.page("b")
    for label, proposal, command in (("a", first, "first"), ("b", second, "second"), ("a", first, "third")):
        proposal = copy.deepcopy(proposal)
        if command == "third":
            proposal["expected_head_version"] = 1
        preview = prepare(native, proposal, command, label)
        assert ok(publish(native, proposal, preview, command, label))["state"] == "confirmed"
    first["expected_head_version"] = 2
    preview = prepare(native, first, "revoked")
    project = ok(native.call("runtime.project.get", project_id=first["project_id"]))["project"]
    ok(native.call("runtime.project.grants.set", project_id=first["project_id"],
        expected_revision=project["revision"], grants=[]))
    denied(publish(native, first, preview, "revoked"), "project_grant_revoked")
    assert native.peers["a"].dispatch_count == 2 and native.peers["b"].dispatch_count == 1


def test_real_producer_process_death_after_native_commit_never_replays(native, tmp_path):
    import os
    import subprocess
    import sys
    from agent.result_artifacts import artifact_actor
    proposal = native.page()
    script = r'''
import importlib.util,json,os,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
from agent.agent_identity import resolve_agent_context
from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.transport import StdioTransport
home=Path(sys.argv[1]); proposal=json.loads(sys.argv[2])
spec=importlib.util.spec_from_file_location("dots_test_peer",sys.argv[3])
test=importlib.util.module_from_spec(spec);spec.loader.exec_module(test)
context=resolve_agent_context(json.loads((home/"config.yaml").read_text()),session_id="configured-session",profile_home=home)
db=SessionDB(home/"state.db")
agent=SimpleNamespace(runtime_context=context,_session_db=db,session_id="configured-session")
peer=test.NativePeer(home/"native.db");peer.transport=StdioTransport(lambda:peer,threading.RLock())
peer.after_commit=lambda frame:os._exit(77)
server._sessions={"live-a":{"agent":agent,"profile_home":str(home),"transport":peer.transport,"session_key":agent.session_id,"history":[],"history_lock":threading.RLock()}}
def call(method,**params):
 return server.dispatch({"jsonrpc":"2.0","id":"child","method":method,"params":{"session_id":"live-a","schema_version":1,**params}},transport=peer.transport)
registered=call("runtime.dots.register",adapter_id="native-pages",kind="page",revision=1,enabled=True,project_ids=[proposal["project_id"]],space_ids=["space"])
assert "result" in registered,registered
preview=call("runtime.dots.page.prepare",command_id="crash",proposal=proposal)
assert "result" in preview,preview
preview=preview["result"]
call("runtime.dots.page.publish",command_id="crash",proposal=proposal,approval_id=preview["approval_id"],approval_digest=preview["approval_digest"])
time.sleep(20)
os._exit(78)
'''
    completed = subprocess.run([sys.executable, "-c", script, str(native.homes["a"]), json.dumps(proposal),
        str(Path(__file__).resolve())], cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "HERMES_HOME": str(native.homes["a"])}, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 77, (completed.stdout, completed.stderr)
    agent = native.agents["a"]
    db, actor = agent._session_db, artifact_actor(agent.runtime_context)
    effects = db.list_effects(agent.session_id, actor)
    assert len(effects) == 1 and effects[0]["state"] == "dispatched"
    lease = db.get_session_turn_lease(agent.session_id)
    # The process is known dead; release its fixture-owned lease to model the
    # completed lease-expiry fence without sleeping through the production TTL.
    db.release_session_turn_lease(agent.session_id, lease["holder"], generation=lease["generation"])
    done = ok(native.call("runtime.dots.effect.reconcile", effect_id=effects[0]["effect_id"]))
    assert done["state"] == "confirmed"
    with sqlite3.connect(native.peers["a"].path) as store:
        assert store.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
        assert store.execute("SELECT version FROM pages").fetchone()[0] == 1
    assert native.peers["a"].dispatch_count == 0


def test_transport_failure_after_native_commit_has_no_dangling_request(native):
    from tui_gateway import server_requests
    proposal = native.computer()
    preview = prepare(native, proposal)
    def fail_after_commit(frame):
        raise OSError("native response disappeared")
    native.peers["a"].after_commit = fail_after_commit
    unknown = ok(publish(native, proposal, preview))
    assert unknown["state"] == "outcome_unknown"
    assert server_requests.open_request_count() == 0
    native.peers["a"].after_commit = None
    assert ok(native.call("runtime.dots.effect.reconcile", effect_id=unknown["effect_id"]))["state"] == "confirmed"
    assert native.peers["a"].dispatch_count == 1
