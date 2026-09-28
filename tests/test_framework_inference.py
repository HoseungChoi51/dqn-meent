"""Independent formats, captured compilers, and recovery across child dispatch."""
from framework_fixtures import researcher_idea
from copy import deepcopy
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from optimization_framework.contracts.diagnostics import ArtifactInference, DiagnosticSchedule
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
from optimization_framework.evaluation import inference
from optimization_framework.evaluation.diagnostics import reconcile
from optimization_framework.execution.service import Workspace


@pytest.fixture
def extension(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures/inference_extension.py"
    package = tmp_path / "installed/fixture_inference"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(source.read_text())
    monkeypatch.syspath_prepend(str(package.parent))
    entry = metadata.EntryPoint(name="constant_point:v1", value="fixture_inference:Adapter", group=inference.GROUP)
    original = metadata.entry_points
    def entries(*, group):
        return [entry] if group == inference.GROUP else original(group=group)
    monkeypatch.setattr(metadata, "entry_points", entries)
    monkeypatch.setattr("optimization_framework.execution.source.entry_points", entries)
    monkeypatch.setattr(inference, "adapters", inference.InferenceRegistry())
    yield package
    sys.modules.pop("fixture_inference", None)


def build_parent(directory):
    from optimization_framework.implementations.models import ImplementationSpec, JobRequest, Package, ReviewResult
    from optimization_framework.implementations.service import ImplementationService

    class MockReview:
        """Labeled model fixture; package validation and worker execution are real."""
        def __init__(self, **kwargs):
            self.usage = {"calls": 0, "api_cost_usd": 0}
            self.callback = kwargs["reservation_callback"]

        def call(self, role, payload, *, result_type, instructions):
            self.usage["calls"] += 1
            self.callback({"type": "provider_call_reserved", "usage": self.usage})
            return ReviewResult(passed=True, criteria=payload["spec"]["acceptance_criteria"], findings=["Fixture semantic review"])

    library = ImplementationService(directory / "library", adapter_factory=MockReview)
    class Client:
        def submit(self, payload): return library.submit(JobRequest(**payload))
        def artifact(self, identity): return library.artifact(identity)
        def versions(self): return library.store.list("implementation_version")
        def job(self, identity): return library.store.get(identity, "implementation_job")
    client = Client()
    workspace = Workspace(directory / "workspace", implementation_client=client)
    campaign = workspace.create_campaign(CampaignInput(name="Independent policy format", validation_reserve_seconds=0,
        implementation_compute_budget_seconds=60, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={"dimensions": 2})]))
    specification = ImplementationSpec(name="Point-producing search", mechanism="Search continuously and export a constant-point policy.",
        acceptance_criteria=["Observe raw objectives", "Export without advancing search"], problem_id="bounded_continuous",
        capabilities=["continuous", "scalar_objective"], n_cells_min=2, n_cells_max=2,
        behavior_checks=[{"name": "Coordinate proposals retain the incumbent", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [.2, .1, .8, .4]}],
        execution_capabilities={"completion_units": ["evaluation_requests", "optimizer_decisions"],
            "exports": [{"kind": "policy", "format": "constant-point:v1"}]})
    source = (Path(__file__).parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    source = source.replace('return []', 'return [{"kind": "policy", "format": "constant-point:v1", "media_type": "application/json", '
        '"metadata": {"dimensions": len(self.bounds)}, "data": {"point": self.incumbent}}]')
    package = Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}])
    hypothesis = researcher_idea(workspace, campaign["id"])
    grant = workspace.implementations.commission(hypothesis["id"], specification, package=package, compute_seconds=60,
        max_calls=3, idempotency_key="point-policy")
    result = library.run_job(grant["job_id"])
    assert result["status"] == "completed", result
    workspace.implementations.reconcile()
    task = workspace.current_tasks(campaign["id"])[0]
    return workspace, client, campaign, task, hypothesis


