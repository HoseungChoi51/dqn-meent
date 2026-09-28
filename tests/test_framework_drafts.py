"""Missing code is editable intent; launch pins one independently ready procedure."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.drafts import DraftSaveInput, DraftLaunchInput
from optimization_framework.contracts.requests import CampaignInput, TaskInput, StudyInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run


def setup(tmp_path, **campaign_options):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Draft research", validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")], **campaign_options))
    task = workspace.current_tasks(campaign["id"])[0]
    return workspace, campaign, task


def save(workspace, campaign, task, **values):
    return workspace.drafts.save(campaign["id"], DraftSaveInput(title="A useful optimizer", procedure={
        "task_id": task["id"], "max_steps": 3, "wall_seconds": 5, **values}))


def launch_request(workspace, draft):
    ready = workspace.drafts.readiness(draft["id"])
    return DraftLaunchInput(draft_id=draft["id"], expected_draft_revision=draft["revision"], expected_readiness_hash=ready["readiness_hash"])


def test_missing_code_draft_can_be_revised_and_launched_once(tmp_path):
    workspace, campaign, task = setup(tmp_path)
    draft = save(workspace, campaign, task)
    missing = workspace.drafts.readiness(draft["id"])
    assert not missing["ready"] and missing["blockers"][0]["code"] == "implementation_missing"
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0
    with pytest.raises(ValueError, match="not ready"):
        workspace.drafts.launch(campaign["id"], launch_request(workspace, draft))
    revised = workspace.drafts.save(campaign["id"], DraftSaveInput(draft_id=draft["id"], expected_draft_revision=1,
        title=draft["title"], procedure={**draft["procedure"], "algorithm": "coordinate"}))
    assert workspace.store.get(draft["revision_id"], "draft_revision")["procedure"]["algorithm"] == ""
    request = launch_request(workspace, revised)
    assert workspace.drafts.readiness(draft["id"])["ready"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: workspace.drafts.launch(campaign["id"], request), range(2)))
    assert outcomes[0] == outcomes[1]
    assert len(workspace.store.list("trial")) == 1
    trial = workspace.store.get(outcomes[0]["trial_id"], "trial")
    result = run(workspace.job_dir(trial["id"]))
    assert result["scientific_complete"] and result["evaluations"] == 3
    later = workspace.drafts.save(campaign["id"], DraftSaveInput(draft_id=draft["id"], expected_draft_revision=2,
        title="A later probe", procedure={**revised["procedure"], "max_steps": 10}))
    assert later["revision"] == 3 and workspace.store.get(trial["experiment_spec_id"], "experiment_spec")["completion"]["count"] == 3
    assert len(workspace.store.list("draft_launch")) == 1


def test_following_proposal_requires_fresh_readiness_and_never_changes_a_frozen_trial(tmp_path):
    workspace, campaign, task = setup(tmp_path)
    hypothesis = {"id": "proposal", "campaign_id": campaign["id"], "algorithm": "", "algorithm_config": {}, "status": "proposed"}
    workspace.store.put("hypothesis", hypothesis)
    draft = workspace.drafts.save(campaign["id"], DraftSaveInput(title="A future implementation",
        follow_proposal_implementation=True, procedure={"task_id": task["id"], "hypothesis_id": hypothesis["id"], "max_steps": 3, "wall_seconds": 5}))
    stale = launch_request(workspace, draft)
    workspace.store.put("hypothesis", {**hypothesis, "algorithm": "coordinate"})
    with pytest.raises(ValueError, match="readiness changed"):
        workspace.drafts.launch(campaign["id"], stale)
    assert not workspace.store.list("trial")
    frozen = workspace.drafts.launch(campaign["id"], launch_request(workspace, draft))
    workspace.store.put("hypothesis", {**hypothesis, "algorithm": "random"})
    assert workspace.store.get(frozen["trial_id"], "trial")["algorithm"] == "coordinate"


def test_draft_readiness_covers_capabilities_scope_parameters_and_allocation(tmp_path):
    workspace, campaign, task = setup(tmp_path, compute_budget_seconds=10)
    draft = workspace.drafts.save(campaign["id"], DraftSaveInput(title="Unavailable capability", required_capabilities=["rcwa_gradients"],
        procedure={"task_id": task["id"], "algorithm": "coordinate", "algorithm_config": {"imaginary_parameter": 1}, "wall_seconds": 20}))
    ready = workspace.drafts.readiness(draft["id"])
    assert {item["code"] for item in ready["blockers"]} == {"missing_evaluator_capability", "procedure_invalid", "allocation_unavailable"}
    workspace.create_study(campaign["id"], StudyInput(goal="A changed scientific scope"))
    assert "study_changed" in {item["code"] for item in workspace.drafts.readiness(draft["id"])["blockers"]}
    assert not workspace.store.list("trial")


def test_draft_commands_are_idempotent_and_manager_launch_uses_resolved_limits(tmp_path):
    workspace, campaign, task = setup(tmp_path, autonomy="delegated", delegated_trial_seconds=2)
    values = dict(campaign_id=campaign["id"], expected_revision=campaign["version"],
        expected_guidance_revision=0, expected_authority_hash=workspace.commands.authority_hash(campaign))
    command = Command(id="save_draft_once", operation="draft.save", **values,
        payload={"title": "Over delegated cap", "procedure": {"task_id": task["id"], "algorithm": "coordinate", "wall_seconds": 5}})
    first = workspace.commands.execute(command, actor="manager")
    assert workspace.commands.execute(command, actor="manager") == first
    assert len(workspace.store.list("draft_revision")) == 1
    draft = workspace.store.get(first["outcome"]["draft_id"], "experiment_draft")
    command = Command(id="launch_draft_once", operation="draft.launch", **values,
        payload=launch_request(workspace, draft).model_dump(mode="json", exclude={"schema_version"}))
    with pytest.raises(ValueError, match="delegated per-experiment"):
        workspace.commands.execute(command, actor="manager")
    assert not workspace.store.list("trial")
    assert workspace.store.list("command_rejection")


def test_manager_reconstructs_drafts_and_current_readiness_after_restart(tmp_path):
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.research.engine import _safe_context
    workspace, campaign, task = setup(tmp_path)
    draft = save(workspace, campaign, task, algorithm="coordinate")
    reopened = Workspace(tmp_path)
    manager = CampaignManager(reopened)
    context = manager.context(campaign["id"], "Continue the saved design")
    entry = next(item for item in context["experiment_drafts"] if item["draft"]["id"] == draft["id"])
    assert entry["readiness"]["ready"] and entry["readiness"]["readiness_hash"]
    assert "draft.launch" in context["application_commands"]
    assert _safe_context(context)["experiment_drafts"] == context["experiment_drafts"]


def test_draft_does_not_substitute_an_attached_package_for_an_explicit_strategy(tmp_path, monkeypatch):
    workspace, campaign, task = setup(tmp_path)
    workspace.store.put("hypothesis", {"id": "package_proposal", "campaign_id": campaign["id"], "algorithm": "package",
        "implementation_version_id": "impl_attached", "algorithm_config": {}, "status": "proposed"})
    workspace.store.put("implementation_cache", {"id": "impl_attached", "spec": {"parameters": {}}})
    monkeypatch.setattr(workspace.implementations, "readiness", lambda *_: {"runnable": True, "state": "ready"})
    draft = save(workspace, campaign, task, algorithm="coordinate", hypothesis_id="package_proposal")
    ready = workspace.drafts.readiness(draft["id"])
    assert not ready["ready"]
    assert ready["resolved_procedure"]["algorithm"] == "coordinate"
    assert "implementation_conflict" in {item["code"] for item in ready["blockers"]}
    with pytest.raises(ValueError, match="conflicts"):
        workspace.drafts.launch(campaign["id"], launch_request(workspace, draft))
    assert not workspace.store.list("trial")
