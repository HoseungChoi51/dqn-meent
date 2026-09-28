"""Real isolated evaluator builds; model transport fixtures are explicitly mocked."""
from pathlib import Path

import pytest

from optimization_framework.implementations.evaluator_runtime import PackageEvaluator
from optimization_framework.implementations.evaluator_validation import validate_package
from optimization_framework.implementations.models import (
    BoundOptimizerSpec, BuildResult, EvaluatorPackage, EvaluatorSpec, JobRequest, Package, ReviewResult,
)
from optimization_framework.implementations.runtime import prepare_runtime, write_package
from optimization_framework.implementations.service import ImplementationService


SOURCE = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_evaluator.py").read_text()


def specification():
    return EvaluatorSpec(name="Shifted quadratic evaluator", mechanism="Sum squared distance to center plus offset, rounded to digits.",
        acceptance_criteria=["Return the declared raw quadratic energy", "Restore all evaluator state"],
        manifest={"id": "commissioned_quadratic", "name": "Commissioned quadratic",
            "candidate_schema": {"representation": "continuous", "dimensions": 2, "bounds": [[-3, 3], [-3, 3]]},
            "primary_objective": {"name": "energy", "direction": "minimize", "units": "arbitrary energy units"},
            "configuration_schema": {"type": "object", "additionalProperties": False, "required": ["center", "offset"],
                "properties": {"center": {"type": "number", "minimum": -2, "maximum": 2}, "offset": {"type": "number"}}},
            "configuration": {"center": 1.5, "offset": -2},
            "fidelity_schema": {"type": "object", "additionalProperties": False, "required": ["digits"],
                "properties": {"digits": {"type": "integer", "minimum": 0, "maximum": 12}}},
            "fidelity": {"digits": 8}},
        correctness_cases=[
            {"name": "origin", "basis": "Analytical: 2*(0-1.5)^2-2 = 2.5", "candidate": [0, 0], "objectives": {"energy": 2.5}},
            {"name": "minimum", "basis": "Analytical: each squared difference is zero", "candidate": [1.5, 1.5], "objectives": {"energy": -2}},
            {"name": "asymmetric", "basis": "Analytical: 6.25+0.25-2 = 4.5", "candidate": [-1, 2], "objectives": {"energy": 4.5}},
            {"name": "configuration and fidelity", "basis": "Analytical: round(0.25+6.25+0.25,0) = 7",
             "candidate": [-1, 2], "configuration": {"center": -.5, "offset": .25}, "fidelity": {"digits": 0}, "objectives": {"energy": 7}},
        ])


def package(source=SOURCE):
    return EvaluatorPackage(files=[{"path": "evaluator.py", "content": source}])


class MockReviewer:
    """Transport double; host execution and protected numerical checks are real."""
    calls = []

    def __init__(self, **kwargs):
        self.usage = {"calls": 0, "api_cost_usd": 0}
        self.callback = kwargs["reservation_callback"]

    def call(self, role, payload, *, result_type, instructions):
        self.usage["calls"] += 1
        self.callback({"type": "provider_call_reserved", "usage": self.usage})
        self.calls.append((role, payload))
        if role == "implementation_builder":
            assert "correctness_cases" not in payload["spec"]
            assert "evaluator_v1" in instructions
            return BuildResult(explanation="Supplied fixture candidate", package=package(), blocker=None)
        assert role == "implementation_validator"
        return ReviewResult(passed=True, criteria=payload["spec"]["acceptance_criteria"], findings=["Mock semantic review"])


def request(**changes):
    return JobRequest(**{**dict(workspace_id="workspace", campaign_id="campaign", grant_id="grant",
        idempotency_key="evaluator", spec=specification(), compute_seconds=30, max_attempts=1, max_calls=3), **changes})


