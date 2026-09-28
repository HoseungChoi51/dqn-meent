"""Executable probes must not be inferred from conditional study prose."""
from copy import deepcopy

import pytest

from test_framework_manager_lifecycle import setup, result, proposal
from optimization_framework.contracts.requests import ResearchInput


def probe(workspace, campaign, **changes):
    return {"id": "proposal_probe", "kind": "probe", "title": "A bounded single trial", "rationale": "Collect actual evidence",
        "question": "What is the measured baseline?", "task_id": workspace.current_tasks(campaign["id"])[0]["id"],
        "budget_calls": 2, "wall_seconds": 5, "campaign_id": campaign["id"], "status": "proposed", **changes}


def test_conditional_multitrial_plan_never_becomes_random_trial(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    action = probe(workspace, campaign, requires_researcher=True, hypothesis_id=None, budget_calls=960,
        title="Conditional fresh-seed annealing comparison", rationale="Do not launch until the study, implementations and allocation are ready.",
        expected_information="Ten trials of three temperature configurations and two shared seeds.")
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Compare proposals"))
    monkeypatch.setattr("optimization_framework.research.engine.run_research", lambda *args: result([action]))
    manager._run(run["id"])
    assert not workspace.store.list("trial")
    assert not [row for row in workspace.store.list("outbox") if row["kind"] == "manager_action"]
    assert any(row.get("action_id") == action["id"] for row in workspace.store.list("decision"))
    with pytest.raises(ValueError, match="explicitly requires a researcher"):
        manager.execute_action(action, actor="manager")
    with pytest.raises(ValueError, match="saved hypothesis or an explicit algorithm"):
        manager.execute_action(action, actor="researcher")


def test_model_plan_returns_to_manager_for_design_instead_of_approval(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    action = probe(workspace, campaign, probe_scope="plan", requires_researcher=False, algorithm="random")
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Design comparisons"))
    monkeypatch.setattr("optimization_framework.research.engine.run_research", lambda *args: result([action]))
    manager._run(run["id"])
    assert not workspace.store.list("trial")
    assert workspace.store.get(action["id"])["status"] == "blocked"
    assert any(row["code"] == "experiment_design" for row in workspace.store.list("manager_issue"))
    assert not [row for row in workspace.store.list("decision") if row.get("action_id") == action["id"]]
    with pytest.raises(ValueError, match="conditional or multi-trial plan"):
        manager.execute_action(action, actor="researcher")


@pytest.mark.parametrize("actor", ["researcher", "manager"])
def test_missing_optimizer_never_defaults_to_random(setup, actor):
    workspace, campaign, manager, _ = setup
    with pytest.raises(ValueError, match="No random baseline is inferred"):
        manager.execute_action(probe(workspace, campaign, probe_scope="single_trial"), actor=actor)
    assert not workspace.store.list("trial")


def test_explicit_single_trial_preserves_optimizer_config_seed_and_limits(setup):
    workspace, campaign, manager, _ = setup
    action = probe(workspace, campaign, probe_scope="single_trial", requires_researcher=False,
        algorithm="coordinate", algorithm_config={"radius": .2}, seed=7)
    outcome = manager.execute_action(action, actor="manager")
    trial = workspace.store.get(outcome["trial_id"])
    assert trial["algorithm"] == "coordinate" and trial["algorithm_config"] == {"radius": .2}
    assert trial["seed"] == 7 and trial["max_steps"] == 2 and trial["wall_seconds"] == 5
    assert manager.execute_action(action, actor="manager") == outcome
    assert len(workspace.store.list("trial")) == 1


def test_explicit_random_baseline_is_allowed(setup):
    workspace, campaign, manager, _ = setup
    outcome = manager.execute_action(probe(workspace, campaign, probe_scope="single_trial", algorithm="random"), actor="manager")
    assert workspace.store.get(outcome["trial_id"])["algorithm"] == "random"


def test_researcher_required_typed_command_waits_but_explicit_approval_still_works(setup, monkeypatch):
    workspace, campaign, manager, _ = setup
    action = {**proposal(workspace, campaign), "requires_researcher": True}
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Review this proposed trial"))
    monkeypatch.setattr("optimization_framework.research.engine.run_research", lambda *args: result([action]))
    manager._run(run["id"])
    action = workspace.store.get(action["id"])
    assert not workspace.store.list("trial")
    with pytest.raises(ValueError, match="explicitly requires a researcher"):
        manager.execute_action(action, actor="manager")
    outcome = manager.execute_action(action, actor="researcher")
    assert workspace.store.get(outcome["trial_id"])["algorithm"] == "coordinate"


def test_approved_concrete_legacy_hypothesis_probe_remains_supported(setup):
    workspace, campaign, manager, _ = setup
    hypothesis = {"id": "saved_coordinate", "campaign_id": campaign["id"], "algorithm": "coordinate",
        "algorithm_config": {"radius": .25}, "implementation_status": "ready", "executable": True}
    workspace.store.put("hypothesis", hypothesis)
    action = probe(workspace, campaign, hypothesis_id=hypothesis["id"], requires_researcher=True)
    outcome = manager.execute_action(action, actor="researcher")
    trial = workspace.store.get(outcome["trial_id"])
    assert trial["algorithm"] == "coordinate" and trial["hypothesis_id"] == hypothesis["id"]
    assert trial["algorithm_config"] == {"radius": .25}
    changed = {**deepcopy(action), "id": "conflicting_probe", "algorithm": "random"}
    with pytest.raises(ValueError, match="conflicts with its saved hypothesis"):
        manager.execute_action(changed, actor="researcher")
