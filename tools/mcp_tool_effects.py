"""Explicit operator-authorized MCP mutations with durable, single-attempt effects.

Server hints never enable writes. Unknown results are not replayed, including in
later turns. Only the operator's local receipt schema can confirm an effect.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
import hashlib
import json

_MUTATION = ContextVar('mcp_mutation_attempt', default=None)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def validate_contracts(raw, tools, reads):
    from agent.agent_identity import IdentityPolicyError
    from agent.memory_mcp_provider import _local_validator
    from tools.mcp_tool_policy import _record
    if not isinstance(raw, Mapping) or not set(raw) <= set(tools) - set(reads):
        raise IdentityPolicyError('Mutation contracts must name explicitly granted non-read tools')
    result = {}
    for name, contract in raw.items():
        if not isinstance(contract, Mapping) or set(contract) != {'input_schema', 'result_schema', 'receipt_field'}:
            raise IdentityPolicyError('Mutation contract requires exact input/result schemas and receipt field')
        _local_validator(_record(contract['input_schema']))
        _local_validator(_record(contract['result_schema']))
        field = contract['receipt_field']
        if not isinstance(field, str) or not field or len(field) > 128:
            raise IdentityPolicyError('Mutation receipt field must be an exact bounded name')
        result[name] = dict(contract)
    return result


def validate_arguments(contract, args):
    from agent.memory_mcp_provider import _local_validator
    from tools.mcp_tool_policy import _record
    if len(_json(args).encode()) > 65536:
        raise ValueError('Mutation arguments exceed the bounded contract')
    try:
        _local_validator(_record(contract['input_schema'])).validate(args)
    except Exception:
        raise ValueError('Mutation arguments do not match the operator contract') from None


def authorize_request(server, tool, args):
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.mcp_tool_policy import policy_for, MCPUnsupported
    grant = policy_for(server)
    if grant is None or tool not in grant.get('mutation_contracts', {}):
        return
    attempt = _MUTATION.get()
    if (attempt is None or attempt['run'] is not assert_runtime_dispatch() or attempt['sent']
            or (server, tool, _digest(args)) != attempt['target']):
        raise MCPUnsupported('MCP mutation requires its exact durable single-attempt effect')
    attempt['sent'] = True


def dispatch_mutation(server, tool, args, callback):
    from agent.runtime_commands import assert_runtime_dispatch
    from agent.budget_account import actor_for
    from agent.memory_mcp_provider import _local_validator
    from tools.mcp_tool_policy import policy_for, require_operation, _record
    run = assert_runtime_dispatch()
    require_operation(server, tool, args, require_run=True)
    grant = policy_for(server)
    contract = grant['mutation_contracts'][tool]
    validate_arguments(contract, args)
    if run.budget is None or grant.get('budget') is None:
        raise PermissionError('MCP mutations require the physical-request budget adapter')
    actor, fence = actor_for(run.context), {'holder': run.holder, 'generation': run.generation}
    target = f"{grant['endpoint']}#{server}/{tool}"
    digest = _digest(args)
    # A retry in another turn cannot silently duplicate an unresolved effect.
    with run.db._runtime_read() as conn:
        pending = conn.execute('SELECT effect_id FROM runtime_effects WHERE principal_id=? AND profile_id=? '
            'AND agent_id=? AND target_ref=? AND input_digest=? AND state IN '
            "('dispatched','outcome_unknown','reconciliation_required') LIMIT 1",
            (actor['principal_id'], actor['profile_id'], actor['agent_id'], target, digest)).fetchone()
    if pending:
        return _json({'status':'outcome_unknown','error':'Unresolved matching mutation; automatic replay denied',
                      'effect_id':pending['effect_id']})
    operation = _digest([server, tool, digest])
    effect = run.db.prepare_effect(run.session_id, actor, run_id=run.run_id, operation_id=operation,
        intent_key=operation, operation_type='mcp_mutation', input_digest=digest, target_ref=target,
        policy_digest=run.context.policy.digest, policy_version=run.context.policy.policy_version,
        input_revision=grant['schema_digests'][tool], artifact_revision='none', **fence)
    dispatched = run.db.dispatch_effect(effect['effect_id'], actor, **fence)
    if not dispatched['dispatched_now']:
        return _json({'status':dispatched['state'],'effect_id':effect['effect_id'],'replayed':False})
    token = _MUTATION.set({'run':run, 'target':(server,tool,digest), 'sent':False})
    try:
        raw = callback()
        require_operation(server, tool, args, require_run=True)
        result = json.loads(raw)
        if result.get('error') or result.get('isError') or result.get('is_error'):
            raise ValueError('Remote mutation did not provide a success receipt')
        payload = result.get('structuredContent', result.get('result'))
        if isinstance(payload, str):
            payload = json.loads(payload)
        _local_validator(_record(contract['result_schema'])).validate(payload)
        reference = payload[contract['receipt_field']]
        if not isinstance(reference, str) or not 1 <= len(reference) <= 2048:
            raise ValueError('Remote mutation receipt reference is invalid')
        run.db.record_effect_outcome(effect['effect_id'], actor, state='confirmed',
            receipt={'kind':'mcp_declared_receipt', 'reference':reference, 'sha256':_digest(payload)}, **fence)
        result['effect'] = {'effect_id':effect['effect_id'], 'state':'confirmed', 'replay_permitted':False}
        return _json(result)
    except BaseException as exc:
        run.dispatch_blocked.set()
        run.db.record_effect_outcome(effect['effect_id'], actor, state='outcome_unknown',
            evidence={'kind':'mcp_mutation_uncertain', 'reason':'No validated durable success receipt; do not replay'}, **fence)
        if not isinstance(exc, Exception):
            raise
        return _json({'status':'outcome_unknown','error':'Mutation outcome is unknown; automatic replay denied',
                      'effect_id':effect['effect_id']})
    finally:
        _MUTATION.reset(token)
