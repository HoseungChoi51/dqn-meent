"""Small real studies exercise conditional cohorts without production claims."""
from copy import deepcopy
import time

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
from optimization_framework.contracts.templates import TemplateFreezeInput
from optimization_framework.evaluation.diagnostics import reconcile as diagnostics
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import ExperimentWorker, run
from optimization_framework.analysis.studies import nominate


def template(*, prefix=True, checks=False):
    methods = {"P": {"procedure": {"algorithm": "hillclimb" if prefix else "coordinate", "max_steps": 4, "wall_seconds": 5}},
        "HC": {"procedure": {"algorithm": "hillclimb" if prefix else "random", "max_steps": 8, "wall_seconds": 5,
            "diagnostics": [{"at_counts": [2], "export_optimizer": False}] if prefix else []}},
        "selected": {"select_from": ["P"]}}
    groups = [{"id": "controls", "scope": "confirmation", "slots": ["HC"], "seeds": [100, 101], "priority": 40},
        {"id": "development", "scope": "development", "slots": ["P"], "seeds": [10], "priority": 60},
        {"id": "confirmation", "scope": "confirmation", "slots": ["P", "selected"], "seeds": [100, 101], "admission": "nomination", "priority": 80}]
    if prefix:
        methods["refine"] = {"procedure": {"algorithm": "refinement", "max_steps": 4, "wall_seconds": 5,
            "input_binding": {"group": "controls", "slot": "HC", "count": 2}}}
        groups.append({"id": "refinement", "scope": "confirmation", "slots": ["refine"], "seeds": [100, 101], "priority": 30})
    policy = {"required_recipes": ["analytic_fixtures:v1"], "validation_wall_seconds": 3} if checks else {}
    return {"id": "test-software-qualification:v1", "name": "Small software qualification", "goal": "Verify staged procedure semantics",
        "qualification_only": True, "methods": methods, "groups": groups, "selection": {"rule_id": "median_objective:v1", "parameters": {"seeds": [10]}},
        "analysis": {"rule_id": "paired_improvement:v1", "parameters": {"minimum_wins": 1}},
        "development_seconds": 60, "total_seconds": 120, "worker_seconds": 100, "max_workers": 4,
        "validation_policies": {"development": policy, "confirmation": policy}}


def prepare(tmp_path, *, prefix=True, checks=False):
    workspace = Workspace(tmp_path, max_workers=4)
    task = TaskInput(name="Small grating", physics={"n_cells": 4, "fourier_order": 1}) if prefix else TaskInput(name="Quadratic", problem_id="bounded_continuous")
    campaign = workspace.create_campaign(CampaignInput(name="Conditional research", compute_budget_seconds=100, validation_reserve_seconds=0, tasks=[task]))
    task = workspace.current_tasks(campaign["id"])[0]
    request = TemplateFreezeInput(template=template(prefix=prefix, checks=checks), task_ids=[task["id"]])
    command = Command(id="freeze_template", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="study.freeze_template", payload=request.model_dump(mode="json"))
    outcome = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == outcome
    execution = workspace.store.get(outcome["outcome"]["execution_id"], "study_execution")
    return workspace, campaign, task, execution


def finish(workspace, trial, worker=None):
    result = worker.run() if worker else run(workspace.job_dir(trial["id"]))
    assert result["scientific_complete"], result
    trial.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return trial


def drain(workspace, execution):
    for _ in range(25):
        diagnostics(workspace)
        workspace.study_executions._reconcile(execution["id"])
        queued = [trial for trial in workspace.store.list("trial") if trial["status"] == "queued"]
        for trial in queued:
            finish(workspace, trial)
        state = workspace.study_executions.assess(execution["id"])
        if state["complete"]:
            workspace.study_executions._reconcile(execution["id"])
            return state
    pytest.fail(str({"cells": [(row["slot_id"], row["status"], row["waiting_for"]) for row in state["cells"]],
        "issues": workspace.store.list("manager_issue")}))


