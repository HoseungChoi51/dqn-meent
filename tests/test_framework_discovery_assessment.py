"""Rough tuning retains poor runs and gates family conclusions on adequacy."""
from copy import deepcopy

import pytest

from test_framework_discovery import setup, start, command
from optimization_framework.execution.worker import run
from optimization_framework.research.discovery.tuning import sample


def prepared(setup, **plan_changes):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, experiment_compute_seconds=60)
    candidate = {"id": "candidate_fixture", "campaign_id": campaign["id"], "session_id": session["id"],
        "family_id": "family_fixture", "task_id": workspace.discovery.tasks(session)[0]["id"],
        "title": "Coordinate fixture", "algorithm_config": {}, "origin": "fixture"}
    workspace.store.put_immutable("discovery_candidate", candidate)
    workspace.store.put("hypothesis", {"id": "hypothesis_" + candidate["id"], "campaign_id": campaign["id"],
        "algorithm": "coordinate", "algorithm_config": {}, "status": "proposed"})
    plan = {"candidate_id": candidate["id"], "question": "Does radius affect early improvement?",
        "mechanism_predictions": ["Small radii trade early coverage for precision"],
        "configurations": {"strategy": "explicit", "count": 3, "initial": [{"algorithm_config": {"radius": radius}} for radius in (.1, .2, .4)]},
        "seeds": [0, 1], "evaluations_per_trial": 4, "wall_seconds_per_trial": 5,
        "startup_requirements": "One initial point and coordinate probes", "minimum_informative_evaluations": 4,
        "minimum_configurations": 3, "minimum_seeds_per_configuration": 2,
        "extension_rule": "Extend promising configurations before drawing an effectiveness conclusion",
        "early_stopping_rule": "Stop failed or infeasible runs and retain their costs", **plan_changes}
    cmd = command(workspace, campaign, "discovery.assessment.save", {"session_id": session["id"], "plan": plan}, "assessment_fixture")
    assessment = workspace.commands.execute(cmd)["outcome"]["assessment"]
    assert workspace.commands.execute(cmd)["outcome"]["assessment"] == assessment
    return workspace, campaign, session, assessment


def launch(workspace, campaign, assessment):
    readiness = workspace.discovery.assessments.readiness(assessment["id"])
    assert readiness["ready"], readiness["blockers"]
    cmd = command(workspace, campaign, "discovery.assessment.launch", {"assessment_id": assessment["id"],
        "expected_readiness_hash": readiness["readiness_hash"]}, "launch_fixture")
    receipt = workspace.commands.execute(cmd)
    assert workspace.commands.execute(cmd) == receipt
    return receipt["outcome"]["trial_ids"]


def test_seeded_sampling_handles_conditionals_constraints_and_duplicates():
    space = {"strategy": "random", "seed": 41, "count": 8, "parameters": [
        {"name": "algorithm_config.mode", "kind": "categorical", "values": ["local", "global"]},
        {"name": "algorithm_config.radius", "kind": "real", "low": .001, "high": 1, "scale": "log", "active_when": {"algorithm_config.mode": "local"}},
        {"name": "training.batch_size", "kind": "integer", "low": 2, "high": 16},
        {"name": "training.buffer_size", "kind": "integer", "low": 4, "high": 32}],
        "constraints": [{"left": "training.batch_size", "operator": "le", "right_parameter": "training.buffer_size"}]}
    selected = sample(space)
    assert selected == sample(space)
    assert all(row["training"]["batch_size"] <= row["training"]["buffer_size"] for row in selected)
    assert all(("radius" in row["algorithm_config"]) == (row["algorithm_config"]["mode"] == "local") for row in selected)
    assert selected != sample({**space, "seed": 42})
    with pytest.raises(ValueError, match="distinct"):
        sample({"count": 2, "initial": [{}, {}]})
    with pytest.raises(ValueError, match="follow"):
        sample({**space, "parameters": list(reversed(space["parameters"]))})


