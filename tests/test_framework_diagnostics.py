"""Periodic evidence has exact prefixes, independent rollouts, and reserved cost."""
from copy import deepcopy

import numpy as np
import pytest

from optimization_framework.contracts.diagnostics import DerivedSeed, DiagnosticSchedule, PolicyRollout, ScheduledRecipe
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, ControlInput, StudyInput
from optimization_framework.evaluation.diagnostics import reconcile
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import ExperimentWorker, run, read_journal


def prepare(tmp_path, budget=200):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Diagnostic snapshots", compute_budget_seconds=budget, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Small grating", physics={"n_cells": 4, "fourier_order": 1})]))
    task = workspace.current_tasks(campaign["id"])[0]
    return workspace, campaign, task


def finish(workspace, trial, result=None):
    result = result or run(workspace.job_dir(trial["id"]))
    assert result["scientific_complete"], result
    trial.update(result=result, progress=result, status=result["status"], attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return result


def test_policy_milestones_preserve_training_and_charge_only_the_captured_prefix(tmp_path):
    workspace, campaign, task = prepare(tmp_path)
    schedule = DiagnosticSchedule(unit="optimizer_decisions", at_counts=[2, 4],
        recipes=[ScheduledRecipe(recipe_id="fourier_convergence:v1", parameters={"orders": [1, 2], "tolerance": .1}, wall_seconds=5)],
        rollouts=[PolicyRollout(seed=101, epsilon=0, horizon=2, wall_seconds=3),
                  PolicyRollout(seed=102, epsilon=.01, horizon=2, wall_seconds=3,
                      recipes=[ScheduledRecipe(recipe_id="reevaluate:v1", parameters={"orders": [2]}, wall_seconds=2)])])
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="dqn", seed=9, max_steps=8, wall_seconds=10,
        schedule_steps=4, completion={"unit": "optimizer_decisions", "count": 4},
        training={"horizon": 3, "batch_size": 2, "buffer_size": 16, "learning_starts": 2, "hidden_sizes": [8, 8]})
    parent = workspace.create_trial(request.model_copy(update={"diagnostics": [schedule]}))
    assert workspace.allocated_seconds(campaign["id"]) == 10 + 2 * (5 + 3 + 3 + 2)
    result = finish(workspace, parent)
    reference = workspace.create_trial(request)
    finish(workspace, reference)
    def trace(trial):
        return [(row["candidate"], row["objectives"]) for row in read_journal(workspace.job_dir(trial["id"]) / "observations.jsonl")]
    assert trace(parent) == trace(reference)
    parent_result = deepcopy(workspace.store.get(parent["id"], "trial")["result"])
    reconcile(workspace)
    grants = workspace.store.list("diagnostic_grant")
    parent_grants = [grant for grant in grants if grant["parent_trial_id"] == parent["id"]]
    assert len(parent_grants) == 2 and all(grant["status"] == "dispatched" for grant in parent_grants)
    initial_children = workspace.store.list("trial")
    reconcile(workspace)
    assert workspace.store.list("trial") == initial_children
    for grant in parent_grants:
        assets = [workspace.store.get(identity, "asset") for identity in grant["asset_ids"]]
        policy = next(asset for asset in assets if asset["kind"] == "policy")
        prefix = policy["payload"]["diagnostic"]["request_prefix_stop"]
        assert prefix == (3 if grant["count"] == 2 else 6)
        assert policy["payload"]["metadata"]["decisions"] == grant["count"]
        assert workspace.assets.attributed_costs([policy["id"]])["quantities"]["evaluation_requests"]["total"] == prefix
        for identity in grant["trial_ids"]:
            child = workspace.store.get(identity, "trial")
            child_result = finish(workspace, child)
            if child["algorithm"] == "frozen_policy":
                assert child_result["diagnostics"]["updates"] == 0
                assert child_result["diagnostics"]["decisions"] == 2 and child_result["evaluations"] == 3
                assert child["source_hash"] == parent["source_hash"]
                current = workspace.store.get(identity, "trial")
                assert workspace.assets.attributed_costs(current["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == prefix + 3
    reconcile(workspace)
    nested = [trial for trial in workspace.store.list("trial") if trial.get("parent_trial_id") not in {None, parent["id"]}]
    assert len(nested) == 2
    for child in nested:
        child_result = finish(workspace, child)
        assert child_result["recipe_result"]["kind"] == "fixed_fidelity_diagnostic"
        assert not child.get("validation_requirement_ids")  # One fidelity is not a convergence assertion.
    assert workspace.store.get(parent["id"], "trial")["result"] == parent_result
    # Final weights match the identical run without instrumentation.
    final_policies = []
    for trial in (parent, reference):
        identities = workspace.store.get(trial["id"], "trial")["latest_output_asset_ids"]
        asset = next(workspace.store.get(identity, "asset") for identity in identities if workspace.store.get(identity, "asset")["kind"] == "policy")
        from optimization_framework.contracts.experiments import ArtifactReference
        with workspace.assets.artifacts.open(ArtifactReference(**asset["artifacts"][0])) as stream, np.load(stream, allow_pickle=False) as values:
            final_policies.append({key: values[key].copy() for key in values.files})
    for key in final_policies[0]:
        np.testing.assert_array_equal(final_policies[0][key], final_policies[1][key])
    assert result["evaluations"] == 6
    from optimization_framework.analysis.general import report
    comparison = report(workspace, campaign["id"])
    assert {trial["id"] for group in comparison["groups"] for trial in group["trials"]} == {parent["id"], reference["id"]}
    assert len(comparison["excluded"]) == 8


def test_active_milestone_waits_for_cost_commit_and_never_includes_future_search(tmp_path):
    workspace, campaign, task = prepare(tmp_path)
    schedule = DiagnosticSchedule(at_counts=[2], export_optimizer=False,
        recipes=[ScheduledRecipe(recipe_id="reevaluate:v1", parameters={"orders": [2]}, wall_seconds=5)])
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="hillclimb",
        max_steps=5, wall_seconds=10, diagnostics=[schedule]))
    worker = ExperimentWorker(workspace.job_dir(trial["id"]))
    worker.one_step(); worker.one_step()
    trial.update(status="running", progress=worker.progress("running"))
    workspace.store.put("trial", trial)
    costs = workspace.job_dir(trial["id"]) / "costs.jsonl"
    committed = costs.read_bytes()
    costs.write_bytes(b"")
    reconcile(workspace)
    assert workspace.store.list("diagnostic_grant")[0]["status"] == "reserved"
    assert not workspace.store.list("asset")
    costs.write_bytes(committed)
    reconcile(workspace)
    grant = workspace.store.list("diagnostic_grant")[0]
    assert grant["status"] == "dispatched"
    snapshot = workspace.store.get(grant["asset_ids"][0], "asset")
    assert workspace.assets.attributed_costs([snapshot["id"]])["quantities"]["evaluation_requests"]["total"] == 2
    finish(workspace, trial, worker.run())
    reconcile(workspace)
    assert workspace.store.get(snapshot["id"], "asset") == snapshot
    assert workspace.assets.attributed_costs([snapshot["id"]])["quantities"]["evaluation_requests"]["total"] == 2