def test_frozen_template_starts_controls_binds_exact_seed_prefix_and_coalesces_selected_p(tmp_path):
    workspace, campaign, task, execution = prepare(tmp_path)
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0
    design = workspace.store.get(execution["design_id"], "confirmation_design")
    frozen_cells = deepcopy(workspace.store.list("protocol_cell"))
    workspace.study_executions.activate(execution["id"])
    activation = workspace.store.list("study_activation")[0]
    workspace.study_executions.activate(execution["id"])
    workspace.study_executions._reconcile(execution["id"])
    assert len(workspace.store.list("trial")) == 3  # Two controls and development; selected roles remain unbound.
    assert workspace.allocated_seconds(campaign["id"]) == 100
    with pytest.raises(ValueError, match="frozen transition"):
        nominate(workspace, design["study_ids"]["development"])
    parent = next(row for row in workspace.store.list("trial") if row["seed"] == 100)
    worker = ExperimentWorker(workspace.job_dir(parent["id"]))
    worker.one_step()
    worker.one_step()
    diagnostics(workspace)
    workspace.study_executions._reconcile(execution["id"])
    child = next(row for row in workspace.store.list("trial") if row["algorithm"] == "refinement")
    assert child["seed"] == parent["seed"]
    assert workspace.store.get(parent["id"], "trial")["status"] == "queued"
    asset = workspace.store.get(child["initial_assets"][0], "asset")
    assert asset["producer_id"] == parent["id"]
    assert workspace.assets.attributed_costs([asset["id"]])["quantities"]["evaluation_requests"]["total"] == 2
    finish(workspace, parent, worker)
    # A new application instance reconstructs activation, bindings and grants.
    workspace = Workspace(tmp_path, max_workers=4)
    workspace.study_executions.activate(execution["id"])
    assert workspace.store.list("study_activation") == [activation]
    state = drain(workspace, execution)
    protocol = workspace.store.get(execution["protocol_id"], "confirmation_protocol")
    assert len(workspace.store.list("trial")) == 7  # Development, HC x2, refinement x2, P x2, no duplicate selected-P runs.
    assert len(workspace.store.list("cell_alias")) == 2
    assert workspace.store.list("protocol_cell") == frozen_cells
    refinement = [row for row in state["cells"] if row["slot_id"] == "refine"]
    assert len({row["method_id"] for row in refinement}) == 1
    assert len({workspace.store.get(row["trial_id"], "trial")["experiment_spec_hash"] for row in refinement}) == 2
    visible = {row["id"] for _, row in workspace.memory._records(campaign["id"])}
    assert parent["id"] not in visible and asset["id"] not in visible
    assert any(kind == "protected_work_status" for kind, _ in workspace.memory._records(campaign["id"]))
    assessment = workspace.confirmations.assess(protocol["id"])
    assert assessment["complete"] and len(assessment["cells"]) == 6
    release = workspace.confirmations.release(protocol["id"])
    report = workspace.store.get(release["report_id"], "confirmation_report")
    assert report["qualification_only"] and report["conclusion"]["complete"]
    workspace.confirmations.reconcile_reports()
    assert len(workspace.store.list("confirmation_report")) == 1
    assert parent["id"] in {row["id"] for _, row in workspace.memory._records(campaign["id"])}
    assert workspace.resources.assessment(campaign["id"])["allocated_seconds"] < 100


def test_required_validation_is_reserved_and_finishes_before_selection(tmp_path):
    workspace, campaign, _, execution = prepare(tmp_path, prefix=False, checks=True)
    workspace.study_executions.activate(execution["id"])
    workspace.study_executions._reconcile(execution["id"])
    first = workspace.store.list("trial")
    assert workspace.resources.assessment(campaign["id"])["grants"][0]["member_committed_seconds"] == 3*(5+3)
    for trial in first:
        finish(workspace, trial)
    workspace.study_executions._reconcile(execution["id"])
    assert not workspace.store.list("nomination")
    checks = [row for row in workspace.store.list("trial") if row.get("recipe")]
    assert len(checks) == 3 and all(row["priority"] == 60 for row in checks)
    state = drain(workspace, execution)
    assert state["complete"]
    assert len(workspace.store.list("trial")) == 10
    assert all(row["status"] == "dispatched" for row in workspace.store.list("execution_check_grant"))


def test_no_eligible_profile_at_cutoff_is_immutable_and_inconclusive(tmp_path, monkeypatch):
    workspace, _, _, execution = prepare(tmp_path, prefix=False)
    workspace.study_executions.activate(execution["id"])
    activation = workspace.store.list("study_activation")[0]
    monkeypatch.setattr(time, "time", lambda: activation["development_deadline"]+1)
    workspace.study_executions._reconcile(execution["id"])
    nomination = workspace.store.list("nomination")[0]
    assert nomination["selected_method_ids"] == []
    monkeypatch.setattr(time, "time", lambda: activation["deadline_at"]+1)
    workspace.reconcile()
    diagnostics(workspace)
    workspace.study_executions._reconcile(execution["id"])
    assert workspace.store.list("nomination") == [nomination]
    assert workspace.store.get(execution["id"], "study_execution")["status"] == "incomplete"
    release = workspace.confirmations.release(execution["protocol_id"], allow_incomplete=True, rationale="The declared deadline expired without eligible development evidence")
    report = workspace.store.get(release["report_id"], "confirmation_report")
    assert report["claim_level"] == "inconclusive" and report["conclusion"]["outcome"] == "inconclusive_execution"


