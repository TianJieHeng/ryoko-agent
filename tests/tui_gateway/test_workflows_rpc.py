"""BE11 real owned consumer: varied runs, immutable promotion and project fences."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish  # noqa: F401
from tests.agent.test_workflow_contract import workflow_record

pytestmark = pytest.mark.platforms("linux")


def create_workflow(rpc, project, *, version=1, predecessor=None, template=None, command=None):
    definition = workflow_record()
    definition.update(project_id=project, version=version, predecessor=predecessor)
    if template:
        definition['steps'][0]['parameters']['template'] = template
    row = result(rpc.call('runtime.workflow.create', command_id=command or f'create-{version}', definition_json=json.dumps(definition)))['workflow']
    return row


def baselines(rpc, project, template='# Greeting\nHello ${input.name}\n', suffix=''):
    cases = []
    for index, name in enumerate(('Ada', 'Béa', 'Cleo', 'Dee')):
        content = template.replace('${input.name}', name)
        artifact = publish(rpc, {'project_id': project, 'command_id': f'baseline-{index}{suffix}',
                                'request_id': f'baseline-{index}{suffix}', 'content': content})
        cases.append({'case_id': f'case-{index}', 'split': 'tuning' if index < 2 else 'held_out',
                      'parameters': {'name': name}, 'expected_sha256': hashlib.sha256(content.encode()).hexdigest(),
                      'generalist_ref': {key: artifact[key] for key in ('artifact_id','version','sha256')}})
    return cases


def evaluate(rpc, row, cases, command='evaluate'):
    return result(rpc.call('runtime.workflow.evaluate', command_id=command, project_id=row['project_id'],
        workflow_id=row['workflow_id'], version=row['version'], expected_revision=row['revision'], cases_json=json.dumps(cases)))


def decision(rpc, row, action='approve', command='approve', recipient=None):
    request = {key: row[key] for key in ('project_id','workflow_id','version','sha256')}
    request.update(command_id=command, action=action, expected_revision=row['revision'],
                   expected_head_revision=row['head_revision'], recipient=recipient)
    prepared = result(rpc.call('runtime.workflow.decision.prepare', **request))
    response = result(rpc.call('runtime.workflow.decision.commit', **request,
        **{key: prepared[key] for key in ('approval_id','approval_digest')}))
    return response


def ready_mission(rpc, project):
    return result(rpc.call('runtime.mission.create', mission_id='workflow-mission', contract={
        'outcome': 'Produce the requested greeting', 'project_id': project, 'risk':'low', 'uncertainty':'low',
        'acceptance': [{'criterion_id':'review', 'kind':'user_acceptance'}]}))['mission']


def run_request(row, mission, name, command):
    return {key: row[key] for key in ('project_id','workflow_id','version','sha256')} | {
        'command_id':command, 'mission_id':mission['mission_id'], 'mission_revision':mission['revision'],
        'parameters_json': json.dumps({'name': name})}


def output_bytes(rpc, project, row):
    fetched = result(rpc.call('runtime.artifact.get', project_id=project, artifact_id=row['artifact_id'], version=row['version']))
    return base64.b64decode(fetched['data_base64'])


def test_evaluated_human_promotion_parameterized_runs_and_canonical_history(artifacts):
    project = artifacts.project()['id']
    row = create_workflow(artifacts, project)
    cases = baselines(artifacts, project)
    tested = evaluate(artifacts, row, cases)
    evidence = json.loads(tested['evaluation_json'])
    assert evidence['candidate_passes'] == evidence['generalist_passes'] == 4
    assert evidence['training_performed'] is False and evidence['external_effects'] == 'none'
    approved = decision(artifacts, tested['workflow'])['workflow']
    assert approved['state'] == 'approved' and approved['sha256'] == row['sha256']
    mission = ready_mission(artifacts, project)
    for number, name in enumerate(('Emi', 'Faye')):
        request = run_request(approved, mission, name, f'run-{number}')
        prepared = result(artifacts.call('runtime.workflow.run.prepare', **request))
        assert result(artifacts.call('runtime.workflow.run.prepare', **request)) == prepared
        approvals = [{key: p[key] for key in ('approval_id','approval_digest')} for p in prepared['proposals']]
        published = result(artifacts.call('runtime.workflow.run.publish', **request, approvals=approvals))
        assert output_bytes(artifacts, project, published['outputs'][0]) == f'# Greeting\nHello {name}\n'.encode()
        assert published['mission_completed'] is False
        mission = result(artifacts.call('runtime.mission.get'))['mission']
    history = result(artifacts.call('runtime.workflow.runs', project_id=project, workflow_id=row['workflow_id'], version=1))
    saved = json.loads(history['runs_json'])
    assert len(saved) == 2 and all(item['pin']['sha256'] == row['sha256'] for item in saved)
    assert saved[0]['pin']['parameters_sha256'] != saved[1]['pin']['parameters_sha256']
    denied(artifacts.call('runtime.workflow.get', 'b', project_id=project, workflow_id=row['workflow_id'], version=1))
    assert result(artifacts.call('runtime.workflow.get', project_id=project, workflow_id=row['workflow_id'], version=1))['workflow']['sha256'] == row['sha256']


def test_draft_immutable_and_promotion_requires_current_exact_human_decision(artifacts):
    project = artifacts.project()['id']
    row = create_workflow(artifacts, project)
    denied(artifacts.call('runtime.workflow.create', command_id='edit', definition_json=row['definition_json']), 'workflow_immutable')
    result(artifacts.call('runtime.artifact.cancel', command_id='edit'))
    request = {key: row[key] for key in ('project_id','workflow_id','version','sha256')}
    request.update(command_id='premature', action='approve', expected_revision=row['revision'], expected_head_revision=0)
    denied(artifacts.call('runtime.workflow.decision.prepare', **request), 'workflow_evaluation_required')
    result(artifacts.call('runtime.artifact.cancel', command_id='premature'))
    row = evaluate(artifacts, row, baselines(artifacts, project))['workflow']
    request.update(command_id='exact', expected_revision=row['revision'])
    prepared = result(artifacts.call('runtime.workflow.decision.prepare', **request))
    denied(artifacts.call('runtime.workflow.decision.commit', **request, approval_id=prepared['approval_id'], approval_digest='0'*64), 'approval_mismatch')
    row = result(artifacts.call('runtime.workflow.decision.commit', **request,
        **{key: prepared[key] for key in ('approval_id','approval_digest')}))['workflow']
    assert row['state'] == 'approved'
    denied(artifacts.call('runtime.workflow.export', project_id=project, workflow_id=row['workflow_id'], version=1,
                         recipient='private-team', approval_id=prepared['approval_id']), 'workflow_share_grant_required')
    shared = decision(artifacts, row, 'authorize_export', 'share', 'private-team')
    grant = json.loads(shared['decision_json'])['approval_id']
    exported = result(artifacts.call('runtime.workflow.export', project_id=project, workflow_id=row['workflow_id'], version=1,
                         recipient='private-team', approval_id=grant))
    assert exported['external_send_performed'] is False
    denied(artifacts.call('runtime.workflow.export', project_id=project, workflow_id=row['workflow_id'], version=1,
                         recipient='different-team', approval_id=grant), 'workflow_share_grant_required')


def test_rollback_retains_immutable_content_and_revocation_denies_new_runs(artifacts):
    project = artifacts.project()['id']
    first = create_workflow(artifacts, project)
    cases = baselines(artifacts, project)
    first = decision(artifacts, evaluate(artifacts, first, cases)['workflow'])['workflow']
    prior = {key:first[key] for key in ('workflow_id','version','sha256')}
    second = create_workflow(artifacts, project, version=2, predecessor=prior)
    second = decision(artifacts, evaluate(artifacts, second, cases, 'eval-2')['workflow'], command='approve-2')['workflow']
    first = result(artifacts.call('runtime.workflow.get', project_id=project, workflow_id=first['workflow_id'], version=1))['workflow']
    restored = decision(artifacts, first, 'rollback', 'rollback')['workflow']
    assert restored['active_version'] == 1 and restored['definition_json'] == first['definition_json']
    revoked = decision(artifacts, restored, 'revoke', 'revoke')['workflow']
    mission = ready_mission(artifacts, project)
    denied(artifacts.call('runtime.workflow.run.prepare', **run_request(revoked, mission, 'Guy', 'blocked-run')), 'workflow_not_approved')
    assert second['version'] == 2


def test_evaluation_holdout_is_not_recycled_as_tuning(artifacts):
    project = artifacts.project()['id']
    row = create_workflow(artifacts, project)
    cases = baselines(artifacts, project)
    row = evaluate(artifacts, row, cases)['workflow']
    changed = deepcopy(cases)
    changed[0]['split'], changed[2]['split'] = changed[2]['split'], changed[0]['split']
    denied(artifacts.call('runtime.workflow.evaluate', command_id='leak', project_id=project,
        workflow_id=row['workflow_id'], version=1, expected_revision=row['revision'], cases_json=json.dumps(changed)), 'workflow_holdout_leakage')


def test_revoked_between_prepare_and_publish_stops_exact_pinned_run(artifacts, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from tui_gateway import server
    from types import SimpleNamespace
    import threading
    project = artifacts.project()['id']
    first = create_workflow(artifacts, project)
    first = decision(artifacts, evaluate(artifacts, first, baselines(artifacts, project))['workflow'])['workflow']
    mission = ready_mission(artifacts, project)
    request = run_request(first, mission, 'Hazel', 'interrupted')
    prepared = result(artifacts.call('runtime.workflow.run.prepare', **request))
    # A second owned conversation in this same profile revokes the version while
    # the first conversation awaits output review. A different profile cannot.
    config = json.loads((artifacts.homes['a'] / 'config.yaml').read_text())
    context = resolve_agent_context(config, session_id='revoking-session', profile_home=artifacts.homes['a'])
    db = artifacts.agents['a']._session_db
    db.create_session(context.identity.session_id, source='tui')
    db.claim_session_agent_identity(context.identity.session_id, context.identity.to_record())
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=context.identity.session_id)
    server._sessions['live-revoker'] = {'agent':agent, 'profile_home':str(artifacts.homes['a']), 'transport':artifacts.peers['a'],
        'session_key':agent.session_id, 'history':[], 'history_lock':threading.RLock()}
    def other_call(method, **params):
        return server.dispatch({'jsonrpc':'2.0','id':'revoke','method':method,
            'params':{'schema_version':1,'session_id':'live-revoker',**params}}, transport=artifacts.peers['a'])
    revoked = decision(SimpleNamespace(call=other_call), first, 'revoke', 'concurrent-revoke')['workflow']
    assert revoked['state'] == 'revoked'
    denied(artifacts.call('runtime.workflow.run.publish', **request,
        approvals=[{key:p[key] for key in ('approval_id','approval_digest')} for p in prepared['proposals']]), 'workflow_not_approved')
    saved = json.loads(result(artifacts.call('runtime.workflow.runs', project_id=project,
        workflow_id=first['workflow_id'], version=1))['runs_json'])[0]
    assert saved['state'] == 'paused_revoked' and saved['artifact_refs'] == []
    result(artifacts.call('runtime.artifact.cancel', command_id='interrupted'))
    denied(artifacts.call('runtime.artifact.get', project_id=project,
        artifact_id=prepared['proposals'][0]['artifact_id'], version=prepared['proposals'][0]['version']))


def test_prepared_inputs_cannot_change_and_cancel_is_visible(artifacts):
    project = artifacts.project()['id']
    row = create_workflow(artifacts, project)
    row = decision(artifacts, evaluate(artifacts, row, baselines(artifacts, project))['workflow'])['workflow']
    mission = ready_mission(artifacts, project)
    request = run_request(row, mission, 'Ivy', 'stable-pin')
    result(artifacts.call('runtime.workflow.run.prepare', **request))
    denied(artifacts.call('runtime.workflow.run.prepare', **{**request,'parameters_json':json.dumps({'name':'Changed'})}), 'idempotency_conflict')
    result(artifacts.call('runtime.artifact.cancel', command_id='stable-pin'))
    saved = json.loads(result(artifacts.call('runtime.workflow.runs', project_id=project, workflow_id=row['workflow_id'], version=1))['runs_json'])[0]
    assert saved['state'] == saved['control_status'] == 'cancelled'


def test_provenance_template_and_feedback_are_separate_from_execution_authority(artifacts):
    import time
    project = artifacts.project()['id']
    template = {'template_id':'style', 'version':1, 'project_id':project, 'style':'Concise',
                'sections':['Greeting'], 'predecessor':None}
    first_template = result(artifacts.call('runtime.workflow.template.create', command_id='style-1', definition_json=json.dumps(template)))
    definition = workflow_record()
    definition.update(project_id=project, template_ref={key:first_template[key] for key in ('template_id','version','sha256')})
    draft = result(artifacts.call('runtime.workflow.create', command_id='templated', definition_json=json.dumps(definition)))['workflow']
    template.update(version=2, style='Detailed', predecessor=definition['template_ref'])
    result(artifacts.call('runtime.workflow.template.create', command_id='style-2', definition_json=json.dumps(template)))
    assert result(artifacts.call('runtime.workflow.get', project_id=project, workflow_id='summary', version=1))['workflow']['definition_json'] == draft['definition_json']
    source = publish(artifacts, {'project_id':project,'command_id':'demo-source','request_id':'demo-source','content':'Recording notes: select a named document, not fixed coordinates'})
    source_ref = {key:source[key] for key in ('artifact_id','version','sha256')}
    consent_record = {'purpose':'workflow_extraction','project_id':project,'source_refs':[source_ref],'retention_until':time.time()+3600}
    def consent(value, command):
        return publish(artifacts, {'project_id':project,'command_id':command,'request_id':command,'mime':'application/json',
            'content_base64':base64.b64encode(json.dumps(value).encode()).decode()}, mode='bytes.')
    consent_output = consent(consent_record, 'consent')
    definition.update(workflow_id='demonstration', template_ref=None,
        provenance={'kind':'demonstration','source_refs':[source_ref],'private_derived':True,
                    'consent_ref':{key:consent_output[key] for key in ('artifact_id','version','sha256')}})
    demonstration = result(artifacts.call('runtime.workflow.create', command_id='demo', definition_json=json.dumps(definition)))['workflow']
    feedback = result(artifacts.call('runtime.workflow.feedback', command_id='feedback', project_id=project,
        workflow_id='demonstration', version=1, evidence_json=json.dumps({'kind':'correction','artifact_ref':source_ref})))
    assert json.loads(feedback['evidence_json'])['training_performed'] is False
    assert result(artifacts.call('runtime.workflow.get', project_id=project, workflow_id='demonstration', version=1))['workflow'] == demonstration
    expired = consent({**consent_record, 'retention_until':time.time()-1}, 'expired-consent')
    definition.update(workflow_id='expired-demo')
    definition['provenance']['consent_ref'] = {key:expired[key] for key in ('artifact_id','version','sha256')}
    denied(artifacts.call('runtime.workflow.create', command_id='expired', definition_json=json.dumps(definition)), 'workflow_consent_required')
    result(artifacts.call('runtime.artifact.cancel', command_id='expired'))
    definition.update(workflow_id='unaccepted', provenance={'kind':'accepted_mission','source_refs':[], 'private_derived':True,
        'reference':{'session_id':artifacts.agents['a'].session_id, 'mission_id':'missing', 'revision':1}})
    denied(artifacts.call('runtime.workflow.create', command_id='unaccepted', definition_json=json.dumps(definition)), 'workflow_mission_unaccepted')


def test_domain_workflow_reuses_installed_producer_with_bound_arguments(artifacts):
    project = artifacts.project()['id']
    definition = workflow_record()
    definition.update(project_id=project, steps=[{'step_id':'creative','kind':'domain','depends_on':[],
        'parameters':{'adapter':'creative','arguments':{'brief':{'$input':'name'},'prompts':['A blue doorway'],'continuity':['Blue door']}}}],
        output_schema={'required_sections':[], 'min_bytes':1})
    row = result(artifacts.call('runtime.workflow.create', command_id='domain-workflow', definition_json=json.dumps(definition)))['workflow']
    cases = []
    from hermes_cli.domain_media import build_creative_package
    from agent.identity_lifecycle import agent_runtime_scope
    agent = artifacts.agents['a']
    for index, name in enumerate(('Task A','Task B','Task C','Task D')):
        with agent_runtime_scope(agent.runtime_context):
            package = build_creative_package(agent.runtime_context, agent._session_db, project_id=project,
                brief=name, prompts=['A blue doorway'], continuity=['Blue door'])
        source = publish(artifacts, {'project_id':project,'command_id':f'creative-baseline-{index}',
            'request_id':f'creative-baseline-{index}','content':package['content_bytes'].decode()})
        cases.append({'case_id':str(index),'split':'tuning' if index<2 else 'held_out','parameters':{'name':name},
            'expected_sha256':source['sha256'],'generalist_ref':{key:source[key] for key in ('artifact_id','version','sha256')}})
    evaluated = evaluate(artifacts, row, cases)
    assert json.loads(evaluated['evaluation_json'])['candidate_passes'] == 4


def test_export_rechecks_revocation_after_initial_lookup(artifacts, monkeypatch):
    from hermes_state_workflows import WorkflowRegistry
    project = artifacts.project()['id']
    row = create_workflow(artifacts, project)
    row = decision(artifacts, evaluate(artifacts, row, baselines(artifacts, project))['workflow'])['workflow']
    shared = decision(artifacts, row, 'authorize_export', 'share', 'team')
    approval = json.loads(shared['decision_json'])['approval_id']
    original = WorkflowRegistry.get
    changed = False
    def revoke_after_lookup(registry, *args):
        nonlocal changed
        fetched = original(registry, *args)
        if not changed:
            changed = True
            decision(artifacts, fetched, 'revoke', 'revoke-during-export')
        return fetched
    monkeypatch.setattr(WorkflowRegistry, 'get', revoke_after_lookup)
    denied(artifacts.call('runtime.workflow.export', project_id=project, workflow_id=row['workflow_id'], version=1,
                         recipient='team', approval_id=approval), 'workflow_share_grant_required')
