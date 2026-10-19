"""Classic CLI handoff from an owner-scoped memory selection to normal resume."""
from __future__ import annotations

from dataclasses import dataclass
import os
import sys

from hermes_cli.memory_sessions import ConversationError, MemorySessions


@dataclass(frozen=True)
class SessionRelaunch:
    target: str
    home: str

    def arguments(self, original=None):
        from hermes_constants import profile_name_for_home
        from hermes_cli.relaunch import _extract_inherited_flags
        # UI modes survive; source-session model, approvals, skills and queries do not.
        flags = _extract_inherited_flags(sys.argv[1:] if original is None else original)
        args = [flag for flag in flags if flag in {'--dev', '--tui', '--native', '--safe-mode'}]
        profile = profile_name_for_home(self.home)
        if profile:
            args += ['-p', profile]
        return [*args, '--resume', self.target]

    def execute(self):
        from hermes_cli.relaunch import relaunch
        os.environ['HERMES_HOME'] = self.home
        try:
            relaunch(self.arguments(), preserve_inherited=False)
        except OSError:
            print('Hermes could not restart. Your sessions are saved. '
                  f'Reopen this profile and use /resume {self.target}.', file=sys.stderr)


def configured():
    from agent.agent_configuration import base_configuration
    return bool((base_configuration().get('memory', {}).get('personal_mcp') or {}).get('server'))


def flush_current(cli):
    agent = getattr(cli, 'agent', None)
    history = getattr(cli, 'conversation_history', None)
    messages = getattr(agent, '_session_messages', None)
    if not isinstance(messages, list):
        messages = history
    if not messages:
        return
    if agent is None:
        raise ConversationError('The current transcript cannot be saved yet; no session was switched.')
    baseline = history if isinstance(history, list) and history is not messages else None
    if agent._flush_messages_to_session_db(messages, baseline) is not True:
        raise ConversationError('Could not save the current transcript. Retry before switching sessions.')


def browse(cli):
    """Return False only after a durable target and clean relaunch have been scheduled."""
    from hermes_cli.cli_sessions_picker import choose_conversation
    from hermes_cli.memory_session_resume import prepare_selected
    from hermes_cli.active_sessions import active_session_registry_snapshot
    try:
        if getattr(cli, '_agent_running', False):
            raise ConversationError('Wait for the current response to finish before switching conversations.')
        cli._pending_resume_sessions = None
        service = MemorySessions.for_cli(cli)
        selected = choose_conversation(cli, service)
        if selected is None:
            return
        # The picker's cancelled/finished request scope must not authorize a new read.
        service = MemorySessions.for_cli(cli)
        target = prepare_selected(cli, service, selected)
        if target is None:
            choices = [('cancel', 'Cancel', 'Keep this conversation'),
                       ('open', 'Continue with available history', 'Open the archived windows in this thread')]
            answer = cli._prompt_text_input_modal(
                title='Continue a memory conversation',
                detail='The harness supplies overlapping archived text windows, which may omit recent '
                       'messages. Hidden tool/model state cannot be restored. New messages will append '
                       'to the selected memory thread.', choices=choices)
            if answer is None or cli._normalize_slash_confirm_choice(answer, choices) != 'open':
                return
            target = prepare_selected(cli, service, selected, allow_available_history=True)
        if target == cli.session_id:
            print('  This conversation is already open.')
            return
        if getattr(cli, '_agent_running', False):
            raise ConversationError('A response started; finish it before switching conversations.')
        # Preflight only: normal startup performs the authoritative lease acquisition.
        if any(row.get('session_id') == target for row in active_session_registry_snapshot(strict=True)):
            raise ConversationError('That conversation is already open in another Hermes process.')
        flush_current(cli)
        cli._pending_session_relaunch = SessionRelaunch(target, service.context.profile_home)
        return False
    except ConversationError as exc:
        print('  ' + str(exc))
    except Exception:
        print('  Could not prepare this conversation safely. Your current session is unchanged. '
              'Use /sessions local or retry.')
