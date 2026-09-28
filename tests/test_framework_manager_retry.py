"""Blocked questions recover without creating a duplicate or replaying model work."""
from copy import deepcopy

import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace


@pytest.fixture
def blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Recover saved question", autonomy="delegated",
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    manager = CampaignManager(workspace)
    attempts = []
    monkeypatch.setattr(manager, "_thread", lambda run: attempts.append(run["id"]))
    original_context = manager.context
    def fail_context(*args, **kwargs):
        raise ValueError("Current campaign evidence exceeds the context allowance")
    monkeypatch.setattr(manager, "context", fail_context)
    accepted = workspace.commands.execute(command(workspace, campaign, "research.start",
        {"message": "Which method should proceed to final validation?", "mode": "plan"}, "question"))
    saved = workspace.store.get(accepted["outcome"]["manager_command_id"], "manager_command")
    assert saved["status"] == "blocked" and not attempts
    assert not workspace.store.list("research_run")
    monkeypatch.setattr(manager, "context", original_context)
    return workspace, campaign, manager, saved, attempts


def command(workspace, campaign, operation, payload, identity):
    current = workspace.store.get(campaign["id"], "campaign")
    return Command(id=identity, campaign_id=campaign["id"], operation=operation,
        expected_revision=current["version"],
        expected_guidance_revision=workspace.memory.state(campaign["id"])["guidance_revision"],
        expected_authority_hash=workspace.commands.authority_hash(current), payload=payload)


def retry_command(workspace, campaign, saved, identity="retry_question"):
    return command(workspace, campaign, "research.retry", {"manager_command_id": saved["id"]}, identity)


def test_retry_preserves_question_and_input_uses_current_context_and_is_idempotent(blocked):
    workspace, campaign, _, saved, attempts = blocked
    current = workspace.memory.sync(campaign["id"])
    workspace.memory.edit(campaign["id"], "Use fresh-seed comparisons; do not launch work.", current["revision"])
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    request = retry_command(workspace, campaign, saved)
    receipt = workspace.commands.execute(request)
    run = workspace.store.get(attempts[0], "research_run")
    assert run["request"]["message"] == saved["request"]["message"]
    assert run["request"]["mode"] == saved["request"]["mode"]
    assert run["guidance_revision"] == guidance
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == guidance
    assert len(workspace.store.list("manager_command")) == 1
    assert len([row for row in workspace.store.list("message") if row["role"] == "user"]) == 1
    original_input = workspace.store.get("manager_input_" + saved["id"], "manager_input")
    assert original_input["status"] == "consumed" and original_input["research_run_id"] == run["id"]
    recovered = workspace.store.get(saved["id"], "manager_command")
    assert recovered["request"] == saved["request"]
    assert recovered["retry_history"][0]["previous_error"] == saved["error"]
    issue = next(row for row in workspace.store.list("manager_issue") if row["affected"] == saved["id"])
    assert issue["status"] == "resolved" and issue["resolution_basis"] == "saved_request_dispatched"
    assert workspace.commands.execute(request) == receipt
    assert len(attempts) == 1 and not workspace.store.list("trial")


def test_failure_keeps_issue_pending_and_does_not_retry_from_polling(blocked, monkeypatch):
    workspace, campaign, manager, saved, attempts = blocked
    context_calls = []
    def still_blocked(*args, **kwargs):
        context_calls.append(1)
        raise ValueError(saved["error"])
    monkeypatch.setattr(manager, "context", still_blocked)
    workspace.commands.execute(retry_command(workspace, campaign, saved))
    manager.tick(campaign["id"])
    manager.tick(campaign["id"])
    assert context_calls == [1] and not attempts
    assert workspace.store.get(saved["id"], "manager_command")["status"] == "blocked"
    assert workspace.store.get("manager_input_" + saved["id"], "manager_input")["status"] == "pending"
    assert next(row for row in workspace.store.list("manager_issue") if row["affected"] == saved["id"])["status"] == "pending"


@pytest.mark.parametrize("dispatch_evidence", ["run_link", "unlinked_run", "discovery_link", "unlinked_task", "consumed_input"])
def test_retry_rejects_any_evidence_of_a_prior_dispatch(blocked, dispatch_evidence):
    workspace, campaign, _, saved, attempts = blocked
    if dispatch_evidence in {"run_link", "discovery_link"}:
        saved["research_run_id" if dispatch_evidence == "run_link" else "discovery_task_id"] = "prior_dispatch"
        workspace.store.put("manager_command", saved)
    elif dispatch_evidence in {"unlinked_run", "unlinked_task"}:
        workspace.store.put("research_run" if dispatch_evidence == "unlinked_run" else "discovery_task",
            {"id": "prior_dispatch", "campaign_id": campaign["id"], "manager_command_id": saved["id"], "status": "needs_reconciliation"})
    else:
        original_input = workspace.store.get("manager_input_" + saved["id"], "manager_input")
        original_input.update(status="consumed", research_run_id="prior_dispatch")
        workspace.store.put("manager_input", original_input)
    with pytest.raises(ValueError, match="already dispatched|unconsumed"):
        workspace.commands.execute(retry_command(workspace, campaign, saved))
    assert not attempts and workspace.store.get(saved["id"], "manager_command")["status"] == "blocked"


def test_retry_requires_researcher_current_authority_and_same_campaign(blocked):
    workspace, campaign, _, saved, attempts = blocked
    request = retry_command(workspace, campaign, saved)
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.commands.execute(request, actor="manager")
    other = workspace.create_campaign(CampaignInput(name="Other campaign",
        tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="another campaign"):
        workspace.commands.execute(retry_command(workspace, other, saved, "foreign_retry"))
    with pytest.raises(ValueError, match="guidance changed"):
        workspace.commands.execute(request.model_copy(update={"expected_guidance_revision": 0}))
    with pytest.raises(ValueError, match="resource grant changed"):
        workspace.commands.execute(request.model_copy(update={"expected_authority_hash": "obsolete"}))
    assert not attempts


def test_retry_intent_rolls_back_without_dispatch(blocked, monkeypatch):
    workspace, campaign, _, saved, attempts = blocked
    original_apply = workspace.commands._apply
    original_input = deepcopy(workspace.store.get("manager_input_" + saved["id"], "manager_input"))
    def fail_commit(request, actor):
        original_apply(request, actor)
        raise RuntimeError("admission interrupted")
    monkeypatch.setattr(workspace.commands, "_apply", fail_commit)
    with pytest.raises(RuntimeError, match="admission interrupted"):
        workspace.commands.execute(retry_command(workspace, campaign, saved))
    assert workspace.store.get(saved["id"], "manager_command") == saved
    assert workspace.store.get(original_input["id"], "manager_input") == original_input
    assert not attempts and not workspace.store.list("research_run")
    assert not [row for row in workspace.store.list("outbox") if row["id"].startswith("research_retry_")]
