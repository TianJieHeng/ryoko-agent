"""Classic CLI conversation picker; curses owns input only while PT is suspended."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
import sys
import threading

from hermes_cli.memory_sessions import ConversationError, display_text


@dataclass
class PickerState:
    rows: list = field(default_factory=list)
    query: str = ''
    cursor: int = 0
    searching: bool = False

    @property
    def visible(self):
        return [r for r in self.rows if self.query.casefold() in r.label.casefold()]

    def move(self, delta):
        self.cursor = max(0, min(self.cursor + delta, len(self.visible) - 1))

    def selected(self):
        rows = self.visible
        return rows[self.cursor] if rows and 0 <= self.cursor < len(rows) else None

    def replace(self, rows):
        current = self.selected()
        self.rows = rows
        self.cursor = next((i for i, row in enumerate(self.visible) if current and row.key == current.key), 0)


def _load(service):
    from agent.memory_provider import spawn_context_thread
    future = Future()
    def work():
        try:
            future.set_result(service.list())
        except BaseException as exc:
            future.set_exception(exc)
    spawn_context_thread(work, name='session-browser-read').start()
    return future


def _curses_picker(screen, service):
    import curses
    from hermes_cli.curses_ui import _addnstr, _decode_menu_key
    state = PickerState()
    pending = _load(service)
    error = ''
    screen.keypad(True)
    screen.timeout(100)
    while True:
        if pending is not None and pending.done():
            try:
                state.replace(pending.result())
                error = ''
            except Exception:
                error = 'Memory read failed. R retries; Esc returns. Local sessions: /sessions local.'
            pending = None
        rows = state.visible
        state.move(0)
        height, width = screen.getmaxyx()
        screen.erase()
        _addnstr(screen, 0, 0, 'Memory conversations', max(1, width - 1), curses.A_BOLD)
        _addnstr(screen, 1, 0, '↑/↓ select · Enter open · / search · R refresh · Esc cancel', max(1, width - 1), curses.A_DIM)
        _addnstr(screen, 2, 0, 'Search: ' + state.query + ('_' if state.searching else ''), max(1, width - 1), curses.A_NORMAL)
        selected = state.selected()
        if selected:
            _addnstr(screen, 3, 0, selected.detail, max(1, width - 1), curses.A_DIM)
        page = max(1, height - 6)
        top = max(0, state.cursor - page + 1)
        for i, row in enumerate(rows[top:top + page], top):
            attr = curses.A_REVERSE if i == state.cursor else curses.A_NORMAL
            _addnstr(screen, 4 + i - top, 0, ('> ' if i == state.cursor else '  ') + row.label,
                     max(1, width - 1), attr)
        status = 'Loading harness conversations…' if pending else (error or f'{len(rows)} of {len(state.rows)} advertised conversations')
        if not pending and not error and getattr(service, 'notice', ''):
            status += ' · ' + service.notice
        if not rows and not pending and not error:
            status = 'No matching conversations. R refreshes; Esc returns.'
        _addnstr(screen, max(0, height - 1), 0, status, max(1, width - 1), curses.A_DIM)
        screen.refresh()
        try:
            raw_key = screen.get_wch()
        except curses.error:
            continue
        key = ord(raw_key) if isinstance(raw_key, str) else raw_key
        nav = _decode_menu_key(screen, key) if not state.searching or key in (27, 3, 10, 13) else None
        screen.timeout(100)  # escape decoding temporarily changes this
        if nav in ('cancel', 'interrupt'):
            return None
        if state.searching:
            if key in (10, 13, curses.KEY_ENTER):
                state.searching = False
            elif key in (8, 127, curses.KEY_BACKSPACE):
                state.query = state.query[:-1]
                state.cursor = 0
            elif isinstance(raw_key, str) and raw_key.isprintable():
                state.query = (state.query + display_text(chr(key)))[:200]
                state.cursor = 0
            continue
        if key == ord('/'):
            state.searching = True
        elif key in (ord('r'), ord('R')) and pending is None:
            pending = _load(service)
        elif nav == 'up':
            state.move(-1)
        elif nav == 'down':
            state.move(1)
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            state.move(-page if key == curses.KEY_PPAGE else page)
        elif nav == 'select' and pending is None:
            chosen = state.selected()
            if chosen and chosen.unavailable:
                error = 'Cannot open: ' + chosen.unavailable
            elif chosen:
                return chosen


def choose_conversation(cli, service):
    """Return a selected immutable reference; cancellation never exits the chat."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ConversationError('The picker needs an interactive terminal. Use /sessions local for a text listing.')
    def choose():
        import curses
        try:
            return curses.wrapper(_curses_picker, service)
        finally:
            # Cancellation also stops an in-flight read; selection finishes with no pending read.
            service.cancel.set()
    app = getattr(cli, '_app', None)
    if app is None:
        if threading.current_thread() is not threading.main_thread():
            raise ConversationError('The terminal picker requires the main CLI input loop.')
        return choose()
    loop = getattr(app, 'loop', None)
    if loop is None or not loop.is_running() or threading.current_thread() is threading.main_thread():
        raise ConversationError('The terminal input loop is not ready for the picker.')

    async def suspended():
        from prompt_toolkit.application import run_in_terminal
        from prompt_toolkit.application.current import set_app
        with set_app(app):
            return await run_in_terminal(choose, in_executor=False)

    # Schedule on the app's context, not a foreign loop; the SDK read uses its own worker.
    future = asyncio.run_coroutine_threadsafe(suspended(), loop)
    try:
        return future.result()
    finally:
        service.cancel.set()