def test_commissioned_evaluator_is_independently_checked_published_and_revocable(tmp_path):
    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    submitted = service.submit(request())
    assert service.submit(request())["id"] == submitted["id"]
    result = service.run_job(submitted["id"])
    assert result["status"] == "completed", result
    bundle = service.artifact(result["version_id"])
    version = bundle["version"]
    assert version["kind"] == "evaluator"
    report = version["validation_report"]
    assert report["kind"] == "evaluator_correctness" and report["passed"]
    assert report["costs"] == {"evaluation_requests": 20, "solver_executions": 20}
    assert len(report["checks"]) == 5
    assert len(report["exposed_conditions"]) == 2
    assert bundle["artifact"]["package"]["contract"] == "evaluator_v1"
    assert len(service.store.list("implementation_version")) == 1
    assert service.run_job(submitted["id"])["attempts"] == result["attempts"]
    service.revoke(version["id"], "Independent counterexample")
    with pytest.raises(ValueError, match="not available"):
        service.artifact(version["id"])


def test_optimizer_contract_checks_do_not_claim_evaluator_numerical_correctness(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    spec = contract_specification()
    job = service.run_job(service.submit(request(spec=spec, package=package()))["id"])
    optimizer_spec = BoundOptimizerSpec(name="Optimizer for an unverified evaluator", mechanism="Coordinate search",
        acceptance_criteria=["Restore the same next proposal"], problem_id=spec.manifest.id,
        evaluator_version_id=job["version_id"], capabilities=["continuous", "scalar_objective"], n_cells_min=2, n_cells_max=2,
        behavior_checks=[{"name": "Keep the best observed incumbent", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [-1, -3, -2, 1]}])
    source = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    optimizer_job = service.run_job(service.submit(request(spec=optimizer_spec, grant_id="optimizer-grant", idempotency_key="optimizer",
        package=Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}])))["id"])
    assert optimizer_job["status"] == "completed", optimizer_job
    version = service.version(optimizer_job["version_id"], ready=True)
    assert version["status"] == "validated"
    assert version["validation_report"]["scope"]["evaluator_numerical_status"] == "unverified"
    assert "numerical correctness remains unverified" in version["validation_report"]["limitations"]


