"""Opt-in, profile-owned daily memory routing before normal CLI resume."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import re
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hermes_cli.memory_sessions import Conversation, ConversationError, MemorySessions


def daily_name(raw, *, now=None):
    options = raw.get('memory', {}).get('daily_conversation') or {}
    prefix = options.get('prefix', 'mobile-daily-')
    if not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', prefix):
        raise ConversationError('Daily conversation prefix must contain only letters, digits, - or _.')
    zone = options.get('timezone') or raw.get('timezone') or ''
    try:
        tz = ZoneInfo(zone) if zone else None
        instant = now if now is not None else datetime.now().astimezone()
        return prefix + instant.astimezone(tz).date().isoformat()
    except (ValueError, TypeError, KeyError) as exc:
        raise ConversationError('Daily conversation timezone must be a valid IANA timezone.') from exc


def prepare_daily(db, service, name, *, announce=print):
    """Bind one stable remote ID per date; never manufacture a conversation turn."""
    from agent.identity_lifecycle import agent_runtime_scope, identity_config
    from agent.conversation_outbox import settings
    from hermes_state_memory_sessions import (
        verified_local, verify_local_owner, prepare_binding, local_history_warning,
    )
    from hermes_cli.memory_session_resume import history_capsule
    from tools.capability_broker import require_live_policy

    # A failed catalog is not evidence that today's conversation is absent.
    rows = service.list()
    with agent_runtime_scope(service.context):
        require_live_policy(require_run=False)
        push = settings(identity_config())
        if push is None:
            raise ConversationError('Daily conversations require enabled conversation capture.')
        matches = [row for row in rows if row.session_id == name]
        if any(row.unavailable or row.source != push['source'] for row in matches):
            raise ConversationError('Today’s conversation has an unavailable or ambiguous identity.')
        row = matches[0] if matches else Conversation(
            push['source'], name, name, '', push['agent'], 0, 0, 'push')
        if row.title == 'Untitled conversation':
            row = replace(row, title=name)
        local = verified_local(db, service.context, service.server, row, push)
        if local:
            verify_local_owner(db, service.context, local)
            target = db.resolve_resume_session_id(local)
            verify_local_owner(db, service.context, target)
            warning = local_history_warning(db, service.context, local, row, push)
            if warning:
                announce('Daily thread: using saved local history. ' + warning)
            prepare_binding(db, service.context, service.server, row, push, local_id=local)
            return target
        capsule = None
        if matches:
            transcript = service.get(row)
            if transcript.get('windows') == []:
                announce('Daily thread: the harness has no sealed history yet; earlier messages are unavailable locally.')
            else:
                capsule = history_capsule(row, transcript)
                announce('Daily thread: loaded archived history; the harness may omit recent messages.')
        else:
            announce('New daily thread: it will appear in memory after your first message.')
        return prepare_binding(db, service.context, service.server, row, push,
                               capsule=capsule, allow_empty=True)


def apply_daily_default(args, *, use_tui=False):
    """Only a plain interactive classic launch opts in; explicit choices stay authoritative."""
    if use_tui or not sys.stdin.isatty():
        return
    if any(getattr(args, key, None) for key in (
        'resume', 'continue_last', 'continue_session', 'query', 'query_file', 'image',
        'oneshot', 'oneshot_exit', 'safe_mode', 'ignore_user_config', 'source',
    )) or getattr(args, 'output_format', 'text') != 'text':
        return
    from agent.agent_configuration import base_configuration
    raw = base_configuration()
    if (raw.get('memory', {}).get('daily_conversation') or {}).get('enabled') is not True:
        return
    db = None
    try:
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import active_session_registry_snapshot
        from hermes_cli.cli_memory_sessions import target_is_active
        name = daily_name(raw)
        print(f'Checking daily conversation: {name}…', flush=True)
        db = SessionDB()
        service = MemorySessions.for_cli(SimpleNamespace(_session_db=db, agent=None))
        target = prepare_daily(db, service, name)
        if target_is_active(db, target, active_session_registry_snapshot(strict=True)):
            raise ConversationError('Today’s conversation is already open in another Hermes process.')
        args.resume = target
        print(f'↪ Daily conversation: {name}')
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConversationError) else 'Could not safely prepare today’s memory conversation.'
        print(message + ' Retry, or use an explicit --resume / --continue selection.', file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if db is not None:
            db.close()
