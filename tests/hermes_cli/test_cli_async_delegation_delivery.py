"""Regression coverage for CLI async-delegation completion ownership."""

import queue
from types import SimpleNamespace

from cli import HermesCLI


def test_cli_completion_drain_uses_visible_session_identity(monkeypatch):
    """A CLI window must not claim another window's restored completion."""
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = "visible-session"
    cli._pending_input = queue.Queue()
    owner_context = object()
    cli.agent = SimpleNamespace(runtime_context=owner_context)

    event = {
        "type": "async_delegation",
        "delegation_id": "deleg_visible",
        "session_key": "visible-session",
        "owner_context": object(),  # an event field is never claim authority
    }
    calls = []

    class FakeRegistry:
        def drain_notifications(self, *, session_key="", owns_event=None):
            calls.append((session_key, owns_event(event)))
            return [(event, "completion payload")]

    claimed = []
    completed = []

    monkeypatch.setattr(
        "tools.process_registry.process_registry",
        FakeRegistry(),
    )
    monkeypatch.setattr(
        "tools.async_delegation.claim_event_delivery",
        lambda evt, consumer, *, owner_context: claimed.append((evt, consumer, owner_context)) or "claim-token",
    )
    monkeypatch.setattr(
        "tools.async_delegation.complete_event_delivery",
        lambda evt, token: completed.append((evt, token)),
    )

    cli._drain_process_notifications("cli-idle")

    assert calls == [("visible-session", True)]
    assert cli._pending_input.get_nowait() == "completion payload"
    assert claimed == [(event, "cli-idle", owner_context)]
    assert completed == [(event, "claim-token")]


def test_cli_completion_ownership_rejects_foreign_session():
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = "visible-session"
    cli._session_db = None

    assert not cli._owns_process_notification(
        {"type": "async_delegation", "session_key": "foreign-session"}
    )


def test_cli_completion_ownership_accepts_compression_lineage():
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = "visible-session"

    class FakeSessionDB:
        def resolve_resume_session_id(self, session_id):
            assert session_id == "pre-compression-session"
            return "visible-session"

    cli._session_db = FakeSessionDB()

    assert cli._owns_process_notification(
        {
            "type": "async_delegation",
            "session_key": "pre-compression-session",
        }
    )


def test_cli_denied_completion_claim_never_queues_or_acknowledges(monkeypatch):
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = "visible-session"
    cli.agent = SimpleNamespace(runtime_context=None)
    cli._pending_input = queue.Queue()
    event = {"type": "async_delegation", "delegation_id": "strict-delegation",
             "session_key": cli.session_id, "owner_context": object()}
    claims, completed = [], []
    monkeypatch.setattr("tools.process_registry.process_registry", SimpleNamespace(
        drain_notifications=lambda **kwargs: [(event, "unclaimed result")]))
    monkeypatch.setattr("tools.async_delegation.claim_event_delivery",
        lambda evt, consumer, *, owner_context: claims.append(owner_context))
    monkeypatch.setattr("tools.async_delegation.complete_event_delivery",
        lambda *args: completed.append(args))

    cli._drain_process_notifications("cli-idle")

    assert claims == [None]
    assert cli._pending_input.empty()
    assert completed == []
