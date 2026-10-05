"""Locally generated TLS, exact host verification, closed errors and no live calls."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
import threading
import time
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from agent.decisions.contracts import DecisionError
from agent.decisions.laya_transport import LayaHttpsTransport, SyntheticTlsFixture
from tests.agent.test_laya_destination_authorization import BUNDLE, manifest, requests_for, runtime


def certificate(home, name, *, issuer=None, hostname="laya.ryoko.okinawa"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    ca = issuer is None
    certificate = (x509.CertificateBuilder().subject_name(subject)
        .issuer_name(issuer[0].subject if issuer else subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer[1] if issuer else key).public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(issuer[1] if issuer else key, hashes.SHA256()))
    cert_path, key_path = home / (name + ".pem"), home / (name + ".key")
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return certificate, key, cert_path, key_path


@contextmanager
def tls_server(home, *, hostname="laya.ryoko.okinawa", status=200, mode="valid", before_reply=None, choices=None):
    ca = certificate(home, "synthetic-ca")
    cert = certificate(home, "synthetic-server", issuer=ca, hostname=hostname)
    received, got_body = [], threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.path, self.headers.get("Authorization"), body, self.headers.get("Host")))
            got_body.set()
            if before_reply:
                before_reply()
            if mode == "stall":
                time.sleep(.4)
            raw = b'{"status":"synthetic"}'
            if body:
                request = json.loads(body)
                answers = {}
                for qid, question in request["questions"].items():
                    choice = (choices or {}).get(qid.split("_")[1], next(iter(question["criteria"])))
                    answers[qid] = {"type": "choice", "choice": choice, "confidence": 1.,
                        "probabilities": {key: float(key == choice) for key in question["criteria"]}}
                raw = json.dumps({"model": "synthetic-fixture", "answers": answers,
                    "usage": {"input_tokens": 10, "output_tokens": 2}}).encode()
            if mode == "oversize":
                raw = b"x" * 32769
            if status != 200:
                raw = b"REMOTE-ERROR-DO-NOT-LEAK synthetic-laya-profile-a"
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw) + (10 if mode == "incomplete" else 0)))
                if 300 <= status < 400:
                    self.send_header("Location", "http://127.0.0.1:9/steal")
                self.end_headers()
                self.wfile.write(raw)
            except OSError:
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert[2]), str(cert[3]))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(fixture=SyntheticTlsFixture(server.server_port, str(ca[2])),
                              received=received, got_body=got_body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_authenticated_batch_and_separate_no_auth_health(tmp_path, monkeypatch):
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    with tls_server(tmp_path) as server, runtime(tmp_path / "profile-a", health_granted=True) as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
        result = transport.decide_many(requests_for(rt.context), 1)
        assert len(result.records) == 2 and result.usage.total_tokens == 12
        assert transport.health() == {"reachable": True, "authenticated": False, "loaded_identity_verified": False}
        assert [item[0] for item in server.received] == ["/v1/systemone", "/health"]
        assert server.received[0][1] == "Bearer synthetic-laya-profile-a" and server.received[1][1] is None
        assert all(item[3] == "laya.ryoko.okinawa" for item in server.received)
        assert b"synthetic-laya-profile-a" not in server.received[0][2]
        assert "synthetic-laya-profile-a" not in repr(transport)
        assert not transport.remote_completion_unknown


@pytest.mark.parametrize("wrong", ["hostname", "ca"])
def test_wrong_tls_identity_gets_no_request_bytes(tmp_path, wrong):
    with tls_server(tmp_path, hostname="wrong.invalid" if wrong == "hostname" else "laya.ryoko.okinawa") as server:
        fixture = server.fixture
        if wrong == "ca":
            fixture = replace(fixture, ca_file=str(certificate(tmp_path, "other-ca")[2]))
        with runtime(tmp_path / "profile-a") as rt:
            transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=fixture)
            with pytest.raises(DecisionError, match="node_unavailable"):
                transport.decide_many(requests_for(rt.context), 1)
            assert not transport.remote_completion_unknown
        assert server.received == []


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 413, 429, 500])
def test_status_errors_closed_and_redirects_never_followed(tmp_path, status, caplog):
    with tls_server(tmp_path, status=status) as server, runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
        with pytest.raises(DecisionError) as caught:
            transport.decide_many(requests_for(rt.context), 1)
        assert str(caught.value) == "node_http_error" and len(server.received) == 1
        assert "REMOTE-ERROR-DO-NOT-LEAK" not in caplog.text
        assert "synthetic-laya-profile-a" not in caplog.text
        assert not transport.remote_completion_unknown


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "100.64.0.1",
                                    "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:8.8.8.8"])
def test_dns_nonpublic_denied_before_socket(tmp_path, monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr("agent.decisions.laya_transport.socket.getaddrinfo",
        lambda *a, **k: [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))])
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: pytest.fail("nonpublic socket"))
        with pytest.raises(DecisionError, match="laya_address_denied"):
            transport.decide_many(requests_for(rt.context), 1)


def test_connected_peer_checked_before_tls_and_no_second_dns_lookup(tmp_path, monkeypatch):
    calls = []
    def resolve(*args, **kw):
        calls.append("dns")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]
    class ForgedPeer:
        def settimeout(self, value):
            pass
        def connect(self, address):
            calls.append(address)
        def getpeername(self):
            return ("127.0.0.1", 443)
        def close(self):
            pass
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.getaddrinfo", resolve)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: ForgedPeer())
        with pytest.raises(DecisionError, match="laya_peer_mismatch"):
            transport.decide_many(requests_for(rt.context), 1)
    assert calls == ["dns", ("8.8.8.8", 443)]


@pytest.mark.parametrize("mode", ["oversize", "incomplete", "stall"])
def test_incomplete_or_timed_out_body_quarantines_transport(tmp_path, mode):
    with tls_server(tmp_path, mode=mode) as server, runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
        with pytest.raises(DecisionError, match="remote_completion_unknown"):
            transport.decide_many(requests_for(rt.context), .15 if mode == "stall" else 1)
        assert transport.remote_completion_unknown and len(server.received) == 1
        with pytest.raises(DecisionError, match="remote_completion_unknown"):
            transport.decide_many(requests_for(rt.context), 1)
        assert len(server.received) == 1


def test_request_cap_and_synthetic_injection_classification_gate(tmp_path, monkeypatch):
    with tls_server(tmp_path) as server, runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(max_request_bytes=16), bundle=BUNDLE, synthetic_fixture=server.fixture)
        with pytest.raises(DecisionError, match="request_too_large"):
            transport.decide_many(requests_for(rt.context), 1)
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
        with pytest.raises(DecisionError, match="loopback_synthetic_only"):
            transport.decide_many(requests_for(rt.context, classification="public"), 1)
        assert server.received == []


def test_revocation_during_connect_rechecked_before_headers_or_body(tmp_path, monkeypatch):
    with tls_server(tmp_path) as server, runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE, synthetic_fixture=server.fixture)
        connect = transport._connect
        def revoke_after_connect(end, authorize):
            connected = connect(end, authorize)
            (rt.home / "config.yaml").write_text("{}")
            return connected
        monkeypatch.setattr(transport, "_connect", revoke_after_connect)
        with pytest.raises(DecisionError, match="laya_authorization_denied"):
            transport.decide_many(requests_for(rt.context), 1)
        assert server.received == []


def test_revocation_during_dns_prevents_even_opening_socket(tmp_path, monkeypatch):
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        def revoke_while_resolving(*args, **kwargs):
            (rt.home / "config.yaml").write_text("{}")
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]
        monkeypatch.setattr("agent.decisions.laya_transport.socket.getaddrinfo", revoke_while_resolving)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: pytest.fail("revoked socket"))
        with pytest.raises(DecisionError, match="laya_authorization_denied"):
            transport.decide_many(requests_for(rt.context), 1)


def test_dns_timeout_retains_one_bounded_resolver_without_socket(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def delayed_dns(*args, **kwargs):
        entered.set()
        release.wait(3)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]
    with runtime(tmp_path / "profile-a") as rt:
        transport = LayaHttpsTransport(manifest(), bundle=BUNDLE)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.getaddrinfo", delayed_dns)
        monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", lambda *a, **k: pytest.fail("expired DNS socket"))
        try:
            with pytest.raises(DecisionError, match="deadline_exceeded"):
                transport.decide_many(requests_for(rt.context), .1)
            assert entered.is_set()
            with pytest.raises(DecisionError, match="node_capacity"):
                transport.decide_many(requests_for(rt.context), 1)
            assert not transport.remote_completion_unknown
        finally:
            release.set()
            # Join the actual resolver, rather than assuming a scheduling delay.
            for thread in threading.enumerate():
                if thread.name == "laya-dns":
                    thread.join(3)


def test_construction_and_disabled_settings_have_zero_socket_or_secret_reads(tmp_path, monkeypatch):
    from agent.decisions.integration import parse_settings
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled construction performed I/O")
    monkeypatch.setattr("agent.decisions.laya_transport.socket.socket", forbidden)
    monkeypatch.setattr("agent.decisions.laya_transport.socket.getaddrinfo", forbidden)
    monkeypatch.setattr("agent.secret_scope.get_secret", forbidden)
    assert parse_settings({"schema_version": 1, "points": {"DP16": {"mode": "off"}}}) is None
    LayaHttpsTransport(manifest(), bundle=BUNDLE)
