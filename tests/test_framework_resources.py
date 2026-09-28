"""Admission conserves authorizations across diagnostics, retries and restarts."""
import time
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, ControlInput
from optimization_framework.contracts.resources import ExecutionGrant
from optimization_framework.evaluation.diagnostics import reconcile as reconcile_diagnostics
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import ExperimentWorker
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import read_json


def setup(tmp_path, *, seconds=100, cutoff=60, grace=5):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Staged research", compute_budget_seconds=seconds,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    start = time.time()
    grant = workspace.resources.create(ExecutionGrant(id="grant_test", campaign_id=campaign["id"], owner_id="execution_test",
        worker_seconds=seconds, starts_at=start, deadline_at=start+cutoff, max_workers=1,
        stop_grace_seconds=grace, authority="researcher"))
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", max_steps=5, wall_seconds=10)
    return workspace, request, grant


def test_execution_grant_counts_once_and_concurrent_admission_cannot_overspend(tmp_path):
    workspace, request, grant = setup(tmp_path, seconds=20)
    assert workspace.allocated_seconds(request.campaign_id) == 20
    execution = {"execution_grant_id": grant["id"]}
    def admit(_):
        try:
            return workspace.create_trial(request, execution=execution)
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(4)))
    assert len([row for row in results if isinstance(row, dict)]) == 2
    assert len(workspace.store.list("resource_reservation")) == 2
    assert workspace.allocated_seconds(request.campaign_id) == 20
    with pytest.raises(ValueError, match="remaining campaign budget"):
        workspace.create_trial(request)
    with pytest.raises(ValueError, match="active reservations"):
        workspace.resources.release(grant["id"], rationale="Do not steal active allocation")
    for trial in workspace.store.list("trial"):
        workspace.control(trial["id"], ControlInput(action="stop"))
    assert workspace.allocated_seconds(request.campaign_id) == 20
    workspace.resources.release(grant["id"], rationale="Abandoned study")
    workspace.resources.release(grant["id"], rationale="Repeated delivery")
    assert workspace.allocated_seconds(request.campaign_id) == 0
    assert len(workspace.store.list("execution_grant_release")) == 1
    with pytest.raises(ValueError, match="released"):
        workspace.create_trial(request, execution=execution)


def test_diagnostic_transfer_uses_parent_grant_and_releases_unused_capacity(tmp_path):
    workspace, request, grant = setup(tmp_path, seconds=15)
    request = request.model_copy(update={"diagnostics": TrialInput(**{**request.model_dump(), "diagnostics": [
        {"at_counts": [2], "export_optimizer": False, "recipes": [{"recipe_id": "analytic_fixtures:v1", "wall_seconds": 5}]}]}).diagnostics})
    parent = workspace.create_trial(request, execution={"execution_grant_id": grant["id"]})
    assert workspace.resources.assessment(request.campaign_id)["grants"][0]["member_committed_seconds"] == 15
    worker = ExperimentWorker(workspace.job_dir(parent["id"]))
    result = worker.run()
    parent.update(status="completed", result=result, progress=result, execution_seconds=result["elapsed_seconds"], attempt=1)
    workspace.store.put("trial", parent)
    workspace.capture_evidence(parent)
    reconcile_diagnostics(workspace)
    reconcile_diagnostics(workspace)
    children = [row for row in workspace.store.list("trial") if row.get("parent_trial_id")]
    assert len(children) == 1
    child = children[0]
    assert child["execution_grant_id"] == grant["id"]
    assert child["absolute_deadline"] == parent["absolute_deadline"]
    assessment = workspace.resources.assessment(request.campaign_id)
    assert assessment["allocated_seconds"] == 15
    assert assessment["grants"][0]["member_committed_seconds"] == pytest.approx(result["elapsed_seconds"] + 5)
    result = ExperimentWorker(workspace.job_dir(child["id"])).run()
    child.update(status="completed", result=result, execution_seconds=result["elapsed_seconds"])
    workspace.store.put("trial", child)
    workspace.resources.release(grant["id"], rationale="All admitted work is terminal")
    assessment = workspace.resources.assessment(request.campaign_id)
    assert assessment["allocated_seconds"] == assessment["actual_seconds"]
    assert assessment["allocated_seconds"] < 15


def test_worker_enforces_frozen_absolute_cutoff_without_live_supervisor(tmp_path):
    workspace, request, grant = setup(tmp_path, cutoff=1)
    trial = workspace.create_trial(request.model_copy(update={"max_steps": 1000000, "wall_seconds": 10}),
        execution={"execution_grant_id": grant["id"]})
    assert trial["experiment_spec"]["schedule"]["absolute_deadline"] == grant["deadline_at"]
    result = ExperimentWorker(workspace.job_dir(trial["id"])).run()
    assert result["reason"] == "study_deadline_reached"
    assert result["status"] == "budget_exhausted" and not result["scientific_complete"]
    assert result["absolute_deadline"] == grant["deadline_at"]
    restarted = Workspace(tmp_path)
    restarted.reconcile()
    expired = restarted.store.get(trial["id"], "trial")
    assert expired["status"] == "budget_exhausted" and expired["absolute_deadline"] == grant["deadline_at"]
    with pytest.raises(ValueError, match="admission window"):
        restarted.create_trial(request, execution={"execution_grant_id": grant["id"]})
    with pytest.raises(ValueError, match="frozen"):
        restarted.control(trial["id"], ControlInput(action="extend", wall_seconds=20))


