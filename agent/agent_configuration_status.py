"""Read-only per-session activation projection, without returning instructions."""
from __future__ import annotations

import json
from pathlib import Path

from agent.agent_configuration import base_configuration, _owner, _read_session, _snapshot_records
from hermes_state_workflows import digest, require


def _workflow_pin(conn, context, pin):
    fields = ('project_id', 'workflow_id', 'version', 'sha256', 'delivery_revision')
    key = digest({'principal_id': context.identity.principal_id, 'profile_id': context.identity.profile_id,
                  'project_id': pin['project_id'], 'workflow_id': pin['workflow_id']})
    row = conn.execute('SELECT state,sha256 FROM workflow_versions WHERE workflow_key=? AND version=?',
                       (key, pin['version'])).fetchone()
    return {field: pin[field] for field in fields} | {
        'workflow_state': row['state'] if row and row['sha256'] == pin['sha256'] else 'unavailable'}


def session_configuration_status(context, db):
    from agent.agent_configuration_revocation import assert_managed_authority_current
    from hermes_state_workflow_delivery import checked_delivery
    from tools.capability_broker import CapabilityDenied

    require(Path(db.db_path).resolve() == Path(context.profile_home) / 'state.db',
            'identity_mismatch', 'Session configuration belongs to another profile store')
    base = base_configuration()
    owner, _ = _owner(base)
    with db._runtime_read() as conn:
        frozen, records, enrollment = _read_session(conn, base, context.identity.session_id)
        from agent.agent_identity import parse_agent_identity_config
        require(parse_agent_identity_config(frozen).digest == context.config_digest,
                'identity_mismatch', 'Frozen session authority changed')
        active = records.get(context.identity.agent_id)
        head = conn.execute('SELECT snapshot_id FROM agent_configuration_heads WHERE owner_key=?', (owner,)).fetchone()
        desired = _snapshot_records(conn, base, head['snapshot_id'] if head else None).get(context.identity.agent_id)
        startup = json.loads(enrollment['startup_json']) if enrollment and enrollment['startup_json'] else None
        active_pins = json.loads(startup['workflow_pins_json']) if startup else []
        floor = conn.execute('SELECT revoked_before_revision FROM agent_configuration_revocations WHERE owner_key=? AND agent_id=?',
                             (owner, context.identity.agent_id)).fetchone()
        deliveries = conn.execute('SELECT d.*,h.revision AS head_revision FROM workflow_delivery_heads h '
            'JOIN workflow_deliveries d ON d.delivery_id=h.delivery_id WHERE d.principal_id=? AND d.profile_id=? '
            'AND d.specialist_id=? ORDER BY d.project_id,d.workflow_key',
            (context.identity.principal_id, context.identity.profile_id, context.identity.agent_id)).fetchall()
        desired_pins = [checked_delivery(conn, context, db, row)['delivery'] for row in deliveries]
        result = {'agent_id': context.identity.agent_id, 'role': context.policy.role,
            'memory_backend': context.policy.memory_backend,
            'active_configuration_revision': active['revision'] if active else None,
            'desired_configuration_revision': desired['revision'] if desired else None,
            'archived': desired['archived'] if desired else False,
            'authority_revocation_revision': floor[0] if floor else 0,
            'startup_frozen': startup is not None,
            'active_workflows': [_workflow_pin(conn, context, pin) for pin in active_pins],
            'desired_workflows': [_workflow_pin(conn, context, pin) for pin in desired_pins],
            'activation': 'next_session', 'execution_authority': False,
            'authority_current': True, 'revocation_code': None}
    try:
        assert_managed_authority_current(context, base)
    except CapabilityDenied as exc:
        result.update(authority_current=False, revocation_code=exc.code)
    return result
