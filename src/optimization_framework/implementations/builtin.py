"""Auditable researcher binding of an installed method to an existing proposal."""
from typing import Any

from pydantic import Field

from optimization_framework.contracts.base import Contract, content_hash
from optimization_framework.contracts.problems import ProblemInstance
from optimization_framework.optimizers.registry import capability_reason, validate_parameters
from optimization_framework.storage.sqlite import now


class BuiltinBindingInput(Contract):
    hypothesis_id: str
    task_id: str
    algorithm: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_algorithm: str
    expected_configuration_hash: str
    reason: str = Field(min_length=1, max_length=10000)


def bind(workspace, campaign_id, payload, command_id):
    values = BuiltinBindingInput.model_validate(payload)
    hypothesis = workspace.store.get(values.hypothesis_id, 'hypothesis')
    task = workspace.store.get(values.task_id, 'task')
    if hypothesis['campaign_id'] != campaign_id or task['campaign_id'] != campaign_id:
        raise ValueError('The proposal and validation task must belong to this campaign')
    if hypothesis.get('implementation_version_id'):
        raise ValueError('This proposal already has a published executable binding')
    if (hypothesis['algorithm'] != values.expected_algorithm or
            content_hash(hypothesis.get('algorithm_config', {})) != values.expected_configuration_hash):
        raise ValueError('Proposal implementation changed; refresh the binding')
    instance = ProblemInstance(**task['problem'])
    reason = capability_reason(values.algorithm, instance)
    if reason:
        raise ValueError(reason)
    validate_parameters(values.algorithm, instance, values.parameters)
    record = {'id': 'builtin_binding_' + command_id, 'campaign_id': campaign_id, 'hypothesis_id': hypothesis['id'],
        'task_id': task['id'], 'algorithm': values.algorithm, 'parameters': values.parameters,
        'previous_algorithm': hypothesis['algorithm'], 'previous_parameters': hypothesis.get('algorithm_config', {}),
        'reason': values.reason, 'created_at': now(), 'validation_level': 'bundled_regression'}
    workspace.store.put_immutable('builtin_binding', record, 'implementation.builtin_bound')
    hypothesis.update(algorithm=values.algorithm, algorithm_config=values.parameters, builtin_binding_id=record['id'])
    workspace.store.put('hypothesis', hypothesis, 'hypothesis.implementation_bound')
    return {'binding_id': record['id'], 'hypothesis_id': hypothesis['id'], 'algorithm': values.algorithm}