def test_production_worker_envelope_is_distinct_from_twenty_four_hour_deadline(tmp_path):
    workspace, request, grant = setup(tmp_path, seconds=8*24*3600, cutoff=24*3600)
    assert grant["worker_seconds"] == 691200
    assert grant["deadline_at"] - grant["starts_at"] == 86400
    assert workspace.allocated_seconds(request.campaign_id) == 691200


@pytest.mark.parametrize("phase", ["initialization", "evaluation", "checkpoint", "cleanup"])
def test_deadline_guard_stops_stalled_attempt_and_restart_retains_uncertain_costs(tmp_path, phase):
    from optimization_framework.execution.service import process_identity
    workspace, request, grant = setup(tmp_path, cutoff=3, grace=.2)
    trial = workspace.create_trial(request.model_copy(update={"max_steps": 1}), execution={"execution_grant_id": grant["id"]})
    assert trial["experiment_spec"]["schedule"]["stop_grace_seconds"] == .2
    assert "optimization_framework/execution/watchdog.py" in trial["execution_manifest"]["scientific_files"]
    directory = workspace.job_dir(trial["id"])
    trial.update(status="running", attempt=1, attempt_started_at=time.time())
    atomic_json(directory / "spec.json", trial)
    script = '''
from pathlib import Path
import sys, time
from optimization_framework.execution import worker
directory, phase = Path(sys.argv[1]), sys.argv[2]
def stall(*args, **kwargs):
    (directory / "entered-phase").write_text(phase)
    time.sleep(60)
original = worker.ExperimentWorker.__init__
def initialize(self, *args, **kwargs):
    if phase == "initialization":
        stall()
    original(self, *args, **kwargs)
    if phase == "evaluation":
        self.evaluator.evaluate = stall
    elif phase == "checkpoint":
        self.save_checkpoint = stall
    elif phase == "cleanup":
        self.optimizer.close = stall
worker.ExperimentWorker.__init__ = initialize
worker.run(directory, enforce_deadline=True)
'''
    env = {**os.environ, "PYTHONPATH": str(directory / "code"), "OPENBLAS_NUM_THREADS": "1"}
    process = subprocess.Popen([sys.executable, "-c", script, str(directory), phase], cwd=directory,
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        trial.update(pid=process.pid, process_identity=process_identity(process.pid))
        workspace.store.put("trial", trial)
        _, errors = process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL, errors.decode()
        assert (directory / "entered-phase").read_text() == phase
        receipt = read_json(directory / "deadline-enforcement.json")
        assert receipt["elapsed_seconds"] > 0 and receipt["grace_seconds"] == .2
        # No scheduler or supervisor was running. Restart reconciles the exact
        # dead attempt instead of interpreting its missing result as a retry.
        restarted = Workspace(tmp_path)
        restarted.reconcile()
        stopped = restarted.store.get(trial["id"], "trial")
        assert stopped["status"] == "budget_exhausted" and stopped["stopped_by"] == "deadline"
        assert stopped["deadline_overshoot_basis"] == "termination_request"
        assert .1 <= stopped["deadline_overshoot_seconds"] < 2
        assert stopped["execution_seconds"] >= receipt["elapsed_seconds"]
        assert stopped["result"]["scientific_complete"] == (phase == "cleanup")
        assert stopped["result"]["uncertain_final_call"] == (phase != "cleanup")
        restarted.reconcile_assets()
        attempt = restarted.store.get(receipt["attempt_id"], "execution_attempt")
        assert attempt["status"] == "budget_exhausted"
        assert attempt["enforcement_id"] == stopped["deadline_enforcement_id"]
        assert attempt["actual_costs"]["worker_seconds"] is None
        costs = restarted.store.list("cost_event")
        assert any(row["status"] == "uncertain" for row in costs)
        restarted.reconcile()
        restarted.reconcile_assets()
        assert restarted.store.list("cost_event") == costs
        assert len(restarted.store.list("deadline_enforcement")) == 1
        assert restarted._deadline_enforcement({**stopped, "attempt": 2}, directory) is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_shutdown_grace_cannot_change_after_experiment_freeze(tmp_path):
    workspace, request, grant = setup(tmp_path, grace=.5)
    trial = workspace.create_trial(request, execution={"execution_grant_id": grant["id"]})
    directory = workspace.job_dir(trial["id"])
    spec = read_json(directory / "spec.json")
    spec["stop_grace_seconds"] = 60
    atomic_json(directory / "spec.json", spec)
    with pytest.raises(ValueError, match="frozen scientific procedure"):
        ExperimentWorker(directory)