def test_no_oracle_is_a_blocker_and_does_not_spend_model_calls(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    spec = specification().model_copy(update={"correctness_cases": []})
    submitted = service.submit(request(spec=spec))
    result = service.run_job(submitted["id"])
    assert result["status"] == "blocked"
    assert "independent numerical fixtures" in result["error"]
    assert not result["usage"] and not service.store.list("implementation_version")


def test_wrong_numerics_cannot_publish_even_with_a_positive_semantic_reviewer(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    wrong = package(SOURCE.replace('value = round(value,', 'value = 1 + round(value,'))
    submitted = service.submit(request(package=wrong))
    result = service.run_job(submitted["id"])
    assert result["status"] == "failed", result
    assert not result["attempts"][0]["report"]["passed"]
    assert "independent fixture" in result["attempts"][0]["report"]["error"]
    assert not result["usage"].get("calls", 0)
    assert not service.store.list("implementation_version")


@pytest.mark.parametrize("replacement,error", [
    ('{"objectives": {"energy": value}, "solver_executions": 0}', "solver_executions"),
    ('{"objectives": {"invented": value}}', "objectives inconsistent"),
    ('{"objectives": {"energy": float("nan")}}', "JSON"),
    ('{"objectives": {"energy": value}, "constraints": {"invented": True}}', "constraints inconsistent"),
])
def test_evaluator_cannot_forge_measurement_schema_or_costs(tmp_path, replacement, error):
    spec = specification()
    candidate = package(SOURCE.replace('{"objectives": {"energy": value}, "metadata": {"evaluations": self.count}}', replacement))
    root, runtime = prepare_runtime(tmp_path / "runtime", {}, kind="evaluator")
    directory = write_package(tmp_path / "package", candidate)
    report = validate_package(spec, candidate.model_dump(), directory, root, runtime)
    assert not report["passed"] and error in report["error"], report
    assert report["costs"]["evaluation_requests"] == 1


def test_isolated_evaluator_checkpoint_and_input_validation(tmp_path):
    spec, candidate = specification(), package()
    root, runtime = prepare_runtime(tmp_path / "runtime", {}, kind="evaluator")
    directory = write_package(tmp_path / "package", candidate)
    instance = spec.manifest.resolve("pinned-version")
    first = PackageEvaluator(directory, root, runtime, candidate.entrypoint, instance)
    second = None
    try:
        assert first.evaluate([0, 0]).objectives == {"energy": 2.5}
        with pytest.raises(ValueError, match="bounds"):
            first.evaluate([4, 0])
        assert first.count == 1
        checkpoint = first.checkpoint()
        expected = first.evaluate([-1, 2])
        second = PackageEvaluator(directory, root, runtime, candidate.entrypoint, instance)
        second.restore(checkpoint)
        assert second.evaluate([-1, 2]) == expected
        changed = {**checkpoint, "evaluation_identity": "other"}
        with pytest.raises(ValueError, match="identity changed"):
            second.restore(changed)
    finally:
        first.close()
        if second is not None:
            second.close()


@pytest.mark.parametrize("body,expected", [
    ("import time; time.sleep(20)", "operation timeout"),
    ("import os; os._exit(3)", "process exited"),
    ("open('/campaign-secret').read()", "FileNotFoundError"),
])
def test_failure_and_timeout_close_the_isolated_host(tmp_path, body, expected):
    spec = specification()
    candidate = package(SOURCE.replace('self.count += 1', body))
    root, runtime = prepare_runtime(tmp_path / "runtime", {}, kind="evaluator")
    directory = write_package(tmp_path / "package", candidate)
    evaluator = PackageEvaluator(directory, root, runtime, candidate.entrypoint,
        spec.manifest.resolve("pinned-version"), timeout=.5)
    try:
        with pytest.raises(Exception, match=expected):
            evaluator.evaluate([0, 0])
        assert evaluator.process is None
    finally:
        evaluator.close()


def test_executable_kinds_are_checked_without_rewriting_legacy_package_serialization():
    legacy = Package(files=[{"path": "optimizer.py", "content": "# legacy"}])
    assert set(legacy.model_dump()) == {"contract", "entrypoint", "files"}
    with pytest.raises(ValueError, match="Package kind"):
        request(package=legacy)
    with pytest.raises(ValueError, match="Extra inputs"):
        specification().manifest.model_validate({**specification().manifest.model_dump(), "resolver": "untrusted:load"})


def test_missing_evaluator_draft_commissions_binds_once_and_runs_with_upstream_costs(tmp_path):
    import time
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.drafts import DraftSaveInput, DraftLaunchInput
    from optimization_framework.contracts.requests import CampaignInput, CampaignUpdate, ControlInput, TaskInput, TrialInput
    from optimization_framework.execution.service import Workspace

    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    class Client:
        def submit(self, payload): return service.submit(JobRequest(**payload))
        def version(self, identity): return service.version(identity)
        def artifact(self, identity): return service.artifact(identity)
        def versions(self): return service.store.list("implementation_version")
        def job(self, identity): return service.store.get(identity, "implementation_job")
        def control(self, identity, action, **kwargs): return service.control_once(identity, action, kwargs.get("idempotency_key"))

    workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
    spec = specification()
    campaign = workspace.create_campaign(CampaignInput(name="Commission a missing problem", compute_budget_seconds=60,
        implementation_compute_budget_seconds=60, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic intent", problem_id=spec.manifest.id, evaluator_manifest=spec.manifest)]))
    task = workspace.current_tasks(campaign["id"])[0]
    study_before = workspace.store.get(campaign["active_study_id"], "study")
    assert study_before["schema_version"] == 2 and not study_before["instance_ids"]
    assert study_before["problem_requirement_ids"] == [task["evaluator_requirement_id"]]
    draft = workspace.drafts.save(campaign["id"], DraftSaveInput(title="Search the declared problem",
        procedure={"task_id": task["id"], "algorithm": "coordinate", "max_steps": 4, "wall_seconds": 5}))
    pending = workspace.drafts.readiness(draft["id"])
    assert not pending["ready"] and {x["code"] for x in pending["blockers"]} == {"evaluator_missing"}
    with pytest.raises(ValueError, match="validated evaluator"):
        workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="random", wall_seconds=5))
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0

    command = Command(id="commission-evaluator", campaign_id=campaign["id"], expected_revision=1,
        operation="evaluator.commission", payload={"task_id": task["id"], "spec": spec.model_dump(mode="json"),
            "compute_seconds": 30, "max_calls": 3})
    outcome = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == outcome
    grant = workspace.store.get(outcome["outcome"]["grant_id"], "implementation_grant")
    job = service.run_job(grant["job_id"])
    assert job["status"] == "completed", job
    workspace.implementations.reconcile()
    assert len(workspace.store.list("evaluator_binding")) == 1
    assert workspace.store.get(campaign["active_study_id"], "study") == study_before
    ready = workspace.drafts.readiness(draft["id"])
    assert ready["ready"], ready
    launch = DraftLaunchInput(draft_id=draft["id"], expected_draft_revision=1, expected_readiness_hash=ready["readiness_hash"])
    launched = workspace.drafts.launch(campaign["id"], launch)
    assert workspace.drafts.launch(campaign["id"], launch) == launched
    trial = workspace.store.get(launched["trial_id"], "trial")
    assert trial["problem"]["evaluator_version"] == job["version_id"]
    assert trial["evaluator_cost_asset_ids"]
    workspace._start_trial(trial)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        workspace.reconcile()
        trial = workspace.store.get(trial["id"], "trial")
        if trial["status"] not in {"queued", "running", "pausing", "stopping"}:
            break
        time.sleep(.05)
    assert trial["status"] == "completed", trial
    assert trial["result"]["evaluations"] == 4
    assert trial["result"]["objective_definition"]["units"] == "arbitrary energy units"
    assert trial["result"]["scientific_complete"]
    assert workspace.store.list("cost_event", campaign["id"])
    # Commission an optimizer independently against this exact evaluator. Its
    # correctness calls also go through the isolated host, not an installed fake.
    optimizer_spec = BoundOptimizerSpec(name="Coordinate optimizer for the commissioned problem",
        mechanism="Sample one coordinate of the best observed incumbent.",
        acceptance_criteria=["Respect bounds", "Restore the same next proposal"],
        problem_id=spec.manifest.id, evaluator_version_id=job["version_id"],
        capabilities=["continuous", "scalar_objective"], n_cells_min=2, n_cells_max=2,
        behavior_checks=[{"name": "Keep the best observed incumbent", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [-1, -3, -2, 1]}])
    source = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    optimizer_package = Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}])
    hypothesis = {"id": "missing-optimizer", "campaign_id": campaign["id"], "status": "proposed", "algorithm": "",
        "title": optimizer_spec.name, "mechanism": optimizer_spec.mechanism, "algorithm_config": {}}
    workspace.store.put("hypothesis", hypothesis)
    optimizer_command = Command(id="commission-optimizer", campaign_id=campaign["id"], expected_revision=1,
        operation="implementation.commission", payload={"hypothesis_id": hypothesis["id"], "spec": optimizer_spec.model_dump(mode="json"),
            "package": optimizer_package.model_dump(), "compute_seconds": 30, "max_calls": 3})
    outcome = workspace.commands.execute(optimizer_command)
    optimizer_grant = workspace.store.get(outcome["outcome"]["grant_id"], "implementation_grant")
    optimizer_job = service.run_job(optimizer_grant["job_id"])
    assert optimizer_job["status"] == "completed", optimizer_job
    workspace.implementations.reconcile()
    paired_draft = workspace.drafts.save(campaign["id"], DraftSaveInput(title="Both commissioned executables",
        follow_proposal_implementation=True, procedure={"task_id": task["id"], "hypothesis_id": hypothesis["id"], "max_steps": 4, "wall_seconds": 5}))
    paired_ready = workspace.drafts.readiness(paired_draft["id"])
    assert paired_ready["ready"], paired_ready
    outcome = workspace.drafts.launch(campaign["id"], DraftLaunchInput(draft_id=paired_draft["id"], expected_draft_revision=1,
        expected_readiness_hash=paired_ready["readiness_hash"]))
    paired = workspace.store.get(outcome["trial_id"], "trial")
    workspace._start_trial(paired)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        workspace.reconcile()
        paired = workspace.store.get(paired["id"], "trial")
        if paired["status"] not in {"queued", "running", "pausing", "stopping"}:
            break
        time.sleep(.05)
    assert paired["status"] == "completed", paired
    assert paired["result"]["evaluations"] == 4
    assert paired["implementation_version_id"] == optimizer_job["version_id"]
    assert paired["evaluator_version_id"] == job["version_id"]
    assert len(paired["contribution_asset_ids"]) == 2
    assert workspace.processes[paired["id"]].wait(timeout=10) == 0
    workspace.reconcile_assets()
    paired = workspace.store.get(paired["id"], "trial")
    costs = workspace.assets.attributed_costs(paired["latest_output_asset_ids"])["quantities"]
    assert costs["evaluation_requests"]["total"] == 28  # evaluator fixtures + optimizer integration + this experiment
    assert costs["solver_executions"]["total"] == 28
    from optimization_framework.research.coordinator import ResearchCoordinator
    context = ResearchCoordinator(workspace).context(campaign["id"])
    assert context["tasks"][0]["evaluator_readiness"]["runnable"]
    assert context["problem_definitions"][0]["evaluator_version"] == job["version_id"]
    assert "evaluator.commission" in context["application_commands"]
    # The browser submits task declarations with charter edits. A budget-only
    # update preserves the frozen requirement and binding instead of replacing it.
    before = workspace.current_tasks(campaign["id"])[0]
    bindings = workspace.store.list("evaluator_binding")
    updated = workspace.update_campaign(campaign["id"], CampaignUpdate(compute_budget_seconds=90,
        objective=campaign["objective"], tasks=[TaskInput(id=before["id"], name=before["name"],
            problem_id=before["problem_id"], configuration=before["configuration"], fidelity=before["fidelity"],
            evaluator_manifest=before["evaluator_manifest"], split=before["split"])]))
    assert updated["active_study_id"] == campaign["active_study_id"]
    assert workspace.current_tasks(campaign["id"])[0] == before
    assert workspace.store.list("evaluator_binding") == bindings
    # Reuse both immutable executables. A cooperative pause persists evaluator
    # state as well as optimizer state across reconstruction of both services.
    from optimization_framework.storage.sqlite import read_json
    from optimization_framework.execution.worker import iter_journal
    reused = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
        hypothesis_id=hypothesis["id"], max_steps=200, wall_seconds=15, recovery={"every_observations": 5}))
    frozen = reused["experiment_spec"]
    workspace._start_trial(reused)
    process = workspace.processes[reused["id"]]
    directory = workspace.job_dir(reused["id"])
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and read_json(directory / "progress.json", {}).get("step", 0) < 2:
            time.sleep(.005)
        assert read_json(directory / "progress.json")["step"] >= 2
        workspace.control(reused["id"], ControlInput(action="pause"))
        assert process.wait(timeout=10) == 0
        workspace.reconcile()
        paused = workspace.store.get(reused["id"], "trial")
        assert paused["status"] == "paused" and 2 <= paused["result"]["evaluations"] < 200
        service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
        workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
        workspace.implementations.reconcile()
        queued = workspace.control(reused["id"], ControlInput(action="resume"))
        workspace._start_trial(queued)
        process = workspace.processes[reused["id"]]
        assert process.wait(timeout=20) == 0
        workspace.reconcile()
        workspace.reconcile_assets()
        resumed = workspace.store.get(reused["id"], "trial")
        assert resumed["status"] == "completed" and resumed["attempt"] == 2
        assert resumed["result"]["scientific_complete"] and resumed["result"]["evaluations"] == 200
        assert resumed["experiment_spec"] == frozen
        assert resumed["evaluator_version_id"] == job["version_id"]
        assert resumed["implementation_version_id"] == optimizer_job["version_id"]
        rows = list(iter_journal(directory / "observations.jsonl"))
        assert [row["metadata"]["generated_evaluator"]["evaluations"] for row in rows] == list(range(1, 201))
        assert len(workspace.store.list("implementation_grant")) == 2
        assert workspace.assets.attributed_costs(resumed["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 224
        assert workspace.current_tasks(campaign["id"])[0]["evaluator_binding_id"] == before["evaluator_binding_id"]
    finally:
        if process.poll() is None:
            workspace._terminate(workspace.store.get(reused["id"], "trial"))
            process.wait(timeout=5)
    # A new start rechecks the service. Frozen historical evidence remains intact.
    service.revoke(job["version_id"], "Counterexample discovered")
    with pytest.raises(ValueError, match="Counterexample"):
        workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="random", wall_seconds=5))
    assert len(workspace.store.list("trial")) == 3
    assert not workspace.drafts.readiness(draft["id"])["ready"]


