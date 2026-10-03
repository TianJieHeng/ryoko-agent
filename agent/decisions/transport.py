"""Pinned mutual-TLS LAN transport, with no proxies, redirects or DNS resolution.

This protocol adapter does not provision a node, install software or claim a
vendor serving API. A separately qualified service must implement this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import ssl
import time

from agent.decisions.contracts import DecisionError, canonical, number, require, sha256

_RFC1918 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


@dataclass(frozen=True)
class NodeManifest:
    address: str
    port: int
    server_certificate_sha256: str
    allowed_client_addresses: tuple[str, ...]
    model_digest: str
    calibration_digest: str
    service_digest: str
    registry_digest: str
    max_queue: int = 4
    max_request_bytes: int = 32768
    max_response_bytes: int = 32768
    synthetic_loopback: bool = False

    def __post_init__(self):
        try:
            address = ipaddress.ip_address(self.address)
            clients = tuple(ipaddress.ip_address(value) for value in self.allowed_client_addresses)
        except ValueError:
            raise DecisionError("literal_lan_address_required") from None
        def permitted(item):
            return any(item in network for network in _RFC1918) or (self.synthetic_loopback and item.is_loopback)
        require(permitted(address) and clients and len(clients) <= 16 and all(permitted(item) for item in clients), "lan_allowlist_required")
        require(type(self.port) is int and 1 <= self.port <= 65535, "invalid_port")
        for value in (self.server_certificate_sha256, self.model_digest, self.calibration_digest,
                      self.service_digest, self.registry_digest):
            sha256(value)
        for value, bound in ((self.max_queue, 64), (self.max_request_bytes, 65536), (self.max_response_bytes, 65536)):
            require(type(value) is int and 1 <= value <= bound, "invalid_node_limit")
        require(type(self.synthetic_loopback) is bool, "invalid_node_manifest")

    @classmethod
    def from_record(cls, data):
        from dataclasses import fields
        require(isinstance(data, dict) and set(data) <= {field.name for field in fields(cls)}, "invalid_node_manifest")
        try:
            copy = dict(data)
            copy["allowed_client_addresses"] = tuple(copy["allowed_client_addresses"])
            return cls(**copy)
        except (TypeError, KeyError):
            raise DecisionError("invalid_node_manifest") from None


class LanTransport:
    def __init__(self, manifest, *, ca_file, client_certificate, client_key):
        self.manifest = manifest
        require(isinstance(manifest, NodeManifest), "invalid_node_manifest")
        # Caller-owned existing credentials only; nothing is generated/saved here.
        context = ssl.create_default_context(cafile=str(ca_file))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(client_certificate), str(client_key))
        self._context = context

    def __call__(self, request, timeout):
        if self.manifest.synthetic_loopback:
            require(request.state_packet.classification == "synthetic", "loopback_synthetic_only")
        return self._exchange("/v1/decide", request.to_record(), timeout)

    def health(self, timeout=1):
        return self._exchange("/v1/health", None, timeout)

    def _exchange(self, path, payload, timeout):
        number(timeout, .001, 1, "invalid_timeout")
        body = None if payload is None else canonical(payload).encode()
        require(body is None or len(body) <= self.manifest.max_request_bytes, "request_too_large")
        conn = http.client.HTTPSConnection(self.manifest.address, self.manifest.port,
                                           context=self._context, timeout=timeout)
        end = time.monotonic() + timeout
        try:
            conn.connect()
            certificate = conn.sock.getpeercert(binary_form=True)
            require(hashlib.sha256(certificate).hexdigest() == self.manifest.server_certificate_sha256, "certificate_pin_mismatch")
            # Certificate pin is checked BEFORE application/private bytes leave.
            remaining = end - time.monotonic()
            require(remaining > 0, "deadline_exceeded")
            conn.sock.settimeout(remaining)
            conn.request("GET" if payload is None else "POST", path, body=body,
                         headers={"Content-Type": "application/json", "Accept": "application/json"})
            response = conn.getresponse()
            require(response.status == 200, "node_http_error")
            require(response.getheader("Content-Type", "").split(";")[0] == "application/json", "node_content_type")
            raw = response.read(self.manifest.max_response_bytes + 1)
            require(len(raw) <= self.manifest.max_response_bytes, "response_too_large")
            require(time.monotonic() <= end, "deadline_exceeded")
            return json.loads(raw)
        except DecisionError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise DecisionError("node_unavailable") from None
        finally:
            conn.close()
