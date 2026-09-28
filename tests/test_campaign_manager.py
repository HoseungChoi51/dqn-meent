import json

import pytest
from fastapi.testclient import TestClient

from dqn_meent.workspace.api import create_app
from dqn_meent.workspace.models import CampaignInput, ResearchInput
from dqn_meent.workspace.service import Workspace
from dqn_meent.workspace.store import identifier, now


def campaign(workspace):
    return workspace.create_campaign(CampaignInput(name="Weeks of research", tasks=[{"name": "Development", "physics": {"n_cells": 8, "fourier_order": 2}}]))


def test_durable_context_survives_many_messages_and_restarts(tmp_path):
    workspace = Workspace(tmp_path)
    charter = campaign(workspace)
    campaign_id = charter['id']
    original = workspace.memory.sync(campaign_id)
    edited = workspace.memory.edit(campaign_id, "## Constraints\n\nPreserve the cobalt checkpoint comparison and keep the exploration schedule fixed.", original['revision'])
    workspace.store.put('message', {'id': 'old_decision', 'campaign_id': campaign_id, 'role': 'user',
        'content': 'The cobalt comparison was deferred because its warmup needed 512 calls. Revisit after the startup experiment.', 'created_at': now()}, 'message.created')
    for i in range(150):
        workspace.store.put('message', {'id': f'message_{i}', 'campaign_id': campaign_id, 'role': 'user',
            'content': f'Routine progress discussion {i}', 'created_at': now()}, 'message.created')
    for i in range(2100):
        workspace.store.event(campaign_id, 'trial.progress', {'step': i})
    restarted = Workspace(tmp_path)
    package = restarted.memory.assemble(campaign_id, 'Why did we defer cobalt?')
    assert 'keep the exploration schedule fixed' in package['document']
    assert any(record['id'] == 'old_decision' for record in package['retrieved_records'])
    assert len(json.dumps(package).encode()) <= 96 * 1024
    root = tmp_path / 'campaigns' / campaign_id / 'manager'
    assert (root / 'context.md').exists()
    journal = [json.loads(line) for line in (root / 'journal.jsonl').read_text().splitlines()]
    assert len(journal) > 2100
    assert [e['id'] for e in journal] == sorted(e['id'] for e in journal)
    with pytest.raises(ValueError, match='changed'):
        restarted.memory.edit(campaign_id, 'Stale overwrite', edited['revision'])
    assert 'Stale overwrite' not in restarted.memory.sync(campaign_id)['guidance']


def test_locked_evidence_and_derived_notes_never_enter_memory(tmp_path):
    workspace = Workspace(tmp_path)
    cid = campaign(workspace)['id']
    workspace.store.put('task', {'id': 'locked', 'campaign_id': cid, 'split': 'test', 'name': 'Secret task', 'physics': {}})
    workspace.store.put('trial', {'id': 'hidden_result', 'campaign_id': cid, 'task_id': 'locked', 'algorithm': 'random',
        'status': 'completed', 'result': {'best_efficiency': .999888}}, 'trial.finished')
    workspace.store.put('manager_note', {'id': 'derived', 'campaign_id': cid, 'kind': 'finding',
        'content': 'SECRET_WINNER', 'source_ids': ['hidden_result']}, 'manager.note')
    workspace.memory.issue(cid, 'hidden_issue', 'SECRET_ISSUE', affected='hidden_result')
    memory = workspace.memory.assemble(cid, 'SECRET_WINNER SECRET_ISSUE')
    assert 'SECRET_' not in json.dumps(memory)
    assert 'hidden_result' not in workspace.memory.sync(cid)['source_ids']
    protected = [row for row in memory['retrieved_records'] if row.get('kind') == 'manager_issue']
    assert any('heldout work' in row.get('text', row.get('content', '')) for row in protected) or 'heldout work' in memory['document']


def test_manager_issue_is_deduplicated_and_resolved_in_one_place(tmp_path):
    workspace = Workspace(tmp_path)
    cid = campaign(workspace)['id']
    first = workspace.memory.issue(cid, 'missing', 'No implementation exists', affected='hypothesis-one')
    assert workspace.memory.issue(cid, 'missing', 'No implementation exists', affected='hypothesis-one')['id'] == first['id']
    assert len(workspace.store.list('message', cid)) == 1
    workspace.memory.resolve_issue(first['id'], 'deferred', 'Wait for the gradient evaluator')
    assert workspace.store.get(first['id'])['status'] == 'deferred'
    assert 'Wait for the gradient evaluator' in workspace.memory.sync(cid)['document']
    assert workspace.memory.issue(cid, 'missing', 'No implementation exists', affected='hypothesis-one')['status'] == 'deferred'