def test_manager_assessment_tools_preserve_authority_replay_and_real_measurements(setup):
    workspace, campaign, session, assessment = prepared(setup)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    task.update(status="waiting", wait_reason="tools")
    workspace.store.put("discovery_task", task)
    request = {"id": "tool_manager_launch", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": task["id"], "charter_version": campaign["version"], "guidance_revision": 0,
        "call": {"tool": "assessment.launch", "arguments": {"assessment_id": assessment["id"]}}}
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.discovery.tools.execute(request)
    campaign["autonomy"] = "delegated"
    workspace.store.put("campaign", campaign)
    launched = workspace.discovery.tools.execute(request)
    assert len(launched["trial_ids"]) == 6
    assert workspace.discovery.tools.execute(request) == launched
    assert len(workspace.store.list("trial")) == 6
    # A lost tool reply must recover the accepted application receipt.
    workspace.store.put("discovery_tool", {**request, "status": "running"})
    workspace.discovery.tools.recover()
    assert workspace.store.get("receipt_" + request["id"])["result"] == launched
    assert workspace.store.get("command_" + request["id"])["actor"] == "manager"
    for trial_id in launched["trial_ids"]:
        result = run(workspace.job_dir(trial_id))
        assert result["scientific_complete"]
        trial = workspace.store.get(trial_id)
        trial["status"] = "running"
        workspace.store.put("trial", trial)
    workspace.reconcile()
    waited = workspace.discovery.tools.execute({**request, "call": {"tool": "assessment.wait", "arguments": {"assessment_id": assessment["id"]}}})
    assert waited["evidence"]["adequate"] and len(waited["evidence"]["measurements"]) == 6
    specialist = workspace.discovery.tasks(session)[0]
    with pytest.raises(ValueError, match="Only the campaign manager"):
        workspace.discovery.tools.execute({**request, "id": "specialist_launch", "task_id": specialist["id"]})


def test_actual_continuous_tuning_preserves_all_runs_and_requires_review_before_rejection(setup):
    workspace, campaign, session, assessment = prepared(setup)
    assert len(workspace.store.list("experiment_draft")) == 6 and not workspace.store.list("trial")
    assert workspace.discovery.assessments.evidence(assessment["id"])["status"] == "under_evaluated"
    ids = launch(workspace, campaign, assessment)
    assert len(ids) == len(set(ids)) == 6
    for trial_id in ids:
        result = run(workspace.job_dir(trial_id))
        assert result["scientific_complete"] and result["evaluations"] == 4
        trial = workspace.store.get(trial_id, "trial")
        trial.update(status="running")
        workspace.store.put("trial", trial)
    workspace.reconcile()
    evidence = workspace.discovery.assessments.evidence(assessment["id"])
    assert evidence["adequate"] and evidence["informative_configurations"] == 3, evidence
    assert len(evidence["measurements"]) == 6
    assert all(workspace.store.get(trial_id)["discovery_session_id"] == session["id"] for trial_id in ids)
    rejection = command(workspace, campaign, "discovery.assessment.decide", {"assessment_id": assessment["id"],
        "outcome": "adequately_assessed_deprioritized", "rationale": "Fixture conclusion", "evidence_ids": ids,
        "limitations": ["Only this tiny fixture problem"]}, "reject_without_review")
    with pytest.raises(ValueError, match="independent review"):
        workspace.commands.execute(rejection)
    assert len(workspace.store.list("trial")) == 6


def test_missing_implementation_does_not_prevent_design_and_insufficient_training_remains_visible(setup):
    workspace, campaign, session, assessment = prepared(setup, minimum_training_updates=100)
    hypothesis = workspace.store.get("hypothesis_candidate_fixture", "hypothesis")
    hypothesis["algorithm"] = "custom"
    workspace.store.put("hypothesis", hypothesis)
    readiness = workspace.discovery.assessments.readiness(assessment["id"])
    assert not readiness["ready"] and any(row["code"].startswith("implementation_") for row in readiness["blockers"])
    hypothesis["algorithm"] = "coordinate"
    workspace.store.put("hypothesis", hypothesis)
    ids = launch(workspace, campaign, assessment)
    # These recorded complete numerical horizons lack the declared training.
    for trial_id in ids:
        trial = workspace.store.get(trial_id)
        trial.update(status="completed", result={"scientific_complete": True, "evaluations": 4, "diagnostics": {"updates": 0}, "origin": "fixture"})
        workspace.store.put("trial", trial)
    evidence = workspace.discovery.assessments.evidence(assessment["id"])
    assert not evidence["adequate"] and {row["reason"] for row in evidence["missing"]} == {"minimum_training_updates_not_observed"}
    revised = deepcopy(assessment["plan"])
    revised.update(predecessor_assessment_id=assessment["id"], minimum_training_updates=0)
    with pytest.raises(ValueError, match="retroactively relax"):
        workspace.commands.execute(command(workspace, campaign, "discovery.assessment.save",
            {"session_id": session["id"], "plan": revised}, "relaxed_amendment"))
