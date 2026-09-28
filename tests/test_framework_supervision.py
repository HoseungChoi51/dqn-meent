"""The ordinary scheduler owns isolated attempts, controls and cost receipts."""
from framework_fixtures import researcher_idea
import copy
import os
from pathlib import Path
import time

import pytest

from optimization_framework.contracts.isolation import IsolationPolicy
from optimization_framework.contracts.requests import ControlInput, RecipeInput, TrialInput
from optimization_framework.execution.service import Workspace, LIVE, process_identity
from optimization_framework.execution import supervision
from optimization_framework.storage.sqlite import atomic_json
from test_framework_provenance import campaign


def wait(workspace, trial_id, predicate, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        workspace.reconcile()
        trial = workspace.store.get(trial_id, "trial")
        if predicate(trial):
            return trial
        if trial["status"] not in LIVE:
            raise AssertionError({key: trial.get(key) for key in ("id", "status", "reason", "result")})
        time.sleep(.05)
    raise AssertionError({key: trial.get(key) for key in ("id", "status", "reason", "progress")})


def finish(workspace, trial):
    workspace._start_trial(trial)
    try:
        result = wait(workspace, trial["id"], lambda row: row["status"] not in LIVE)
        workspace.processes[trial["id"]].wait(timeout=3)
        workspace.capture_evidence(result)
        return workspace.store.get(trial["id"], "trial")
    finally:
        process = workspace.processes[trial["id"]]
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_scheduler_uses_frozen_host_policy_and_receipts_without_double_counting(tmp_path):
    workspace, owner, task = campaign(tmp_path)
    trial = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=4, wall_seconds=15),
        isolation_policy=IsolationPolicy())
    frozen = copy.deepcopy(trial["experiment_spec"])
    result = finish(workspace, trial)
    assert result["status"] == "completed", result.get("reason")
    assert result["result"]["scientific_complete"]
    receipt = workspace.store.get(result["execution_host_receipt_id"], "execution_host_receipt")
    assert receipt["process_exit"] == 0 and receipt["host_pid"] == result["pid"]
    assert result["execution_seconds"] == receipt["elapsed_seconds"]
    assert result["experiment_spec"] == frozen
    assert not (workspace.job_dir(trial["id"]) / "worker-lease.json").exists()
    actual = workspace.assets.actual_costs(owner)["quantities"]
    attributed = workspace.assets.attributed_costs(result["output_asset_ids"])["quantities"]
    assert actual["evaluation_requests"]["total"] == attributed["evaluation_requests"]["total"] == 4
    assert actual["worker_seconds"]["total"] == pytest.approx(receipt["elapsed_seconds"])
    assert attributed["worker_seconds"]["total"] == pytest.approx(receipt["elapsed_seconds"])
    before = workspace.store.list("cost_event")
    workspace.reconcile()
    workspace.capture_evidence(result)
    assert workspace.store.list("cost_event") == before
    assert len(workspace.store.list("execution_host_receipt")) == 1
    assert len(workspace.store.list("cost_reconciliation")) == 1


def test_captured_source_forces_isolation_and_diagnostics_inherit_it(tmp_path):
    workspace, owner, task = campaign(tmp_path)
    original = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=15))
    parent = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=15),
        captured_source=(workspace.job_dir(original["id"]), original["execution_manifest"]))
    assert parent["isolation_policy"]["kind"] == "supervised_capture"
    parent = finish(workspace, parent)
    child = workspace.run_recipe(parent["id"], RecipeInput(recipe_id="analytic_fixtures:v1", wall_seconds=15))
    assert child["isolation_policy"] == parent["isolation_policy"]
    assert child["source_hash"] == parent["source_hash"]
    child = finish(workspace, child)
    assert child["status"] == "completed", child.get("reason")
    assert child["result"]["recipe_result"]["verdict"] == "passed"
    assert workspace.validations.assess(child["validation_requirement_ids"][0])["measured_pass"]


@pytest.mark.parametrize("mutation", ["remove", "limits", "embedded_spec"])
def test_mutable_trial_cannot_downgrade_the_host_policy(tmp_path, mutation):
    workspace, owner, task = campaign(tmp_path)
    trial = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=15),
        isolation_policy=IsolationPolicy())
    if mutation == "remove":
        trial.pop("isolation_policy")
    elif mutation == "limits":
        trial["isolation_policy"]["private_bytes"] *= 2
    else:
        trial["experiment_spec"]["schedule"].pop("isolation_policy")
        trial.pop("isolation_policy")
    workspace.store.put("trial", trial)
    with pytest.raises(ValueError, match="isolation policy"):
        workspace._start_trial(trial)
    assert trial["attempt"] == 0 and not workspace.processes