def test_diagnostic_grants_are_reserved_upfront_and_reacquired_on_resume(tmp_path):
    workspace, campaign, task = prepare(tmp_path, budget=20)
    schedule = DiagnosticSchedule(at_counts=[2], recipes=[ScheduledRecipe(recipe_id="reevaluate:v1", parameters={"orders": [1]}, wall_seconds=10)])
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="hillclimb", max_steps=3, wall_seconds=12, diagnostics=[schedule])
    with pytest.raises(ValueError, match="remaining campaign budget"):
        workspace.create_trial(request)
    assert not workspace.store.list("trial") and not workspace.store.list("diagnostic_grant")
    parent = workspace.create_trial(request.model_copy(update={"wall_seconds": 5}))
    assert workspace.allocated_seconds(campaign["id"]) == 15
    workspace.control(parent["id"], ControlInput(action="stop"))
    reconcile(workspace)
    assert workspace.allocated_seconds(campaign["id"]) == 0
    other = workspace.create_trial(request.model_copy(update={"diagnostics": [], "wall_seconds": 10}))
    with pytest.raises(ValueError, match="remaining campaign budget"):
        workspace.control(parent["id"], ControlInput(action="resume"))
    workspace.control(other["id"], ControlInput(action="stop"))
    workspace.control(parent["id"], ControlInput(action="resume"))
    assert workspace.allocated_seconds(campaign["id"]) == 15
    assert workspace.store.list("diagnostic_grant")[0]["status"] == "reserved"