def test_manager_accepts_queued_messages_and_invalidates_stale_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv('GRATING_LLM_DISABLED', 'true')
    monkeypatch.setattr('optimization_framework.research.coordinator.provider_status', lambda: {'configured': True})
    app = create_app(tmp_path, start_workers=False)
    cid = campaign(app.state.workspace)['id']
    manager = app.state.manager
    monkeypatch.setattr(manager, '_thread', lambda record: None)
    first = manager.start(ResearchInput(campaign_id=cid, message='Review our current plan'))
    queued = manager.start(ResearchInput(campaign_id=cid, message='Change direction: defer implementation work'))
    assert queued['status'] == 'queued'
    first_run = manager.store.get(first['id'])
    assert first_run['guidance_revision'] < app.state.workspace.memory.state(cid)['guidance_revision']
    first_run['status'] = 'completed'
    manager.store.put('research_run', first_run)
    manager.tick()
    manager.tick()
    matching = [r for r in manager.store.list('research_run', cid) if r['manager_command_id'] == queued['id']]
    assert len(matching) == 1


def test_memory_api_edits_and_missing_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv('GRATING_LLM_DISABLED', 'true')
    with TestClient(create_app(tmp_path, start_workers=False)) as client:
        cid = campaign(client.app.state.workspace)['id']
        state = client.get('/api/state').json()
        assert not state['hypotheses']
        for number in range(3):
            supplied = client.post('/api/hypotheses', json={'campaign_id': cid, 'title': f'Researcher idea {number}',
                'algorithm': 'custom', 'mechanism': 'A proposed mechanism requiring independent implementation'})
            assert supplied.status_code in {200, 201}, supplied.text
        state = client.get('/api/state').json()
        missing = [h for h in state['hypotheses'] if h['implementation_readiness']['state'] == 'missing']
        assert len(missing) == 3
        current = state['manager_context']
        saved = client.put(f'/api/campaigns/{cid}/manager/context', json={'content': '## Guidance\nKeep the original baselines.', 'expected_revision': current['revision']})
        assert saved.status_code == 200
        assert client.put(f'/api/campaigns/{cid}/manager/context', json={'content': 'Overwrite', 'expected_revision': current['revision']}).status_code == 409
        history = client.get(f'/api/campaigns/{cid}/manager/context/history').json()
        assert len(history) >= 2


def test_migration_preserves_history_and_does_not_promote_legacy_protocol_checks(tmp_path):
    from dqn_meent.workspace.migrate_manager import backup_database, historical_digest, migrate
    workspace = Workspace(tmp_path / 'original')
    cid = campaign(workspace)['id']
    workspace.store.put('hypothesis', {'id': 'legacy', 'campaign_id': cid, 'algorithm': 'custom',
        'title': 'Historical code', 'status': 'tested', 'source': 'def propose(state): pass',
        'verification': {'passed': True}, 'executable': True}, 'hypothesis.created')
    before = historical_digest(workspace.store.path)
    backup_database(workspace.store.path, tmp_path / 'copy/workspace.sqlite3')
    report = migrate(tmp_path / 'copy')
    assert report['historical_records_unchanged'] and report['historical_digest'] == before
    assert historical_digest(workspace.store.path) == before
    row = next(h for h in report['campaigns'][0]['readiness'] if h['id'] == 'legacy')
    assert row['state'] == 'validation_required' and not row['runnable']


def test_queued_experiment_refuses_changed_numerical_environment(tmp_path, monkeypatch):
    from dqn_meent.workspace.models import TrialInput
    workspace = Workspace(tmp_path)
    charter = campaign(workspace)
    task = workspace.current_tasks(charter['id'])[0]
    trial = workspace.create_trial(TrialInput(campaign_id=charter['id'], task_id=task['id'], algorithm='random', wall_seconds=5))
    assert trial['scientific_environment']['packages']['meent']
    from optimization_framework.execution import provenance
    original = provenance._package_versions
    monkeypatch.setattr(provenance, '_package_versions', lambda imports: {**original(imports), 'meent': 'changed'})
    with pytest.raises(ValueError, match='compatible frozen execution runtime is unavailable'):
        workspace._start_trial(trial)
    assert workspace.store.get(trial['id'])['status'] == 'queued'
    assert not workspace.processes
