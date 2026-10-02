"""The v1 boundary accepts intent, never arbitrary authority or backend state."""

import pytest
from pydantic import ValidationError

from tui_gateway.contracts.runtime_v1 import RuntimeCommandParams, RuntimeEventsSinceParams


def command(**changes):
    return {"session_id": "live-session", "schema_version": 1, "command_id": "command-1",
            "idempotency_key": "intent-1", "expected_revision": 0, "operation": "submit",
            "payload": {"text": "A short request"}, **changes}


def test_command_payloads_are_closed_versioned_intents():
    payloads = {
        "submit": {"text": "A short request"},
        "steer": {"text": "Use the existing result"},
        "cancel": {"reason": "Stop this run"},
        "approval": {"approval_id": "approval-1", "decision": "deny"},
    }
    for operation, payload in payloads.items():
        parsed = RuntimeCommandParams.model_validate(command(operation=operation, payload=payload))
        assert parsed.model_dump()["payload"] == payload
    forbidden = [
        {"principal_id": "another-owner"}, {"agent_id": "another-agent"},
        {"identity_binding": {"agent_id": "another-agent"}}, {"profile": "other"},
        {"history": []}, {"schema_version": 2}, {"schema_version": True},
        {"schema_version": "1"}, {"expected_revision": True}, {"expected_revision": -1},
        {"operation": {}}, {"operation": "run_tool"}, {"payload": {"text": ""}},
        {"payload": {"text": "   "}}, {"payload": {"text": "x", "provider": "raw-provider"}},
        {"operation": "cancel", "payload": {"text": "not a cancel payload"}},
        {"payload": {"text": "x" * 65537}},
    ]
    for change in forbidden:
        with pytest.raises(ValidationError):
            RuntimeCommandParams.model_validate(command(**change))


def test_replay_limit_and_cursor_are_bounded_before_store_access():
    params = {"session_id": "live-session", "schema_version": 1}
    assert RuntimeEventsSinceParams.model_validate(params).limit == 100
    assert RuntimeEventsSinceParams.model_validate({**params, "limit": 200}).limit == 200
    for change in ({"limit": 0}, {"limit": 201}, {"limit": True}, {"limit": "2"},
                   {"cursor": ""}, {"cursor": "x" * 257}, {"actor": {}}):
        with pytest.raises(ValidationError):
            RuntimeEventsSinceParams.model_validate({**params, **change})
