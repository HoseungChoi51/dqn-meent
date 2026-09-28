"""Context factoring preserves schema validation and recorded numerical settings."""
from copy import deepcopy

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from optimization_framework.campaigns.commands import CommandService
from optimization_framework.research.context import _share_command_definitions, _share_trial_settings, _inventory, trim_memory, size
from optimization_framework.research.engine import _safe_context


def validator(schema, definitions):
    registry = Registry().with_resources((key, Resource.from_contents(value, default_specification=DRAFT202012))
        for key, value in definitions.items())
    return Draft202012Validator(schema, registry=registry)


def test_shared_application_schemas_resolve_and_preserve_validation():
    original = CommandService.describe()
    context = {"application_commands": deepcopy(original)}
    _share_command_definitions(context)
    assert size(context) < size({"application_commands": original})
    corpus = [None, False, [], {}, {"unknown": True},
        {"campaign_id": "campaign", "task_id": "task", "algorithm": "random", "max_steps": 10},
        {"campaign_id": "campaign", "task_id": "task", "algorithm": "random", "max_steps": -1},
        {"goal": "Compare methods", "scope": "exploratory", "task_ids": ["task"]},
        {"goal": "Compare methods", "scope": "invented", "task_ids": ["task"]}]
    definitions = context["application_schema_definitions"]
    for operation, record in original.items():
        before = Draft202012Validator(record["payload_schema"])
        after = validator(context["application_commands"][operation]["payload_schema"], definitions)
        assert [before.is_valid(value) for value in corpus] == [after.is_valid(value) for value in corpus], operation


def test_same_definition_names_with_different_dependencies_remain_distinct():
    def schema(minimum):
        return {"type": "object", "properties": {"value": {"$ref": "#/$defs/Outer"}}, "required": ["value"],
            "$defs": {"Outer": {"$ref": "#/$defs/Inner"}, "Inner": {"type": "integer", "minimum": minimum}}}
    original = {"small": {"payload_schema": schema(1)}, "large": {"payload_schema": schema(10)}}
    context = {"application_commands": deepcopy(original)}
    _share_command_definitions(context)
    for operation, record in original.items():
        before = Draft202012Validator(record["payload_schema"])
        after = validator(context["application_commands"][operation]["payload_schema"], context["application_schema_definitions"])
        assert [before.is_valid({"value": value}) for value in [0, 1, 9, 10]] == [
            after.is_valid({"value": value}) for value in [0, 1, 9, 10]]


def test_shared_recursive_definitions_remain_resolvable():
    node = {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}},
        "required": ["next"], "additionalProperties": False}]}
    schema = {"$ref": "#/$defs/Node", "$defs": {"Node": node}}
    context = {"application_commands": {key: {"payload_schema": deepcopy(schema)} for key in ["one", "two"]}}
    _share_command_definitions(context)
    result = validator(context["application_commands"]["one"]["payload_schema"], context["application_schema_definitions"])
    assert result.is_valid({"next": {"next": None}})
    assert not result.is_valid({"next": 1})


def test_large_repeated_training_objects_keep_every_setting_and_safe_context():
    training = {"learning_starts": 64, "learning_rate": .001, "seed": 3, "details": "exact setting " * 40}
    context = {"campaign": {}, "tasks": [{"id": "task", "split": "development"}], "trials": [],
        "hypotheses": [], "active_study": {"comparison": {"trial_requests": [
            {"training": deepcopy(training), "algorithm_config": deepcopy(training)} for _ in range(6)]}}}
    original = deepcopy(context)
    _share_trial_settings(context)
    safe = _safe_context(context)
    for row in safe["active_study"]["comparison"]["trial_requests"]:
        for field in ("training", "algorithm_config"):
            pointer = row[field]["$ref"].split("/")
            assert pointer[1] == "shared_trial_settings"
            assert safe[pointer[1]][pointer[2]] == training
    assert size(context) < size(original)


def test_completed_learner_inventory_preserves_stopping_rule_instance_and_updates():
    problem = {"definition_id": "grating", "definition_version": "1", "evaluator_id": "rcwa",
        "evaluator_version": "2", "scientific_identity": "different_instance", "fidelity": {"order": 15}}
    context = {"tasks": [{"id": "task", "problem": {**problem, "scientific_identity": "current_instance"}}],
        "hypotheses": [], "trials": [{"id": "dqn", "task_id": "task", "algorithm": "dqn", "status": "completed",
            "completion": {"unit": "optimizer_decisions", "count": 128}, "max_steps": 129,
            "training": {"learning_starts": 64, "learning_rate": .001}, "problem": problem,
            "result": {"evaluations": 129, "diagnostics": {"updates": 65, "phase": "learning", "loss": .12,
                "large_trace": [1, 2, 3]}}}]}
    row = _inventory(context)["trials"][0]
    assert row["completion"] == context["trials"][0]["completion"]
    assert row["training"] == context["trials"][0]["training"]
    assert row["problem_identity"]["scientific_identity"] == "different_instance"
    assert row["problem_identity"]["evaluator_id"] == "rcwa"
    assert row["measurement"]["diagnostics"] == {"updates": 65, "phase": "learning", "loss": .12}


def test_objective_reference_is_only_used_for_exact_supplied_definition():
    objective = {"name": "efficiency", "direction": "maximize", "units": "fraction"}
    context = {"hypotheses": [], "tasks": [{"id": "task", "problem": {"primary_objective": objective}}],
        "trials": [{"id": "same", "task_id": "task", "result": {"best_objective": .8, "objective_definition": objective}},
            {"id": "different", "task_id": "task", "result": {"best_objective": .1,
                "objective_definition": {**objective, "direction": "minimize"}}}]}
    same, different = _inventory(context)["trials"]
    assert same["measurement"] == {"best_objective": .8, "objective_definition_task_reference": "task"}
    assert different["measurement"]["objective_definition"]["direction"] == "minimize"
    assert "objective_definition_task_reference" not in different["measurement"]


def test_closed_cycle_metadata_is_indexed_without_erasing_unresolved_status():
    memory = {"document": "", "retrieved_records": [], "structured": {"narrative_guidance": "Current instruction.",
        "pending_issues": [{"id": "issue", "message": "Still pending"}], "discovery": {
            "sessions": [{"id": "session", "status": "completed", "policy": {"old_objective": "old " * 1000}}],
            "recent_artifacts": [{"id": "artifact", "kind": "review", "stale": False, "title": "Old review",
                "limitations": ["old detail " * 1000]}],
            "active_tasks": [{"id": "unresolved", "status": "waiting", "brief": {"role": "critic", "stage": "review",
                "objective": "old assignment " * 1000}}]}}}
    trim_memory(memory, 1024)
    discovery = memory["structured"]["discovery"]
    assert discovery["recent_artifacts"] == [{"id": "artifact", "kind": "review", "stale": False}]
    assert discovery["active_tasks"][0]["status"] == "waiting"
    assert discovery["active_tasks"][0]["brief_record_id"] == "unresolved"
    assert "not supplied scientific findings or resolved-task claims" in discovery["history_projection"]
    assert memory["structured"]["narrative_guidance"] == "Current instruction."
    assert memory["structured"]["pending_issues"] == [{"id": "issue", "message": "Still pending"}]
