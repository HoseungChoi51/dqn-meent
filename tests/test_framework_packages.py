"""Actual sandboxed continuous packages and binary (non-JSON) state transport."""
from framework_fixtures import researcher_idea
from pathlib import Path

import pytest

from optimization_framework.implementations.models import ImplementationSpec, Package
from optimization_framework.implementations.runtime import PackageOptimizer, prepare_runtime, write_package
from optimization_framework.implementations.validation import context, validate_package
from optimization_framework.implementations.bridge import ImplementationBridge


SOURCE = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer.py").read_text()


def spec():
    return ImplementationSpec(name="Continuous coordinate reference", mechanism="Sample one coordinate of the best observed incumbent.",
        acceptance_criteria=["Respect continuous bounds", "Restore the same next candidate"],
        problem_id="bounded_continuous", capabilities=["continuous", "scalar_objective"], n_cells_min=2, n_cells_max=4,
        behavior_checks=[{"name": "Reject deteriorating utility", "n_cells": 2, "assertion": "one_coordinate_from_incumbent",
                          "efficiencies": [-10, -20, -5, -50, 100]}])


def test_optimizer_files_only_package_uses_the_specification_kind():
    from optimization_framework.implementations.models import JobRequest
    request = JobRequest(workspace_id="workspace", campaign_id="campaign", grant_id="grant", idempotency_key="package_defaults",
        spec=spec().model_dump(), package={"files": [{"path": "optimizer.py", "content": SOURCE}]})
    assert isinstance(request.package, Package)
    assert request.package.contract == "ask_tell" and request.package.entrypoint == "optimizer:create_optimizer"


@pytest.mark.parametrize("contract", ["ask_tell", "optimizer_v1"])
def test_continuous_package_passes_independent_evaluator_validation(tmp_path, contract):
    specification = spec()
    source = SOURCE if contract == "ask_tell" else (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    package = Package(contract=contract, files=[{"path": "optimizer.py", "content": source}])
    root, runtime = prepare_runtime(tmp_path / "runtimes", {})
    directory = write_package(tmp_path / "package", package)
    report = validate_package(specification, package.model_dump(), directory, root, runtime)
    assert report["passed"], report
    integration = report["checks"][-1]["detail"]
    assert integration["problem"]["primary_objective"]["direction"] == "minimize"
    assert integration["evaluations"] == 4
    assert report["scope"]["problem_id"] == "bounded_continuous"
    from optimization_framework.evaluation.registry import problems
    incompatible = {"problem": problems.resolve("meent_grating", {"n_cells": 4}).model_dump()}
    with pytest.raises(ValueError, match="different problem"):
        ImplementationBridge.compatible({"spec": specification.model_dump()}, incompatible, {})


def test_package_checkpoint_over_16_mib_uses_binary_transport_and_restores(tmp_path):
    # A large payload exercises the transport without relying on a framework or
    # evaluator import inside the package sandbox.
    source = SOURCE.replace('return pickle.dumps(self.__dict__)', 'return pickle.dumps(self.__dict__) + b"x" * (20 * 1024**2)')
    package = Package(files=[{"path": "optimizer.py", "content": source}])
    root, runtime = prepare_runtime(tmp_path / "runtimes", {})
    directory = write_package(tmp_path / "package", package)
    optimizer = PackageOptimizer(directory, root, runtime, package.entrypoint, context(spec(), 2, 4), max_checkpoint_bytes=32 * 1024**2)
    try:
        first = optimizer.ask()
        assert any(value not in (0, 1) for value in first)
        optimizer.tell(first, -42.5)
        state = optimizer.state_dict()
        assert isinstance(state["checkpoint"], bytes)
        assert len(state["checkpoint"]) > 16 * 1024**2
        expected = optimizer.ask().tolist()
        optimizer.load_state_dict(state)
        assert optimizer.ask().tolist() == expected
    finally:
        optimizer.close()


def test_native_package_build_binding_and_experiment_share_the_common_worker(tmp_path):
    from optimization_framework.implementations.models import JobRequest, ReviewResult
    from optimization_framework.implementations.service import ImplementationService
    from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
    from optimization_framework.execution.service import Workspace
    from optimization_framework.execution.worker import run, read_journal

    class MockSemanticReviewer:
        """A labeled transport fixture; this is not live-model acceptance evidence."""
        def __init__(self, **kwargs):
            self.usage = {"calls": 0, "api_cost_usd": 0}
            self.callback = kwargs["reservation_callback"]

        def call(self, role, payload, *, result_type, instructions):
            self.usage["calls"] += 1
            self.callback({"type": "provider_call_reserved", "usage": self.usage})
            return ReviewResult(passed=True, criteria=payload["spec"]["acceptance_criteria"], findings=["Mock semantic review"])

    service = ImplementationService(tmp_path / "library", adapter_factory=MockSemanticReviewer)

    class Client:
        def submit(self, payload): return service.submit(JobRequest(**payload))
        def artifact(self, version_id): return service.artifact(version_id)
        def versions(self): return service.store.list("implementation_version")
        def job(self, job_id): return service.store.get(job_id, "implementation_job")

    workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
    campaign = workspace.create_campaign(CampaignInput(name="Native package lifecycle", compute_budget_seconds=120,
        validation_reserve_seconds=0, implementation_compute_budget_seconds=60,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={"dimensions": 2})]))
    hypothesis = researcher_idea(workspace, campaign["id"])
    source = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    package = Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}])
    grant = workspace.implementations.commission(hypothesis["id"], spec(), package=package, compute_seconds=60, max_calls=3, idempotency_key="native-v1")
    built = service.run_job(grant["job_id"])
    assert built["status"] == "completed", built
    workspace.implementations.reconcile()
    task = workspace.current_tasks(campaign["id"])[0]
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], hypothesis_id=hypothesis["id"],
        max_steps=8, wall_seconds=10))
    result = run(workspace.job_dir(trial["id"]))
    assert result["status"] == "completed", result
    assert result["scientific_complete"] and result["evaluations"] == 8
    observed = read_journal(workspace.job_dir(trial["id"]) / "observations.jsonl")
    assert [o["proposal_id"] for o in observed] == [f"candidate_{i}" for i in range(1, 9)]
    assert result["best_objective"] == min(o["objectives"]["value"] for o in observed)