def slow_trial(tmp_path):
    from test_framework_revalidation import campaign_fixture
    from test_framework_evaluators import SOURCE, specification
    source = SOURCE.replace("def evaluate(self, candidate):", "def evaluate(self, candidate):\n        import time\n        time.sleep(.03)")
    workspace, service, owner, task = campaign_fixture(tmp_path, source=source, spec=specification())
    trial = workspace.create_trial(TrialInput(campaign_id=owner["id"], task_id=task["id"], algorithm="coordinate", max_steps=100,
        wall_seconds=30, recovery={"every_observations": 2}), isolation_policy=IsolationPolicy())
    return workspace, trial


def test_reconstruction_adopts_host_ignores_namespace_lease_and_resumes_checkpoint(tmp_path):
    workspace, trial = slow_trial(tmp_path)
    workspace._start_trial(trial)
    process = workspace.processes[trial["id"]]
    current = None
    try:
        wait(workspace, trial["id"], lambda row: row.get("progress", {}).get("evaluations", 0) >= 3)
        directory = workspace.job_dir(trial["id"])
        atomic_json(directory / "worker-lease.json", {"experiment_id": trial["id"], "pid": os.getpid(),
            "process_identity": process_identity(os.getpid()), "attempt": 999, "fingerprint": supervision.identity(trial)["fingerprint"]})
        lost = workspace.store.get(trial["id"], "trial")
        lost.pop("pid")
        lost.pop("process_identity")
        workspace.store.put("trial", lost)
        current = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
        current.reconcile()
        adopted = current.store.get(trial["id"], "trial")
        assert adopted["pid"] == process.pid and adopted["attempt"] == 1
        current.control(trial["id"], ControlInput(action="pause"))
        paused = wait(current, trial["id"], lambda row: row["status"] not in LIVE)
        process.wait(timeout=5)
        assert paused["status"] == "paused", paused
        current.capture_evidence(paused)
        assert paused["progress"]["checkpoint_available"]
        frozen = paused["experiment_spec_hash"]
        current.control(trial["id"], ControlInput(action="resume"))
        resumed = finish(current, current.store.get(trial["id"], "trial"))
        assert resumed["status"] == "completed", resumed
        assert resumed["attempt"] == 2 and resumed["experiment_spec_hash"] == frozen
        assert len(resumed["execution_host_receipt_ids"]) == 2
        receipts = [current.store.get(identity, "execution_host_receipt") for identity in resumed["execution_host_receipt_ids"]]
        attributed = current.assets.attributed_costs(resumed["output_asset_ids"])["quantities"]
        # The fixture's implementation-production worker time is unknown. Its
        # 20 validation requests remain upstream of these 100 numerical calls.
        assert attributed["worker_seconds"]["total"] is None
        assert attributed["worker_seconds"]["known"] == pytest.approx(sum(row["elapsed_seconds"] for row in receipts))
        assert attributed["evaluation_requests"]["total"] == 120
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if current:
            current.close()


