"""Faults at model/result/command boundaries must not repeat paid work."""
from copy import deepcopy
import threading
import time

import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, ResearchInput, TaskInput, TrialInput
from optimization_framework.execution.service import Workspace
from optimization_framework.research.lifecycle import recover
from optimization_framework.storage.sqlite import now


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Durable manager", autonomy="delegated",
        compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    manager = CampaignManager(workspace)
    dispatched = []
    monkeypatch.setattr(manager, "_thread", lambda run: dispatched.append(run["id"]))
    return workspace, campaign, manager, dispatched


def request(workspace, campaign, operation, payload, identity):
    return Command(id=identity, campaign_id=campaign["id"], operation=operation,
        expected_revision=workspace.store.get(campaign["id"], "campaign")["version"],
        expected_guidance_revision=workspace.memory.state(campaign["id"])["guidance_revision"],
        expected_authority_hash=workspace.commands.authority_hash(workspace.store.get(campaign["id"], "campaign")), payload=payload)


def proposal(workspace, campaign, **payload):
    return {"id": "proposed_coordinate", "kind": "command", "title": "Measure a coordinate probe", "rationale": "Check the local response.",
        "command_operation": "trial.create", "command_payload": {"task_id": workspace.current_tasks(campaign["id"])[0]["id"],
            "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5, **payload}, "status": "proposed"}


def result(actions=None):
    return {"status": "completed", "mode": "llm", "usage": {"calls": 1, "input_tokens": 10, "output_tokens": 5,
        "cost_usd": .01, "api_cost_usd": .01, "reserved_cost_usd": .01, "elapsed_seconds": .2}, "trace": [],
        "messages": [{"role": "assistant", "content": "The coordinate probe is bounded and informative."}],
        "hypotheses": [], "decisions": [], "actions": actions or []}


def test_unavailable_provider_retains_inbox_and_one_issue_without_model_turn(setup, monkeypatch):
    workspace, campaign, manager, dispatched = setup
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": False})
    manager.start(ResearchInput(campaign_id=campaign["id"], message="Find the next experiment."))
    manager.start(ResearchInput(campaign_id=campaign["id"], message="Keep the baseline."))
    manager.tick()
    restarted = Workspace(workspace.directory)
    resumed = CampaignManager(restarted)
    resumed.tick()
    assert len(restarted.store.list("manager_command")) == 2
    assert len(restarted.store.list("manager_input")) == 2
    assert len(restarted.store.list("manager_issue")) == 1
    assert not restarted.store.list("research_run") and not dispatched
    # The unavailable reasoning service does not own the numerical controls.
    accepted = restarted.commands.execute(request(restarted, campaign, "trial.create", proposal(workspace, campaign)["command_payload"], "manual_probe"))
    assert restarted.store.get(accepted["outcome"]["trial_id"])["status"] == "queued"
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    monkeypatch.setattr(resumed, "_thread", lambda run: dispatched.append(run["id"]))
    resumed.tick()
    assert len(dispatched) == 1
    assert restarted.store.list("manager_issue")[0]["status"] == "resolved"
    assert sum(row["status"] == "consumed" for row in restarted.store.list("manager_input")) == 1


def test_completion_events_and_cursor_survive_duplicate_delivery_and_restart(setup, monkeypatch):
    workspace, campaign, manager, dispatched = setup
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], **proposal(workspace, campaign)["command_payload"]))
    trial.update(status="completed", result={"best_objective": 1}, attempt=1)
    workspace.store.put("trial", trial, "trial.evidence_cataloged")
    manager.tick()
    assert len(dispatched) == 1
    run = workspace.store.get(dispatched[0])
    original = deepcopy(run["context_snapshot"])
    run.update(status="completed")
    workspace.store.put("research_run", run)
    workspace.store.event(campaign["id"], "trial.evidence_cataloged", {"record_id": trial["id"]})
    restarted = Workspace(workspace.directory)
    resumed = CampaignManager(restarted)
    monkeypatch.setattr(resumed, "_thread", lambda record: dispatched.append(record["id"]))
    resumed.tick()
    resumed.tick()
    assert len(dispatched) == 1
    assert len(restarted.store.list("manager_input")) == 1
    assert restarted.store.list("manager_input")[0]["research_run_id"] == run["id"]
    assert restarted.store.get(run["id"])["context_snapshot"] == original
    assert restarted.store.get("manager_inbox_cursor_" + campaign["id"])["position"] > 0


