"""Trusted stdio selection of registered stable agents and their frozen sessions.

A selector is a lookup key, never authority. Conversation references derive their
agent from the persisted binding after proving the configured human owner, home
and profile. The normal identity resolver remains the sole policy constructor.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from agent.agent_identity import IdentityBinding, resolve_agent_context
from agent.agent_configuration import _owner, _snapshot_records, _overlay, _read_session, _base_digest, base_configuration
from hermes_state_workflows import require


def _store(owner, db):
    require(Path(db.db_path).resolve() == Path(owner.profile_home) / 'state.db',
            'identity_mismatch', 'Conversation identity belongs to another profile store')
    owner.validate_profile_home()


def _select_authority(owner, agent_id):
    require(owner.identity.lifecycle == 'stable' and
            (owner.policy.role == 'primary' or owner.identity.agent_id == agent_id),
            'agent_selection_denied', 'Only the configured primary owner can select another stable agent')


def selected_conversation_scope(owner, base, db, agent_id, *, session_id):
    """Return a read-only agent scope and an enrollment proposal, without writes."""
    _store(owner, db)
    owner_key, parsed = _owner(base)
    selected = agent_id or owner.identity.agent_id
    _select_authority(owner, selected)
    require((owner.identity.principal_id, owner.identity.profile_id) == (parsed.principal_id, parsed.profile_id),
            'identity_mismatch', 'Conversation owner changed')
    with db._runtime_read() as conn:
        head = conn.execute('SELECT snapshot_id FROM agent_configuration_heads WHERE owner_key=?', (owner_key,)).fetchone()
        snapshot_id = head['snapshot_id'] if head else None
        records = _snapshot_records(conn, base, snapshot_id)
    require(selected in records, 'agent_not_found', 'Agent is not registered for this owner')
    # Read-only list and receipt recovery remain possible after archive. The
    # atomic create path separately rejects fresh enrollment of archived agents.
    readable = deepcopy(records)
    readable[selected]['archived'] = False
    selected_config = _overlay(base, readable, selected)
    context = resolve_agent_context(selected_config, session_id=session_id, profile_home=owner.profile_home)
    enrollment = {'snapshot_id': snapshot_id, 'selected_agent_id': selected, 'base_sha256': _base_digest(base)}
    return context, enrollment


def validate_conversation_enrollment(conn, context, enrollment):
    """CAS the exact configuration inside the conversation creation transaction."""
    base = base_configuration()
    owner_key, _ = _owner(base)
    require(enrollment['base_sha256'] == _base_digest(base), 'agent_configuration_revoked',
            'The configured identity ceiling changed before enrollment')
    head = conn.execute('SELECT snapshot_id FROM agent_configuration_heads WHERE owner_key=?', (owner_key,)).fetchone()
    current = head['snapshot_id'] if head else None
    require(current == enrollment['snapshot_id'], 'revision_conflict', 'Agent configuration changed before enrollment')
    records = _snapshot_records(conn, base, current)
    selected = enrollment['selected_agent_id']
    require(selected in records and not records[selected]['archived'], 'agent_unavailable',
            'Selected agent is archived or missing')
    expected = resolve_agent_context(_overlay(base, records, selected), session_id=context.identity.session_id,
                                     profile_home=context.profile_home)
    require(expected == context, 'identity_mismatch', 'Selected conversation authority changed')


def recorded_conversation_scope(owner, base, db, conversation_id):
    """Authorize metadata before resolving any recorded specialist authority."""
    _store(owner, db)
    with db._runtime_read() as conn:
        row = conn.execute('SELECT binding_json FROM runtime_conversations WHERE conversation_id=?', (conversation_id,)).fetchone()
        require(row is not None, 'session_not_found', 'Conversation not found')
        try:
            binding = IdentityBinding.from_record(json.loads(row['binding_json']))
        except (ValueError, TypeError) as exc:
            from hermes_state_runtime import RuntimeStoreError
            raise RuntimeStoreError('identity_mismatch', 'Stored conversation identity is invalid') from exc
        require(binding.lifecycle == 'stable' and binding.session_id == conversation_id and
                all(getattr(binding, key) == getattr(owner.identity, key)
                    for key in ('principal_id', 'profile_id', 'profile_home_digest')),
                'session_not_found', 'Conversation not found')
        _select_authority(owner, binding.agent_id)
        frozen, _, enrollment = _read_session(conn, base, conversation_id)
        if enrollment is None:
            # A legacy bound session selected its configured ID before this API
            # existed. Changing the launch default must not relabel its owner.
            frozen = deepcopy(base)
            frozen['agent_identity']['active_agent_id'] = binding.agent_id
    context = resolve_agent_context(frozen, session_id=conversation_id, profile_home=owner.profile_home,
                                     stored_binding=binding.to_record())
    return context, frozen


def recorded_owner_scope(db, *, owner_binding, session_id, profile_home):
    """Resolve an off-turn durable owner from its original enrollment, never the head.

    Schedule and notice records carry a trusted source identity. Temporary finite
    occurrence sessions borrow that source's immutable configuration, so later
    broker checks still enforce its monotonic revocation floor after restart.
    """
    from dataclasses import replace
    from agent.agent_identity import resolve_owned_agent_context
    from agent.agent_configuration_revocation import assert_managed_authority_current
    from agent.runtime_context import canonical_profile_home
    from hermes_constants import get_hermes_home

    home = canonical_profile_home(profile_home)
    require(Path(db.db_path).resolve() == Path(home) / 'state.db' and
            canonical_profile_home(get_hermes_home()) == home,
            'identity_mismatch', 'Recorded owner belongs to another profile store')
    owner = IdentityBinding.from_record(owner_binding)
    base = base_configuration()
    with db._runtime_read() as conn:
        source = conn.execute('SELECT model_config FROM sessions WHERE id=?', (owner.session_id,)).fetchone()
        require(source is not None and json.loads(source[0] or '{}').get('agent_identity') == owner.to_record(),
                'identity_mismatch', 'Recorded owner source identity changed or is unavailable')
        frozen, _, enrollment = _read_session(conn, base, owner.session_id)
        target = conn.execute('SELECT model_config FROM sessions WHERE id=?', (session_id,)).fetchone()
        target_binding = json.loads(target[0] or '{}').get('agent_identity') if target else None
    context = resolve_owned_agent_context(frozen, owner_binding=owner.to_record(), session_id=session_id,
                                          profile_home=home)
    if enrollment is not None:
        require(context.config_digest == owner.config_digest,
                'identity_mismatch', 'Recorded managed configuration differs from its original binding')
        require(target_binding is None or target_binding == context.identity.to_record(),
                'identity_mismatch', 'Scheduled destination belongs to another frozen identity')
        if session_id != owner.session_id:
            context = replace(context, configuration_session_id=owner.session_id)
    assert_managed_authority_current(context, base)
    return context