def test_confirmation_does_not_release_before_declared_snapshots_are_cataloged(tmp_path):
    workspace, campaign, task = prepare(tmp_path)
    prototype = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="hillclimb",
        max_steps=2, wall_seconds=5, diagnostics=[DiagnosticSchedule(at_counts=[1], export_optimizer=False)]))
    study = workspace.create_study(campaign["id"], StudyInput(goal="Confirm a procedure with diagnostic evidence", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=[17]))
    protocol = study["confirmation"]["id"]
    identity = workspace.confirmations.schedule(workspace, protocol)["created_trial_ids"][0]
    finish(workspace, workspace.store.get(identity, "trial"))
    assert not workspace.confirmations.assess(protocol)["complete"]
    with pytest.raises(ValueError, match="Incomplete"):
        workspace.confirmations.release(protocol)
    reconcile(workspace)
    assert workspace.confirmations.assess(protocol)["complete"]
    assert workspace.confirmations.release(protocol)["outcome"] == "completed_roster"


def test_derived_rollout_seeds_keep_one_method_and_record_exact_child_bindings(tmp_path):
    from optimization_framework.evaluation.confirmation import method_definition
    workspace, campaign, task = prepare(tmp_path)
    derivation = DerivedSeed(offset=1_000_000_000, parent_seed_factor=10_000_000, milestone_factor=20)
    schedule = DiagnosticSchedule(unit="optimizer_decisions", at_counts=[1, 2],
        rollouts=[PolicyRollout(seed=derivation, epsilon=0, horizon=1, wall_seconds=3),
                  PolicyRollout(seed=derivation, epsilon=.01, horizon=1, wall_seconds=3)])
    parents = []
    for seed in (10, 11):
        trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="dqn", seed=seed,
            max_steps=3, schedule_steps=2, completion={"unit": "optimizer_decisions", "count": 2}, wall_seconds=5,
            training={"horizon": 3, "hidden_sizes": [4, 4], "buffer_size": 8, "batch_size": 2, "learning_starts": 5},
            diagnostics=[schedule]))
        parents.append(trial)
        finish(workspace, trial)
    assert method_definition(parents[0]) == method_definition(parents[1])
    reconcile(workspace)
    children = [trial for trial in workspace.store.list("trial") if trial.get("diagnostic_seed_binding")]
    assert len(children) == 8
    for child in children:
        binding = child["diagnostic_seed_binding"]
        assert binding["declaration"] == derivation.model_dump(mode="json")
        assert child["seed"] == binding["resolved_seed"] == 1_000_000_000 + binding["parent_seed"] * 10_000_000 + binding["milestone"] * 20 + binding["episode_index"]
        finish(workspace, child)
    before = len(workspace.store.list("trial"))
    reconcile(workspace)
    assert len(workspace.store.list("trial")) == before
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="dqn", seed=1000,
            max_steps=3, diagnostics=[schedule]))
    assert len(workspace.store.list("trial")) == before
