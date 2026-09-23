"""Real worker integration: resource reservations, controls, recovery, validation."""
import json
import time

import pytest

from dqn_meent.workspace.coordinator import ResearchCancelled, ResearchCoordinator
from dqn_meent.workspace.models import CampaignInput, ControlInput, ResearchInput, TaskInput, TrialInput, ValidationInput
from dqn_meent.workspace.service import ACTIVE, Workspace, alive
from dqn_meent.workspace.store import Store


def wait_for(workspace, trial_id, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        workspace.reconcile()
        trial = workspace.store.get(trial_id, "trial")
        if predicate(trial):
            return trial
        if trial["status"] == "failed":
            pytest.fail(f"Worker failed: {trial.get('reason')} {trial.get('result')}")
        time.sleep(.04)
    pytest.fail(f"Timed out waiting for trial {trial_id}: {workspace.store.get(trial_id, 'trial')}")


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    instances = []

    def create(directory=None, **kwargs):
        workspace = Workspace(directory or tmp_path / "workspace", **kwargs)
        instances.append(workspace)
        return workspace

    yield create
    # close() intentionally leaves jobs alive for service recovery. Tests own
    # their numerical children and explicitly stop them before releasing files.
    for workspace in reversed(instances):
        for trial in workspace.store.list("trial"):
            if trial["status"] in ACTIVE:
                workspace.control(trial["id"], ControlInput(action="stop"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(alive(t) for t in workspace.store.list("trial")):
            workspace.reconcile()
            time.sleep(.05)
        for trial in workspace.store.list("trial"):
            if alive(trial):
                workspace._terminate(trial)
        workspace.close()
        for process in workspace.processes.values():
            process.wait(timeout=5)


def campaign(workspace, **kwargs):
    settings = {"name": "Integration campaign", "compute_budget_seconds": 600,
                "validation_reserve_seconds": 30, "llm_budget_usd": 0,
                "tasks": [TaskInput(name="Small real grating", physics={"n_cells": 6, "fourier_order": 1})]}
    settings.update(kwargs)
    created = workspace.create_campaign(CampaignInput(**settings))
    return created, workspace.current_tasks(created["id"])[0]


def trial_request(campaign_record, task, **kwargs):
    settings = dict(campaign_id=campaign_record["id"], task_id=task["id"], algorithm="random",
                    max_steps=100000, wall_seconds=40, schedule_steps=50)
    settings.update(kwargs)
    return TrialInput(**settings)


def test_queued_reservations_cannot_spend_validation_reserve(workspaces):
    workspace = workspaces()
    charter, task = campaign(workspace, compute_budget_seconds=100, validation_reserve_seconds=20)
    first = workspace.create_trial(trial_request(charter, task, wall_seconds=40))
    workspace.create_trial(trial_request(charter, task, wall_seconds=40))
    assert workspace.allocated_seconds(charter["id"]) == 80
    with pytest.raises(ValueError, match="budget"):
        workspace.create_trial(trial_request(charter, task, wall_seconds=1))
    workspace.control(first["id"], ControlInput(action="pause"))
    assert workspace.allocated_seconds(charter["id"]) == 40
    workspace.create_trial(trial_request(charter, task, wall_seconds=40))
    with pytest.raises(ValueError, match="budget"):
        workspace.control(first["id"], ControlInput(action="resume"))
    assert workspace.store.get(first["id"], "trial")["status"] == "paused"


def test_concurrent_stop_isolation_and_cooperative_pause_resume(workspaces):
    workspace = workspaces(max_workers=2)
    charter, task = campaign(workspace)
    first = workspace.create_trial(trial_request(charter, task, seed=7))
    second = workspace.create_trial(trial_request(charter, task, seed=8))
    workspace.start()
    started_first = wait_for(workspace, first["id"], lambda t: t.get("progress", {}).get("step", 0) >= 4)
    started_second = wait_for(workspace, second["id"], lambda t: t.get("progress", {}).get("step", 0) >= 4)
    assert alive(started_first) and alive(started_second)
    workspace.control(first["id"], ControlInput(action="stop"))
    stopped = wait_for(workspace, first["id"], lambda t: t["status"] == "stopped")
    assert stopped["result"]["reason"] == "researcher_stop"
    advanced = wait_for(workspace, second["id"], lambda t: t.get("progress", {}).get("step", 0) > started_second["progress"]["step"])
    assert alive(advanced)
    workspace.control(second["id"], ControlInput(action="pause"))
    paused = wait_for(workspace, second["id"], lambda t: t["status"] == "paused")
    assert paused["progress"]["checkpoint_available"]
    completed_step = paused["progress"]["step"]
    previous_calls = paused["progress"]["solver_calls"]
    workspace.control(second["id"], ControlInput(action="resume"))
    resumed = wait_for(workspace, second["id"], lambda t: t.get("progress", {}).get("step", 0) > completed_step + 2)
    assert resumed["attempt"] == 2
    assert resumed["progress"]["solver_calls"] >= previous_calls
    assert resumed["progress"]["schedule_steps"] == 50
    rows = workspace.metrics(second["id"])
    assert len({row["step"] for row in rows}) == len(rows)
    assert workspace.store.get(first["id"], "trial")["attempt"] == 1


def test_service_restart_reconciles_live_pid_without_duplicate_work(workspaces):
    original = workspaces(max_workers=1)
    charter, task = campaign(original)
    trial = original.create_trial(trial_request(charter, task))
    original.start()
    before = wait_for(original, trial["id"], lambda t: t.get("progress", {}).get("step", 0) >= 4)
    original.close()
    restarted = workspaces(original.directory, max_workers=1)
    restarted.start()
    after = wait_for(restarted, trial["id"], lambda t: t.get("progress", {}).get("step", 0) > before["progress"]["step"])
    assert (after["pid"], after["process_identity"], after["attempt"]) == (
        before["pid"], before["process_identity"], before["attempt"])
    assert alive(after)
    assert after["execution_seconds"] >= before["execution_seconds"]
    assert len([event for event in restarted.store.events(charter["id"], limit=1000)
                if event["kind"] == "trial.started"]) == 1


def test_completed_dqn_extension_preserves_schedule_costs_and_history(workspaces):
    workspace = workspaces(max_workers=1)
    charter, task = campaign(workspace)
    trial = workspace.create_trial(trial_request(charter, task, algorithm="dqn", max_steps=8, schedule_steps=40,
        training={"hidden_sizes": [8], "horizon": 3, "batch_size": 2, "buffer_size": 32,
                  "learning_starts": 2, "target_update_interval": 2}))
    workspace.start()
    before = wait_for(workspace, trial["id"], lambda t: t["status"] == "completed")
    assert before["progress"]["step"] == 8
    workspace.control(trial["id"], ControlInput(action="extend", max_steps=16))
    after = wait_for(workspace, trial["id"], lambda t: t["status"] == "completed" and t["attempt"] == 2)
    assert after["progress"]["step"] == after["progress"]["budget_requests"] == 16
    assert after["progress"]["diagnostics"]["schedule_steps"] == 40
    assert after["progress"]["solver_calls"] >= before["progress"]["solver_calls"]
    assert after["execution_seconds"] > before["execution_seconds"]
    assert [row["step"] for row in workspace.metrics(trial["id"])] == list(range(1, 17))
    original_spec = json.loads((workspace.job_dir(trial["id"]) / "spec.json").read_text())
    assert original_spec["schedule_steps"] == 40


def test_validation_uses_multiple_archived_designs_and_common_orders(workspaces):
    workspace = workspaces(max_workers=1)
    charter, task = campaign(workspace)
    parent = workspace.create_trial(trial_request(charter, task, max_steps=16))
    workspace.start()
    completed = wait_for(workspace, parent["id"], lambda t: t["status"] == "completed")
    assert len(completed["progress"]["archive"]) >= 3
    validation = workspace.validate_trial(parent["id"], ValidationInput(orders=[1, 2], max_designs=3, wall_seconds=20))
    assert len(validation["algorithm_config"]["designs"]) == 3
    assert validation["max_steps"] == 6
    result = wait_for(workspace, validation["id"], lambda t: t["status"] == "completed")
    assert result["result"]["evaluations"] == 6
    assert len(result["result"]["validation"]) == 3
    assert all(item["complete"] for item in result["result"]["validation"])
    refreshed = workspace.store.get(parent["id"], "trial")
    assert refreshed["validation"]["trial_id"] == validation["id"]
    assert refreshed["validation"]["status"] == "completed"


def test_forced_worker_exit_is_visible_and_recovery_never_restarts_stopped_job(workspaces):
    workspace = workspaces(max_workers=1, stop_grace_seconds=.1)
    charter, task = campaign(workspace)
    trial = workspace.create_trial(trial_request(charter, task))
    workspace.start()
    before = wait_for(workspace, trial["id"], lambda t: t.get("progress", {}).get("step", 0) >= 4)
    workspace._terminate(before)
    interrupted = wait_for(workspace, trial["id"], lambda t: t["status"] == "interrupted")
    assert interrupted["progress"]["checkpoint_available"]
    assert interrupted["progress"]["solver_calls"] >= before["progress"]["solver_calls"]
    workspace.control(trial["id"], ControlInput(action="stop"))
    workspace.reconcile()
    retained = workspace.store.get(trial["id"], "trial")
    assert retained["status"] == "stopped"
    assert retained["attempt"] == 1
    assert not alive(retained)


def research_usage(cost, *, pending=False):
    usage = {"calls": 1 if cost else 0, "input_tokens": 100, "output_tokens": 20,
             "cost_usd": cost, "reserved_cost_usd": cost, "pricing_known": True}
    if pending:
        usage["pending_reservation"] = {"id": "reservation-example", "cost_usd_reserved": cost}
    return usage


def dormant_research(workspace, charter, monkeypatch):
    coordinator = ResearchCoordinator(workspace)
    monkeypatch.setattr(coordinator, "_thread", lambda record: None)
    run = coordinator.start(ResearchInput(campaign_id=charter["id"], message="Test a bounded research decision"))
    return coordinator, run


def test_provider_reservation_is_durable_idempotent_and_restart_does_not_replay(workspaces, monkeypatch):
    original = workspaces()
    charter, _ = campaign(original, llm_budget_usd=1)
    coordinator, run = dormant_research(original, charter, monkeypatch)
    event = {"type": "provider_call_reserved", "role": "research_synthesizer", "usage": research_usage(.4, pending=True)}
    coordinator._emit(run["id"], event)
    coordinator._emit(run["id"], event)
    # Read through an independent SQLite connection, as after a process loss.
    durable = Store(original.directory).get(run["id"], "research_run")
    assert durable["usage"]["cost_usd"] == .4
    assert durable["usage"]["pending_reservation"]["id"] == "reservation-example"
    restarted = workspaces(original.directory)
    restarted.start()
    recovered = restarted.store.get(run["id"], "research_run")
    assert recovered["status"] == "needs_reconciliation"
    assert recovered["usage"] == durable["usage"]
    decisions = restarted.store.list("decision", charter["id"])
    assert len(decisions) == 1 and decisions[0]["options"][0]["id"] == "close_reserved"
    restored_coordinator = ResearchCoordinator(restarted)
    with pytest.raises(ValueError, match="interrupted"):
        restored_coordinator.control(run["id"], "resume")
    assert restarted.research_threads == {}
    restarted.close()
    third = workspaces(original.directory)
    third.start()
    assert len(third.store.list("decision", charter["id"])) == 1
    assert third.store.get(run["id"], "research_run")["usage"]["cost_usd"] == .4


def test_restart_reconciliation_is_idempotent_after_partial_database_write(workspaces, monkeypatch):
    original = workspaces()
    charter, _ = campaign(original, llm_budget_usd=1)
    coordinator, run = dormant_research(original, charter, monkeypatch)
    coordinator._emit(run["id"], {"type": "provider_call_reserved", "role": "research_synthesizer",
                                 "usage": research_usage(.4, pending=True)})
    real_put = original.store.put

    def fail_after_decision(kind, record, event=None):
        if kind == "research_run" and record.get("status") == "needs_reconciliation":
            raise OSError("Simulated process failure after persisting reconciliation decision")
        return real_put(kind, record, event)

    monkeypatch.setattr(original.store, "put", fail_after_decision)
    with pytest.raises(OSError, match="Simulated process failure"):
        original.start()
    original.close()
    assert len(original.store.list("decision", charter["id"])) == 1
    restarted = workspaces(original.directory)
    restarted.start()
    decisions = [item for item in restarted.store.list("decision", charter["id"])
                 if item.get("research_run_id") == run["id"] and item["status"] == "pending"]
    assert len(decisions) == 1
    assert restarted.store.get(run["id"], "research_run")["status"] == "needs_reconciliation"
    assert restarted.store.get(run["id"], "research_run")["usage"]["cost_usd"] == .4


def test_stop_before_provider_send_releases_reservation_and_makes_no_replay(workspaces, monkeypatch):
    from dqn_meent.workspace import research

    workspace = workspaces()
    charter, _ = campaign(workspace, llm_budget_usd=1)
    coordinator, run = dormant_research(workspace, charter, monkeypatch)

    def interrupted_before_send(request, context, emit):
        coordinator.control(run["id"], "stop")
        try:
            emit({"type": "provider_call_reserved", "role": "research_synthesizer", "usage": research_usage(.4, pending=True)})
        except ResearchCancelled:
            emit({"type": "provider_call_cancelled_before_send", "usage": research_usage(0)})
            raise
        pytest.fail("Cancelled reservation must not reach provider execution")

    monkeypatch.setattr(research, "run_research", interrupted_before_send)
    coordinator._run(run["id"])
    cancelled = workspace.store.get(run["id"], "research_run")
    assert cancelled["status"] == "stopped"
    assert cancelled["usage"]["cost_usd"] == cancelled["usage"]["calls"] == 0
    assert "pending_reservation" not in cancelled["usage"]
    assert any(event["kind"] == "research.reservation_released" for event in workspace.store.events(charter["id"]))
    with pytest.raises(ValueError):
        coordinator.control(run["id"], "resume")


def test_stop_during_provider_call_retains_actual_usage_and_checkpoint(workspaces, monkeypatch):
    from dqn_meent.workspace import research

    workspace = workspaces()
    charter, _ = campaign(workspace, llm_budget_usd=1)
    coordinator, run = dormant_research(workspace, charter, monkeypatch)

    def completes_after_stop(request, context, emit):
        emit({"type": "provider_call_reserved", "role": "research_synthesizer", "usage": research_usage(.6, pending=True)})
        coordinator.control(run["id"], "stop")
        emit({"type": "research_checkpoint", "fingerprint": "completed-call-fingerprint",
              "state": {"usage": research_usage(.2), "completed": ["research_synthesizer"], "trace": []}})
        pytest.fail("Stop must prevent the next provider role")

    monkeypatch.setattr(research, "run_research", completes_after_stop)
    coordinator._run(run["id"])
    stopped = workspace.store.get(run["id"], "research_run")
    assert stopped["status"] == "stopped"
    assert stopped["usage"]["cost_usd"] == .2
    assert stopped["checkpoint"]["completed"] == ["research_synthesizer"]
    assert "pending_reservation" not in stopped["usage"]
    next_run = coordinator.start(ResearchInput(campaign_id=charter["id"], message="Use remaining funds"))
    assert next_run["request"]["llm_budget_usd"] == pytest.approx(.8)


def test_resumed_discussion_cannot_reserve_funds_spent_by_another_run(workspaces, monkeypatch):
    workspace = workspaces()
    charter, _ = campaign(workspace, llm_budget_usd=1)
    coordinator, first = dormant_research(workspace, charter, monkeypatch)
    interrupted = workspace.store.get(first["id"], "research_run")
    interrupted.update(status="interrupted", usage=research_usage(.2),
                       checkpoint={"usage": research_usage(.2), "completed": ["assumption_reviewer"]},
                       resume_fingerprint="previous-checkpoint")
    workspace.store.put("research_run", interrupted)
    second = coordinator.start(ResearchInput(campaign_id=charter["id"], message="A separate bounded discussion"))
    completed = workspace.store.get(second["id"], "research_run")
    completed.update(status="completed", usage=research_usage(.5))
    workspace.store.put("research_run", completed)
    coordinator.control(first["id"], "resume")
    # Run A's historical request allowance was $1. Its proposed cumulative
    # usage of $0.6 would now push the campaign to $1.1 with run B's $0.5.
    with pytest.raises(ResearchCancelled):
        coordinator._emit(first["id"], {"type": "provider_call_reserved", "role": "research_synthesizer",
                                       "usage": research_usage(.6, pending=True)})
    blocked = workspace.store.get(first["id"], "research_run")
    assert blocked["usage"]["cost_usd"] == .2
    assert "fund" in blocked["error"] or "budget" in blocked["error"]
    coordinator._emit(first["id"], {"type": "provider_call_reserved", "role": "research_synthesizer",
                                   "usage": research_usage(.4, pending=True)})
    assert sum(item.get("usage", {}).get("cost_usd", 0) for item in workspace.store.list("research_run", charter["id"])) == pytest.approx(.9)


def test_stopping_discussion_blocks_new_or_resumed_provider_work(workspaces, monkeypatch):
    workspace = workspaces()
    charter, _ = campaign(workspace, llm_budget_usd=1)
    coordinator, interrupted = dormant_research(workspace, charter, monkeypatch)
    saved = workspace.store.get(interrupted["id"], "research_run")
    saved.update(status="interrupted", checkpoint={"usage": research_usage(.1)}, resume_fingerprint="saved")
    workspace.store.put("research_run", saved)
    active = coordinator.start(ResearchInput(campaign_id=charter["id"], message="Current discussion"))
    coordinator.control(active["id"], "stop")
    with pytest.raises(ValueError, match="active|finish|stopp|discussion"):
        coordinator.start(ResearchInput(campaign_id=charter["id"], message="Must wait for the in-flight request"))
    with pytest.raises(ValueError, match="active|finish|stopp|discussion"):
        coordinator.control(interrupted["id"], "resume")


def test_stop_after_last_checkpoint_prevents_delegated_probe_launch(workspaces, monkeypatch):
    from dqn_meent.workspace import research

    workspace = workspaces()
    charter, task = campaign(workspace, llm_budget_usd=1, autonomy="delegated")
    coordinator, run = dormant_research(workspace, charter, monkeypatch)

    def stop_before_result_commit(request, context, emit):
        emit({"type": "research_checkpoint", "fingerprint": "finished-role",
              "state": {"usage": research_usage(.2), "completed": ["research_synthesizer"], "trace": []}})
        coordinator.control(run["id"], "stop")
        return {"status": "completed", "usage": research_usage(.2), "decisions": [],
                "actions": [{"id": "late-probe", "kind": "probe", "title": "Late proposal", "algorithm": "random",
                             "task_id": task["id"], "rationale": "Returned after stop", "question": "Would this help?", "budget_calls": 8}]}

    monkeypatch.setattr(research, "run_research", stop_before_result_commit)
    coordinator._run(run["id"])
    stopped = workspace.store.get(run["id"], "research_run")
    assert stopped["status"] == "stopped"
    assert stopped["usage"]["cost_usd"] == .2
    assert workspace.store.list("trial", charter["id"]) == []
    assert not any(action["status"] == "executed_within_delegation" for action in workspace.store.list("action", charter["id"]))


def test_delegated_reconsideration_is_once_per_batch_and_respects_pending_judgment(workspaces, monkeypatch):
    workspace = workspaces()
    charter, task = campaign(workspace, autonomy="delegated")
    coordinator = ResearchCoordinator(workspace)
    launched = []
    monkeypatch.setattr(coordinator, "_thread", lambda record: launched.append(record["id"]))
    trial = workspace.create_trial(trial_request(charter, task, max_steps=8))
    trial.update(status="completed", research_pending=True, progress={"step": 8, "solver_calls": 7, "best_efficiency": .2})
    workspace.store.put("trial", trial)
    decision = {"id": "expert-question", "campaign_id": charter["id"], "charter_version": 1,
                "status": "pending", "title": "Interpret startup-limited evidence"}
    workspace.store.put("decision", decision)
    coordinator.reconsider_finished()
    assert launched == []
    assert workspace.store.get(trial["id"], "trial")["research_pending"]
    decision["status"] = "resolved"
    workspace.store.put("decision", decision)
    coordinator.reconsider_finished()
    coordinator.reconsider_finished()
    assert len(launched) == 1
    assert not workspace.store.get(trial["id"], "trial")["research_pending"]
    automatic = workspace.store.get(launched[0], "research_run")
    assert automatic["automatic"]
    assert trial["id"] in automatic["request"]["message"]


def test_delegated_reconsideration_leaves_researcher_stopped_branches_stopped(workspaces, monkeypatch):
    workspace = workspaces()
    charter, task = campaign(workspace, autonomy="delegated")
    coordinator = ResearchCoordinator(workspace)
    launched = []
    monkeypatch.setattr(coordinator, "_thread", lambda record: launched.append(record["id"]))
    stopped = workspace.create_trial(trial_request(charter, task))
    stopped.update(status="stopped", stopped_by="researcher", research_pending=True)
    workspace.store.put("trial", stopped)
    coordinator.reconsider_finished()
    assert launched == []
    assert workspace.store.get(stopped["id"], "trial")["status"] == "stopped"
    assert not workspace.store.get(stopped["id"], "trial")["research_pending"]
