"""Real local mutual-TLS synthetic roundtrips, never a live decision node."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError, ModelBundle, digest
from agent.decisions.health import validate_health
from agent.decisions.policy import PointPolicy
from agent.decisions.registry import REGISTRY
from agent.decisions.service import TypedDecisionService
from agent.decisions.state import build_state
from agent.decisions.transport import LanTransport, NodeManifest


def certificate(tmp_path, name, *, issuer=None, ca=False):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer[0].subject if issuer else subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer[1] if issuer else key).public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False))
    cert = builder.sign(issuer[1] if issuer else key, hashes.SHA256())
    cert_path, key_path = tmp_path / (name + ".pem"), tmp_path / (name + ".key")
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return cert, key, cert_path, key_path


@pytest.fixture
def node(tmp_path):
    ca = certificate(tmp_path, "synthetic-ca", ca=True)
    server_cert = certificate(tmp_path, "synthetic-server", issuer=ca)
    client_cert = certificate(tmp_path, "synthetic-client", issuer=ca)
    model, calibration = tmp_path / "model.fixture", tmp_path / "calibration.fixture"
    model.write_bytes(b"synthetic-not-a-trained-checkpoint")
    calibration.write_bytes(b"synthetic-not-fitted")
    def predictor(request):
        return {"distribution": {key: float(key == "needs_tools") for key in request.live_options},
                "selected": "needs_tools", "unclear": False}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.process()
        def do_GET(self):
            self.process()
        def process(self):
            calls.append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(min(length, 65537))
            try:
                result = service.handle(self.path, body, peer_address=self.client_address[0],
                                        mutually_authenticated=bool(self.connection.getpeercert()))
                raw, status = json.dumps(result).encode(), 200
            except DecisionError as exc:
                raw, status = json.dumps({"error": exc.code}).encode(), 403
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    manifest = NodeManifest("127.0.0.1", server.server_port,
        server_cert[0].fingerprint(hashes.SHA256()).hex(), ("127.0.0.1",),
        hashlib.sha256(model.read_bytes()).hexdigest(), hashlib.sha256(calibration.read_bytes()).hexdigest(),
        "3" * 64, digest({key: value.contract_digest for key, value in REGISTRY.items()}), synthetic_loopback=True)
    service = TypedDecisionService(manifest, predictor, model_path=model, calibration_path=calibration,
        resource_snapshot=lambda: {"memory_used_bytes": 1024, "memory_limit_bytes": 8192})
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(server_cert[2]), str(server_cert[3]))
    context.load_verify_locations(str(ca[2]))
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    kwargs = {"ca_file": ca[2], "client_certificate": client_cert[2], "client_key": client_cert[3]}
    try:
        yield manifest, kwargs, service, calls, model
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_mutual_tls_pinned_roundtrip_health_and_redacted_receipt(node):
    manifest, kwargs, service, calls, _ = node
    transport = LanTransport(manifest, **kwargs)
    health = validate_health(transport.health(), manifest)
    assert health["model_digest"] == manifest.model_digest and not health["hardware_verified"]
    receipts = []
    bundle = ModelBundle(manifest.model_digest, manifest.calibration_digest, manifest.service_digest)
    client = DecisionClient(bundle=bundle, transport=transport, policies={"DP16": PointPolicy("shadow", timeout_seconds=1)}, sink=receipts.append)
    packet = build_state("DP16", {"request": "synthetic-secret-canary"}, scope_digest="4" * 64, classification="synthetic")
    result = client.decide("DP16", packet, 1, time.time()+1)
    assert result.fallback == "shadow_observation" and result.receipt_persisted
    assert calls == ["/v1/health", "/v1/decide"]
    assert "synthetic-secret-canary" not in json.dumps([receipts, health])
    assert result.receipt["distribution"]["needs_tools"] == 1


def test_pin_checked_before_payload_and_client_certificate_required(node):
    manifest, kwargs, _, calls, _ = node
    bad = LanTransport(replace(manifest, server_certificate_sha256="0" * 64), **kwargs)
    with pytest.raises(DecisionError, match="certificate_pin"):
        bad.health()
    assert calls == []
    context = ssl.create_default_context(cafile=str(kwargs["ca_file"]))
    connection = http.client.HTTPSConnection("127.0.0.1", manifest.port, context=context, timeout=1)
    try:
        with pytest.raises((ssl.SSLError, http.client.RemoteDisconnected, ConnectionResetError)):
            connection.request("GET", "/v1/health")
            connection.getresponse()
    finally:
        connection.close()
    assert calls == []


def test_allowlist_metadata_pins_resource_and_local_artifact_checks(node):
    manifest, _, service, _, model = node
    with pytest.raises(DecisionError, match="node_auth"):
        service.handle("/v1/health", b"", peer_address="192.168.1.8", mutually_authenticated=True)
    with pytest.raises(DecisionError, match="node_auth"):
        service.handle("/v1/health", b"", peer_address="127.0.0.1", mutually_authenticated=False)
    with pytest.raises(DecisionError, match="lan_allowlist"):
        replace(manifest, address="8.8.8.8")
    with pytest.raises(DecisionError, match="literal_lan"):
        replace(manifest, address="node.example")
    for change in ({"model_digest": "8" * 64}, {"registry_digest": "8" * 64}, {"memory_used_bytes": 999999}):
        with pytest.raises(DecisionError):
            validate_health({**service.health(), **change}, manifest)
    model.write_bytes(b"swapped")
    with pytest.raises(DecisionError, match="model_artifact"):
        TypedDecisionService(manifest, lambda _: None, model_path=model, calibration_path=model, resource_snapshot=lambda: {})


def test_service_capacity_and_invalid_body_fail_closed(node):
    _, _, service, _, _ = node
    for _ in range(service.manifest.max_queue):
        assert service._slots.acquire(blocking=False)
    try:
        with pytest.raises(DecisionError, match="node_capacity"):
            service.handle("/v1/decide", b"{}", peer_address="127.0.0.1", mutually_authenticated=True)
    finally:
        for _ in range(service.manifest.max_queue):
            service._slots.release()
    with pytest.raises(DecisionError, match="invalid_request"):
        service.handle("/v1/decide", b"{}", peer_address="127.0.0.1", mutually_authenticated=True)
    assert service.health()["queue_depth"] == 0


def test_benchmark_measures_synthetic_batch_without_claiming_hardware(node, record_property):
    from evals.decisions.benchmark import benchmark, compare
    manifest, kwargs, _, _, _ = node
    report = benchmark(LanTransport(manifest, **kwargs), manifest, backend="synthetic_fixture", rounds=2, warmup=1)
    assert report["samples"] == 6 and report["failure_count"] == 0
    assert report["end_to_end_ms"]["p95"] > 0 and report["sequential_batch_ms"]["p95"] > 0
    assert not report["hardware_qualified"] and not report["promotion_eligible"]
    assert compare(report, report)["answer_parity"]
    wrong = dict(report, calibration_digest="8" * 64)
    with pytest.raises(DecisionError, match="bundle_mismatch"):
        compare(report, wrong)
    record_property("synthetic_benchmark", json.dumps(report, sort_keys=True))