def finish_archived(workspace, trial):
    directory = workspace.job_dir(trial["id"])
    env = {**os.environ, "PYTHONPATH": str(directory / "code"), "PYTHONDONTWRITEBYTECODE": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    process = subprocess.run([sys.executable, "-B", "-m", "optimization_framework.execution.worker", "--directory", str(directory)],
        cwd=directory, env=env, capture_output=True, timeout=30)
    result = json.loads((directory / "result.json").read_text())
    assert process.returncode == 0 and result["scientific_complete"], (result, process.stderr.decode())
    trial.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return result


def test_non_dqn_export_uses_captured_adapter_and_recovers_partial_dispatch(tmp_path, extension, monkeypatch):
    from optimization_framework.analysis.general import report
    from optimization_framework.execution.worker import read_journal
    workspace, client, campaign, task, hypothesis = build_parent(tmp_path)
    rollout = ArtifactInference(adapter_id="constant_point:v1", seed={"offset": 100, "milestone_factor": 2}, wall_seconds=5,
        parameters={"repeats": 3}, recipes=[{"recipe_id": "analytic_fixtures:v1", "wall_seconds": 5}])
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], hypothesis_id=hypothesis["id"], max_steps=4, wall_seconds=10)
    deadline = time.time() + 180
    grant_record = workspace.resources.create({"id": "inference-test-grant", "campaign_id": campaign["id"],
        "owner_id": campaign["active_study_id"], "worker_seconds": 40, "starts_at": time.time(), "deadline_at": deadline,
        "max_workers": 1, "authority": "researcher"})
    parent = workspace.create_trial(request.model_copy(update={"diagnostics": [DiagnosticSchedule(at_counts=[2], rollouts=[rollout])]}),
        execution={"execution_grant_id": grant_record["id"], "absolute_deadline": deadline})
    reference = workspace.create_trial(request)
    assert parent["execution_manifest"]["inference_entry_points"]["constant_point:v1"] == "fixture_inference:Adapter"
    assert "fixture_inference/__init__.py" in parent["execution_manifest"]["scientific_files"]
    finish_archived(workspace, parent)
    finish_archived(workspace, reference)
    def trace(trial):
        return [(item["candidate"], item["objectives"]) for item in read_journal(workspace.job_dir(trial["id"]) / "observations.jsonl")]
    assert trace(parent) == trace(reference)
    grant = workspace.store.list("diagnostic_grant", campaign["id"])[0]
    snapshot_path = workspace.job_dir(parent["id"]) / "diagnostics" / grant["schedule_digest"] / "2.json"
    snapshot_bytes = snapshot_path.read_bytes()
    before = {kind: deepcopy(workspace.store.list(kind)) for kind in ("trial", "asset", "cost_event", "reuse_decision", "resource_reservation")}
    reserved = workspace.allocated_seconds(campaign["id"])

    # A restarted workspace must not ask the current registry to reinterpret
    # the saved parent. Its installed extension can even be removed or changed.
    monkeypatch.setattr(inference, "adapters", inference.InferenceRegistry(inference.BUILTINS))
    (extension / "__init__.py").write_text('raise RuntimeError("Changed installed adapter must not execute")\n')
    original = workspace.create_trial
    def interrupt_after_allocation(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("Interrupt before diagnostic dispatch commits")
    monkeypatch.setattr(workspace, "create_trial", interrupt_after_allocation)
    with pytest.raises(KeyboardInterrupt):
        reconcile(workspace)
    assert workspace.store.get(grant["id"], "diagnostic_grant")["status"] == "reserved"
    for kind, records in before.items():
        assert workspace.store.list(kind) == records
    assert workspace.allocated_seconds(campaign["id"]) == reserved
    assert snapshot_path.read_bytes() == snapshot_bytes

    restarted = Workspace(workspace.store.directory, implementation_client=client)
    reconcile(restarted)
    dispatched = restarted.store.get(grant["id"], "diagnostic_grant")
    assert dispatched["status"] == "dispatched" and len(dispatched["trial_ids"]) == 1
    child = restarted.store.get(dispatched["trial_ids"][0], "trial")
    assert child["algorithm"] == "artifact_inference" and child["seed"] == 104
    assert child["source_hash"] == parent["source_hash"]
    assert child["execution_manifest"] == parent["execution_manifest"]
    assert child["absolute_deadline"] == parent["absolute_deadline"] == deadline
    assert child["execution_grant_id"] == parent["execution_grant_id"]
    assert child["max_steps"] == 3 and child["completion"] == {"unit": "evaluation_requests", "count": 3}
    result = finish_archived(restarted, child)
    assert result["evaluations"] == 3 and result["diagnostics"]["updates"] == 0
    output = restarted.store.get(child["id"], "trial")["latest_output_asset_ids"]
    # The protected implementation fixture used four evaluator requests. Full
    # lineage retains those, the exact two-request prefix, and child execution.
    assert restarted.assets.attributed_costs(output)["quantities"]["evaluation_requests"]["total"] == 4 + 2 + 3
    reconcile(restarted)
    nested = [trial for trial in restarted.store.list("trial") if trial.get("parent_trial_id") == child["id"]]
    assert len(nested) == 1
    finish_archived(restarted, nested[0])
    stable = {kind: restarted.store.list(kind) for kind in before}
    reconcile(restarted)
    for kind, records in stable.items():
        assert restarted.store.list(kind) == records
    assert snapshot_path.read_bytes() == snapshot_bytes
    compared = report(restarted, campaign["id"])
    assert {trial["id"] for group in compared["groups"] for trial in group["trials"]} == {parent["id"], reference["id"]}


def test_registered_read_only_inference_is_eligible_for_policy_transfer(tmp_path, extension):
    from optimization_framework.contracts.assets import ReuseDecision
    from optimization_framework.contracts.requests import StudyInput
    from optimization_framework.storage.sqlite import now
    workspace, _, campaign, task, hypothesis = build_parent(tmp_path)
    parent = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], hypothesis_id=hypothesis["id"],
        max_steps=2, wall_seconds=5))
    finish_archived(workspace, parent)
    policy = next(asset for asset in workspace.store.list("asset") if asset["kind"] == "policy" and asset["producer_id"] == parent["id"])
    reuse = workspace.assets.decide(ReuseDecision(id="use-point-policy", campaign_id=campaign["id"], study_id=campaign["active_study_id"],
        asset_id=policy["id"], decision="reuse", intended_use="optimizer_input", authority="researcher", rationale="Evaluate the fixed point", created_at=now()))
    prototype = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="artifact_inference",
        algorithm_config={"adapter_id": "constant_point:v1", "parameters": {"repeats": 1}}, initial_assets=[policy["id"]],
        reuse_decision_ids=[reuse["id"]], max_steps=1, wall_seconds=5))
    assert prototype["inference_adapter"]["adaptation"] == "forbidden"
    study = workspace.create_study(campaign["id"], StudyInput(goal="Transfer the frozen point policy", scope="confirmation",
        confirmation_kind="policy_transfer", prototype_trial_ids=[prototype["id"]], seeds=[13], policy_asset_id=policy["id"]))
    workspace.assets.decide(ReuseDecision(id="transfer-point-policy", campaign_id=campaign["id"], study_id=study["id"],
        asset_id=policy["id"], decision="reuse", intended_use="optimizer_input", authority="researcher",
        rationale="Explicit frozen-policy input for this transfer study", created_at=now()))
    allocated = workspace.confirmations.schedule(workspace, study["confirmation"]["id"])
    assert len(allocated["created_trial_ids"]) == 1
    trial = workspace.store.get(allocated["created_trial_ids"][0], "trial")
    result = finish_archived(workspace, trial)
    assert result["evaluations"] == 1 and result["diagnostics"]["adaptation"] == "forbidden"
    assert workspace.confirmations.release(study["confirmation"]["id"])["outcome"] == "completed_roster"