def test_invalid_conditional_claim_and_overlapping_seeds_fail_before_activation(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Design checks", compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    definition = template(prefix=False)
    definition["analysis"]["parameters"]["minimum_wins"] = 3
    with pytest.raises(ValueError, match="required wins"):
        workspace.study_executions.freeze(campaign["id"], {"template": definition, "task_ids": [task["id"]]})
    definition = template(prefix=False)
    definition["groups"][1]["seeds"] = [100]
    definition["selection"]["parameters"]["seeds"] = [100]
    with pytest.raises(ValueError, match="share a seed"):
        workspace.study_executions.freeze(campaign["id"], {"template": definition, "task_ids": [task["id"]]})
    assert not workspace.store.list("confirmation_design")
    assert len(workspace.store.list("study")) == 1
    assert workspace.allocated_seconds(campaign["id"]) == 0


def test_declared_cohort_blocks_outside_exposure_and_withdraws_later_counterevidence(tmp_path, monkeypatch):
    from optimization_framework.analysis.studies import experiment_evidence
    from optimization_framework.contracts.validation import ValidationResult
    workspace, campaign, task, execution = prepare(tmp_path, prefix=False, checks=True)
    with pytest.raises(ValueError, match="frozen template cells|open frozen confirmation cohort"):
        workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="random", seed=100, max_steps=2, wall_seconds=5))
    workspace.study_executions.activate(execution["id"])
    state = drain(workspace, execution)
    row = next(row for row in state["cells"] if row["scope"] == "confirmation" and row["slot_id"] == "HC")
    trial = workspace.store.get(row["trial_id"], "trial")
    before_commit = workspace.store.committed_at(trial["id"], "trial.evidence_cataloged")-.001
    assert not experiment_evidence(workspace.store, trial, cutoff_at=before_commit)["evidence_complete"]
    protocol = execution["protocol_id"]
    released = workspace.confirmations.release(protocol)
    workspace.confirmations.reconcile_reports()
    with monkeypatch.context() as patch:
        patch.setattr(workspace.confirmations, "assess", lambda *_: pytest.fail("Unchanged evidence should not rerun archived rules"))
        workspace.confirmations.reconcile_reports()
    from optimization_framework.contracts.requests import RecipeInput
    counter = workspace.run_recipe(trial["id"], RecipeInput(recipe_id="analytic_fixtures:v1", wall_seconds=5))
    assert counter["independent_countercheck"] and not counter.get("execution_grant_id")
    assert counter["source_hash"] == trial["source_hash"]
    finish(workspace, counter)
    requirement = next(row for row in workspace.store.list("validation_requirement") if row["scope"]["parent_trial_id"] == trial["id"])
    previous = next(row for row in workspace.store.list("validation_result") if row["requirement_id"] == requirement["id"])
    result = {key: value for key, value in previous.items() if key != "content_hash"}
    result.update(id="countercheck_after_release", verdict="failed", rationale="Later independent evaluator counterexample", created_at="2000-01-01T00:00:00+00:00")
    workspace.validations.record_result(ValidationResult(**result))
    assert workspace.store.committed_at(result["id"], "validation.measured") > before_commit
    workspace.confirmations.reconcile_reports()
    reports = workspace.store.list("confirmation_report")
    assert len(reports) == 2 and reports[-1]["claim_level"] == "inconclusive"
    assert reports[-1]["supersedes_report_id"] == released["report_id"]
    assert workspace.confirmations.release(protocol) == released


def test_unseen_template_runs_declared_test_instances_without_exposing_them_to_selection(tmp_path):
    workspace = Workspace(tmp_path, max_workers=4)
    campaign = workspace.create_campaign(CampaignInput(name="Unseen staged study", compute_budget_seconds=100,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Development", problem_id="bounded_continuous"),
            TaskInput(name="Protected target", problem_id="bounded_continuous", configuration={"center": [1., 1.]}, split="test")]))
    source, target = workspace.current_tasks(campaign["id"])
    definition = template(prefix=False, checks=True)
    definition["confirmation_kind"] = "unseen_instance"
    for group in definition["groups"]:
        group["task_ids"] = [source["id"] if group["scope"] == "development" else target["id"]]
    invalid = deepcopy(definition)
    next(group for group in invalid["groups"] if group["scope"] == "development")["task_ids"] = [target["id"]]
    with pytest.raises(ValueError, match="cannot supply development"):
        workspace.study_executions.freeze(campaign["id"], {"template": invalid, "task_ids": [source["id"], target["id"]]})
    execution = workspace.study_executions.freeze(campaign["id"], {"template": definition, "task_ids": [source["id"], target["id"]]})
    workspace.study_executions.activate(execution["id"])
    state = drain(workspace, execution)
    assert state["complete"]
    nomination = workspace.store.list("nomination")[0]
    design = workspace.store.get(execution["design_id"], "confirmation_design")
    assert nomination["study_id"] == design["study_ids"]["development"]
    for cell in state["cells"]:
        trial = workspace.store.get(cell["trial_id"], "trial")
        assert trial["task_id"] == (source["id"] if cell["scope"] == "development" else target["id"])
        if cell["scope"] == "confirmation":
            assert trial["protected_cohort_id"] == execution["protocol_id"]
    assert workspace.confirmations.release(execution["protocol_id"])["assessment"]["complete"]
