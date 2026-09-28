"""A shared prefix executes once but contributes in full to each result."""
import pytest

from optimization_framework.assets.catalog import AssetCatalog
from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice, ReuseDecision
from optimization_framework.storage.sqlite import Store


def setup_catalog(tmp_path):
    store = Store(tmp_path)
    catalog = AssetCatalog(store)
    store.put_immutable("study", {"id": "study", "campaign_id": "current"})
    return catalog


def asset(catalog, name, costs=(), dependencies=(), **kwargs):
    return catalog.publish(Asset(id=name, campaign_id="current", kind="solution", title=name,
        costs=list(costs), dependency_ids=list(dependencies), authority="researcher", created_at="test", **kwargs))


def test_shared_prefix_and_overlapping_slices_never_double_count_per_result(tmp_path):
    catalog = setup_catalog(tmp_path)
    for source, count in (("hc", 10), ("refine", 4)):
        for i in range(count):
            catalog.record_cost(CostEvent(id=f"{source}_{i}", campaign_id="current", source_id=source, ordinal=i,
                category="evaluation", quantities={"evaluation_requests": 1, "solver_executions": int(i % 2 == 0), "worker_seconds": .1}, created_at="test"))
    asset(catalog, "prefix", [CostSlice(source_id="hc", stop=5)], cost_provenance="complete")
    asset(catalog, "continued", [CostSlice(source_id="hc", start=3, stop=10)], ["prefix"], cost_provenance="complete")
    asset(catalog, "refined", [CostSlice(source_id="refine", stop=4)], ["prefix"], cost_provenance="complete")
    actual = catalog.actual_costs("current")["quantities"]["evaluation_requests"]["total"]
    continued = catalog.attributed_costs(["continued"])["quantities"]["evaluation_requests"]["total"]
    refined = catalog.attributed_costs(["refined"])["quantities"]["evaluation_requests"]["total"]
    assert (actual, continued, refined) == (14, 10, 9)
    assert catalog.attributed_costs(["continued", "refined"])["quantities"]["evaluation_requests"]["total"] == 14
    catalog.record_cost(CostEvent(**{key: value for key, value in catalog.store.get("hc_0").items() if key != "content_hash"}))
    assert catalog.actual_costs("current")["event_count"] == 14


def test_unknown_history_and_missing_intervals_do_not_become_zero(tmp_path):
    catalog = setup_catalog(tmp_path)
    asset(catalog, "imported", cost_provenance="unknown", exposure_status="unknown")
    asset(catalog, "result", [CostSlice(source_id="missing", stop=3)], ["imported"], cost_provenance="complete")
    costs = catalog.attributed_costs(["result"])["quantities"]["solver_executions"]
    assert costs == {"known": 0, "unknown_events": 3, "unknown_provenance": 1, "total": None}


def test_reading_evidence_and_declining_reuse_never_exposes_worker_inputs(tmp_path):
    catalog = setup_catalog(tmp_path)
    asset(catalog, "solution", payload={"candidate": [0., 1.]})
    def decide(name, decision, use):
        return catalog.decide(ReuseDecision(id=name, campaign_id="current", study_id="study", asset_id="solution",
            decision=decision, intended_use=use, rationale="Assess usefulness for the active problem", authority="manager", created_at="test"))
    decide("read", "reference", "manager_evidence")
    decide("decline", "decline", "optimizer_input")
    assert catalog.manager_references("current", "study")[0]["payload"] == {"candidate": [0., 1.]}
    with pytest.raises(ValueError, match="explicit"):
        catalog.inputs("current", "study", ["solution"], ["read", "decline"])
    decide("reuse", "reuse", "optimizer_input")
    assert catalog.inputs("current", "study", ["solution"], ["reuse"])[0]["payload"] == {"candidate": [0., 1.]}
    with pytest.raises(ValueError, match="immutable"):
        asset(catalog, "solution", payload={"candidate": [1., 1.]})