def test_implementation_control_receipts_survive_lost_acknowledgement(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    submitted = service.submit(request(spec=specification().model_copy(update={"correctness_cases": []})))
    assert service.run_job(submitted["id"])["status"] == "blocked"
    resumed = service.control_once(submitted["id"], "resume", "resume-once")
    assert resumed["status"] == "queued"
    assert service.control_once(submitted["id"], "resume", "resume-once") == resumed
    with pytest.raises(ValueError, match="different action"):
        service.control_once(submitted["id"], "cancel", "resume-once")
    service.control_once(submitted["id"], "cancel", "cancel-once")
    # Retrying an old acknowledgement does not resume a subsequently cancelled job.
    assert service.control_once(submitted["id"], "resume", "resume-once")["status"] == "cancelled"
    assert len(service.store.list("implementation_control")) == 2


def test_failed_evaluator_has_one_manager_issue_while_independent_work_continues(tmp_path):
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
    from optimization_framework.execution.service import Workspace

    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)

    class Client:
        def submit(self, payload): return service.submit(JobRequest(**payload))
        def job(self, identity): return service.store.get(identity, "implementation_job")
        def versions(self): return service.store.list("implementation_version")

    workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
    spec = specification()
    campaign = workspace.create_campaign(CampaignInput(name="Scoped evaluator failure", compute_budget_seconds=60,
        implementation_compute_budget_seconds=30, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Evaluator needed", problem_id=spec.manifest.id, evaluator_manifest=spec.manifest),
               TaskInput(name="Independent benchmark", problem_id="bounded_continuous")]))
    task, independent = workspace.current_tasks(campaign["id"])
    # Missing independent evidence is a durable blocker with zero model calls.
    command = Command(id="missing-oracle", campaign_id=campaign["id"], expected_revision=1,
        operation="evaluator.commission", payload={"task_id": task["id"],
            "spec": spec.model_copy(update={"correctness_cases": []}).model_dump(mode="json"), "compute_seconds": 10, "max_calls": 1})
    outcome = workspace.commands.execute(command)
    grant = workspace.store.get(outcome["outcome"]["grant_id"], "implementation_grant")
    result = service.run_job(grant["job_id"])
    assert result["status"] == "blocked" and not result["usage"].get("calls")
    for _ in range(3):
        workspace.implementations.reconcile()
    issues = workspace.store.list("manager_issue", campaign["id"])
    assert len(issues) == 1 and issues[0]["status"] == "pending"
    assert grant["id"] in str(issues[0])
    assert not workspace.evaluators.readiness(task)["runnable"]
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=independent["id"],
        algorithm="coordinate", max_steps=4, wall_seconds=5))
    workspace._start_trial(trial)
    assert workspace.processes[trial["id"]].wait(timeout=10) == 0
    workspace.reconcile()
    assert workspace.store.get(trial["id"], "trial")["result"]["scientific_complete"]
    assert workspace.store.list("manager_issue", campaign["id"]) == issues


