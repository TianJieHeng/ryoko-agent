"""Real owner/run/profile grants. All credentials and packets are synthetic."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import uuid

import httpx
import openai
import pytest

from agent.agent_identity import resolve_agent_context
from agent.decisions.contracts import DecisionError, DecisionRequest, ModelBundle, StatePacket, digest
from agent.decisions.laya_prompts import render_dp16_state
from agent.decisions.laya_transport import LAYA_ENDPOINT, LAYA_HEALTH_ENDPOINT, LayaDestinationManifest, LayaHttpsTransport
from agent.decisions.planner_context import build_planner_context
from agent.decisions.receipts import scope_digest
from agent.decisions.registry import contract_for
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from agent.secret_scope import reset_multiplex_context, set_multiplex_context
from hermes_state import SessionDB

BUNDLE = ModelBundle("1" * 64, "2" * 64, "3" * 64)


def manifest(**changes):
    fields = dict(schema_version=1, endpoint=LAYA_ENDPOINT, recipient_id="laya-fixture",
        secret_ref="LAYA_API_KEY", expected_model_digest=BUNDLE.model_digest,
        expected_calibration_digest=BUNDLE.calibration_digest, expected_service_digest=BUNDLE.service_digest)
    return LayaDestinationManifest(**(fields | changes))


def requests_for(context, *, classification="synthetic"):
    import time
    scope = scope_digest(context)
    planner = build_planner_context("Synthetic classification fixture", scope_digest=scope, classification=classification)
    catalog = {"version": "4" * 64, "scope_digest": scope, "policy_digest": context.policy.digest,
               "tool_view_revision": "5" * 64, "families": [], "tools": [], "bridges": []}
    state = StatePacket(render_dp16_state(planner, catalog=catalog, stage=1), scope, classification)
    contract = contract_for("DP16", 2)
    deadline = time.time() + 10
    return tuple(DecisionRequest("DP16", 2, contract.contract_digest, name, state,
        contract.question(name).options, deadline, digest({"question": name, "nonce": uuid.uuid4().hex}))
        for name in ("need", "effort"))


@contextmanager
def runtime(home, *, secret="synthetic-laya-profile-a", secret_granted=True,
            recipients=True, purpose="decision_inference", health_granted=False):
    home.mkdir(parents=True, exist_ok=True)
    grants = [{"recipient_id": "laya-fixture", "purpose": purpose, "endpoint": LAYA_ENDPOINT,
               "transport": "httpx"}] if recipients else []
    if health_granted:
        grants.append({"recipient_id": "laya-fixture", "purpose": "decision_inference", "endpoint": LAYA_HEALTH_ENDPOINT,
                       "transport": "httpx"})
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": home.name,
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {"primary": {
            "policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
            "secret_refs": ["LAYA_API_KEY"] if secret_granted else [],
            "recipient_plan": {"schema_version": 1, "envelope": "declared", "grants": grants}}}}}
    (home / "config.yaml").write_text(json.dumps(raw))
    (home / ".env").write_text("" if secret is None else "LAYA_API_KEY=" + secret + "\n")
    session = "session-" + uuid.uuid4().hex
    context = resolve_agent_context(raw, session_id=session, profile_home=home)
    db = SessionDB(home / "state.db")
    db.create_session(session, source="cli")
    db.claim_session_agent_identity(session, context.identity.to_record())
    actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command", "expected_revision": None,
               "operation": "submit", "payload": {"text": "Synthetic fixture"}, "identity_binding": actor}
    receipt = db.submit_runtime_command(session, actor=actor, command=command)
    assert db.acquire_session_turn_lease(session, "fixture-owner", wait_seconds=0)
    generation = db.get_session_turn_lease(session)["generation"]
    assert db.claim_runtime_command(session, "command", holder="fixture-owner", generation=generation)
    placeholder = openai.OpenAI(api_key="synthetic-placeholder", base_url="https://unused.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(platform="cli", api_mode="chat_completions", provider="openai", client=placeholder,
                            runtime_context=context)
    run = RuntimeRun(agent, db, session, "command", receipt["run_id"], "fixture-owner", generation, context)
    multiplex = set_multiplex_context(True)
    try:
        with agent_runtime_scope(context):
            token = bind_runtime_run(run)
            try:
                yield SimpleNamespace(context=context, run=run, db=db, raw=raw, home=home, session=session)
            finally:
                reset_runtime_run(token, run)
    finally:
        reset_multiplex_context(multiplex)
        placeholder.close()
        db.close()


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"endpoint": "http://laya.ryoko.okinawa/v1/systemone"},
    {"endpoint": LAYA_ENDPOINT + "/"}, {"endpoint": LAYA_ENDPOINT + "?key=x"},
    {"endpoint": "https://evil.invalid/v1/systemone"}, {"endpoint": LAYA_HEALTH_ENDPOINT},
    {"secret_ref": "OPENAI_API_KEY"}, {"allowed_classifications": ("private",)},
    {"allowed_classifications": ("synthetic", "synthetic")}, {"max_response_bytes": 32769},
    {"max_request_bytes": True}, {"expected_service_digest": "arbitrary"},
])
def test_manifest_rejects_unqualified_destinations(changes):
    with pytest.raises(DecisionError):
        manifest(**changes)


def test_closed_manifest_and_configured_release_bindings():
    record = asdict(manifest())
    assert LayaDestinationManifest.from_record(record) == manifest()
    for field in ("synthetic_loopback", "ssl_verify", "api_key", "privacy_qualified", "test_fixture"):
        with pytest.raises(DecisionError, match="invalid_laya_manifest"):
            LayaDestinationManifest.from_record(record | {field: True})
    with pytest.raises(DecisionError, match="bundle_mismatch"):
        LayaHttpsTransport(manifest(expected_service_digest="9" * 64), bundle=BUNDLE)


def test_purpose_grant_never_authorizes_child_path_or_other_method(tmp_path):
    from tools.egress_policy import EgressDenied, prepare_recipient, wrap_httpx_transport
    seen = []
    with runtime(tmp_path / "profile-a"):
        auth = prepare_recipient("decision_inference", LAYA_ENDPOINT, recipient_id="laya-fixture")
        inner = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))
        with httpx.Client(transport=wrap_httpx_transport(inner, auth), trust_env=False) as client:
            with pytest.raises(EgressDenied, match="decision_operation_unsupported"):
                client.post(LAYA_ENDPOINT + "/other", content=b"synthetic")
            with pytest.raises(EgressDenied, match="decision_operation_unsupported"):
                client.get(LAYA_ENDPOINT)
            with pytest.raises(EgressDenied, match="host_header_changed"):
                client.post(LAYA_ENDPOINT, headers={"Host": "other.invalid"}, content=b"synthetic")
    assert seen == []


@pytest.mark.parametrize("kwargs", [
    {"secret": None}, {"secret_granted": False}, {"recipients": False}, {"purpose": "aux_model"},
    {"secret": "synthetic-laya-invalid header"},
])
def test_missing_authorization_precedes_serialization_and_socket(tmp_path, monkeypatch, kwargs):
    def forbidden(*args, **kw):
        pytest.fail("No serialization or socket is authorized")
    monkeypatch.setattr("agent.decisions.laya_codec.encode_laya_batch", forbidden)
    monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", forbidden)
    with runtime(tmp_path / "profile-a", **kwargs) as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        with pytest.raises(DecisionError):
            transport.decide_many(requests_for(rt.context), 1)


@pytest.mark.parametrize("revoke", ["policy", "secret", "scope", "lease"])
def test_revocation_precedes_encoding_and_socket(tmp_path, monkeypatch, revoke):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        requests = requests_for(rt.context)
        reset = None
        if revoke == "policy":
            (rt.home / "config.yaml").write_text("{}")
        elif revoke == "lease":
            rt.db.release_session_turn_lease(rt.session, "fixture-owner")
            assert rt.db.acquire_session_turn_lease(rt.session, "different-owner", wait_seconds=0)
        else:
            reset = set_secret_scope({} if revoke == "secret" else {"LAYA_API_KEY": "synthetic-laya-foreign"},
                                     profile_home=str(rt.home) if revoke == "secret" else str(tmp_path / "foreign"))
        monkeypatch.setattr("agent.decisions.laya_codec.encode_laya_batch", lambda *a, **k: pytest.fail("encoded after revoke"))
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: pytest.fail("socket after revoke"))
        try:
            with pytest.raises(DecisionError):
                transport.decide_many(requests, 1)
        finally:
            if reset is not None:
                reset_secret_scope(reset)


def test_private_body_never_serializes_even_with_valid_authorization(tmp_path, monkeypatch):
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        requests = requests_for(rt.context, classification="private")
        monkeypatch.setattr("agent.decisions.laya_codec.encode_laya_batch", lambda *a, **k: pytest.fail("private encoding"))
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: pytest.fail("private socket"))
        with pytest.raises(DecisionError, match="private_destination_unqualified"):
            transport.decide_many(requests, 1)


def test_unbound_and_foreign_request_owner_cannot_borrow_ambient_key(tmp_path, monkeypatch):
    from agent.runtime_context import bind_agent_context
    with bind_agent_context(None):
        unbound = LayaHttpsTransport(manifest(), bundle=BUNDLE)
    monkeypatch.setenv("LAYA_API_KEY", "synthetic-laya-ambient-forbidden")
    with runtime(tmp_path / "profile-a") as rt:
        requests = requests_for(rt.context)
        with pytest.raises(DecisionError, match="decision_owner_required"):
            unbound.decide_many(requests, 1)
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        packet = replace(requests[0].state_packet, scope_digest="0" * 64)
        with pytest.raises(DecisionError, match="decision_scope_mismatch"):
            transport.decide_many((replace(requests[0], state_packet=packet),), 1)


def test_profile_a_b_a_isolation_through_real_tls(tmp_path):
    from tests.agent.test_laya_transport import tls_server
    with tls_server(tmp_path) as server:
        with runtime(tmp_path / "profile-a") as a:
            transport_a = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
            transport_a.decide_many(requests_for(a.context), 1)
            with runtime(tmp_path / "profile-b", secret="synthetic-laya-profile-b") as b:
                transport_b = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
                with pytest.raises(DecisionError, match="decision_owner_required"):
                    transport_a.decide_many(requests_for(b.context), 1)
                transport_b.decide_many(requests_for(b.context), 1)
                assert transport_a.admission_key == transport_b.admission_key
            transport_a.decide_many(requests_for(a.context), 1)
        assert [item[1] for item in server.received] == [
            "Bearer synthetic-laya-profile-a", "Bearer synthetic-laya-profile-b", "Bearer synthetic-laya-profile-a"]