def test_manager_reference_does_not_release_heldout_measurements(tmp_path):
    catalog = setup_catalog(tmp_path)
    catalog.store.put("trial", {"id": "heldout", "campaign_id": "current", "task_split": "test"})
    asset(catalog, "protected", producer_id="heldout", payload={"secret_score": .9876})
    catalog.decide(ReuseDecision(id="reference", campaign_id="current", study_id="study", asset_id="protected",
        decision="reference", intended_use="manager_evidence", rationale="Inspect applicability", authority="manager", created_at="test"))
    reference = catalog.manager_references("current", "study")[0]
    assert "payload" not in reference and "Heldout" in reference["unavailable_reason"]


def test_worker_cost_and_artifact_ingestion_survives_repeat_reconciliation(tmp_path):
    from optimization_framework.assets.execution import ingest_costs, ingest_outputs
    from optimization_framework.evaluation.registry import problems
    from optimization_framework.execution.worker import run
    from optimization_framework.storage.artifacts import atomic_json
    catalog = setup_catalog(tmp_path / "workspace")
    directory = tmp_path / "worker"
    problem = problems.resolve("bounded_continuous", {"dimensions": 2})
    trial = {"id": "experiment", "campaign_id": "current", "study_id": "study", "seed": 3,
        "algorithm": "coordinate", "algorithm_config": {}, "problem": problem.model_dump(mode="json"),
        "max_steps": 4, "wall_seconds": 30, "schedule_steps": 4, "created_at": "test"}
    atomic_json(directory / "spec.json", trial)
    trial["result"] = run(directory)
    first_costs = ingest_costs(catalog, trial, directory)
    outputs = ingest_outputs(catalog, trial, directory, first_costs)
    events_before = catalog.store.events()
    assert ingest_costs(catalog, trial, directory) == first_costs
    assert ingest_outputs(catalog, trial, directory, first_costs) == outputs
    assert catalog.store.events() == events_before
    assert catalog.actual_costs("current")["quantities"]["evaluation_requests"]["total"] == 4
    assert catalog.attributed_costs(outputs)["quantities"]["evaluation_requests"]["total"] == 4
    assert catalog.actual_costs("current")["quantities"]["worker_seconds"]["total"] > 0
    assert len(catalog.store.list("execution_attempt")) == 1


def test_refinement_requires_explicit_asset_use_and_attributes_its_source_work(tmp_path):
    from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
    from optimization_framework.execution.service import Workspace
    from optimization_framework.execution.worker import run
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Refinement", compute_budget_seconds=200, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Optical case", physics={"n_cells": 4, "fourier_order": 1})]))
    task = workspace.current_tasks(campaign["id"])[0]
    def execute(trial):
        result = run(workspace.job_dir(trial["id"]))
        assert result["status"] == "completed", result
        trial.update(result=result, progress=result, status="completed", attempt=1, execution_seconds=result["elapsed_seconds"])
        workspace.store.put("trial", trial)
        return workspace.capture_evidence(trial)
    parent = execute(workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
        algorithm="hillclimb", max_steps=5, wall_seconds=30)))
    solution = next(workspace.store.get(item, "asset") for item in parent["latest_output_asset_ids"] if workspace.store.get(item, "asset")["kind"] == "solution")
    child = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="refinement", max_steps=4, wall_seconds=30)
    with pytest.raises(ValueError, match="versioned solution"):
        workspace.create_trial(child)
    for choice in ("decline", "reuse"):
        workspace.assets.decide(ReuseDecision(id=choice, campaign_id=campaign["id"], study_id=campaign["active_study_id"],
            asset_id=solution["id"], decision=choice, intended_use="optimizer_input", rationale="Assess this starting candidate",
            authority="researcher", created_at="test"))
    with pytest.raises(ValueError, match="explicit"):
        workspace.create_trial(child.model_copy(update={"initial_assets": [solution["id"]], "reuse_decision_ids": ["decline"]}))
    child = execute(workspace.create_trial(child.model_copy(update={"initial_assets": [solution["id"]], "reuse_decision_ids": ["reuse"]})))
    assert child["algorithm_config"]["initial_design"] == solution["payload"]["candidate"]
    assert child["experiment_spec"]["asset_digests"] == {solution["id"]: solution["content_hash"]}
    full = workspace.assets.attributed_costs(child["latest_output_asset_ids"])
    assert full["quantities"]["evaluation_requests"]["total"] == 9
    assert workspace.assets.actual_costs(campaign["id"])["quantities"]["evaluation_requests"]["total"] == 9
