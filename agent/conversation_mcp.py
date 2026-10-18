"""Save visible conversation turns through an explicitly granted MCP mutation.

Runs synchronously before the owning turn closes. No worker, database mount,
extraction model, or implicit fallback to durable personal memories is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging

logger = logging.getLogger("run_agent")


def sync_turn(run, user_message, result):
    """Opt-in user/final-assistant transcript capture; report failures explicitly.

    Tool internals, reasoning, injected memory and system prompts are excluded.
    Stable command-derived IDs make the harness's turn deduplication usable.
    """
    from agent.identity_lifecycle import identity_config
    config = identity_config().get('memory', {}).get('conversation_mcp')
    if config is None or not isinstance(result, dict):
        return result
    if run.context.policy.role != 'primary' or run.context.policy.memory_backend != 'personal_mcp':
        return result
    try:
        from agent.memory_router import assert_memory_owner
        from agent.runtime_commands import assert_runtime_dispatch
        assert_memory_owner(run.context)
        run.agent._memory_manager.assert_owner()
        if assert_runtime_dispatch() is not run:
            raise PermissionError('Conversation capture must belong to the live turn')
        if (not isinstance(config, dict) or set(config) != {'server', 'append_tool', 'source', 'agent'}
                or any(not isinstance(value, str) or not value for value in config.values())):
            raise ValueError('Invalid conversation MCP configuration')
        from tools.mcp_tool_policy import policy_for
        grant = policy_for(config['server'])
        if grant is None or config['append_tool'] not in grant.get('mutation_contracts', {}):
            raise PermissionError('Conversation capture requires a granted receipt-validated mutation')
        from agent.redact import redact_sensitive_text
        timestamp = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
        if timestamp is None:
            raise ValueError('Conversation capture requires durable acceptance time')
        # Capture only explicit text. Do not serialize opaque image/audio blocks,
        # provider state, or arbitrary object representations into remote memory.
        if isinstance(user_message, list):
            user_message = '\n'.join(part['text'] for part in user_message
                if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str))
        rows = [('user', user_message), ('assistant', result.get('final_response'))]
        turns = []
        for offset, (role, content) in enumerate(rows):
            if not isinstance(content, str) or not content.strip():
                continue
            turns.append({'id': f'{run.command_id}:{role}', 'seq': int(timestamp * 1000000) * 2 + offset,
                'role': role, 'content': redact_sensitive_text(content, force=True, redact_url_credentials=True),
                'ts': datetime.fromtimestamp(timestamp, timezone.utc).isoformat()})
        if not turns:
            return {**result, 'conversation_sync': {'status': 'no_visible_text'}}
        args = {'source': config['source'], 'session_id': run.session_id,
                'agent': config['agent'], 'turns': turns}
        from tools.mcp_tool_schema import mcp_prefixed_tool_name
        from tools.registry import registry
        name = mcp_prefixed_tool_name(config['server'], config['append_tool'])
        if registry.get_entry(name) is None:
            from tools.mcp_tool_discovery import discover_mcp_tools
            discover_mcp_tools(allowed_mcp_names=[config['server']])
        response = registry.dispatch(name, args)
        if isinstance(response, str):
            response = json.loads(response)
        if response.get('effect', {}).get('state') != 'confirmed' and response.get('status') != 'confirmed':
            raise RuntimeError('Conversation append did not return a confirmed durable receipt')
        return {**result, 'conversation_sync': {'status': 'confirmed', 'session_id': run.session_id}}
    except Exception as exc:
        # Avoid logging transcript content, credentials or server error bodies.
        logger.warning('Conversation was not confirmed saved to the memory harness (%s).', type(exc).__name__)
        return {**result, 'conversation_sync': {'status': 'not_confirmed', 'error_type': type(exc).__name__}}
