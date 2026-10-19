"""Finite operator-initiated MCP reads, separate from model-turn authority.

Only the CLI composition root opens this scope. No model handler registers it.
It cannot authorize model dispatch, mutations, sampling, or background discovery.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

_READ = ContextVar('mcp_user_read', default=None)
TOOLS = frozenset({'list_conversations', 'get_conversation'})


@dataclass
class UserRead:
    context: object
    server: str
    contract: dict
    max_attempts: int
    deadline: float
    cancel: threading.Event
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempts: int = 0
    closed: bool = False
    uncertain: bool = False

    def check(self, server=None, operation=None):
        from tools.capability_broker import require_live_policy
        from agent.runtime_commands import _RUN
        if (_RUN.get() is not None or self.closed or self.uncertain or self.cancel.is_set()
                or time.monotonic() >= self.deadline):
            raise PermissionError('Conversation read expired or cancelled')
        if require_live_policy(require_run=False) != self.context:
            raise PermissionError('Conversation read owner changed')
        if server is not None and server != self.server:
            raise PermissionError('Conversation read server changed')
        if operation is not None and operation not in TOOLS:
            raise PermissionError('Only conversation list/get are admitted by this operator read')

    def audit(self, state):
        # No endpoints, titles, arguments, transcripts or credentials enter the journal.
        path = Path(self.context.profile_home) / 'session-browser.sqlite3'
        if path.is_symlink():
            raise PermissionError('Conversation audit cannot follow a symlink')
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS reads '
                       '(id TEXT PRIMARY KEY, owner TEXT, attempts INTEGER, state TEXT, updated REAL)')
            identity = self.context.identity
            owner = json.dumps([identity.principal_id, identity.profile_id, identity.agent_id])
            db.execute('INSERT OR REPLACE INTO reads VALUES (?,?,?,?,?)',
                       (self.operation_id, owner, self.attempts, state, time.time()))
        path.chmod(0o600)


def current_read():
    return _READ.get()


@contextmanager
def user_read_scope(context, server, cancel=None):
    from agent.runtime_commands import _RUN
    from agent.identity_lifecycle import identity_config
    from agent.budget_account import parse_budget_policy
    from tools.mcp_tool_budget import parse_contract
    if _RUN.get() is not None or current_read() is not None:
        raise PermissionError('Operator reads cannot be nested in model execution')
    if context.policy.role != 'primary' or context.policy.memory_backend != 'personal_mcp':
        raise PermissionError('Conversation browsing requires the primary personal-memory owner')
    grant = context.policy.mcp_policies.get(server)
    if not grant or server not in context.policy.personal_mcp_servers:
        raise PermissionError('Conversation server is not granted')
    if not TOOLS <= set(grant['read_only_tools']) or not TOOLS <= set(grant['schema_digests']):
        raise PermissionError('Conversation reads require explicit read-only grants and schema pins')
    contract = parse_contract(grant.get('budget'))
    policy = parse_budget_policy(identity_config())
    attempts = min(12, policy.limits['attempts']) if policy else 12
    seconds = min(90, policy.deadline_seconds, policy.limits['wall_ms'] / 1000) if policy else 90
    read = UserRead(context, server, contract, attempts, time.monotonic() + seconds,
                    cancel if cancel is not None else threading.Event())
    token = _READ.set(read)
    try:
        read.check()
        read.audit('started')
        yield read
        read.check()
        read.audit('completed')
    except BaseException:
        read.audit('unknown' if read.uncertain else 'failed')
        raise
    finally:
        read.closed = True
        _READ.reset(token)


def wrap_user_transport(httpx, inner, server, contract, read):
    server._agent_budget_transport = True

    class UserTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.method == 'GET':
                return httpx.Response(405, request=request)
            from tools.mcp_tool_policy import require_operation, require_transport
            read.check(server.name)
            require_transport(server.name, server._config, connection=server)
            if request.method != 'POST' or str(request.url) != server._config['url']:
                raise PermissionError('Conversation read destination or method changed')
            if len(request.content) > contract['max_request_bytes']:
                raise ValueError('Conversation request exceeds the configured bound')
            body = json.loads(request.content)
            method, params = body.get('method'), body.get('params', {})
            if body.get('jsonrpc') != '2.0' or not isinstance(params, dict):
                raise ValueError('Invalid conversation protocol request')
            if method == 'tools/call':
                read.check(server.name, params.get('name'))
                require_operation(server.name, params['name'], params.get('arguments', {}),
                                  connection=server, require_run=True)
            elif method not in {'initialize', 'notifications/initialized', 'tools/list', 'ping'}:
                raise PermissionError('Unsupported conversation browser operation')
            if read.attempts >= read.max_attempts:
                raise PermissionError('Conversation read request budget exhausted')
            read.attempts += 1
            read.audit('dispatched')
            response = None
            try:
                timeout = min(contract['request_timeout_ms'] / 1000, read.deadline - time.monotonic())
                async with asyncio.timeout(timeout):
                    response = await inner.handle_async_request(request)
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise ValueError('Compressed conversation responses are not supported')
                    data = bytearray()
                    async for chunk in response.stream:
                        read.check(server.name)
                        data.extend(chunk)
                        if len(data) > contract['max_response_bytes']:
                            raise ValueError('Conversation response exceeds the configured bound')
                    read.check(server.name)
                    read.audit('received')
                    return httpx.Response(response.status_code, headers=response.headers,
                                          content=bytes(data), request=request)
            except BaseException:
                read.uncertain = True
                raise
            finally:
                if response is not None:
                    await response.aclose()

        async def aclose(self):
            await inner.aclose()

    return UserTransport()
