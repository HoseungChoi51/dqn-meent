"""Commissioned evaluators use captured reviewed recipes and worker-owned costs."""
from copy import deepcopy
from importlib import metadata
from pathlib import Path
import sys
import time

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.evaluators import EvaluatorManifest
from optimization_framework.contracts.requests import CampaignInput, ControlInput, RecipeInput, StudyInput, TaskInput, TrialInput
from optimization_framework.contracts.templates import TemplateFreezeInput
from optimization_framework.evaluation.generated import DeclaredEvaluator, declared_registry
from optimization_framework.evaluation.recipes import validation_policy
from optimization_framework.evaluation.registry import problems
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import read_journal
from optimization_framework.implementations.models import EvaluatorSpec, JobRequest
from optimization_framework.implementations.service import ImplementationService
from optimization_framework.storage.sqlite import read_json

from test_framework_evaluators import MockReviewer, SOURCE, contract_specification, package, specification
from test_framework_templates import template


RECIPES = ["candidate_reevaluation:v1", "fidelity_comparison:v1"]


class Client:
    def __init__(self, service): self.service = service
    def submit(self, payload): return self.service.submit(JobRequest(**payload))
    def version(self, identity): return self.service.version(identity)
    def artifact(self, identity): return self.service.artifact(identity)
    def versions(self): return self.service.store.list("implementation_version")
    def job(self, identity): return self.service.store.get(identity, "implementation_job")
    def control(self, identity, action, **kwargs): return self.service.control_once(identity, action, kwargs.get("idempotency_key"))


