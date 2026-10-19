"""Owner-scoped conversation catalog and finite MCP reads for the CLI."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import threading
import unicodedata


class ConversationError(ValueError):
    """Safe operator-facing failure; never embeds remote bodies or credentials."""


def display_text(value, limit=160):
    text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', str(value or ''))
    return ''.join(c if c.isprintable() and unicodedata.category(c) != 'Cf' else ' ' for c in text)[:limit]


@dataclass(frozen=True)
class Conversation:
    source: str
    session_id: str
    title: str
    hub: str
    agent: str
    turns: int | None
    last_active: float
    via: str
    unavailable: str = ''

    @property
    def key(self):
        return self.source, self.session_id

    @property
    def label(self):
        suffix = f' [{self.unavailable}]' if self.unavailable else ''
        return f'{self.title}  · {display_text(self.source)} · {display_text(self.session_id, 24)}{suffix}'


def catalog(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('conversations'), list):
        raise ConversationError('The harness returned an invalid conversation catalog.')
    if payload.get('nextCursor') or payload.get('next_cursor') or payload.get('has_more'):
        raise ConversationError('The harness changed its pagination contract; refresh the adapter before continuing.')
    records = payload['conversations']
    if len(records) > 10000:
        raise ConversationError('The conversation catalog exceeds the browser limit; no rows were silently omitted.')
    missing = payload.get('unavailable', [])
    found = {}
    ids = {}
    for row in records:
        if not isinstance(row, dict):
            raise ConversationError('Invalid conversation row.')
        source, sid = row.get('source'), row.get('session_id')
        if any(not isinstance(v, str) or not v or len(v) > 512 for v in (source, sid)):
            raise ConversationError('Conversation identity is missing or oversized.')
        key = source, sid
        if key in found:
            continue
        title = display_text(row.get('title')) or 'Untitled conversation'
        stamp = row.get('last_push_at', row.get('updated_at', 0))
        stamp = float(stamp) if isinstance(stamp, (int, float)) else 0
        if stamp > 100000000000:
            stamp /= 1000
        reason = 'unavailable' if sid in missing else ''
        if source not in {'hermes', 'openclaw'}:
            reason = 'unsupported source'
        found[key] = Conversation(source, sid, title, str(row.get('hub_lineage') or ''),
                                  str(row.get('agent') or ''), row.get('turns'), stamp,
                                  str(row.get('via') or ''), reason)
        ids.setdefault(sid, set()).add(source)
    from dataclasses import replace
    return sorted((replace(c, unavailable='ambiguous source ID') if len(ids[c.session_id]) > 1 else c
                   for c in found.values()), key=lambda c: (-c.last_active, c.title.casefold(), c.key))


class MemorySessions:
    def __init__(self, context, server, cancel=None):
        self.context, self.server = context, server
        self.cancel = cancel if cancel is not None else threading.Event()

    @classmethod
    def for_cli(cls, cli, cancel=None):
        from agent.agent_configuration import base_configuration
        from agent.agent_identity import resolve_agent_context
        from hermes_constants import get_hermes_home
        raw = base_configuration()
        server = (raw.get('memory', {}).get('personal_mcp') or {}).get('server')
        if not server:
            raise ConversationError('No personal memory harness is configured. Use /sessions local.')
        context = getattr(getattr(cli, 'agent', None), 'runtime_context', None)
        if context is None:
            # Operator browsing creates no conversation or model instance.
            context = resolve_agent_context(raw, session_id='session-browser', profile_home=get_hermes_home())
        if context is None:
            raise ConversationError('An owned personal-memory profile is required. Use /sessions local.')
        return cls(context, server, cancel)

    def call(self, tool, arguments):
        from agent.identity_lifecycle import agent_runtime_scope
        from tools.mcp_user_read import user_read_scope
        try:
            with agent_runtime_scope(self.context), user_read_scope(self.context, self.server, self.cancel):
                return asyncio.run(self._call(tool, arguments))
        except ConversationError:
            raise
        except Exception as exc:
            # Do not expose transport exception bodies, which may contain private data.
            raise ConversationError('Memory read failed or was cancelled. Check connectivity, credentials, '
                                    'and schema grants; /sessions local remains available.') from exc

    async def _call(self, tool, arguments):
        from tools import mcp_tool as core
        from tools.agent_policy_gate import bind_mcp_connection
        from tools.mcp_tool_config import _load_mcp_config
        from tools.mcp_tool_transport import _connect_inputs
        from tools.mcp_tool_policy import require_http_inputs, require_operation, validate_schemas
        from agent.memory_mcp_provider import _local_validator
        from tools.mcp_user_read import current_read
        core._ensure_mcp_sdk()
        server = core.MCPServerTask(self.server)
        bind_mcp_connection(server)
        server._config = _load_mcp_config()[self.server]
        (url, headers), names = _connect_inputs(self.server, server._config)
        server._agent_recipient_authorization = require_http_inputs(self.server, server._config, url, headers, connection=server)
        server._http_rejection = {}
        headers['mcp-protocol-version'] = core.LATEST_HANDSHAKE_VERSION
        headers['User-Agent'] = 'ryoko-agent/session-browser'
        timeout = current_read().contract['request_timeout_ms'] / 1000
        transport = server._streamable_http_transport(url, headers, timeout, True, None, None, True, names)

        async def perform():
            async with transport as streams:
                async with core.ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    schemas = await core._paginate_full_list(session.list_tools, 'tools', self.server)
                    validate_schemas(server, schemas)
                    schema = next((s for s in schemas if s.name == tool), None)
                    if schema is None:
                        raise ConversationError('The harness does not advertise this conversation operation.')
                    _local_validator(schema.inputSchema).validate(arguments)
                    require_operation(self.server, tool, arguments, connection=server, require_run=True)
                    response = await session.call_tool(tool, arguments)
                    if response.isError:
                        raise ConversationError('The harness could not read this conversation.')
                    payload = getattr(response, 'structuredContent', None)
                    if payload is None:
                        texts = [block.text for block in response.content if block.type == 'text']
                        if len(texts) != 1:
                            raise ConversationError('Unexpected conversation response format.')
                        payload = json.loads(texts[0])
                    if not isinstance(payload, dict):
                        raise ConversationError('Unexpected conversation response format.')
                    return payload

        task = asyncio.create_task(perform())
        try:
            while not task.done():
                current_read().check(self.server)
                await asyncio.wait({task}, timeout=0.1)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def list(self):
        return catalog(self.call('list_conversations', {}))

    def get(self, conversation):
        if conversation.unavailable:
            raise ConversationError('This conversation is unavailable or has an ambiguous identity.')
        result = self.call('get_conversation', {'session': conversation.session_id})
        if result.get('session') != conversation.session_id or result.get('source') != conversation.source:
            raise ConversationError('The harness returned a different conversation; nothing was opened.')
        return result
