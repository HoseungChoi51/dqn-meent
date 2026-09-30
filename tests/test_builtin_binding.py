"""Binding installed code preserves proposal identity, review, and old settings."""
from copy import deepcopy

import pytest

from optimization_framework.contracts.base import content_hash
from test_framework_discovery import setup, command
from framework_fixtures import researcher_idea


def test_binding_is_idempotent_and_preserves_scientific_proposal(setup):
    workspace, campaign = setup
    h = researcher_idea(workspace, campaign['id'])
    h.update(algorithm='custom', algorithm_config={'design_note': 'original specification'},
             parent_ids=['parent_a', 'parent_b'], reviews=[{'text': 'Independent objection'}])
    workspace.store.put('hypothesis', h)
    before = deepcopy(h)
    task = workspace.current_tasks(campaign['id'])[0]
    payload = {'hypothesis_id': h['id'], 'task_id': task['id'], 'algorithm': 'coordinate', 'parameters': {'radius': .1},
        'expected_algorithm': 'custom', 'expected_configuration_hash': content_hash(h['algorithm_config']),
        'reason': 'Connect the reviewed installed implementation of the declared procedure.'}
    cmd = command(workspace, campaign, 'implementation.bind_builtin', payload, 'bind_coordinate')
    receipt = workspace.commands.execute(cmd)
    assert workspace.commands.execute(cmd) == receipt
    after = workspace.store.get(h['id'])
    changed_fields = {'algorithm','algorithm_config','builtin_binding_id','executable','implementation_status'}
    assert {k:v for k,v in after.items() if k not in changed_fields} == {
        k:v for k,v in before.items() if k not in changed_fields}
    assert after['algorithm'] == 'coordinate' and after['algorithm_config'] == {'radius': .1}
    assert after['executable'] is True and after['implementation_status'] == 'builtin'
    record = workspace.store.get(receipt['outcome']['binding_id'], 'builtin_binding')
    assert record['previous_algorithm'] == 'custom' and record['previous_parameters'] == before['algorithm_config']
    assert workspace.implementations.readiness(after)['runnable']
    with pytest.raises(ValueError, match='changed'):
        workspace.commands.execute(command(workspace, campaign, 'implementation.bind_builtin', payload, 'stale_binding'))


def test_binding_rejects_an_incompatible_installed_method(setup):
    workspace, campaign = setup
    h = researcher_idea(workspace, campaign['id'])
    task = workspace.current_tasks(campaign['id'])[0]
    payload = {'hypothesis_id': h['id'], 'task_id': task['id'], 'algorithm': 'flrl_ppo', 'parameters': {},
        'expected_algorithm': h['algorithm'], 'expected_configuration_hash': content_hash(h.get('algorithm_config', {})),
        'reason': 'This incompatible binding must fail.'}
    with pytest.raises(ValueError, match='2D MEENT'):
        workspace.commands.execute(command(workspace, campaign, 'implementation.bind_builtin', payload, 'invalid_binding'))
    assert not workspace.store.list('builtin_binding')
