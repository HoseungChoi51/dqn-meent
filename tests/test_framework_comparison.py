"""General comparisons retain raw minimization scores and incomplete runs."""
from optimization_framework.analysis.general import report
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run


def test_general_report_separates_problem_identities_and_clean_incomplete_exits(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Two problems", compute_budget_seconds=200, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Continuous", problem_id="bounded_continuous", configuration={"dimensions": 2}),
               TaskInput(name="Optical", physics={"n_cells": 4, "fourier_order": 1})]))
    tasks = workspace.current_tasks(campaign["id"])
    for task, method in zip(tasks, ["coordinate", "hillclimb"]):
        trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm=method, max_steps=4, wall_seconds=20))
        result = run(workspace.job_dir(trial["id"]))
        assert result["status"] == "completed"
        trial.update(result=result, progress=result, attempt=1, status="completed")
        workspace.store.put("trial", trial)
        workspace.capture_evidence(trial)
    result = report(workspace, campaign["id"], cost_axis="evaluation_requests")
    assert result["cost_view"] == "full_attributed_cost" and len(result["groups"]) == 2
    continuous = next(group for group in result["groups"] if group["problem"]["definition_id"] == "bounded_continuous")
    assert continuous["condition"]["primary_objective"]["direction"] == "minimize"
    assert all("efficiency" not in point for point in continuous["trials"][0]["curve"])
    assert continuous["common_observed_cost"] == 4
    assert result["actual_campaign_costs"]["quantities"]["evaluation_requests"]["total"] == 8
    row = workspace.store.get(continuous["trials"][0]["id"], "trial")
    row["result"].update(scientific_complete=False, process_exit=0, allocation_stop="wall_budget_exhausted")
    workspace.store.put("trial", row)
    revised = report(workspace, campaign["id"], cost_axis="evaluation_requests")
    row = next(trial for group in revised["groups"] for trial in group["trials"] if trial["id"] == row["id"])
    assert row["censored"] and row["process_exit"] == 0