def contract_specification():
    original = specification().model_dump(mode="json")
    probes = [{key: value for key, value in case.items() if key in {"name", "candidate", "configuration", "fidelity"}}
              for case in original["correctness_cases"]]
    return EvaluatorSpec(**{**original, "validation_mode": "contract_only", "correctness_cases": [], "contract_cases": probes})


def test_contract_publication_is_not_numerical_validation_or_a_contract_bypass(tmp_path):
    from optimization_framework.implementations.evaluator_validation import current
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    spec = contract_specification()
    submitted = service.submit(request(spec=spec, package=package()))
    result = service.run_job(submitted["id"])
    assert result["status"] == "completed", result
    version = service.artifact(result["version_id"])["version"]
    assert version["status"] == "contract_validated"
    assert version["validation_report"]["kind"] == "evaluator_contract"
    assert version["validation_report"]["numerical_status"] == "unverified"
    assert current(version, numerical=False) and not current(version)
    assert "validation_mode" not in specification().model_dump()
    assert "contract_cases" not in specification().model_dump()
    with pytest.raises(ValueError, match="supplied oracles"):
        EvaluatorSpec(**{**specification().model_dump(), "validation_mode": "contract_only", "contract_cases": spec.contract_cases})
    bad = package(SOURCE.replace('"energy": value', '"energy": float("nan")'))
    submitted = service.submit(request(spec=spec, package=bad, idempotency_key="bad-contract", grant_id="bad-contract-grant"))
    result = service.run_job(submitted["id"])
    assert result["status"] == "failed" and not result["attempts"][0]["report"]["passed"]
    assert len(service.store.list("implementation_version")) == 1


