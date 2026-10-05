"""Exact, identity-bound systemone HTTPS; configured hashes are expectations only.

The LAN protocol remains separate. No construction-time network/credential reads,
ambient proxies, redirect following, URL arguments, retries or private admission.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from contextvars import copy_context
from dataclasses import dataclass, fields
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time

import httpx

from agent.decisions.contracts import DecisionError, DecisionRequest, ModelBundle, number, require, sha256
from tools.egress_policy import prepare_recipient, wrap_httpx_transport

LAYA_ENDPOINT = "https://laya.ryoko.okinawa/v1/systemone"
LAYA_HEALTH_ENDPOINT = "https://laya.ryoko.okinawa/health"
_HOST = "laya.ryoko.okinawa"
_DNS_SLOT = threading.BoundedSemaphore(1)


@dataclass(frozen=True)
class LayaDestinationManifest:
    schema_version: int
    endpoint: str
    recipient_id: str
    secret_ref: str
    expected_model_digest: str
    expected_calibration_digest: str
    expected_service_digest: str
    allowed_classifications: tuple[str, ...] = ("synthetic", "public")
    max_request_bytes: int = 32768
    max_response_bytes: int = 32768
    model_alias: str | None = None

    def __post_init__(self):
        require(type(self.schema_version) is int and self.schema_version == 1, "invalid_laya_manifest_version")
        require(self.endpoint == LAYA_ENDPOINT, "laya_destination_not_allowed")
        require(self.secret_ref == "LAYA_API_KEY", "invalid_laya_secret_reference")
        require(isinstance(self.recipient_id, str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.recipient_id) is not None,
                "invalid_laya_recipient")
        for value in (self.expected_model_digest, self.expected_calibration_digest, self.expected_service_digest):
            sha256(value)
        allowed = self.allowed_classifications
        require(isinstance(allowed, (tuple, list)) and 1 <= len(allowed) <= 2
                and all(type(item) is str and item in {"synthetic", "public"} for item in allowed)
                and len(set(allowed)) == len(allowed), "private_destination_unqualified")
        object.__setattr__(self, "allowed_classifications", tuple(allowed))
        for value in (self.max_request_bytes, self.max_response_bytes):
            require(type(value) is int and 1 <= value <= 32768, "invalid_laya_size_limit")
        require(self.model_alias is None or (isinstance(self.model_alias, str)
                and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", self.model_alias) is not None),
                "invalid_laya_model_alias")

    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and set(record) <= {item.name for item in fields(cls)},
                "invalid_laya_manifest")
        try:
            return cls(**record)
        except TypeError:
            raise DecisionError("invalid_laya_manifest") from None


@dataclass(frozen=True)
class SyntheticTlsFixture:
    """Python-only test injection, never a manifest/config option or TLS bypass."""
    port: int
    ca_file: str
    address: str = "127.0.0.1"

    def __post_init__(self):
        require(self.address in {"127.0.0.1", "::1"}, "invalid_synthetic_destination")
        require(type(self.port) is int and 1 <= self.port <= 65535, "invalid_synthetic_destination")
        require(isinstance(self.ca_file, str) and bool(self.ca_file), "invalid_synthetic_ca")


def _remaining(end):
    value = end - time.monotonic()
    require(value > 0, "deadline_exceeded")
    return value


def _public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise DecisionError("laya_address_denied") from None
    require(address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_unspecified and not address.is_loopback
            and not address.is_link_local and not address.is_private
            and getattr(address, "ipv4_mapped", None) is None
            and getattr(address, "sixtofour", None) is None
            and getattr(address, "teredo", None) is None,
            "laya_address_denied")
    return address


def _resolve(end):
    # getaddrinfo has no timeout API. One bounded daemon retains this slot until
    # the OS resolver exits; an expired resolution can never open a socket.
    require(_DNS_SLOT.acquire(blocking=False), "node_capacity")
    future = Future()

    def work():
        try:
            future.set_result(socket.getaddrinfo(_HOST, 443, type=socket.SOCK_STREAM))
        except Exception:
            future.set_exception(DecisionError("node_unavailable"))
        finally:
            _DNS_SLOT.release()

    thread = threading.Thread(target=copy_context().run, args=(work,), daemon=True, name="laya-dns")
    try:
        thread.start()
    except RuntimeError:
        _DNS_SLOT.release()
        raise DecisionError("node_capacity") from None
    try:
        addresses = future.result(timeout=_remaining(end))
    except TimeoutError:
        raise DecisionError("deadline_exceeded") from None
    require(bool(addresses) and len(addresses) <= 32, "laya_address_denied")
    for family, kind, protocol, _, address in addresses:
        require(family in {socket.AF_INET, socket.AF_INET6} and kind == socket.SOCK_STREAM
                and protocol in {0, socket.IPPROTO_TCP} and address[1] == 443, "laya_address_denied")
        _public_address(address[0])
    return addresses[0]


class _ExactHttpTransport(httpx.BaseTransport):
    def __init__(self, owner, authorization, requests, end):
        self.owner, self.authorization, self.requests, self.end = owner, authorization, requests, end

    def handle_request(self, request):
        return self.owner._exchange(request, self.authorization, self.requests, self.end)


class LayaHttpsTransport:
    protocol = "laya_systemone"
    protocol_version = 2

    def __init__(self, manifest, *, bundle, synthetic_fixture=None):
        require(type(manifest) is LayaDestinationManifest and type(bundle) is ModelBundle, "invalid_laya_manifest")
        for key in ("model_digest", "calibration_digest", "service_digest"):
            require(getattr(manifest, "expected_" + key) == getattr(bundle, key), "bundle_mismatch")
        require(synthetic_fixture is None or type(synthetic_fixture) is SyntheticTlsFixture,
                "invalid_synthetic_destination")
        from agent.runtime_context import current_agent_context
        self._owner = current_agent_context()
        self.manifest, self.bundle = manifest, bundle
        self._fixture = synthetic_fixture
        self.remote_completion_unknown = False
        self.admission_key = LAYA_ENDPOINT if synthetic_fixture is None else (
            f"synthetic:{synthetic_fixture.address}:{synthetic_fixture.port}/v1/systemone")

    def _authorize(self, requests, endpoint, *, authorization=None):
        """Re-read run, live policy, owner, recipient and secret grant at each edge."""
        from agent.decisions.receipts import scope_digest
        from agent.secret_scope import current_secret_scope, get_secret
        from tools.capability_broker import require_live_policy
        try:
            context = require_live_policy()
            require(context is not None and context == self._owner, "decision_owner_required")
            if authorization is None:
                authorization = prepare_recipient("decision_inference", endpoint,
                    recipient_id=self.manifest.recipient_id)
            require(authorization is not None, "laya_recipient_required")
            authorization.check_url(endpoint)
            for request in requests:
                require(type(request) is DecisionRequest, "invalid_request")
                classification = request.state_packet.classification
                require(classification in {"synthetic", "public"}, "private_destination_unqualified")
                require(classification in self.manifest.allowed_classifications, "laya_data_class_denied")
                require(request.state_packet.scope_digest == scope_digest(context), "decision_scope_mismatch")
                require(request.deadline > time.time(), "deadline_exceeded")
                if self._fixture is not None:
                    require(classification == "synthetic", "loopback_synthetic_only")
            if requests:
                require(context.policy.allows_secret(self.manifest.secret_ref), "laya_secret_not_granted")
                scope = current_secret_scope()
                require(scope is not None and self.manifest.secret_ref in scope, "laya_secret_required")
                secret = get_secret(self.manifest.secret_ref)
                require(isinstance(secret, str) and re.fullmatch(r"[A-Za-z0-9._~+/-]{1,2048}=*", secret) is not None,
                        "laya_secret_required")
                if self._fixture is not None:
                    require(secret.startswith("synthetic-laya-"), "synthetic_credential_required")
                return authorization, secret
            return authorization, None
        except DecisionError:
            raise
        except Exception:
            raise DecisionError("laya_authorization_denied") from None

    def decide_many(self, requests, timeout):
        from agent.decisions.laya_codec import encode_laya_batch, decode_laya_batch
        number(timeout, .001, 1, "invalid_timeout")
        require(isinstance(requests, (tuple, list)) and 1 <= len(requests) <= 64, "invalid_batch")
        requests = tuple(requests)
        require(not self.remote_completion_unknown, "remote_completion_unknown")
        start = time.monotonic()
        end = start + timeout
        authorization, _ = self._authorize(requests, LAYA_ENDPOINT)
        batch = encode_laya_batch(requests, model_alias=self.manifest.model_alias)
        require(len(batch.body) <= self.manifest.max_request_bytes, "request_too_large")
        raw = self._perform(requests, authorization, LAYA_ENDPOINT, batch.body, end)
        self._authorize(requests, LAYA_ENDPOINT, authorization=authorization)
        result = decode_laya_batch(raw, batch, self.bundle, latency_ms=(time.monotonic() - start) * 1000)
        _remaining(end)
        return result

    def __call__(self, request, timeout):
        return self.decide_many((request,), timeout).records[0]

    def health(self, timeout=1):
        """Explicit separately granted unauthenticated liveness, never attestation."""
        number(timeout, .001, 1, "invalid_timeout")
        end = time.monotonic() + timeout
        authorization, _ = self._authorize((), LAYA_HEALTH_ENDPOINT)
        self._perform((), authorization, LAYA_HEALTH_ENDPOINT, None, end)
        return {"reachable": True, "authenticated": False, "loaded_identity_verified": False}

    def _perform(self, requests, authorization, endpoint, body, end):
        inner = _ExactHttpTransport(self, authorization, requests, end)
        transport = wrap_httpx_transport(inner, authorization)
        try:
            with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                              timeout=_remaining(end)) as client:
                response = client.request("POST" if body is not None else "GET", endpoint,
                    content=body, headers={"Accept": "application/json", "Content-Type": "application/json"})
                require(response.status_code == 200, "node_http_error")
                return response.content
        except DecisionError:
            raise
        except Exception:
            raise DecisionError("node_unavailable") from None

    def _connect(self, end, authorize):
        fixture = self._fixture
        if fixture is None:
            family, kind, protocol, _, address = _resolve(end)
        else:
            family = socket.AF_INET6 if ":" in fixture.address else socket.AF_INET
            kind, protocol, address = socket.SOCK_STREAM, socket.IPPROTO_TCP, (fixture.address, fixture.port)
        # Resolution can outlive a revocation. Recheck before even opening the
        # validated socket, then again after TLS before any application bytes.
        authorize()
        _remaining(end)
        raw = socket.socket(family, kind, protocol)
        secured = None
        try:
            raw.settimeout(_remaining(end))
            # Literal sockaddr from the single validated DNS result: no second lookup.
            raw.connect(address)
            peer = ipaddress.ip_address(raw.getpeername()[0])
            require(peer == ipaddress.ip_address(address[0]), "laya_peer_mismatch")
            if fixture is None:
                _public_address(str(peer))
            context = ssl.create_default_context(cafile=fixture.ca_file if fixture else None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            raw.settimeout(_remaining(end))
            secured = context.wrap_socket(raw, server_hostname=_HOST)
            require(ipaddress.ip_address(secured.getpeername()[0]) == peer, "laya_peer_mismatch")
            return secured
        except BaseException:
            (secured if secured is not None else raw).close()
            raise

    def _exchange(self, request, authorization, requests, end):
        conn = http.client.HTTPSConnection(_HOST, 443, timeout=_remaining(end))
        attempted_body = False
        complete = False
        timer = None
        try:
            self._authorize(requests, str(request.url), authorization=authorization)
            conn.sock = self._connect(end, lambda: self._authorize(requests, str(request.url), authorization=authorization))
            sock = conn.sock

            def expire():
                # Interrupt slow-drip headers/bodies at the absolute deadline.
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    return

            timer = threading.Timer(_remaining(end), expire)
            timer.daemon = True
            timer.start()
            _, secret = self._authorize(requests, str(request.url), authorization=authorization)
            headers = {"Accept": "application/json", "Content-Type": "application/json", "Connection": "close"}
            if requests:
                headers["Authorization"] = "Bearer " + secret
            body = request.content if requests else None
            require(body is None or len(body) <= self.manifest.max_request_bytes, "request_too_large")
            conn.sock.settimeout(_remaining(end))
            attempted_body = body is not None
            conn.request(request.method, request.url.raw_path.decode("ascii"), body=body, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                # No redirects or remote error bodies enter decoding/logging.
                complete = True
                raise DecisionError("node_http_error")
            require(response.getheader("Content-Type", "").split(";")[0].strip().lower() == "application/json",
                    "node_content_type")
            require(response.getheader("Content-Encoding", "identity").lower() == "identity", "node_content_encoding")
            raw = response.read(self.manifest.max_response_bytes + 1)
            require(len(raw) <= self.manifest.max_response_bytes, "response_too_large")
            # read(size) may return early on EOF without raising IncompleteRead.
            require(response.length in (None, 0), "node_incomplete_response")
            complete = True
            _remaining(end)
            return httpx.Response(200, content=raw, headers={"Content-Type": "application/json"})
        except Exception as exc:
            if attempted_body and not complete:
                self.remote_completion_unknown = True
                raise DecisionError("remote_completion_unknown") from None
            if isinstance(exc, DecisionError):
                raise
            raise DecisionError("node_unavailable") from None
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()