def test_missing_export_and_counter_are_readiness_errors_without_allocation(tmp_path, extension):
    from optimization_framework.contracts.drafts import DraftSaveInput
    workspace = Workspace(tmp_path / "workspace")
    campaign = workspace.create_campaign(CampaignInput(name="Missing capability", validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="random", max_steps=2,
        diagnostics=[DiagnosticSchedule(at_counts=[1], rollouts=[ArtifactInference(adapter_id="constant_point:v1", seed=1)])])
    draft = workspace.drafts.save(campaign["id"], DraftSaveInput(title="Still designable", procedure=request.model_dump(mode="json")))
    status = workspace.drafts.readiness(draft["id"])
    assert not status["ready"] and any("does not declare the artifact export" in item["message"] for item in status["blockers"])
    with pytest.raises(ValueError, match="does not declare the artifact export"):
        workspace.create_trial(request)
    with pytest.raises(ValueError, match="decision counter"):
        workspace.create_trial(request.model_copy(update={"diagnostics": [], "completion": {"unit": "optimizer_decisions", "count": 2}}))
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0


def test_implementation_validation_rejects_a_false_export_claim(tmp_path):
    from optimization_framework.implementations.models import ImplementationSpec, Package
    from optimization_framework.implementations.runtime import prepare_runtime, write_package
    from optimization_framework.implementations.validation import validate_package
    source = (Path(__file__).parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    spec = ImplementationSpec(name="Invalid export claim", mechanism="Fixture with a missing export", acceptance_criteria=["Publish a policy"],
        problem_id="bounded_continuous", capabilities=["continuous"], n_cells_min=2, n_cells_max=2,
        execution_capabilities={"exports": [{"kind": "policy", "format": "missing:v1"}]})
    package = Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}])
    root, runtime = prepare_runtime(tmp_path / "runtime", {})
    directory = write_package(tmp_path / "package", package)
    report = validate_package(spec, package.model_dump(), directory, root, runtime)
    assert not report["passed"] and "exactly one artifact" in report["error"]
    assert report["checks"][-1]["name"] == "declared diagnostic capabilities and independent export"
