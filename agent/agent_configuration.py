"""Frozen session enrollment for owner-edited agent configuration.

The configured identity policy remains the ceiling and revocation source. UI
records can narrow it, never change a role, backend, secret or MCP grant. Existing
sessions read their immutable snapshot, including after restart; updating the
head does not reload a conversation's prompt or tools. Permission narrowing and
archive advance a separate monotonic floor checked before subsequent effects.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
import uuid

from agent.agent_identity import parse_agent_identity_config, _digest, _child_policy
from dataclasses import replace
from hermes_state_workflows import require

_SELECTION = ContextVar('owned_agent_configuration_selection', default=None)
_MEMORY_TOOLS = {'memory', 'session_search'}
_LOCAL_TOOLS = _MEMORY_TOOLS | {'todo_list', 'clarify', 'tool_search', 'tool_describe', 'tool_call'}


def _configuration_json(value, maximum=16 * 1024 * 1024):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    require(len(encoded.encode('utf-8')) <= maximum, 'agent_configuration_capacity', 'Configuration snapshot exceeds capacity')
    return encoded


def base_configuration():
    from hermes_cli.config_effective import load_user_config_effective
    return load_user_config_effective(fail_closed=True)


def _owner(config):
    parsed = parse_agent_identity_config(config)
    require(parsed is not None, 'identity_required', 'Configured identities are required')
    return _digest([parsed.principal_id, parsed.profile_id]), parsed


def _base_digest(config):
    return _digest(config['agent_identity'])


def _ceiling(parsed, source):
    if source is not None:
        require(source in parsed.agents, 'agent_configuration_revoked', 'Agent ceiling is unavailable')
        return parsed.agents[source]
    require(parsed.child_policy is not None, 'agent_creation_unconfigured',
            'Creating the first specialist requires a configured child policy ceiling')
    return replace(_child_policy(parsed.agents[parsed.primary_agent_id], parsed.child_policy), role='specialist')


def _defaults(config):
    _, parsed = _owner(config)
    return {key: {'agent_id': key, 'ceiling_agent_id': key, 'revision': 1, 'archived': False,
        'config': {'name': key, 'instructions': '', 'research_allowed': True, 'memory_allowed': True,
                   'project_grants': sorted(policy.project_grants), 'default_project_id': None}}
            for key, policy in parsed.agents.items()}


def _snapshot_records(conn, base, snapshot_id):
    if snapshot_id is None:
        return _defaults(base)
    row = conn.execute('SELECT * FROM agent_configuration_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    owner, _ = _owner(base)
    require(row is not None and row['owner_key'] == owner and row['base_sha256'] == _base_digest(base),
            'agent_configuration_revoked', 'The configured identity ceiling changed')
    records = json.loads(row['records_json'])
    require(_digest({'owner': owner, 'base': row['base_sha256'], 'revision': row['revision'], 'records': records}) == snapshot_id,
            'agent_configuration_changed', 'Immutable agent configuration changed')
    return records


def _overlay(base, records, selected_agent_id):
    config = deepcopy(base)
    identities = config['agent_identity']
    _, parsed = _owner(base)
    identities['agents'] = {}
    manifests = (config.get('delegation') or {}).get('specialists') or {}
    for agent_id, record in records.items():
        if record['archived']:
            continue
        source = record['ceiling_agent_id']
        policy = _ceiling(parsed, source).to_record()
        editable = record['config']
        require(set(editable['project_grants']) <= set(policy.get('project_grants', [])),
                'agent_grant_escalation', 'Projects exceed the configured ceiling')
        policy['project_grants'] = editable['project_grants']
        tools = policy.get('allowed_tools', [])
        if not editable['research_allowed']:
            tools = [name for name in tools if name in _LOCAL_TOOLS]
        if not editable['memory_allowed']:
            tools = [name for name in tools if name not in _MEMORY_TOOLS]
        policy['allowed_tools'] = tools
        personal_servers = set(identities.get('personal_mcp_servers', []))
        denied_servers = set()
        if not editable['research_allowed']:
            denied_servers |= set(policy.get('mcp_grants', {})) - personal_servers
        if not editable['memory_allowed']:
            denied_servers |= personal_servers
            policy['secret_refs'] = [name for name in policy.get('secret_refs', [])
                                     if name not in identities.get('personal_secret_refs', [])]
        for field in ('mcp_grants', 'mcp_policies'):
            if field in policy:
                policy[field] = {name: grant for name, grant in policy[field].items() if name not in denied_servers}
        identities['agents'][agent_id] = policy
        if source in manifests and agent_id != source:
            manifests[agent_id] = deepcopy(manifests[source])
    require(selected_agent_id in identities['agents'], 'agent_unavailable', 'Selected agent is archived or missing')
    identities['active_agent_id'] = selected_agent_id
    parse_agent_identity_config(config)
    return config


def _read_session(conn, base, session_id):
    row = conn.execute('SELECT * FROM agent_configuration_sessions WHERE session_id=?', (session_id,)).fetchone()
    if row is None:
        return base, _defaults(base), None
    records = _snapshot_records(conn, base, row['snapshot_id'])
    return _overlay(base, records, row['selected_agent_id']), records, row


def frozen_configuration(base, context):
    """Live policy checks revalidate the base ceiling, then use the session pin."""
    if context is None:
        return base
    path = Path(context.profile_home) / 'state.db'
    if not path.exists():
        return base
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_configuration_sessions'").fetchone() is None:
            return base
        config, _, enrollment = _read_session(conn, base, context.identity.session_id)
        source_id = context.configuration_session_id
        if enrollment is not None or source_id is None:
            return config
        from agent.agent_identity import IdentityBinding
        source = conn.execute('SELECT model_config FROM sessions WHERE id=?', (source_id,)).fetchone()
        require(source is not None, 'identity_mismatch', 'Frozen configuration source is unavailable')
        binding = IdentityBinding.from_record(json.loads(source[0] or '{}').get('agent_identity', {}))
        require(binding.session_id == source_id and binding.config_digest == context.config_digest and
                all(getattr(binding, field) == getattr(context.identity, field)
                    for field in ('principal_id', 'profile_id', 'profile_home_digest')),
                'identity_mismatch', 'Frozen configuration source belongs to another authority')
        return _read_session(conn, base, source_id)[0]


@contextmanager
def configured_agent_selection(agent_id):
    """Trusted owned-session adapter seam; request bytes never assign authority."""
    token = _SELECTION.set(agent_id)
    try:
        yield
    finally:
        _SELECTION.reset(token)


def prepare_construction(base, db, session_id, *, parent=None, stored=None, is_child=False):
    require(db is not None or _SELECTION.get() is None, 'durable_store_required',
            'Named identity selection requires its durable session store')
    if db is None or parse_agent_identity_config(base) is None:
        return base, None
    from hermes_constants import get_hermes_home
    require(Path(db.db_path).resolve().parent == Path(get_hermes_home()).resolve(),
            'identity_mismatch', 'Session enrollment belongs to another profile store')
    if Path(db.db_path).resolve() != (Path(get_hermes_home()) / 'state.db').resolve():
        require(_SELECTION.get() is None, 'agent_configuration_store_unsupported',
                'Named managed identities require the canonical profile state store')
        # Legacy alternate stores retain their original constructor semantics;
        # they cannot advertise or persist managed configuration enrollments.
        return base, None
    owner, parsed = _owner(base)
    selected = _SELECTION.get()
    def write(conn):
        existing = conn.execute('SELECT * FROM agent_configuration_sessions WHERE session_id=?', (session_id,)).fetchone()
        if existing is not None:
            require(selected is None or selected == existing['selected_agent_id'],
                    'identity_mismatch', 'A conversation cannot change agents')
            config, records, row = _read_session(conn, base, session_id)
            return config, records.get((stored or {}).get('agent_id') or row['selected_agent_id'])
        if parent is not None and is_child:
            source = conn.execute('SELECT * FROM agent_configuration_sessions WHERE session_id=?',
                                  (parent.identity.session_id,)).fetchone()
            snapshot_id = source['snapshot_id'] if source else None
            selected_id = source['selected_agent_id'] if source else parsed.active_agent_id
        else:
            head = conn.execute('SELECT * FROM agent_configuration_heads WHERE owner_key=?', (owner,)).fetchone()
            # Preexisting unpinned sessions retain their original base configuration.
            snapshot_id = head['snapshot_id'] if head is not None and stored is None else None
            selected_id = selected or (stored or {}).get('agent_id') or parsed.active_agent_id
        records = _snapshot_records(conn, base, snapshot_id)
        config = _overlay(base, records, selected_id)
        # Existing strict conversations predating this feature must not enroll
        # current workflow knowledge on reconnect. Their prior prefix is empty.
        startup = None
        if stored is not None:
            record = records.get(stored.get('agent_id'))
            if record is not None:
                startup = _configuration_json({'revision': record['revision'],
                    'instructions': record['config']['instructions'], 'memory_allowed': record['config']['memory_allowed'],
                    'workflow_prompt': '', 'workflow_pins_json': '[]'}, 256 * 1024)
        # The construction caller creates the empty session before this transaction.
        conn.execute('INSERT INTO agent_configuration_sessions(session_id,snapshot_id,selected_agent_id,startup_json) VALUES(?,?,?,?)',
                     (session_id, snapshot_id, selected_id, startup))
        return config, records.get(selected_id)
    return db._execute_write(write)


class AgentConfigurationRegistry:
    def __init__(self, context, db, *, management_session_id=None):
        from tools.capability_broker import require_live_policy
        from agent.agent_configuration_revocation import owned_configuration_management
        self.context, self.db, self.base = context, db, base_configuration()
        self.owner, self.parsed = _owner(self.base)
        self.management_authorized = owned_configuration_management(context, db, management_session_id)
        if self.management_authorized:
            frozen = parse_agent_identity_config(frozen_configuration(self.base, context))
            require(frozen is not None and frozen.digest == context.config_digest,
                    'identity_mismatch', 'Configured identity ceiling changed')
        else:
            require(require_live_policy(require_run=False) == context, 'identity_mismatch', 'Live identity required')
        require(context.identity.principal_id == self.parsed.principal_id
                and context.identity.profile_id == self.parsed.profile_id
                and Path(db.db_path).resolve() == Path(context.profile_home) / 'state.db',
                'identity_mismatch', 'Agent configuration belongs to another profile')
        require(context.policy.role == 'primary' and context.identity.agent_id == self.parsed.primary_agent_id,
                'agent_configuration_owner_required', 'Only the configured primary owner can manage identities')

    def _current(self, conn):
        head = conn.execute('SELECT * FROM agent_configuration_heads WHERE owner_key=?', (self.owner,)).fetchone()
        return _snapshot_records(conn, self.base, head['snapshot_id'] if head else None), head

    def _public(self, record, conn):
        from agent.agent_identity import resolve_agent_context
        from agent.individual_memory_scope import IndividualMemoryScope
        policy = _ceiling(self.parsed, record['ceiling_agent_id'])
        active = _read_session(conn, self.base, self.context.identity.session_id)[1].get(record['agent_id'])
        floor = conn.execute('SELECT revoked_before_revision FROM agent_configuration_revocations WHERE owner_key=? AND agent_id=?',
                             (self.owner, record['agent_id'])).fetchone()
        revocation_revision = floor[0] if floor else 0
        namespace = None
        if policy.role == 'specialist':
            records, _ = self._current(conn)
            records = deepcopy(records)
            records[record['agent_id']]['archived'] = False
            context = resolve_agent_context(_overlay(self.base, records, record['agent_id']), session_id='namespace-preview',
                                            profile_home=self.context.profile_home)
            namespace = IndividualMemoryScope.from_context(context).namespace_id
        return {key: record[key] for key in ('agent_id', 'config', 'revision', 'archived')} | {
            'role': policy.role, 'memory_backend': policy.memory_backend, 'builtin_memory_namespace': namespace,
            'active_session_revision': active['revision'] if active else None, 'activation': 'next_session',
            'authority_revocation_revision': revocation_revision,
            'active_session_revision_revoked': active is not None and active['revision'] < revocation_revision,
            'personal_memory_mutation_supported': False}

    def list(self):
        with self.db._runtime_read() as conn:
            records, _ = self._current(conn)
            return [self._public(record, conn) for record in records.values()]

    def get_on_conn(self, conn, agent_id):
        records, _ = self._current(conn)
        require(agent_id in records, 'agent_not_found', 'Agent is not registered for this owner')
        return self._public(records[agent_id], conn)

    def get(self, agent_id):
        with self.db._runtime_read() as conn:
            return self.get_on_conn(conn, agent_id)

    def mutate(self, operation, *, agent_id=None, config=None, expected_revision=None, copy_from_agent_id=None):
        from tui_gateway import server
        from agent.runtime_commands import _RUN
        require(self.management_authorized and server._current_rpc_method.get() == 'runtime.agent.' + operation and _RUN.get() is None,
                'agent_configuration_human_control_required', 'Exact owned human configuration control required')
        def write(conn):
            records, head = self._current(conn)
            previous = records.get(agent_id)
            if operation == 'create':
                require(len(records) < 100, 'agent_capacity', 'Agent capacity reached')
                source = records.get(copy_from_agent_id) if copy_from_agent_id is not None else None
                if copy_from_agent_id is not None:
                    require(source is not None and not source['archived']
                            and _ceiling(self.parsed, source['ceiling_agent_id']).role == 'specialist',
                            'agent_copy_denied', 'Only a registered specialist ceiling may be copied')
                    # A copy cannot regain grants its source has removed.
                    require(set(config['project_grants']) <= set(source['config']['project_grants'])
                            and (not config['research_allowed'] or source['config']['research_allowed'])
                            and (not config['memory_allowed'] or source['config']['memory_allowed']),
                            'agent_grant_escalation', 'Copy exceeds the source configuration')
                else:
                    _ceiling(self.parsed, None)
                chosen = 'dot_' + uuid.uuid4().hex
                record = {'agent_id': chosen, 'ceiling_agent_id': source['ceiling_agent_id'] if source else None,
                          'revision': 1, 'archived': False, 'config': config}
            else:
                chosen = agent_id
                require(chosen in records, 'agent_not_found', 'Agent is not registered')
                record = deepcopy(records[chosen])
                require(record['revision'] == expected_revision, 'revision_conflict', 'Agent revision changed')
                require(not record['archived'], 'agent_unavailable', 'Archived identities cannot be reused')
                record['revision'] += 1
                if operation == 'archive':
                    require(chosen != self.parsed.primary_agent_id, 'agent_primary_immutable', 'The primary cannot be archived')
                    record['archived'] = True
                else:
                    record['config'] = config
            records[chosen] = record
            _overlay(self.base, records, self.parsed.primary_agent_id)
            if previous is not None:
                from agent.agent_configuration_revocation import narrows_authority, revoke_before
                if narrows_authority(previous, record):
                    revoke_before(conn, self.owner, chosen, record['revision'])
            revision = head['revision'] + 1 if head else 1
            snapshot_id = _digest({'owner': self.owner, 'base': _base_digest(self.base), 'revision': revision, 'records': records})
            conn.execute('INSERT INTO agent_configuration_snapshots VALUES(?,?,?,?,?,?)',
                         (snapshot_id, self.owner, _base_digest(self.base), revision, _configuration_json(records), time.time()))
            conn.execute('INSERT INTO agent_configuration_heads VALUES(?,?,?) ON CONFLICT(owner_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,revision=excluded.revision',
                         (self.owner, snapshot_id, revision))
            return self._public(record, conn)
        return self.db._execute_write(write)


def bind_startup_configuration(context, db, values):
    """Compose instructions once before AIAgent builds its cached prompt."""
    from tools.capability_broker import require_live_policy
    require_live_policy(require_run=False)
    if db is None:
        return
    base = base_configuration()
    sid = context.identity.session_id
    with db._runtime_read() as conn:
        _, records, session = _read_session(conn, base, sid)
        if session is None:
            return
        saved = json.loads(session['startup_json']) if session['startup_json'] else None
        record = records.get(context.identity.agent_id)
    # Ephemeral children never inherit a primary's personal instructions.
    if record is None:
        return
    if saved is None:
        from agent.workflow_delivery import snapshot_specialist_workflows
        workflows = snapshot_specialist_workflows(context, db) if context.policy.role == 'specialist' else None
        saved = {'revision': record['revision'], 'instructions': record['config']['instructions'],
                 'memory_allowed': record['config']['memory_allowed'],
                 'workflow_prompt': workflows.prompt if workflows else '',
                 'workflow_pins_json': workflows.pins_json if workflows else '[]'}
        encoded = _configuration_json(saved, 256 * 1024)
        def write(conn):
            conn.execute('UPDATE agent_configuration_sessions SET startup_json=? WHERE session_id=? AND startup_json IS NULL',
                         (encoded, sid))
            return json.loads(conn.execute('SELECT startup_json FROM agent_configuration_sessions WHERE session_id=?', (sid,)).fetchone()[0])
        saved = db._execute_write(write)
    instructions = saved['instructions'] + saved['workflow_prompt']
    if instructions:
        values['ephemeral_system_prompt'] = (values.get('ephemeral_system_prompt') or '') + '\n' + instructions
    if not saved['memory_allowed']:
        values['skip_memory'] = True
