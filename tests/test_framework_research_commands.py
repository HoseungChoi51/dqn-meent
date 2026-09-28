"""Remaining research controls retain intent through transactions and lost replies."""
from copy import deepcopy
import threading

import pytest
from fastapi.testclient import TestClient

from optimization_framework.api.app import create_app
from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
from optimization_framework.execution.service import Workspace
from optimization_framework.storage.sqlite import now


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Research controls", autonomy="delegated",
        compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    manager = CampaignManager(workspace)
    attempts = []
    def start_thread(run):
        assert not workspace.store.in_transaction
        attempts.append(run["id"])
    monkeypatch.setattr(manager, "_thread", start_thread)
    yield workspace, campaign, manager, attempts
    for thread in workspace.source_threads.values():
        thread.join(timeout=5)


def command(workspace, campaign, operation, payload, identity):
    return Command(id=identity, campaign_id=campaign["id"], operation=operation,
        expected_revision=workspace.store.get(campaign["id"], "campaign")["version"], payload=payload)


def trial(workspace, campaign):
    return workspace.create_trial(TrialInput(campaign_id=campaign["id"],
        task_id=workspace.current_tasks(campaign["id"])[0]["id"], algorithm="coordinate", max_steps=2, wall_seconds=5))


def test_research_admission_replays_without_second_turn_or_guidance_change(prepared):
    workspace, campaign, _, attempts = prepared
    request = command(workspace, campaign, "research.start", {"message": "Compare the current evidence."}, "first_message")
    accepted = workspace.commands.execute(request)
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    assert guidance == 1 and len(attempts) == 1
    workspace.commands.execute(command(workspace, campaign, "research.start", {"message": "New direction: defer the surrogate."}, "second_message"))
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == 2
    assert workspace.commands.execute(request) == accepted
    assert len(workspace.store.list("manager_command")) == 2 and len(attempts) == 1
    with pytest.raises(ValueError, match="different request"):
        workspace.commands.execute(request.model_copy(update={"payload": {"message": "Changed content"}}))
    restarted = Workspace(workspace.directory)
    assert restarted.commands.execute(request) == accepted
    assert restarted.memory.state(campaign["id"])["guidance_revision"] == 2


def test_failed_admission_rolls_back_guidance_and_never_starts_a_model(prepared, monkeypatch):
    workspace, campaign, _, attempts = prepared
    original = workspace.commands._apply
    def fail(request, actor):
        original(request, actor)
        raise RuntimeError("before acceptance")
    monkeypatch.setattr(workspace.commands, "_apply", fail)
    with pytest.raises(RuntimeError, match="before acceptance"):
        workspace.commands.execute(command(workspace, campaign, "research.start", {"message": "Keep this direction"}, "rolled_back"))
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == 0
    assert not attempts and not workspace.store.list("manager_command") and not workspace.store.list("research_run")
    assert not [effect for effect in workspace.store.list("outbox") if effect["kind"] == "manager_start"]


