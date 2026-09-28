"""A closed roster releases its evidence without permitting later adaptation."""
import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, StudyInput, ControlInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run
from optimization_framework.research.engine import _safe_context


def prepare(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Unseen confirmation", compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Development", problem_id="bounded_continuous"),
               TaskInput(name="Heldout", problem_id="bounded_continuous", configuration={"center": [1., 1.]}, split="test")]))
    source, target = workspace.current_tasks(campaign["id"])
    request = TrialInput(campaign_id=campaign["id"], task_id=source["id"], algorithm="coordinate", max_steps=3, wall_seconds=5)
    prototype = workspace.create_trial(request)
    study = workspace.create_study(campaign["id"], StudyInput(goal="A fixed procedure on an unseen condition", scope="confirmation",
        confirmation_kind="unseen_instance", prototype_trial_ids=[prototype["id"]], task_ids=[target["id"]], seeds=[17, 18]))
    request = request.model_copy(update={"task_id": target["id"], "confirmation_protocol_id": study["confirmation"]["id"]})
    return workspace, campaign, target, study["confirmation"]["id"], request


def finish(workspace, trial):
    result = run(workspace.job_dir(trial["id"]))
    assert result["scientific_complete"]
    trial.update(result=result, progress=result, status=result["status"], attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)


def test_completed_roster_releases_manager_evidence_and_blocks_new_cells(tmp_path):
    workspace, campaign, target, protocol, request = prepare(tmp_path)
    manager = CampaignManager(workspace)
    first = workspace.create_trial(request.model_copy(update={"seed": 17}))
    finish(workspace, first)
    before = _safe_context(manager.context(campaign["id"]))
    assert first["id"] not in {trial["id"] for trial in before["trials"]}
    assert not workspace.confirmations.assess(protocol)["complete"]
    with pytest.raises(ValueError, match="Incomplete"):
        workspace.confirmations.release(protocol, authority="manager")
    second = workspace.create_trial(request.model_copy(update={"seed": 18}))
    finish(workspace, second)
    release = workspace.confirmations.release(protocol, authority="manager")
    assert release["outcome"] == "completed_roster"
    after = _safe_context(manager.context(campaign["id"]))
    assert {first["id"], second["id"]} <= {trial["id"] for trial in after["trials"]}
    assert target["id"] in {task["id"] for task in after["tasks"]}
    with pytest.raises(ValueError, match="closed"):
        workspace.create_trial(request.model_copy(update={"seed": 17}))
    with pytest.raises(ValueError, match="closed"):
        workspace.control(first["id"], ControlInput(action="extend", max_steps=8))
    assert workspace.confirmations.release(protocol) == release


def test_researcher_can_close_missing_evidence_only_as_inconclusive(tmp_path):
    workspace, _, _, protocol, _ = prepare(tmp_path)
    with pytest.raises(ValueError, match="Incomplete"):
        workspace.confirmations.release(protocol, authority="manager", allow_incomplete=True, rationale="Stop early")
    release = workspace.confirmations.release(protocol, authority="researcher", allow_incomplete=True, rationale="The remaining allocation is unavailable")
    assert release["outcome"] == "inconclusive" and not release["assessment"]["complete"]
