"""BE05 recipient plans through real SQLite ownership and real loopback HTTP.

Loopback listeners only receive harmless seeded values. MockTransport tests below
cover failure shape; the network tests separately establish the actual boundary.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools.egress_policy import (EgressDenied, PURPOSES, RecipientGrant, RecipientPlan,
    assert_provider_client, build_model_client, intersect_recipient_plans, prepare_recipient,
    resolve_auxiliary_client, wrap_httpx_transport)


def plan(endpoint, *, envelope="declared", purposes=("main_model", "aux_model")):
    return {"schema_version": 1, "envelope": envelope, "grants": [
        {"recipient_id": "fixture", "purpose": p, "endpoint": endpoint, "transport": "httpx"}
        for p in purposes]}


def config(recipient_plan):
    policy = {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}
    if recipient_plan is not None:
        policy["recipient_plan"] = recipient_plan
    return {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {"primary": policy}}}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)

    @contextmanager
    def use(endpoint="https://fixture.invalid/v1", *, recipient_plan="default"):
        raw = config(plan(endpoint) if recipient_plan == "default" else recipient_plan)
        (tmp_path / "config.yaml").write_text(json.dumps(raw))
        context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
        db = SessionDB(tmp_path / "state.db")
        db.create_session("session", source="cli")
        db.claim_session_agent_identity("session", context.identity.to_record())
        identity = context.identity
        actor = {"principal_id": identity.principal_id, "profile_id": identity.profile_id, "agent_id": identity.agent_id}
        command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command", "expected_revision": None,
                   "operation": "submit", "payload": {"text": "harmless fixture"}, "identity_binding": actor}
        receipt = db.submit_runtime_command("session", actor=actor, command=command)
        assert db.acquire_session_turn_lease("session", "owner", wait_seconds=0)
        generation = db.get_session_turn_lease("session")["generation"]
        assert db.claim_runtime_command("session", "command", holder="owner", generation=generation)
        placeholder = openai.OpenAI(api_key="fixture", base_url=endpoint,
            http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
        agent = SimpleNamespace(platform="cli", api_mode="chat_completions", provider="openai", client=placeholder,
                                runtime_context=context)
        run = RuntimeRun(agent, db, "session", "command", receipt["run_id"], "owner", generation, context)
        with agent_runtime_scope(context):
            token = bind_runtime_run(run)
            try:
                yield SimpleNamespace(context=context, agent=agent, run=run, db=db, raw=raw, home=tmp_path)
            finally:
                reset_runtime_run(token, run)
        placeholder.close()
        db.close()
    return use


@contextmanager
def server(*, redirect=None):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.path, self.headers.get("Authorization"), body))
            if redirect:
                self.send_response(307)
                self.send_header("Location", redirect)
                self.end_headers()
                return
            response = {"id": "fixture", "object": "chat.completion", "created": 1, "model": "fixture",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "okay"}}]}
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.server_port}/v1", received
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("purpose", sorted(PURPOSES))
def test_all_outbound_purposes_require_exact_explicit_recipient(purpose, runtime):
    endpoint = "http://192.168.10.8:8000/v1"
    with runtime(endpoint, recipient_plan=plan(endpoint, purposes=(purpose,))):
        authorization = prepare_recipient(purpose, endpoint, recipient_id="fixture")
        assert authorization.grant.purpose == purpose
        with pytest.raises(EgressDenied, match="not_granted"):
            prepare_recipient(purpose, "http://192.168.10.9:8000/v1")
        with pytest.raises(EgressDenied, match="not_granted"):
            prepare_recipient(purpose, endpoint, recipient_id="invented")
        with pytest.raises(EgressDenied, match="unsupported"):
            prepare_recipient(purpose, endpoint, transport="subprocess")


@pytest.mark.parametrize("endpoint", ["http://localhost:8000/v1", "http://192.168.1.4:8000/v1", "https://example.test/v1"])
def test_lan_and_dns_are_not_local_only(endpoint):
    with pytest.raises(EgressDenied, match="literal_loopback"):
        RecipientPlan.from_record(plan(endpoint, envelope="local_only"))


def test_plan_immutable_exact_intersection_and_redacted_inspection():
    parent = RecipientPlan.from_record(plan("http://127.0.0.1:8000/v1", envelope="local_only"))
    child = RecipientPlan.from_record(plan("http://127.0.0.1:8000/v1", purposes=("main_model",)))
    with pytest.raises(FrozenInstanceError):
        parent.envelope = "declared"
    intersection = intersect_recipient_plans(parent, child)
    assert intersection.envelope == "local_only" and intersection.grants == child.grants
    assert intersect_recipient_plans(None, child).grants == ()
    redacted = json.dumps(parent.to_record(redacted=True))
    assert "127.0.0.1" not in redacted and "main_model" in redacted


@pytest.mark.parametrize("endpoint", ["https://user:secret@example.test/v1", "https://example.test/v1?secret=x",
    "https://example.test/v1#fragment", "https://example.test/v1/../other", "https://example.test/%2fsecret", "https://example.test/v1\\other"])
def test_ambiguous_or_credential_bearing_endpoint_rejected(endpoint):
    with pytest.raises(EgressDenied):
        RecipientGrant("fixture", "main_model", endpoint, "httpx")


@pytest.mark.parametrize("configured", [None, {"schema_version": 1, "envelope": "declared", "grants": []}])
def test_strict_missing_and_empty_plan_deny(runtime, configured):
    with runtime(recipient_plan=configured):
        with pytest.raises(EgressDenied):
            prepare_recipient("main_model", "https://fixture.invalid/v1")


def test_real_sdk_loopback_main_aux_and_per_hop_denial(runtime):
    with server() as (destination, delivered):
        with server(redirect=destination + "/chat/completions") as (origin, sent):
            with runtime(origin, recipient_plan=plan(origin, envelope="local_only")):
                authorization = prepare_recipient("main_model", origin)
                transport = wrap_httpx_transport(httpx.HTTPTransport(), authorization)
                with httpx.Client(transport=transport, follow_redirects=True, trust_env=False) as client:
                    with pytest.raises(EgressDenied, match="endpoint_changed"):
                        client.post(origin + "/chat/completions", headers={"Authorization": "Bearer harmless-fixture"},
                                    json={"messages": [{"content": "harmless-fixture"}]})
                assert len(sent) == 1 and delivered == []
                # Even a mutable endpoint on the SDK cannot escape the transport grant.
                with build_model_client({"api_key": "harmless-fixture", "base_url": origin}, purpose="main_model") as client:
                    client.base_url = destination
                    with pytest.raises(EgressDenied, match="endpoint_changed"):
                        assert_provider_client(client, purpose="main_model")
                assert delivered == []


def test_real_model_wire_and_unsupported_sdk_operation(runtime):
    with server() as (endpoint, received):
        with runtime(endpoint):
            for purpose in ("main_model", "aux_model"):
                with build_model_client({"api_key": "harmless-fixture", "base_url": endpoint}, purpose=purpose) as client:
                    assert_provider_client(client, purpose=purpose)
                    assert client.chat.completions.create(model="fixture", messages=[]).choices[0].message.content == "okay"
                    with pytest.raises(openai.APIConnectionError) as caught:
                        client.embeddings.create(model="fixture", input="must not transmit")
                    assert isinstance(caught.value.__cause__, EgressDenied)
            assert len(received) == 2
            assert all(item[1] == "Bearer harmless-fixture" for item in received)


def test_async_actual_transport_and_revoked_policy(runtime):
    with server() as (endpoint, received):
        with runtime(endpoint) as rt:
            async def perform():
                async with build_model_client({"api_key": "harmless-fixture", "base_url": endpoint},
                                              purpose="aux_model", async_mode=True) as client:
                    assert (await client.chat.completions.create(model="fixture", messages=[])).choices[0].message.content == "okay"
                    (rt.home / "config.yaml").write_text("{}")
                    with pytest.raises(openai.APIConnectionError):
                        await client.chat.completions.create(model="fixture", messages=[])
            asyncio.run(perform())
            assert len(received) == 1


def test_generation_policy_and_identity_rechecked_before_payload(runtime):
    with runtime() as rt:
        auth = prepare_recipient("main_model", "https://fixture.invalid/v1")
        seen = []
        transport = wrap_httpx_transport(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)), auth)
        with httpx.Client(transport=transport) as client:
            rt.db.release_session_turn_lease("session", "owner")
            assert rt.db.acquire_session_turn_lease("session", "new_owner", wait_seconds=0)
            with pytest.raises(InterruptedError):
                client.post("https://fixture.invalid/v1/chat/completions", content="private fixture")
        assert not seen


def test_proxy_opaque_clients_and_automatic_aux_discovery_denied(runtime, monkeypatch):
    with runtime():
        with openai.OpenAI(api_key="fixture") as unprotected:
            with pytest.raises(EgressDenied, match="unprotected"):
                assert_provider_client(unprotected, purpose="main_model")
        with pytest.raises(EgressDenied, match="route_required"):
            resolve_auxiliary_client(provider="auto", model="x", base_url=None, api_key=None,
                                     api_mode=None, main_runtime=None, async_mode=False)
        monkeypatch.setattr("agent.process_bootstrap._get_proxy_for_base_url", lambda _: "http://proxy.invalid")
        with pytest.raises(EgressDenied, match="proxy"):
            build_model_client({"api_key": "fixture", "base_url": "https://fixture.invalid/v1"}, purpose="main_model")


def test_forged_marker_custom_mount_and_host_header_do_not_authorize(runtime):
    with runtime():
        auth = prepare_recipient("main_model", "https://fixture.invalid/v1")
        raw = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)), trust_env=False)
        raw._hermes_recipient_authorization = auth
        with openai.OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", http_client=raw) as client:
            with pytest.raises(EgressDenied, match="unprotected"):
                assert_provider_client(client, purpose="main_model")
        sent = []
        wrapped = wrap_httpx_transport(httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)), auth)
        with httpx.Client(transport=wrapped, trust_env=False) as client:
            with pytest.raises(EgressDenied, match="host_header"):
                client.post("https://fixture.invalid/v1/chat/completions", headers={"Host": "other-recipient.invalid"})
        assert not sent


def test_plan_removal_cannot_resume_as_legacy_and_child_cannot_widen(runtime):
    with runtime() as rt:
        stored = rt.context.identity.to_record()
        changed = config(None)
        with pytest.raises(ValueError, match="stored identity"):
            resolve_agent_context(changed, session_id="session", profile_home=rt.home, stored_binding=stored)
        with pytest.raises(ValueError):
            resolve_agent_context({}, session_id="session", profile_home=rt.home, stored_binding=stored)


def test_opaque_aux_callback_rejected_before_budget_or_payload(runtime):
    from agent.auxiliary_client import _relay_sync_completion
    with runtime():
        called = []
        with pytest.raises(EgressDenied, match="unsupported"):
            _relay_sync_completion(object(), {}, create=lambda request: called.append(request))
        assert called == []


def test_guard_preserves_shared_pool_cancellation_owner(runtime):
    from agent.agent_runtime_helpers import _iter_httpx_pools_with_owner
    with runtime():
        options = {"api_key": "fixture", "base_url": "https://fixture.invalid/v1"}
        with build_model_client(options, purpose="main_model") as first:
            with build_model_client(options, purpose="main_model") as second:
                a = list(_iter_httpx_pools_with_owner(first._client))
                b = list(_iter_httpx_pools_with_owner(second._client))
                assert a and len(a) == len(b)
                assert all(pool_a is pool_b and owner_a is not None and owner_a != owner_b
                           for (pool_a, owner_a), (pool_b, owner_b) in zip(a, b))


def test_constructed_authorization_is_not_a_policy_grant(runtime):
    from tools.egress_policy import RecipientAuthorization
    with runtime() as rt:
        forged = RecipientAuthorization(rt.context,
            RecipientGrant("invented", "main_model", "https://other.invalid/v1", "httpx"), False)
        seen = []
        transport = wrap_httpx_transport(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)), forged)
        with httpx.Client(transport=transport) as client:
            with pytest.raises(EgressDenied, match="not_granted"):
                client.post("https://other.invalid/v1/chat/completions", content="private")
        assert not seen
