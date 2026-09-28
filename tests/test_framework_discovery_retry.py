"""Explicit timeout retries preserve failed attempts, costs and research authority."""
from copy import deepcopy
import json

import pytest

from test_framework_discovery import Adapter, command, setup, settle, start
from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.execution.service import Workspace
from optimization_framework.research.codex_provider import CodexProviderError
from optimization_framework.research.discovery.controller import DiscoveryController
from optimization_framework.storage.sqlite import identifier


class TimeoutAdapter(Adapter):
    def call_with_prompt(self, role, system, context, result_type):
        call_id = identifier("timeout_call")
        self.usage.update(calls=self.usage["calls"] + 1,
            subscription_calls=self.usage["subscription_calls"] + 1,
            pending_reservation={"id": call_id})
        self.emit({"type": "provider_call_reserved", "role": role, "reservation_id": call_id,
            "usage": deepcopy(self.usage)})
        self.emit({"type": "provider_request", "role": role, "reservation_id": call_id,
            "messages": [{"role": "user", "content": json.dumps(context)}]})
        self.usage.pop("pending_reservation")
        self.usage["token_accounting"] = "contains_unknown_subscription_usage"
        self.emit({"type": "provider_error", "role": role, "reservation_id": call_id,
            "error": "timeout", "usage": deepcopy(self.usage)})
        raise CodexProviderError("Codex inference exceeded its 120-second time limit.", code="timeout", usage_unknown=True)


def failures(setup, **policy):
    workspace, campaign = setup
    workspace.discovery.adapter_factory = TimeoutAdapter
    session, _ = start(workspace, campaign, **policy)
    settle(workspace, campaign, turns=2)
    tasks = workspace.discovery.tasks(session)
    assert len(tasks) == 3 and all(row["status"] == "failed" for row in tasks)
    return workspace, campaign, session, tasks


def retry_command(workspace, campaign, session, tasks, **extra):
    return command(workspace, campaign, "discovery.retry", {
        "session_id": session["id"], "expected_control_revision": session["control_revision"],
        "task_ids": [row["id"] for row in tasks], "reason": "Retry after correcting the provider timeout", **extra}, "retry_timeouts")


def test_timeout_retry_retains_receipts_usage_and_refreshes_manager_after_dependencies(setup):
    workspace, campaign, session, tasks = failures(setup)
    old_attempts = deepcopy(workspace.store.list("discovery_attempt"))
    receipts = deepcopy(workspace.store.list("discovery_response"))
    for task in tasks:
        assert task["error_code"] == "timeout" and "120-second" in task["error"] and task["finished_at"]
    cmd = retry_command(workspace, campaign, session, tasks)
    accepted = workspace.commands.execute(cmd)["outcome"]
    assert accepted["session"]["control_revision"] == 1
    assert workspace.commands.execute(cmd)["outcome"] == accepted
    assert workspace.store.list("discovery_attempt") == old_attempts
    assert workspace.store.list("discovery_response") == receipts
    retry = workspace.store.get(accepted["retry_id"], "discovery_retry")
    assert len(retry["previous_attempts"]) == 3
    assert all(row["usage"]["calls"] == 1 and row["error_code"] == "timeout" for row in retry["previous_attempts"])
    assert all(task["step"] == 1 and task["status"] == "queued" for task in workspace.discovery.tasks(session))
    assert all(row["status"] == "pending" for row in workspace.store.list("manager_issue") if row["code"] == "discovery_task")
    # Restart before dispatch and change routing: the old attempt stays frozen,
    # while a newly authorized call picks up the current model policy.
    workspace.models.save(campaign["id"], {"expected_revision": 0, "policy": {
        "default": {"model": "fixture-updated", "reasoning_effort": "xhigh"}, "roles": {}}})
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    CampaignManager(restarted)
    restarted.discovery = DiscoveryController(restarted, adapter_factory=Adapter)
    restarted.discovery.recover()
    settle(restarted, campaign, turns=3)
    finished = restarted.discovery.tasks(session)
    assert all(row["status"] == "completed" for row in finished)
    assert all(restarted.store.get(row["run_id"])["usage"]["calls"] == 2 for row in finished)
    assert all(restarted.store.get(row["run_id"])["usage"]["token_accounting"] == "contains_unknown_subscription_usage" for row in finished)
    attempts = restarted.store.list("discovery_attempt")
    assert len(attempts) == 6
    assert all(row["provider_snapshot"]["model"] == "fixture-updated" for row in attempts if row["step"] == 1)
    assert all(restarted.store.get(row["id"], "discovery_attempt") == row for row in old_attempts)
    manager = next(row for row in finished if row["brief"]["role"] == "campaign_manager")
    context = restarted.store.get(manager["attempt_id"])["context_snapshot"]["discovery"]
    assert all(row["status"] == "completed" for row in context["dependencies"])
    assert all(row["status"] == "resolved" for row in restarted.store.list("manager_issue") if row["code"] == "discovery_task")
    restarted.discovery.project_pending()
    records = (restarted.directory / "campaigns" / campaign["id"] / "discovery" / "records.jsonl").read_text()
    assert '"kind": "discovery_retry"' in records


def test_retry_preserves_pause_and_rejects_stale_control_or_manager_authority(setup):
    workspace, campaign, session, tasks = failures(setup)
    session = workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    cmd = retry_command(workspace, campaign, session, tasks)
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.commands.execute(cmd.model_copy(update={"expected_guidance_revision": session["guidance_revision"],
            "expected_authority_hash": workspace.commands.authority_hash(campaign)}), actor="manager")
    with pytest.raises(ValueError, match="control changed"):
        workspace.commands.execute(cmd.model_copy(update={"payload": {**cmd.payload, "expected_control_revision": 0}}))
    result = workspace.commands.execute(cmd)["outcome"]
    assert result["session"]["status"] == "paused"
    settle(workspace, campaign)
    assert len(workspace.store.list("discovery_response")) == 3


