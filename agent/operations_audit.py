"""Purpose-separated audit projections. Recovery payloads never enter exports.

Correlation tokens are keyed pseudonyms, not anonymization. The caller owns key
custody and export consent; this module creates no key and opens no network.
"""
from __future__ import annotations

from collections import deque
import hashlib
import hmac
import json
import threading

from hermes_state_runtime import _EVENT_TYPES

_CORRELATIONS = ("session_id", "mission_id", "run_id", "operation_id", "effect_id",
                 "delivery_id", "approval_id")


def project_event(event, *, purpose="operator", correlation_key=None, scope=None):
    if purpose not in {"operator", "analytics"} or event.get("type") not in _EVENT_TYPES:
        raise ValueError("unsupported_audit_projection")
    if purpose == "analytics" and (not isinstance(correlation_key, bytes)
            or len(correlation_key) < 32 or not isinstance(scope, str) or not scope):
        raise ValueError("scoped_correlation_key_required")
    correlations = {}
    for name in _CORRELATIONS:
        value = event.get(name)
        if value is not None:
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError("invalid_audit_correlation")
            correlations[name] = (hmac.new(correlation_key,
                json.dumps([scope, name, value], separators=(",", ":")).encode(),
                hashlib.sha256).hexdigest() if purpose == "analytics" else value)
    # Deliberately do not recursively scrub payloads: an unfamiliar key cannot
    # accidentally become a telemetry field, even if it doesn't look like a key.
    return {"schema_version": 1, "type": event["type"], "purpose": purpose,
            "correlation_ids": correlations,
            "retention_class": "optional_analytics" if purpose == "analytics" else "operational_audit",
            "redacted_fields": ["payload", "provider_receipts", "arguments", "paths", "credentials"]}


class OptionalAuditSink:
    """One bounded, opt-in worker; a blocked sink never blocks runtime dispatch.

    Drop-newest at capacity. No retries, no raw payload buffer, no implicit sink
    setup. A stuck worker may remain a daemon until process exit; it cannot spawn
    replacement workers or accumulate unbounded in-flight requests.
    """
    def __init__(self, send, *, consent=False, capacity=32):
        if consent is not True or not callable(send) or type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError("explicit_bounded_analytics_consent_required")
        self._send, self._capacity = send, capacity
        self._queue = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._dropped = self._failed = self._sent = 0
        self._thread = threading.Thread(target=self._run, name="optional-redacted-audit", daemon=True)
        self._thread.start()

    def offer(self, event, *, correlation_key, scope):
        projection = project_event(event, purpose="analytics", correlation_key=correlation_key, scope=scope)
        with self._condition:
            if self._closed or len(self._queue) >= self._capacity:
                self._dropped += 1
                return False
            self._queue.append(projection)
            self._condition.notify()
            return True

    def status(self):
        with self._condition:
            return {"queued": len(self._queue), "capacity": self._capacity, "dropped": self._dropped,
                    "failed": self._failed, "sent": self._sent, "closed": self._closed}

    def close(self):
        with self._condition:
            self._closed = True
            self._dropped += len(self._queue)
            self._queue.clear()
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or bool(self._queue))
                if self._closed:
                    return
                item = self._queue.popleft()
            try:
                self._send(item)
            except Exception:
                with self._condition:
                    self._failed += 1
            else:
                with self._condition:
                    self._sent += 1