def test_saved_model_result_recovery_is_atomic_and_never_calls_provider_again(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Design a bounded probe."))
    answer = result([proposal(workspace, campaign)])
    calls = []
    def model(*args):
        calls.append(1)
        return deepcopy(answer)
    monkeypatch.setattr("optimization_framework.research.engine.run_research", model)
    original = workspace.store.put
    def fail_projection(kind, record, event=None):
        if kind == "action":
            raise RuntimeError("Crash while projecting the saved output")
        return original(kind, record, event)
    monkeypatch.setattr(workspace.store, "put", fail_projection)
    manager._run(run["id"])
    assert len(calls) == 1 and len(workspace.store.list("research_result")) == 1
    assert not workspace.store.list("action") and not workspace.store.list("trial")
    assert not [row for row in workspace.store.list("outbox") if row["kind"] == "manager_action"]
    assert not [row for row in workspace.store.list("message") if row["role"] == "assistant" and row.get("origin") == "llm"]
    restarted = Workspace(workspace.directory)
    resumed = CampaignManager(restarted)
    monkeypatch.setattr(resumed, "_thread", lambda record: None)
    recover(restarted)
    resumed._run(run["id"])
    resumed._run(run["id"])
    assert len(calls) == 1 and len(restarted.store.list("trial")) == 1
    assert len([row for row in restarted.store.list("message") if row.get("origin") == "llm"]) == 1
    assert restarted.store.get(run["id"])["finalized_result_id"] == "research_result_" + run["id"]


def test_action_lost_ack_reconciles_before_later_guidance(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Proceed with a probe."))
    monkeypatch.setattr("optimization_framework.research.engine.run_research", lambda *args: result([proposal(workspace, campaign)]))
    original = manager.execute_action
    attempts = []
    def lose_reply(action, **kwargs):
        reply = original(action, **kwargs)
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Lost acknowledgement")
        return reply
    monkeypatch.setattr(manager, "execute_action", lose_reply)
    manager._run(run["id"])
    assert len(workspace.store.list("trial")) == 1
    effect = next(row for row in workspace.store.list("outbox") if row["kind"] == "manager_action")
    assert effect["status"] == "pending"
    current = workspace.memory.sync(campaign["id"])
    workspace.memory.edit(campaign["id"], "Now defer additional probes.", current["revision"])
    workspace.dispatch_outbox()
    assert workspace.store.get(effect["id"])["status"] == "completed"
    assert len(workspace.store.list("trial")) == 1 and len(attempts) == 2


def test_changed_guidance_retains_stale_output_and_reconsiders_once(setup, monkeypatch):
    workspace, campaign, manager, dispatched = setup
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Design a probe."))
    def model(*args):
        current = workspace.memory.sync(campaign["id"])
        workspace.memory.edit(campaign["id"], "Compare convergence before launching optimization.", current["revision"])
        return result([proposal(workspace, campaign)])
    monkeypatch.setattr("optimization_framework.research.engine.run_research", model)
    manager._run(run["id"])
    assert not workspace.store.list("trial")
    assert workspace.store.get("proposed_coordinate")["status"] == "needs_reconsideration"
    assert workspace.store.get(run["id"])["result"]["stale_charter"]
    assert len(dispatched) == 2
    reconsidered = workspace.store.get(dispatched[-1])
    assert reconsidered["guidance_revision"] > run["guidance_revision"]
    assert reconsidered["manager_input_ids"]
    manager.tick()
    assert len(dispatched) == 2


def test_issue_blocks_only_affected_manager_action_and_accepted_replay_survives(setup):
    workspace, campaign, _, _ = setup
    bad_task = workspace.current_tasks(campaign["id"])[0]
    campaign2 = workspace.create_campaign(CampaignInput(name="Independent", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    # Both tasks belong to the original campaign for a true within-campaign check.
    other_task = {**workspace.current_tasks(campaign2["id"])[0], "id": "independent_task", "campaign_id": campaign["id"]}
    workspace.store.put("task", other_task)
    blocked = request(workspace, campaign, "trial.create", proposal(workspace, campaign)["command_payload"], "blocked_probe")
    workspace.memory.issue(campaign["id"], "numerical_check", "The evaluator check needs review.", affected=bad_task["id"])
    with pytest.raises(ValueError, match="pending manager issue"):
        workspace.commands.execute(blocked, actor="manager")
    independent = request(workspace, campaign, "trial.create", proposal(workspace, campaign, task_id=other_task["id"])["command_payload"], "independent_probe")
    accepted = workspace.commands.execute(independent, actor="manager")
    workspace.memory.issue(campaign["id"], "later_issue", "Review later evidence.", affected=other_task["id"])
    assert workspace.commands.execute(independent, actor="manager") == accepted
    assert len(workspace.store.list("trial", campaign["id"])) == 1


def test_uncertain_call_blocks_later_turn_and_resume_dispatch_gap_is_recoverable(setup, monkeypatch):
    workspace, campaign, manager, dispatched = setup
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="First turn."))
    stored = workspace.store.get(run["id"])
    stored["usage"] = {"calls": 1, "pending_reservation": {"id": "unknown_call"}}
    workspace.store.put("research_run", stored)
    recover(workspace)
    manager.start(ResearchInput(campaign_id=campaign["id"], message="Later turn."))
    manager.tick()
    assert len(dispatched) == 1
    assert workspace.store.get(run["id"])["status"] == "needs_reconciliation"
    assert len(workspace.store.list("decision")) == 1
    # A different campaign simulates a committed resume before thread.start.
    second = workspace.create_campaign(CampaignInput(name="Recover unsent resume", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    unsent = manager.start(ResearchInput(campaign_id=second["id"], message="Resume from a completed role."))
    unsent = workspace.store.get(unsent["id"])
    unsent.update(checkpoint={"completed": ["reviewer"], "usage": {"calls": 1}}, resume_fingerprint="pinned",
        dispatch_phase="queued", last_resume_effect="resume_accepted")
    workspace.store.put("research_run", unsent)
    recover(workspace)
    assert workspace.store.get(unsent["id"])["dispatch_phase"] == "recovery_pending"
    assert workspace.store.get(unsent["id"])["checkpoint"]["usage"]["calls"] == 1


def test_workspace_lease_remains_owned_until_a_returning_model_receipt_is_saved(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    monkeypatch.undo()
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    entered, release = threading.Event(), threading.Event()
    def model(*args):
        entered.set()
        assert release.wait(10)
        return result()
    monkeypatch.setattr("optimization_framework.research.engine.run_research", model)
    workspace.start()
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Wait for the captured model reply."))
    assert entered.wait(2)
    workspace.close()
    with pytest.raises(RuntimeError, match="Another service"):
        Workspace(workspace.directory).start()
    release.set()
    workspace.research_threads[run["id"]].join(timeout=5)
    deadline = time.monotonic() + 5
    while workspace._lease and time.monotonic() < deadline:
        time.sleep(.02)
    assert workspace._lease is None
    restarted = Workspace(workspace.directory)
    restarted.start()
    try:
        assert restarted.store.get("research_result_" + run["id"])["result"]["usage"]["calls"] == 1
    finally:
        restarted.close()


def test_repeated_rejected_proposal_does_not_create_an_unbounded_reasoning_loop(setup, monkeypatch):
    workspace, campaign, manager, dispatched = setup
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Check whether the requested work is eligible."))
    calls = []
    def model(*args):
        calls.append(1)
        action = proposal(workspace, campaign, wall_seconds=10000)
        action["id"] = "invalid_proposal_" + str(len(calls))
        return result([action])
    monkeypatch.setattr("optimization_framework.research.engine.run_research", model)
    manager._run(run["id"])
    assert len(dispatched) == 2
    manager._run(dispatched[-1])
    manager.tick()
    manager.tick()
    assert len(dispatched) == len(calls) == 2
    assert not workspace.store.list("trial")
    assert len(workspace.store.list("command_rejection")) == 2
    assert all(row["status"] == "consumed" for row in workspace.store.list("manager_input"))


def test_completed_implementation_input_waits_for_binding(setup):
    workspace, campaign, manager, dispatched = setup
    grant = {"id": "binding_grant", "campaign_id": campaign["id"], "status": "completed", "job_id": "job_one", "version_id": "version_one"}
    workspace.store.put("implementation_grant", grant, "implementation.updated")
    # Only consume inputs here; this abbreviated grant has no commissioning payload.
    from optimization_framework.campaigns.inbox import consume_events
    consume_events(workspace, campaign["id"])
    assert not workspace.store.list("manager_input")
    grant["attached"] = True
    workspace.store.put("implementation_grant", grant, "implementation.ready")
    consume_events(workspace, campaign["id"])
    assert len(workspace.store.list("manager_input")) == 1