def test_retry_resumes_session_waiting_for_researcher_direction(setup):
    workspace, campaign, session, tasks = failures(setup)
    session["status"] = "waiting_for_direction"
    workspace.store.put("discovery_session", session)
    result = workspace.commands.execute(retry_command(workspace, campaign, session, tasks))["outcome"]
    assert result["session"]["status"] == "running"
    workspace.discovery.adapter_factory = Adapter
    settle(workspace, campaign)
    assert all(row["status"] == "completed" for row in workspace.discovery.tasks(session))
    assert all(workspace.store.get(row["run_id"])["usage"]["calls"] == 2 for row in tasks)


@pytest.mark.parametrize("dispatch_state", ["not_reserved", "pending", "increased_usage", "received"])
def test_restart_after_retry_attempt_creation_distinguishes_old_timeout_usage(setup, monkeypatch, dispatch_state):
    workspace, campaign, session, tasks = failures(setup)
    workspace.commands.execute(retry_command(workspace, campaign, session, tasks))
    # Freeze the exact attempt/context as normal, then lose the process before
    # its worker reserves a new provider call.
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.research.discovery.controller.threading.Thread.start", lambda thread: None)
        workspace.discovery.tick(campaign["id"])
    task = workspace.store.get(tasks[0]["id"], "discovery_task")
    assert task["status"] == "running" and task["step"] == 1
    attempt = deepcopy(workspace.store.get(task["attempt_id"], "discovery_attempt"))
    run = workspace.store.get(task["run_id"], "research_run")
    assert run["usage"]["calls"] == 1
    if dispatch_state == "pending":
        run["usage"]["pending_reservation"] = {"id": "retry_reservation"}
    elif dispatch_state == "increased_usage":
        run["usage"]["calls"] += 1
    elif dispatch_state == "received":
        workspace.store.put_immutable("discovery_response", {"id": "retry_error_receipt", "campaign_id": campaign["id"],
            "session_id": session["id"], "task_id": task["id"], "step": task["step"],
            "event": {"type": "provider_error", "error": "timeout", "usage": deepcopy(run["usage"])}})
    workspace.store.put("research_run", run)
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    CampaignManager(restarted)
    restarted.discovery = DiscoveryController(restarted, adapter_factory=Adapter)
    restarted.discovery.recover()
    recovered = restarted.store.get(task["id"], "discovery_task")
    assert restarted.store.get(task["attempt_id"], "discovery_attempt") == attempt
    assert restarted.store.get(task["run_id"], "research_run")["usage"] == run["usage"]
    if dispatch_state == "not_reserved":
        assert recovered["status"] == "queued"
        settle(restarted, campaign)
        assert restarted.store.get(task["id"])["status"] == "completed"
        assert restarted.store.get(task["run_id"])["usage"]["calls"] == 2
        assert len([row for row in restarted.store.list("discovery_attempt") if row["task_id"] == task["id"]]) == 2
    else:
        assert recovered["status"] == "waiting" and recovered["wait_reason"] == "provider_receipt_reconciliation"
        assert restarted.store.get(task["run_id"])["status"] == "needs_reconciliation"


@pytest.mark.parametrize("mutation,match", [
    ("pending_reservation", "Reconcile"), ("provider_response", "without accepted output"),
    ("wrong_error", "recorded timeout"), ("accepted_step", "without accepted output"),
    ("tool_effect", "tool effects"), ("stale_guidance", "guidance changed"),
    ("running", "Only failed"), ("task_limit", "task call allocation"),
    ("session_limit", "session call allocation"), ("other_session", "belong to this"),
])
def test_unsafe_or_unfunded_retry_is_atomic(setup, mutation, match):
    workspace, campaign, session, tasks = failures(setup)
    task = tasks[-1]
    run = workspace.store.get(task["run_id"])
    if mutation == "pending_reservation":
        run["usage"]["pending_reservation"] = {"id": "unresolved"}
    elif mutation in {"provider_response", "wrong_error"}:
        receipt = next(row for row in workspace.store.list("discovery_response") if row["task_id"] == task["id"])
        receipt.update(id="additional_attempt_receipt")
        receipt.pop("content_hash", None)
        receipt["event"].update(type="provider_response" if mutation == "provider_response" else "provider_error", error="invalid_output")
        workspace.store.put_immutable("discovery_response", receipt)
    elif mutation == "accepted_step":
        workspace.store.put("discovery_step", {"id": task["id"] + "_step_0", "campaign_id": campaign["id"], "task_id": task["id"]})
    elif mutation == "tool_effect":
        workspace.store.put("discovery_tool", {"id": "already_accepted_tool", "campaign_id": campaign["id"],
            "task_id": task["id"], "step_id": task["id"] + "_step_0"})
    elif mutation == "stale_guidance":
        run["guidance_revision"] -= 1
    elif mutation == "running":
        task["status"] = "running"
    elif mutation == "task_limit":
        run["usage"]["calls"] = session["policy"]["max_calls_per_task"]
    elif mutation == "session_limit":
        session["policy"]["model_call_limit"] = 4
        workspace.store.put("discovery_session", session)
    elif mutation == "other_session":
        task["session_id"] = "other_session"
    workspace.store.put("research_run", run)
    workspace.store.put("discovery_task", task)
    before = deepcopy(workspace.discovery.tasks(session))
    with pytest.raises(ValueError, match=match):
        workspace.commands.execute(retry_command(workspace, campaign, session, tasks))
    assert workspace.discovery.tasks(session) == before
    assert workspace.store.get(session["id"])["control_revision"] == 0
    assert not workspace.store.list("discovery_retry")