def test_queued_revision_keeps_feedback_selected_at_admission(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    idea = workspace.commands.execute(command(workspace, campaign, "hypothesis.create",
        {"title": "Specialized search", "algorithm": "coordinate"}, "idea"))["outcome"]["hypothesis"]
    first = workspace.commands.execute(command(workspace, campaign, "hypothesis.review",
        {"hypothesis_id": idea["id"], "text": "Keep the coordinate probe."}, "first_feedback"))["outcome"]["hypothesis"]["reviews"][-1]
    workspace.commands.execute(command(workspace, campaign, "research.start", {"message": "Discuss"}, "running"))
    active = workspace.store.list("research_run")[0]
    revision = workspace.commands.execute(command(workspace, campaign, "research.start",
        {"message": "Revise with the saved feedback", "mode": "evolve", "hypothesis_id": idea["id"]}, "revision"))
    workspace.commands.execute(command(workspace, campaign, "hypothesis.review",
        {"hypothesis_id": idea["id"], "text": "Consider a different initializer next."}, "later_feedback"))
    active["status"] = "completed"
    workspace.store.put("research_run", active)
    manager.tick()
    run = next(row for row in workspace.store.list("research_run") if row["manager_command_id"] == revision["outcome"]["manager_command_id"])
    assert run["context_snapshot"]["revision_context"]["reviews"] == [first]


def test_research_controls_reject_stale_intent_and_resume_only_after_commit(prepared):
    workspace, campaign, _, attempts = prepared
    workspace.commands.execute(command(workspace, campaign, "research.start", {"message": "Explain the evidence"}, "message"))
    run = workspace.store.list("research_run")[0]
    stop = command(workspace, campaign, "research.control", {"run_id": run["id"], "action": "stop", "expected_control_revision": 0}, "stop")
    stopped = workspace.commands.execute(stop)
    run = workspace.store.get(run["id"], "research_run")
    run.update(status="interrupted", checkpoint={"stage": "planner"})
    workspace.store.put("research_run", run)
    resume = command(workspace, campaign, "research.control", {"run_id": run["id"], "action": "resume", "expected_control_revision": 1}, "resume")
    resumed = workspace.commands.execute(resume)
    assert len(attempts) == 2
    workspace.commands.execute(command(workspace, campaign, "research.control", {"run_id": run["id"], "action": "stop", "expected_control_revision": 2}, "new_stop"))
    effect = workspace.store.get("research_resume_resume", "outbox")
    effect["status"] = "pending"
    workspace.store.put("outbox", effect)
    workspace.dispatch_outbox()
    assert len(attempts) == 2 and workspace.store.get(run["id"], "research_run")["status"] == "stopping"
    assert workspace.commands.execute(resume) == resumed and workspace.commands.execute(stop) == stopped
    with pytest.raises(ValueError, match="controls changed"):
        workspace.commands.execute(resume.model_copy(update={"id": "stale_resume"}))


def test_research_control_cannot_cross_campaign_or_impersonate_researcher(prepared):
    workspace, campaign, _, _ = prepared
    workspace.commands.execute(command(workspace, campaign, "research.start", {"message": "Inspect"}, "message"))
    run = workspace.store.list("research_run")[0]
    request = command(workspace, campaign, "research.control", {"run_id": run["id"], "action": "stop", "expected_control_revision": 0}, "control")
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.commands.execute(request.model_copy(update={"expected_guidance_revision": 1,
            "expected_authority_hash": workspace.commands.authority_hash(campaign)}), actor="manager")
    other = workspace.create_campaign(CampaignInput(name="Other", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="another campaign"):
        workspace.commands.execute(request.model_copy(update={"campaign_id": other["id"]}))
    assert workspace.store.get(run["id"], "research_run")["status"] == "running"


def extension_decision(workspace, campaign, item):
    decision = {"id": "extension", "campaign_id": campaign["id"], "charter_version": campaign["version"],
        "trial_id": item["id"], "incremental_solver_calls": 3, "estimated_seconds": 5,
        "title": "Extend the bounded test", "status": "pending", "created_at": now(),
        "options": [{"id": "0", "label": "Extend"}, {"id": "defer", "label": "Defer"}]}
    workspace.store.put("decision", decision)
    return command(workspace, campaign, "decision.resolve", {"decision_id": decision["id"], "choice": "0", "comment": "Measure the plateau",
        "expected_resolution_revision": 0}, "resolve_extension")


def test_decision_extension_reconciles_accepted_child_before_new_guidance(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    item = trial(workspace, campaign)
    request = extension_decision(workspace, campaign, item)
    finish = manager._finish_decision
    def lost_reply(*args):
        raise RuntimeError("lost child acknowledgement")
    monkeypatch.setattr(manager, "_finish_decision", lost_reply)
    accepted = workspace.commands.execute(request)
    extended = workspace.store.get(item["id"], "trial")
    assert extended["max_steps"] == 5 and extended["control_revision"] == 1
    assert workspace.store.get("extension", "decision")["status"] == "executing"
    memory = workspace.memory.state(campaign["id"])
    memory["guidance_revision"] += 1
    workspace.store.put("manager_state", memory)
    monkeypatch.setattr(manager, "_finish_decision", finish)
    workspace.dispatch_outbox()
    assert workspace.store.get("extension", "decision")["status"] == "resolved"
    assert workspace.store.get(item["id"], "trial")["control_revision"] == 1
    assert workspace.commands.execute(request) == accepted
    assert len([row for row in workspace.store.list("message") if row["content"].startswith("Decision:")]) == 1
    assert len([row for row in workspace.store.list("work_command") if row["request"]["operation"] == "trial.control"]) == 1


def test_unaccepted_decision_delivery_cannot_apply_after_guidance_changes(prepared, monkeypatch):
    workspace, campaign, _, _ = prepared
    item = trial(workspace, campaign)
    request = extension_decision(workspace, campaign, item)
    dispatch = workspace.dispatch_outbox
    monkeypatch.setattr(workspace, "dispatch_outbox", lambda: None)
    workspace.commands.execute(request)
    memory = workspace.memory.state(campaign["id"])
    memory["guidance_revision"] += 1
    workspace.store.put("manager_state", memory)
    monkeypatch.setattr(workspace, "dispatch_outbox", dispatch)
    workspace.dispatch_outbox()
    assert workspace.store.get(item["id"], "trial")["control_revision"] == 0
    decision = workspace.store.get("extension", "decision")
    assert decision["status"] == "pending" and "guidance changed" in decision["delivery_error"]
    assert workspace.store.get("decision_" + request.id, "outbox")["status"] == "failed"


def test_slow_retrieval_does_not_hold_experiment_controls_and_receipt_survives_restart(prepared, monkeypatch):
    workspace, campaign, _, _ = prepared
    started, release = threading.Event(), threading.Event()
    calls = []
    def search(query, **kwargs):
        assert not workspace.store.in_transaction
        calls.append(query)
        started.set()
        assert release.wait(5)
        return {"provider": "crossref", "sources": [{"id": "paper", "title": "Retained source", "url": "https://example.org/paper"}]}
    monkeypatch.setattr("optimization_framework.research.evidence.search_literature", search)
    request = command(workspace, campaign, "literature.search", {"query": "bounded search"}, "search")
    accepted = workspace.commands.execute(request)
    try:
        assert started.wait(2)
        item = trial(workspace, campaign)
        workspace.commands.execute(command(workspace, campaign, "trial.control", {"trial_id": item["id"], "action": "stop", "expected_control_revision": 0}, "stop_trial"))
        effect = workspace.store.get(f"control_{item['id']}_1", "outbox")
        assert effect["status"] == "completed" and not release.is_set()
    finally:
        release.set()
        workspace.source_threads[accepted["outcome"]["effect_id"]].join(5)
    source = deepcopy(workspace.store.list("source")[0])
    assert len(workspace.store.list("source_retrieval")) == 1
    effect = workspace.store.get(accepted["outcome"]["effect_id"], "outbox")
    effect["status"] = "pending"
    workspace.store.put("outbox", effect)
    restarted = Workspace(workspace.directory)
    restarted.dispatch_outbox()
    for thread in restarted.source_threads.values():
        thread.join(5)
    assert calls == ["bounded search"]
    assert restarted.store.list("source") == [source]
    assert restarted.commands.execute(request) == accepted


def test_source_routes_preserve_keyed_replies_and_changed_retrievals(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Sources", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    calls = []
    def ingest(identifier):
        calls.append(identifier)
        return {"id": "paper", "title": "Version " + str(len(calls)), "url": "https://example.org/paper", "verification": "metadata_retrieved"}
    monkeypatch.setattr("optimization_framework.research.evidence.ingest_source", ingest)
    with TestClient(app) as client:
        payload = {"campaign_id": campaign["id"], "identifier": "10.1234/paper"}
        first = client.post("/api/sources/ingest", json=payload, headers={"Idempotency-Key": "first"})
        assert first.status_code == 200, first.text
        assert client.post("/api/sources/ingest", json=payload, headers={"Idempotency-Key": "first"}).json() == first.json()
        second = client.post("/api/sources/ingest", json=payload, headers={"Idempotency-Key": "second"})
        assert second.status_code == 200 and second.json()["id"] != first.json()["id"]
        assert workspace.store.get(first.json()["id"], "source")["title"] == "Version 1"
        assert len(calls) == 2
        monkeypatch.setattr("optimization_framework.research.evidence.ingest_source", lambda identifier:
            {"id": "paper", "title": "Version 1", "url": "https://example.org/paper", "verification": "metadata_retrieved"})
        unchanged = client.post("/api/sources/ingest", json=payload, headers={"Idempotency-Key": "unchanged"})
        assert unchanged.status_code == 200 and unchanged.json()["id"] == first.json()["id"]
        assert len(workspace.store.list("source")) == 2
        assert len(workspace.store.list("source_retrieval")) == 3
        bad = client.post("/api/sources", json={"campaign_id": campaign["id"], "title": "Invalid", "url": "file:///secret"})
        assert bad.status_code == 409
