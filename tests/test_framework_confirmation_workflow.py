"""A frozen roster executes captured code and requires its declared evidence."""
from concurrent.futures import ThreadPoolExecutor
import shutil

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, StudyInput, RecipeInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run


def prepare(tmp_path, *, budget=500, validation=None, seeds=(17, 18)):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Frozen workflow", compute_budget_seconds=budget,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    prototype = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate",
        max_steps=3, wall_seconds=5))
    study = workspace.create_study(campaign["id"], StudyInput(goal="Fixed seed replication", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=list(seeds),
        validation_policy=validation or {}))
    return workspace, workspace.store.get(campaign["id"], "campaign"), prototype, study["confirmation"]["id"]


def execute(workspace, identity):
    trial = workspace.store.get(identity, "trial")
    result = run(workspace.job_dir(identity))
    assert result["scientific_complete"], result
    trial.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return result


def test_roster_retries_use_prototype_source_after_workspace_code_changes(tmp_path, monkeypatch):
    workspace, campaign, prototype, protocol = prepare(tmp_path)
    from optimization_framework.execution import source
    changed_root = tmp_path / "new_installation"
    shutil.copytree(workspace.job_dir(prototype["id"]) / "code", changed_root)
    changed_file = changed_root / "optimization_framework/execution/worker.py"
    changed_file.write_text(changed_file.read_text() + "\n# A later workspace installation\n")
    monkeypatch.setattr(source, "source_root", lambda: changed_root)
    assert source.scientific_hash() != prototype["scientific_source_hash"]
    # Any attempt to substitute the current workspace source fails the test.
    monkeypatch.setattr(workspace, "_snapshot_code", lambda _: pytest.fail("Confirmation must copy captured code"))
    command = Command(id="allocate_roster", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="confirmation.schedule", payload={"protocol_id": protocol})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: workspace.commands.execute(command), range(2)))
    assert outcomes[0] == outcomes[1]
    identities = outcomes[0]["outcome"]["created_trial_ids"]
    assert len(identities) == 2
    assert workspace.confirmations.schedule(workspace, protocol)["existing_trial_ids"] == identities
    for identity in identities:
        trial = workspace.store.get(identity, "trial")
        assert trial["source_hash"] == prototype["source_hash"]
        assert trial["scientific_source_hash"] == prototype["scientific_source_hash"]
        assert trial["source_prototype_id"] == prototype["id"]
        execute(workspace, identity)
    release = workspace.confirmations.release(protocol)
    assert release["outcome"] == "completed_roster"
    with pytest.raises(ValueError, match="closed"):
        workspace.confirmations.schedule(workspace, protocol)


def test_roster_budget_failure_rolls_back_all_new_cells(tmp_path):
    workspace, campaign, prototype, protocol = prepare(tmp_path, budget=12)
    with pytest.raises(ValueError, match="remaining campaign budget"):
        workspace.confirmations.schedule(workspace, protocol)
    assert [trial["id"] for trial in workspace.store.list("trial")] == [prototype["id"]]
    assert len(workspace.store.list("experiment_spec")) == 1
    assert workspace.allocated_seconds(campaign["id"]) == 5


def test_changed_prototype_files_cannot_be_scheduled(tmp_path):
    workspace, _, prototype, protocol = prepare(tmp_path)
    path = workspace.job_dir(prototype["id"]) / "code/optimization_framework/execution/worker.py"
    path.write_text(path.read_text() + "\n# accidental modification\n")
    with pytest.raises(ValueError, match="prototype source|source archive"):
        workspace.confirmations.schedule(workspace, protocol)
    assert len(workspace.store.list("trial")) == 1


def test_required_checks_bind_frozen_parameters_and_do_not_duplicate_jobs(tmp_path):
    workspace, _, _, protocol = prepare(tmp_path, seeds=(17,), validation={
        "required_recipes": ["analytic_fixtures:v1"], "validation_wall_seconds": 5,
        "required_recipe_parameters": {"analytic_fixtures:v1": {"tolerance": 1e-13}}})
    identity = workspace.confirmations.schedule(workspace, protocol)["created_trial_ids"][0]
    execute(workspace, identity)
    # Passing a different, looser check does not satisfy the frozen requirement.
    other = workspace.run_recipe(identity, RecipeInput(recipe_id="analytic_fixtures:v1", parameters={"tolerance": 1e-8}, wall_seconds=5))
    execute(workspace, other["id"])
    assert not workspace.confirmations.assess(protocol)["complete"]
    with pytest.raises(ValueError, match="Incomplete"):
        workspace.confirmations.release(protocol)
    checks = workspace.confirmations.schedule_checks(workspace, protocol)
    assert len(checks["created_trial_ids"]) == 1
    assert workspace.confirmations.schedule_checks(workspace, protocol)["existing_trial_ids"] == checks["created_trial_ids"]
    execute(workspace, checks["created_trial_ids"][0])
    assert workspace.confirmations.assess(protocol)["complete"]
    release = workspace.confirmations.release(protocol)
    assert release["outcome"] == "completed_roster"
    original = workspace.store.get(release["report_id"], "confirmation_report")
    assert original["claim_level"] == "descriptive"
    # Independent later counterevidence changes the current interpretation,
    # while the published release and original report remain inspectable.
    from optimization_framework.contracts.validation import ValidationResult
    previous = workspace.store.list("validation_result")[-1]
    values = {key: value for key, value in previous.items() if key != "content_hash"}
    values.update(id="later_countercheck", verdict="failed", rationale="A later independently recorded check disagreed")
    workspace.validations.record_result(ValidationResult(**values))
    workspace.confirmations.reconcile_reports()
    workspace.confirmations.reconcile_reports()
    reports = workspace.store.list("confirmation_report")
    assert len(reports) == 2
    assert reports[0] == original
    assert reports[-1]["supersedes_report_id"] == original["id"]
    assert reports[-1]["claim_level"] == "inconclusive"
    assert workspace.confirmations.release(protocol) == release


def test_fidelity_cells_share_instance_identity_without_colliding(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Fidelity roster", compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name=f"Order {order}", physics={"n_cells": 4, "fourier_order": order}) for order in (1, 2)]))
    task = workspace.current_tasks(campaign["id"])[0]
    prototype = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="hillclimb", max_steps=2, wall_seconds=5))
    study = workspace.create_study(campaign["id"], StudyInput(goal="A fixed fidelity comparison", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=[17, 18]))
    assert len(workspace.confirmations.schedule(workspace, study["confirmation"]["id"])["created_trial_ids"]) == 4