def test_exploratory_evaluator_waiver_is_scoped_pinned_and_rechecked_before_launch(tmp_path):
    import json
    import time
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.requests import CampaignInput, ControlInput, StudyInput, TaskInput, TrialInput
    from optimization_framework.contracts.validation import WaiverRevocation
    from optimization_framework.execution.service import Workspace
    from optimization_framework.storage.sqlite import now, read_json

    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    spec = contract_specification()

    class Client:
        def version(self, identity): return service.version(identity)
        def artifact(self, identity): return service.artifact(identity)
        def versions(self): return service.store.list("implementation_version")

    workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
    campaign = workspace.create_campaign(CampaignInput(name="Scoped exploratory evaluator", compute_budget_seconds=60,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Missing numerical evidence", problem_id=spec.manifest.id,
            evaluator_manifest=spec.manifest)]))
    task = workspace.current_tasks(campaign["id"])[0]
    before_binding = workspace.create_study(campaign["id"], StudyInput(goal="A study created while the evaluator is missing"))
    job = service.run_job(service.submit(request(spec=spec, package=package()))["id"])
    binding = workspace.evaluators.attach(task["id"], job["version_id"], rationale="Retain this unverified evaluator for an explicitly scoped decision")
    assert {row["study_id"] for row in workspace.store.list("validation_requirement")} == {campaign["active_study_id"], before_binding["id"]}
    assert binding["validation_evidence_id"] in workspace.memory.sync(campaign["id"])["source_ids"]
    original = workspace.evaluators.readiness(task)
    assert not original["runnable"] and not original["waiver_allowed"]
    from optimization_framework.research.engine import select_probe
    assert select_probe([{**workspace.evaluators.task_view(task), "evaluator_readiness": original}], [], [])["status"] == "needs_validation"
    with pytest.raises(ValueError, match="independent numerical evidence"):
        workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", wall_seconds=5))
    assert not workspace.store.list("trial")
    study = workspace.create_study(campaign["id"], StudyInput(goal="Exploratory contract-only evaluation",
        validation_policy={"waivable_kinds": ["evaluator_correctness"], "manager_may_waive": False}))
    ready = workspace.evaluators.readiness(task)
    assert ready["requirement_id"] != original["requirement_id"] and ready["waiver_allowed"]
    version = workspace.store.get(campaign["id"], "campaign")["version"]
    command = Command(id="waive-evaluator-numerics", campaign_id=campaign["id"], expected_revision=version,
        operation="validation.waive", payload={"requirement_id": ready["requirement_id"],
            "rationale": "Explore the harness; no claim of numerical correctness or confirmation.",
            "evidence_ids": [binding["validation_evidence_id"]]})
    receipt = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == receipt
    ready = workspace.evaluators.readiness(task)
    assert ready["runnable"] and ready["state"] == "ready_with_waiver"
    proposal = select_probe([{**workspace.evaluators.task_view(task), "evaluator_readiness": ready}], [], [])
    assert proposal["status"] == "proposed" and not proposal["metrics"]["numerically_reliable"]
    assessment = workspace.validations.assess(ready["requirement_id"])
    assert assessment["status"] == "waived" and not assessment["measured_pass"]
    assert not assessment["scientific_claim_supported"]
    assert not workspace.evaluators.readiness(task, allow_waived=False)["runnable"]
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", max_steps=4, wall_seconds=5))
    assert trial["evaluator_eligibility"] == trial["experiment_spec"]["schedule"]["evaluator_eligibility"] == ready["eligibility"]
    workspace._start_trial(trial)
    assert workspace.processes[trial["id"]].wait(timeout=10) == 0
    workspace.reconcile()
    assert workspace.store.get(trial["id"], "trial")["result"]["scientific_complete"]
    workspace.memory.sync(campaign["id"])
    remembered = json.loads(next(row["text"] for row in workspace.memory.retrieve(campaign["id"], trial["id"]) if row["id"] == trial["id"]))
    assert remembered["claim_level"] == "exploratory_unverified_evaluator"
    assert remembered["evaluator_eligibility"] == ready["eligibility"]
    # A later waiver does not replace an experiment's still-valid authorization.
    workspace.commands.execute(command.model_copy(update={"id": "additional-authorization"}))
    additional = workspace.evaluators.readiness(task)["eligibility"]["waiver_id"]
    assert additional != ready["eligibility"]["waiver_id"]
    workspace.evaluators.check_launch(trial)
    workspace.validations.revoke(WaiverRevocation(id="withdraw-additional-waiver", campaign_id=campaign["id"],
        waiver_id=additional, rationale="Retain only the original authorization", authority="researcher", created_at=now()))
    queued = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate",
        max_steps=2000, wall_seconds=15, recovery={"every_observations": 5}))
    workspace._start_trial(queued)
    process = workspace.processes[queued["id"]]
    try:
        directory = workspace.job_dir(queued["id"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and read_json(directory / "progress.json", {}).get("step", 0) < 2:
            time.sleep(.005)
        assert read_json(directory / "progress.json")["step"] >= 2
        workspace.validations.revoke(WaiverRevocation(id="withdraw-evaluator-waiver", campaign_id=campaign["id"],
            waiver_id=ready["eligibility"]["waiver_id"], rationale="Review before further use", authority="researcher", created_at=now()))
        workspace.implementations.reconcile()
        workspace.implementations.reconcile()
        issues = [row for row in workspace.store.list("manager_issue", campaign["id"]) if row["code"] == "evaluator_eligibility"]
        assert len(issues) == 1 and issues[0]["affected"] == queued["id"]
        workspace.control(queued["id"], ControlInput(action="pause"))
        assert process.wait(timeout=10) == 0
        workspace.reconcile()
        paused = workspace.store.get(queued["id"], "trial")
        assert paused["status"] == "paused" and paused["progress"]["checkpoint_available"]
        service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
        workspace = Workspace(tmp_path / "workspace", implementation_client=Client())
        queued = workspace.control(queued["id"], ControlInput(action="resume"))
        assert queued["evaluator_eligibility"] == ready["eligibility"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    with pytest.raises(ValueError, match="independent numerical evidence"):
        workspace._start_trial(queued)
    renewed = workspace.commands.execute(command.model_copy(update={"id": "new-exploratory-authorization"}))
    assert renewed != receipt
    with pytest.raises(ValueError, match="authorization changed"):
        workspace._start_trial(queued)
    assert queued["id"] not in workspace.processes
    following = workspace.create_study(campaign["id"], StudyInput(goal="A different exploratory question",
        validation_policy={"waivable_kinds": ["evaluator_correctness"]}))
    assert following["id"] != study["id"]
    assert not workspace.evaluators.readiness(task)["runnable"]
    assert workspace.store.get(trial["id"], "trial")["study_id"] == study["id"]
    assert len(workspace.store.list("trial")) == 2