def setup(directory, *, contract_only=False, slow=False, recipe_ids=None):
    data = (contract_specification() if contract_only else specification()).model_dump(mode="json")
    data["manifest"]["recipe_ids"] = recipe_ids if recipe_ids is not None else [*RECIPES, "unavailable_analysis:v1"]
    spec = EvaluatorSpec.model_validate(data)
    service = ImplementationService(directory / "library", adapter_factory=MockReviewer)
    workspace = Workspace(directory / "workspace", implementation_client=Client(service))
    campaign = workspace.create_campaign(CampaignInput(name="Commissioned recipe qualification", compute_budget_seconds=500,
        implementation_compute_budget_seconds=60, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Declared quadratic", problem_id=spec.manifest.id, evaluator_manifest=spec.manifest)]))
    task = workspace.current_tasks(campaign["id"])[0]
    source = SOURCE.replace("self.count += 1", "import time; time.sleep(.02); self.count += 1") if slow else SOURCE
    receipt = workspace.commands.execute(Command(id="commission-evaluator", campaign_id=campaign["id"], expected_revision=1,
        operation="evaluator.commission", payload={"task_id": task["id"], "spec": spec.model_dump(mode="json"),
            "package": package(source).model_dump(mode="json"), "compute_seconds": 30, "max_calls": 1}))
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    job = service.run_job(grant["job_id"])
    assert job["status"] == "completed", job
    workspace.implementations.reconcile()
    return workspace, service, campaign, workspace.current_tasks(campaign["id"])[0], spec


def finish(workspace, trial):
    workspace._start_trial(trial)
    process = workspace.processes[trial["id"]]
    assert process.wait(timeout=35) == 0
    workspace.reconcile()
    workspace.reconcile_assets()
    result = workspace.store.get(trial["id"], "trial")
    assert result["status"] == "completed" and result["result"]["scientific_complete"], result
    return result


def parent(workspace, campaign, task, **changes):
    return finish(workspace, workspace.create_trial(TrialInput(**{**dict(campaign_id=campaign["id"], task_id=task["id"],
        algorithm="coordinate", max_steps=4, wall_seconds=10), **changes})))


def test_manifest_recipe_declarations_preserve_legacy_identity_and_scoped_resolution():
    original = specification().manifest
    assert "recipe_ids" not in original.model_dump(mode="json")
    assert EvaluatorManifest(**{**original.model_dump(), "recipe_ids": []}).version == original.version
    for identities in ([RECIPES[0], RECIPES[0]], ["unreviewed.module:create"]):
        with pytest.raises(ValueError):
            EvaluatorManifest(**{**original.model_dump(), "recipe_ids": identities})
    # The same local name can refer to distinct versioned definitions without
    # replacing the installed adapter for any other caller.
    name = problems.ids()[0]
    installed = problems.get(name)
    manifest = EvaluatorManifest(**{**original.model_dump(), "id": name, "recipe_ids": RECIPES})
    problem = manifest.resolve("published-evaluator")
    registry = declared_registry(problem, manifest)
    assert problems.get(name) is installed
    assert registry.resolve(name, problem.configuration, problem.fidelity) == problem
    with pytest.raises(ValueError, match="no executable"):
        registry.evaluator(problem)
    with pytest.raises(ValueError, match="differs"):
        declared_registry(problem.model_copy(update={"evaluator_version": "other"}), original)


def test_parameters_and_evidence_limits_are_validated_before_a_study_or_diagnostic_runs():
    manifest = EvaluatorManifest(**{**specification().manifest.model_dump(), "recipe_ids": RECIPES})
    problem = manifest.resolve("unresolved")
    registry = declared_registry(problem, manifest)
    task = {"problem": problem.model_dump(mode="json")}
    policy = {"required_recipes": [RECIPES[1]], "required_recipe_parameters": {
        RECIPES[1]: {"fidelities": [{"digits": 0}, {"digits": 4}, {"digits": 8}]}}}
    result = validation_policy([task], policy, registry_resolver=lambda _: registry)
    assert result["required_recipe_parameters"][RECIPES[1]]["absolute_tolerance"] == 1e-6
    for values, message in [({"fidelities": [{"digits": 8}, {}]}, "distinct"),
            ({"fidelities": [{"digits": 2}, {"digits": 20}]}, "bounds"),
            ({"fidelities": [{"digits": 2}, {"digits": 8}], "absolute_tolerance": -1}, "greater than or equal"),
            ({"fidelities": [{"digits": 2}]}, "at least 2"),
            ({"fidelities": [{"digits": 2}, {"digits": 8}], "unreviewed": True}, "Extra inputs")]:
        with pytest.raises(ValueError, match=message):
            validation_policy([task], {**policy, "required_recipe_parameters": {RECIPES[1]: values}}, registry_resolver=lambda _: registry)
    stochastic = manifest.model_copy(update={"deterministic": False})
    stochastic_problem = stochastic.resolve("unresolved")
    adapter = DeclaredEvaluator(stochastic, "unresolved")
    with pytest.raises(ValueError, match="deterministic"):
        adapter.recipe_parameters(stochastic_problem, RECIPES[1], policy["required_recipe_parameters"][RECIPES[1]])
    assert adapter.recipe_parameters(stochastic_problem, RECIPES[0], {"repeats": 3})["repeats"] == 3
    with pytest.raises(ValueError, match="registered validation assertion"):
        validation_policy([task], {"required_recipes": [RECIPES[0]]}, registry_resolver=lambda _: registry)


def test_real_worker_recipes_pin_evaluator_and_attribute_shared_costs_once(tmp_path, monkeypatch):
    workspace, _, campaign, task, _ = setup(tmp_path)
    source = parent(workspace, campaign, task)
    assert set(source["execution_manifest"]["recipe_entry_points"]) == set(RECIPES)
    records = {kind: deepcopy(workspace.store.list(kind)) for kind in ("trial", "validation_requirement", "resource_reservation", "asset")}
    allocation = workspace.allocated_seconds(campaign["id"])
    for identity, parameters, message in [("unavailable_analysis:v1", {}, "unavailable"),
            (RECIPES[1], {"fidelities": [{"digits": 8}, {}]}, "distinct"),
            (RECIPES[0], {"fidelity": {"digits": 13}}, "bounds")]:
        with pytest.raises(ValueError, match=message):
            workspace.run_recipe(source["id"], RecipeInput(recipe_id=identity, parameters=parameters))
        assert {kind: workspace.store.list(kind) for kind in records} == records
        assert workspace.allocated_seconds(campaign["id"]) == allocation
    # New requests against an old experiment use its captured registration and
    # compiler, even after the currently installed registry changes.
    from optimization_framework.evaluation import registered_recipes
    monkeypatch.setattr(registered_recipes.recipes, "entries", lambda: {})
    monkeypatch.setattr(registered_recipes.CandidateReevaluation, "plan", lambda *_: pytest.fail("Used current recipe code"))
    reevaluation = finish(workspace, workspace.run_recipe(source["id"], RecipeInput(recipe_id=RECIPES[0],
        parameters={"repeats": 3, "fidelity": {"digits": 3}}, subject_limit=2, wall_seconds=10)))
    assert reevaluation["evaluator_version_id"] == source["evaluator_version_id"]
    assert reevaluation["execution_manifest"] == source["execution_manifest"]
    assert not reevaluation["validation_requirement_ids"]
    assert "no independent numerical correctness" in reevaluation["result"]["recipe_result"]["limitations"]
    observations = read_journal(workspace.job_dir(reevaluation["id"]) / "observations.jsonl")
    assert len(observations) == 6 and all(row["fidelity"] == {"digits": 3} for row in observations)
    fidelity = finish(workspace, workspace.run_recipe(source["id"], RecipeInput(recipe_id=RECIPES[1],
        parameters={"fidelities": [{"digits": 0}, {"digits": 4}, {"digits": 8}], "absolute_tolerance": .0001},
        subject_limit=2, wall_seconds=10)))
    findings = fidelity["result"]["recipe_result"]["subjects"]
    assert len(findings) == 2 and all(row["verdict"] == "passed" for row in findings)
    assert all("final two" in row["rationale"] for row in findings)
    observations = read_journal(workspace.job_dir(fidelity["id"]) / "observations.jsonl")
    assert len({row["evaluator_identity"] for row in observations}) == 3
    for row in observations:
        expected = round(sum((value - 1.5)**2 for value in row["candidate"]) - 2, row["fidelity"]["digits"])
        assert row["objectives"]["energy"] == expected
    for identity in fidelity["validation_requirement_ids"]:
        check = workspace.validations.assess(identity)
        assert check["measured_pass"] and check["requirement"]["kind"] == "solution_fidelity"
    for trial in (reevaluation, fidelity):
        assert workspace.assets.attributed_costs(trial["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 30
    both = workspace.assets.attributed_costs(reevaluation["latest_output_asset_ids"] + fidelity["latest_output_asset_ids"])
    assert both["quantities"]["evaluation_requests"]["total"] == 36  # 20 checks + 4 shared prefix + 12 new measurements


def test_recipe_pause_restores_multiple_hosts_and_preserves_attempt_costs(tmp_path):
    workspace, service, campaign, task, _ = setup(tmp_path, slow=True)
    source = parent(workspace, campaign, task)
    trial = workspace.run_recipe(source["id"], RecipeInput(recipe_id=RECIPES[1], parameters={
        "fidelities": [{"digits": index} for index in range(13)], "absolute_tolerance": .0001}, subject_limit=2, wall_seconds=30))
    workspace._start_trial(trial)
    process = workspace.processes[trial["id"]]
    directory = workspace.job_dir(trial["id"])
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and read_json(directory / "progress.json", {}).get("step", 0) < 2:
            time.sleep(.01)
        workspace.reconcile()
        workspace.control(trial["id"], ControlInput(action="pause"))
        assert process.wait(timeout=10) == 0
        workspace.reconcile()
        paused = workspace.store.get(trial["id"], "trial")
        assert paused["status"] == "paused" and 2 <= paused["progress"]["step"] < 26
        previous = read_journal(directory / "observations.jsonl")
        assert len({row["evaluator_identity"] for row in previous}) >= 2
    finally:
        if process.poll() is None:
            workspace.control(trial["id"], ControlInput(action="stop"))
            process.wait(timeout=10)
    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    workspace = Workspace(tmp_path / "workspace", implementation_client=Client(service))
    workspace.control(trial["id"], ControlInput(action="resume"))
    completed = finish(workspace, workspace.store.get(trial["id"], "trial"))
    observations = read_journal(directory / "observations.jsonl")
    assert observations[:len(previous)] == previous
    assert len(observations) == len({row["request_id"] for row in observations}) == 26
    assert completed["result"]["evaluations"] == completed["result"]["solver_calls"] == 26
    assert len({row["attempt_id"] for row in observations}) == 2
    assert workspace.assets.attributed_costs(completed["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 50
    before = workspace.store.list("validation_result")
    workspace.reconcile()
    assert workspace.store.list("validation_result") == before


def test_fidelity_pass_does_not_validate_a_waived_evaluator(tmp_path):
    workspace, _, campaign, task, _ = setup(tmp_path, contract_only=True)
    study = workspace.create_study(campaign["id"], StudyInput(goal="Explore declared fidelities with unverified numerics",
        validation_policy={"waivable_kinds": ["evaluator_correctness"]}))
    ready = workspace.evaluators.readiness(task)
    binding = workspace.evaluators.binding(task)
    workspace.commands.execute(Command(id="waive-numerics", campaign_id=campaign["id"],
        expected_revision=workspace.store.get(campaign["id"], "campaign")["version"], operation="validation.waive",
        payload={"requirement_id": ready["requirement_id"], "rationale": "Explore only; independent numerical truth remains unknown",
            "evidence_ids": [binding["validation_evidence_id"]]}))
    source = parent(workspace, campaign, task)
    checked = finish(workspace, workspace.run_recipe(source["id"], RecipeInput(recipe_id=RECIPES[1],
        parameters={"fidelities": [{"digits": 4}, {"digits": 8}], "absolute_tolerance": .0001}, wall_seconds=10)))
    assert checked["study_id"] == study["id"]
    assert checked["evaluator_eligibility"] == source["evaluator_eligibility"]
    assert workspace.validations.assess(checked["validation_requirement_ids"][0])["measured_pass"]
    numerical = workspace.validations.assess(ready["requirement_id"])
    assert numerical["status"] == "waived" and not numerical["measured_pass"]
    with pytest.raises(ValueError, match="cannot support confirmation"):
        workspace.create_study(campaign["id"], StudyInput(goal="Unsupported confirmation", scope="confirmation",
            confirmation_kind="seed_replication", prototype_trial_ids=[source["id"]], seeds=[100]))


def test_generated_recipe_policy_and_milestone_work_in_frozen_template(tmp_path):
    workspace, _, campaign, task, _ = setup(tmp_path)
    declaration = template(prefix=False)
    parameters = {"fidelities": [{"digits": 4}, {"digits": 8}], "absolute_tolerance": .0001}
    policy = {"required_recipes": [RECIPES[1]], "required_recipe_parameters": {RECIPES[1]: parameters}, "validation_wall_seconds": 5}
    declaration["validation_policies"] = {"development": policy, "confirmation": policy}
    declaration["methods"]["P"]["procedure"]["diagnostics"] = [{"at_counts": [2], "export_optimizer": False,
        "recipes": [{"recipe_id": RECIPES[0], "parameters": {"repeats": 2}, "wall_seconds": 5}]}]
    execution = workspace.study_executions.freeze(campaign["id"], TemplateFreezeInput(template=declaration, task_ids=[task["id"]]))
    workspace.study_executions.activate(execution["id"])
    from optimization_framework.evaluation.diagnostics import reconcile
    for _ in range(15):
        reconcile(workspace)
        workspace.study_executions._reconcile(execution["id"])
        for trial in workspace.store.list("trial"):
            if trial["status"] == "queued":
                finish(workspace, trial)
        state = workspace.study_executions.assess(execution["id"])
        if state["complete"]:
            break
    assert state["complete"], {"assessment": state, "issues": workspace.store.list("manager_issue")}
    checks = [row for row in workspace.store.list("trial") if row.get("recipe")]
    assert {row["recipe"]["recipe_id"] for row in checks} == set(RECIPES)
    assert all(row["evaluator_version_id"] == task["evaluator_version_id"] for row in checks)
    assert all(row["status"] == "completed" for row in checks)
    assert workspace.store.list("nomination")[0]["selected_method_ids"]


def test_campaign_catalog_preserves_distinct_declarations_and_exact_evaluator_versions(tmp_path):
    from fastapi.testclient import TestClient
    from optimization_framework.api.app import create_app
    from optimization_framework.contracts.requests import CampaignUpdate
    workspace, service, campaign, task, spec = setup(tmp_path)
    changed = spec.manifest.model_copy(update={"recipe_ids": [RECIPES[0]]})
    workspace.update_campaign(campaign["id"], CampaignUpdate(tasks=[TaskInput(name="Next declaration",
        problem_id=changed.id, evaluator_manifest=changed)]))
    client = TestClient(create_app(workspace.directory, start_workers=False, implementation_client=Client(service)))
    catalog = client.get("/api/v1/problems", params={"campaign_id": campaign["id"]}).json()["problems"]
    definitions = [row for row in catalog if row["id"] == spec.manifest.id]
    assert {(row["version"], row["evaluator_version"]) for row in definitions} == {
        (spec.manifest.version, task["evaluator_version_id"]), (changed.version, "unresolved")}
    original = next(row for row in definitions if row["version"] == spec.manifest.version)
    assert original["recipe_schemas"]["unavailable_analysis:v1"]["available"] is False
    assert not any(row["id"] == spec.manifest.id for row in client.get("/api/v1/problems").json()["problems"])


def test_reviewed_extension_keeps_its_captured_source_after_uninstallation(tmp_path, monkeypatch):
    from optimization_framework.evaluation import registered_recipes
    directory = tmp_path / "installed/fixture_recipes"
    directory.mkdir(parents=True)
    module = directory / "__init__.py"
    module.write_text((Path(__file__).parent / "fixtures/recipe_extension.py").read_text())
    monkeypatch.syspath_prepend(str(directory.parent))
    entry = metadata.EntryPoint(name="fixture_measurements:v1", value="fixture_recipes:Adapter", group=registered_recipes.GROUP)
    original = metadata.entry_points
    def entries(*, group):
        return [entry] if group == registered_recipes.GROUP else original(group=group)
    monkeypatch.setattr(metadata, "entry_points", entries)
    monkeypatch.setattr("optimization_framework.execution.source.entry_points", entries)
    try:
        workspace, _, campaign, task, _ = setup(tmp_path, recipe_ids=[entry.name])
        source = parent(workspace, campaign, task)
        assert source["execution_manifest"]["recipe_entry_points"] == {entry.name: entry.value}
        assert "fixture_recipes/__init__.py" in source["execution_manifest"]["scientific_files"]
        module.write_text('raise RuntimeError("Changed installed recipe must not execute")\n')
        sys.modules.pop("fixture_recipes", None)
        monkeypatch.setattr(metadata, "entry_points", original)
        monkeypatch.setattr("optimization_framework.execution.source.entry_points", original)
        catalog = workspace.describe_trial_problem(source["id"])
        assert catalog["basis"] == "captured_source"
        assert catalog["definition"]["recipe_schemas"][entry.name]["available"]
        assert catalog["definition"]["evaluator_version"] == source["evaluator_version_id"]
        child = finish(workspace, workspace.run_recipe(source["id"], RecipeInput(recipe_id=entry.name,
            parameters={"repeats": 3}, subject_limit=1, wall_seconds=10)))
        summary = child["result"]["recipe_result"]["subjects"][0]
        assert summary["count"] == 3 and summary["range"] == [summary["mean"], summary["mean"]]
        assert child["result"]["evaluations"] == 3 and not child["validation_requirement_ids"]
        assert workspace.assets.attributed_costs(child["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 27
    finally:
        sys.modules.pop("fixture_recipes", None)
