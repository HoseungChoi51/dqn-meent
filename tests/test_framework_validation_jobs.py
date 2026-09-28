"""Executable validation binds a frozen observed subject and retains its verdict."""
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, RecipeInput, ControlInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run
from optimization_framework.storage.sqlite import atomic_json, now


def setup(tmp_path, *, continuous=False):
    workspace = Workspace(tmp_path)
    task = TaskInput(name="Quadratic", problem_id="bounded_continuous") if continuous else TaskInput(name="Grating", physics={"n_cells": 4, "fourier_order": 1})
    campaign = workspace.create_campaign(CampaignInput(name="Validation integration", compute_budget_seconds=300, validation_reserve_seconds=150, tasks=[task]))
    task = workspace.current_tasks(campaign["id"])[0]
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="random", max_steps=3,
        schedule_steps=10, wall_seconds=20))
    return workspace, campaign, trial


def finish(workspace, trial):
    result = run(workspace.job_dir(trial["id"]))
    trial = workspace.store.get(trial["id"], "trial")
    trial.update(status=result["status"], result=result, progress=result, attempt=trial["attempt"] + 1, finished_at=now())
    workspace.store.put("trial", trial)
    return workspace.capture_evidence(trial)


def test_requirement_keeps_the_candidate_and_prefix_when_the_source_continues(tmp_path):
    workspace, campaign, trial = setup(tmp_path)
    parent = finish(workspace, trial)
    requirement = workspace.run_recipe(parent["id"], RecipeInput(recipe_id="fourier_convergence:v1",
        parameters={"orders": [1, 2], "tolerance": .1}, wall_seconds=20), require_only=True)
    identity = requirement["requirement_ids"][0]
    snapshot = workspace.store.get(requirement["subject_asset_ids"][0], "asset")
    assert workspace.validations.assess(identity)["status"] == "pending"
    assert len(workspace.store.list("trial")) == 1
    workspace.control(parent["id"], ControlInput(action="extend", max_steps=6))
    finish(workspace, parent)
    assert workspace.assets.attributed_costs([snapshot["id"]])["quantities"]["evaluation_requests"]["total"] == 3
    validation = workspace.run_requirement(identity, wall_seconds=20)
    assert validation["recipe"]["subjects"] == [snapshot["payload"]["candidate"]]
    completed = finish(workspace, validation)
    assessment = workspace.validations.assess(identity)
    assert assessment["status"] == completed["result"]["recipe_result"]["subjects"][0]["verdict"]
    assert len(assessment["results"]) == 1
    workspace.capture_evidence(completed)
    assert len(workspace.validations.assess(identity)["results"]) == 1


def test_stopped_validation_does_not_become_a_pass(tmp_path):
    workspace, _, trial = setup(tmp_path)
    parent = finish(workspace, trial)
    validation = workspace.run_recipe(parent["id"], RecipeInput(recipe_id="fourier_convergence:v1",
        parameters={"orders": [1, 2], "tolerance": .1}, wall_seconds=20))
    atomic_json(workspace.job_dir(validation["id"]) / "control.json", {"command": "pause", "revision": 1})
    finish(workspace, validation)
    assessment = workspace.validations.assess(validation["validation_requirement_ids"][0])
    assert assessment["status"] == "inconclusive" and not assessment["measured_pass"]
    assert len(workspace.store.list("manager_issue")) == 1


def test_analytic_evaluator_recipe_runs_before_search_and_uses_same_scheduler(tmp_path):
    workspace, _, trial = setup(tmp_path, continuous=True)
    validation = workspace.run_recipe(trial["id"], RecipeInput(recipe_id="analytic_fixtures:v1", wall_seconds=20))
    assert validation["execution_contract"] == 1
    assert validation["recipe_subject_asset_ids"] == []
    completed = finish(workspace, validation)
    assessment = workspace.validations.assess(validation["validation_requirement_ids"][0])
    assert assessment["requirement"]["kind"] == "evaluator_correctness"
    assert assessment["status"] == "passed" and assessment["measured_pass"]
    assert completed["result"]["evaluations"] == len(validation["recipe"]["cases"])