def test_dead_host_is_reconciled_once_without_source_and_cost_uncertainty_survives(tmp_path):
    workspace, trial = slow_trial(tmp_path)
    workspace._start_trial(trial)
    process = workspace.processes[trial["id"]]
    try:
        wait(workspace, trial["id"], lambda row: row.get("progress", {}).get("evaluations", 0) >= 3)
        process.kill()
        process.wait(timeout=5)
        directory = workspace.job_dir(trial["id"])
        (directory / "code").rename(directory / "unavailable-code")
        saved = (directory / "observations.jsonl").read_bytes()
        restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
        restarted.reconcile()
        result = restarted.store.get(trial["id"], "trial")
        assert result["status"] == "interrupted", result.get("reason")
        restarted.capture_evidence(result)
        receipt = restarted.store.get(result["execution_host_receipt_id"], "execution_host_receipt")
        assert receipt["elapsed_seconds"] is None and receipt["recovered_after_interruption"]
        assert result["execution_seconds_upper_bound"] >= result["execution_seconds"]
        assert restarted.resources.assessment(trial["campaign_id"])["allocated_seconds"] >= result["execution_seconds_upper_bound"]
        costs = restarted.assets.actual_costs(trial["campaign_id"])["quantities"]
        assert costs["worker_seconds"]["total"] is None
        assert costs["evaluation_requests"]["total"] is None
        restarted.reconcile()
        restarted.capture_evidence(result)
        assert (directory / "observations.jsonl").read_bytes() == saved
        assert len(restarted.store.list("execution_host_receipt")) == 1
        assert not restarted.processes
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_generated_optimizer_and_evaluator_use_the_scheduler_and_export_host_evidence(tmp_path):
    from optimization_framework.implementations.models import BoundOptimizerSpec, Package
    from optimization_framework.assets.bundle_export import Exporter
    from test_framework_evaluators import specification, MockReviewer
    from test_framework_revalidation import campaign_fixture
    workspace, service, owner, task = campaign_fixture(tmp_path, spec=specification())
    service.adapter_factory = MockReviewer
    hypothesis = researcher_idea(workspace, owner["id"])
    source = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    optimizer = BoundOptimizerSpec(name="Specialized optimizer", mechanism="Coordinate search on the commissioned problem",
        acceptance_criteria=["Restore the next proposal"], problem_id=task["problem"]["definition_id"],
        evaluator_version_id=task["evaluator_version_id"], capabilities=["continuous", "scalar_objective"],
        n_cells_min=2, n_cells_max=2,
        behavior_checks=[{"name": "Keep the best incumbent", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [-1, -3, -2, 1]}])
    grant = workspace.implementations.commission(hypothesis["id"], optimizer,
        package=Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}]),
        compute_seconds=30, max_calls=1, idempotency_key="scheduler-optimizer")
    assert service.run_job(grant["job_id"])["status"] == "completed"
    workspace.implementations.reconcile()
    trial = workspace.create_trial(TrialInput(campaign_id=owner["id"], task_id=task["id"], hypothesis_id=hypothesis["id"],
        algorithm="package", max_steps=4, wall_seconds=15), isolation_policy=IsolationPolicy())
    result = finish(workspace, trial)
    assert result["status"] == "completed", result.get("reason")
    receipt = workspace.store.get(result["execution_host_receipt_id"], "execution_host_receipt")
    assert {row["kind"] for row in receipt["package_processes"]} == {"optimizer", "evaluator"}
    exporter = Exporter(workspace)
    exporter.add(result["output_asset_ids"][0], required=True)
    assert any(row.reference.kind == "execution_host_receipt" and row.data["id"] == receipt["id"]
               for row in exporter.records.values())


def test_forced_stop_retains_known_total_and_unknown_internal_work(tmp_path):
    from test_framework_isolated_host import adversarial_capture
    workspace, owner, task = campaign(tmp_path)
    original = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=15))
    worker = workspace.job_dir(original["id"]) / "code/optimization_framework/execution/worker.py"
    program = worker.read_text().replace('return int(run(args.directory, enforce_deadline=True)["status"] == "failed")',
        'while True:\n        time.sleep(.1)')
    adversarial_capture(workspace, original, program)
    trial = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=15),
        captured_source=(workspace.job_dir(original["id"]), original["execution_manifest"]))
    workspace._start_trial(trial)
    process = workspace.processes[trial["id"]]
    try:
        deadline = time.monotonic() + 10
        while not (supervision.directory(workspace, trial) / "publication.json").exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(.05)
        workspace.control(trial["id"], ControlInput(action="stop"))
        result = wait(workspace, trial["id"], lambda row: row["status"] not in LIVE)
        process.wait(timeout=5)
        assert result["status"] == "stopped", result.get("reason")
        workspace.capture_evidence(result)
        receipt = workspace.store.get(result["execution_host_receipt_id"], "execution_host_receipt")
        assert receipt["stopped_by"] == "control_stop" and receipt["elapsed_seconds"] is not None
        amounts = workspace.assets.actual_costs(owner)["quantities"]
        assert amounts["worker_seconds"]["total"] == pytest.approx(receipt["elapsed_seconds"])
        assert amounts["evaluation_requests"]["total"] is None
        assert any(row["quantities"]["worker_seconds"] is None for row in workspace.store.list("cost_event"))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
