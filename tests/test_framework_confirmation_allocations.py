"""Finalist budgets change before freezing, independently of exploratory runs."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, StudyInput, TaskInput, TrialInput
from optimization_framework.evaluation.confirmation import method_definition
from optimization_framework.evaluation.confirmation_allocations import allocated_method
from optimization_framework.execution.service import Workspace


def prepare(tmp_path, *, budget=200):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Longer finalist training", compute_budget_seconds=budget,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    prototype = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
        algorithm="coordinate", max_steps=3, wall_seconds=5))
    return workspace, campaign, workspace.store.get(prototype["id"], "trial")


def freeze(workspace, campaign, prototype, allocation=None, **extra):
    values = {"goal": "Allocate final runs before freezing", "scope": "confirmation",
        "confirmation_kind": "seed_replication", "prototype_trial_ids": [prototype["id"]], "seeds": [17, 18], **extra}
    if allocation is not None:
        values["prototype_allocations"] = {prototype["id"]: {"expected_control_revision": prototype["control_revision"], **allocation}}
    return workspace.create_study(campaign["id"], StudyInput(**values))


def test_larger_per_method_allocations_freeze_and_schedule_without_changing_prototypes(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    other = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=prototype["task_id"],
        algorithm="coordinate", seed=2, max_steps=4, wall_seconds=4))
    other = workspace.store.get(other["id"], "trial")
    before = deepcopy(workspace.store.list("trial"))
    study = freeze(workspace, campaign, prototype, {"max_steps": 12, "wall_seconds": 20, "schedule_steps": 10},
        prototype_trial_ids=[prototype["id"], other["id"]])
    protocol = workspace.store.get(study["confirmation"]["id"], "confirmation_protocol")
    method_id = next(key for key, value in protocol["prototypes"].items() if value == prototype["id"])
    assert method_id != content_hash(method_definition(prototype))
    method = protocol["methods"][method_id]
    assert (method["max_steps"], method["wall_seconds"], method["schedule_steps"]) == (12, 20, 10)
    assert method["completion"] == {"unit": "evaluation_requests", "count": 12}
    assert workspace.store.list("trial") == before
    binding = workspace.store.list("confirmation_allocation_binding")[0]
    assert binding["methods"][method_id]["source_procedure"] == method_definition(prototype)
    with pytest.raises(ValueError, match="immutable"):
        workspace.store.put("confirmation_allocation_binding", {**binding, "methods": {}})
    created = workspace.confirmations.schedule(workspace, protocol["id"])["created_trial_ids"]
    assert len(created) == 4
    for identity in created:
        trial = workspace.store.get(identity, "trial")
        source = prototype if trial["source_prototype_id"] == prototype["id"] else other
        expected = method if source == prototype else method_definition(other)
        assert method_definition(trial) == expected
        assert trial["source_hash"] == source["source_hash"]
        assert trial["experiment_spec"]["extension_policy"] == "forbidden"
        assert trial["completion"] == expected["completion"]
    assert workspace.confirmations.schedule(workspace, protocol["id"])["created_trial_ids"] == []
    assert workspace.store.get(prototype["id"], "trial") == prototype


@pytest.mark.parametrize("field,value", [
    ("max_steps", 0), ("max_steps", 10000001), ("max_steps", 1.5), ("max_steps", True),
    ("wall_seconds", 0), ("wall_seconds", float("inf")), ("wall_seconds", float("nan")),
    ("wall_seconds", True), ("wall_seconds", 86401), ("schedule_steps", -1),
    ("completion_count", 0), ("completion_count", 2.5), ("expected_control_revision", True),
    ("algorithm", "dqn"), ("training", {"learning_rate": 0.1}), ("completion_unit", "optimizer_decisions"),
])
def test_allocation_contract_rejects_invalid_caps_and_scientific_parameter_changes(field, value):
    with pytest.raises(ValidationError):
        StudyInput(goal="Invalid final allocation", scope="confirmation",
            prototype_allocations={"trial": {"expected_control_revision": 0, field: value}})


def test_allocation_requires_confirmation_and_explicit_source_revision():
    with pytest.raises(ValidationError, match="confirmation study"):
        StudyInput(goal="Wrong scope", prototype_allocations={"trial": {"expected_control_revision": 0, "max_steps": 10}})
    with pytest.raises(ValidationError, match="expected_control_revision"):
        StudyInput(goal="Missing source revision", scope="confirmation", prototype_allocations={"trial": {"max_steps": 10}})


def test_unknown_cross_campaign_and_stale_source_allocations_cannot_freeze(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    with pytest.raises(ValueError, match="selected prototype trials"):
        freeze(workspace, campaign, prototype, prototype_allocations={"other_trial": {"expected_control_revision": 0, "max_steps": 10}})
    with pytest.raises(ValueError, match="allocation changed"):
        freeze(workspace, campaign, prototype, {"expected_control_revision": 1, "max_steps": 10})
    other = workspace.create_campaign(CampaignInput(name="Other campaign", validation_reserve_seconds=0,
        tasks=[TaskInput(name="Other quadratic", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="this campaign"):
        freeze(workspace, other, prototype, {"max_steps": 10})
    assert not workspace.store.list("confirmation_protocol")
    assert not workspace.store.list("confirmation_allocation_binding")


def test_source_changes_after_freeze_cannot_substitute_a_new_procedure(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    study = freeze(workspace, campaign, prototype, {"max_steps": 12, "wall_seconds": 20})
    workspace.store.put("trial", {**prototype, "wall_seconds": 6, "control_revision": 1})
    with pytest.raises(ValueError, match="frozen source prototype changed"):
        workspace.confirmations.schedule(workspace, study["confirmation"]["id"])
    assert len(workspace.store.list("trial")) == 1


def test_main_roster_budget_failure_rolls_back_all_cells(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path, budget=24)
    study = freeze(workspace, campaign, prototype, {"max_steps": 12, "wall_seconds": 10})
    with pytest.raises(ValueError, match="remaining campaign budget"):
        workspace.confirmations.schedule(workspace, study["confirmation"]["id"])
    assert [trial["id"] for trial in workspace.store.list("trial")] == [prototype["id"]]


def test_shortlist_pins_original_source_while_final_allocation_has_its_own_identity(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    prototype.update(status="completed", result={"scientific_complete": True, "best_objective": 0.1})
    workspace.store.put("trial", prototype)
    receipt = workspace.commands.execute(Command(id="select_longer_finalist", campaign_id=campaign["id"],
        expected_revision=campaign["version"], operation="finalist.set",
        payload={"study_id": campaign["active_study_id"], "trial_ids": [prototype["id"]]}))
    shortlist = workspace.store.get(receipt["outcome"]["finalist_selection_id"], "finalist_selection")
    study = freeze(workspace, campaign, prototype, {"max_steps": 30, "wall_seconds": 30},
        finalist_selection_id=shortlist["id"], finalist_selection_revision=shortlist["revision"])
    original_id = content_hash(method_definition(prototype))
    assert original_id not in study["confirmation"]["methods"]
    assert workspace.store.list("finalist_confirmation_binding")[0]["prototype_procedure_ids"] == {prototype["id"]: original_id}
    assert workspace.store.get(shortlist["id"]) == shortlist
    assert workspace.store.get(prototype["id"]) == prototype


def test_frozen_nomination_disallows_changed_allocations_but_accepts_noop(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    method = method_definition(prototype)
    identity = content_hash(method)
    nomination = workspace.store.put_immutable("nomination", {"id": "nomination_fixed", "campaign_id": campaign["id"],
        "methods": {identity: method}, "prototypes": {identity: prototype["id"]}})
    with pytest.raises(ValueError, match="frozen nomination fixes"):
        freeze(workspace, campaign, prototype, {"max_steps": 12}, nomination_id=nomination["id"])
    study = freeze(workspace, campaign, prototype, {"max_steps": 3, "wall_seconds": 5}, nomination_id=nomination["id"])
    assert study["confirmation"]["methods"] == nomination["methods"]
    assert len(workspace.confirmations.schedule(workspace, study["confirmation"]["id"])["created_trial_ids"]) == 2


def test_seed_replicas_deduplicate_matching_allocations_and_reject_conflicts(tmp_path):
    workspace, campaign, prototype = prepare(tmp_path)
    replica = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=prototype["task_id"],
        algorithm="coordinate", seed=1, max_steps=3, wall_seconds=5))
    allocations = {trial["id"]: {"expected_control_revision": 0, "max_steps": 12} for trial in (prototype, replica)}
    with pytest.raises(ValueError, match="conflicting final allocations"):
        freeze(workspace, campaign, prototype, prototype_trial_ids=[prototype["id"], replica["id"]],
            prototype_allocations={**allocations, replica["id"]: {"expected_control_revision": 0, "max_steps": 14}})
    study = freeze(workspace, campaign, prototype, prototype_trial_ids=[prototype["id"], replica["id"]],
        prototype_allocations=allocations)
    assert len(study["confirmation"]["methods"]) == 1
    assert len(workspace.confirmations.schedule(workspace, study["confirmation"]["id"])["created_trial_ids"]) == 2


def test_completion_units_schedule_and_nonallocation_fields_are_preserved():
    from optimization_framework.contracts.requests import PrototypeAllocationInput
    source = {"algorithm": "dqn", "algorithm_config": {"epsilon": 0.1}, "training": {"total_steps": 3},
        "max_steps": 3, "wall_seconds": 5, "schedule_steps": 3,
        "completion": {"unit": "optimizer_decisions", "count": 2}, "initial_assets": ["policy"],
        "diagnostics": [{"unit": "optimizer_decisions", "at_counts": [2]}]}
    original = deepcopy(source)
    allocated, _ = allocated_method(source, PrototypeAllocationInput(expected_control_revision=0, max_steps=12))
    assert allocated["completion"] == source["completion"]
    assert allocated["schedule_steps"] == 3
    assert allocated["training"] == source["training"]
    assert allocated["initial_assets"] == source["initial_assets"]
    with pytest.raises(ValueError, match="diagnostic milestones"):
        allocated_method(source, PrototypeAllocationInput(expected_control_revision=0, completion_count=1))
    source["completion"]["unit"] = "evaluation_requests"
    with pytest.raises(ValueError, match="completion target exceeds"):
        allocated_method(source, PrototypeAllocationInput(expected_control_revision=0, max_steps=4, completion_count=5))
    source["completion"]["unit"] = "optimizer_decisions"
    assert source == original
