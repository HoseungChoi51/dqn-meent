"""Pinned reference assets are executable inputs, with separate scientific roles."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace

from test_framework_templates import drain, template


def reference_template(candidate):
    definition = template(prefix=False, checks=True)
    definition["methods"]["reference"] = {"procedure": {"algorithm": "evaluate_asset", "max_steps": 1,
        "wall_seconds": 5, "input_binding": {"kind": "declared_asset:v1", "slot": "historical"}}}
    definition["input_requirements"] = {"historical": {"title": "Historical solution", "candidate_digest": content_hash(candidate)}}
    definition["groups"].insert(0, {"id": "reference", "scope": "reference", "reference_role": "historical_control",
        "slots": ["reference"], "seeds": [0], "priority": 100})
    definition["validation_policies"]["reference"] = deepcopy(definition["validation_policies"]["development"])
    return definition


def reference_workspace(tmp_path):
    workspace = Workspace(tmp_path, max_workers=4)
    campaign = workspace.create_campaign(CampaignInput(name="Declared references", compute_budget_seconds=100,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    return workspace, campaign, workspace.current_tasks(campaign["id"])[0]


def publish(workspace, campaign, task, candidate, identity="historical", **options):
    return workspace.assets.publish(Asset(id=identity, campaign_id=campaign["id"], kind="solution", title=identity,
        payload={"candidate": candidate, "problem": task["problem"]}, authority="researcher", created_at="test", **options))


def test_reference_runs_once_keeps_upstream_cost_and_stays_out_of_selection(tmp_path):
    workspace, campaign, task = reference_workspace(tmp_path)
    candidate = [0.25, -0.75]
    workspace.assets.record_cost(CostEvent(id="historical_work", campaign_id="original_campaign", source_id="historical_search",
        ordinal=0, category="imported", quantities={"evaluation_requests": 7, "solver_executions": 7, "worker_seconds": 2},
        status="historical", created_at="test"))
    asset = publish(workspace, campaign, task, candidate, costs=[CostSlice(source_id="historical_search", stop=1)],
        cost_provenance="complete", exposure_status="known")
    command = Command(id="freeze_references", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="study.freeze_template", payload={"template": reference_template(candidate), "task_ids": [task["id"]],
            "asset_bindings": {"historical": asset["id"]}})
    outcome = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == outcome
    execution = workspace.store.get(outcome["outcome"]["execution_id"], "study_execution")
    design = workspace.store.get(execution["design_id"], "confirmation_design")
    assert design["input_bindings"] == {"historical": {"asset_id": asset["id"], "asset_digest": asset["content_hash"]}}
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0
    with pytest.raises(ValueError, match="immutable"):
        publish(workspace, campaign, task, [1., 1.])
    workspace.study_executions.activate(execution["id"])
    state = drain(workspace, execution)
    reference = next(row for row in state["cells"] if row["scope"] == "reference")
    trial = workspace.store.get(reference["trial_id"], "trial")
    assert trial["result"]["evaluations"] == 1
    assert trial["result"]["best_candidate"] == candidate
    assert len([row for row in workspace.store.list("trial") if row["algorithm"] == "evaluate_asset"]) == 1
    assert workspace.assets.attributed_costs(trial["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 8
    nomination = workspace.store.list("nomination")[0]
    assert {row["trial_id"] for row in nomination["evidence"]["experiments"]}.isdisjoint({trial["id"]})
    checks = [row for row in workspace.store.list("trial") if row.get("parent_trial_id") == trial["id"]]
    assert len(checks) == 1 and checks[0]["result"]["scientific_complete"]
    release = workspace.confirmations.release(execution["protocol_id"])
    report = workspace.store.get(release["report_id"], "confirmation_report")
    evidence = report["evidence"]["analysis_evidence"]
    assert evidence["references"][0]["reference_role"] == "historical_control"
    assert evidence["references"][0]["input_assets"][0]["contributions"]["intervals"] == {"historical_search": [[0, 1]]}
    assert len(evidence["experiments"]) == 4 and report["qualification_only"]


def test_invalid_reference_binding_cannot_freeze_or_allocate(tmp_path):
    workspace, campaign, task = reference_workspace(tmp_path)
    candidate = [0., 0.]
    definition = reference_template(candidate)
    request = {"template": definition, "task_ids": [task["id"]]}
    with pytest.raises(ValueError, match="Bind every declared"):
        workspace.study_executions.freeze(campaign["id"], request)
    publish(workspace, campaign, task, [1., 1.], "wrong")
    with pytest.raises(ValueError, match="declared reference candidate"):
        workspace.study_executions.freeze(campaign["id"], {**request, "asset_bindings": {"historical": "wrong"}})
    publish(workspace, campaign, task, candidate, "unavailable", availability="unavailable")
    with pytest.raises(ValueError, match="available solution"):
        workspace.study_executions.freeze(campaign["id"], {**request, "asset_bindings": {"historical": "unavailable"}})
    workspace.store.put("trial", {"id": "held_out", "campaign_id": campaign["id"], "protected_cohort_id": "another_protocol", "status": "stopped", "wall_seconds": 0})
    publish(workspace, campaign, task, candidate, "protected", producer_id="held_out")
    with pytest.raises(ValueError, match="Protected cohort"):
        workspace.study_executions.freeze(campaign["id"], {**request, "asset_bindings": {"historical": "protected"}})
    publish(workspace, campaign, task, candidate, "unknown_history")
    definition["confirmation_kind"] = "unseen_instance"
    with pytest.raises(ValueError, match="historical inputs"):
        workspace.study_executions.freeze(campaign["id"], {**request, "asset_bindings": {"historical": "unknown_history"}})
    assert not workspace.store.list("confirmation_design")
    assert [row["id"] for row in workspace.store.list("trial")] == ["held_out"]
    assert workspace.allocated_seconds(campaign["id"]) == 0


def test_production_freezes_full_roster_and_priorities_without_running_a_research_campaign(tmp_path):
    from dqn_meent.replication import profile_config
    from dqn_meent.study_rules import conditional_design
    from dqn_meent.study_templates import production
    from optimization_framework.assets.references import registered
    workspace = Workspace(tmp_path, max_workers=8)
    campaign = workspace.create_campaign(CampaignInput(name="Production specification qualification",
        compute_budget_seconds=691200, validation_reserve_seconds=0,
        tasks=[TaskInput(name="1100 nm / 70 degrees", configuration=asdict(profile_config("P").physics))]))
    task = workspace.current_tasks(campaign["id"])[0]
    manifest = registered("meent_grating")[0]
    command = Command(id="import_reference_set", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="asset.import_reference_set", payload={"provider": "meent_grating", "reference_set_id": manifest.id,
            "manifest_digest": manifest.digest()})
    imported = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == imported
    assert workspace.commands.execute(command.model_copy(update={"id": "repeat_import"}))["outcome"] == imported["outcome"]
    bindings = imported["outcome"]["asset_bindings"]
    assert len(workspace.store.list("asset")) == 7 and len(workspace.store.list("reference_import")) == 1
    assert not workspace.store.list("reuse_decision") and not workspace.store.list("cost_event")
    assert workspace.assets.attributed_costs(list(bindings.values()))["quantities"]["worker_seconds"]["total"] is None
    with pytest.raises(ValueError, match="manifest is unavailable or changed"):
        workspace.commands.execute(command.model_copy(update={"id": "changed_manifest", "payload": {**command.payload, "manifest_digest": "0"*64}}))
    def browser_numbers(value):
        if isinstance(value, dict):
            return {key: browser_numbers(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [browser_numbers(item) for item in value]
        return int(value) if type(value) is float and value.is_integer() else value
    execution = workspace.study_executions.freeze(campaign["id"], {"template": browser_numbers(production()), "task_ids": [task["id"]], "asset_bindings": bindings})
    design = workspace.store.get(execution["design_id"], "confirmation_design")
    cells = [workspace.store.get(identity, "protocol_cell") for identity in design["cell_ids"]]
    assert len(cells) == 60
    assert sum(row["scope"] == "development" for row in cells) == 33
    assert sum(row["scope"] == "reference" for row in cells) == 7
    assert [(row["seed"], row["slot_id"]) for row in cells if row["group_id"] == "controls"] == [
        (seed, slot) for seed in range(100, 105) for slot in ("HC", "refine")]
    assert not workspace.store.list("trial") and workspace.allocated_seconds(campaign["id"]) == 0
    captured = workspace.directory / "sources" / design["source_id"] / "code/dqn_meent/data/replication_references.json"
    assert captured.is_file()
    for mutation in ("seed", "budget", "reference", "check"):
        changed = deepcopy(design)
        changed["instances"] = [task["problem"]]
        if mutation == "seed":
            changed["template"]["groups"][0]["seeds"] = [999]
        elif mutation == "budget":
            changed["template"]["total_seconds"] += 1
        elif mutation == "reference":
            changed["template"]["input_requirements"]["paper"]["candidate_digest"] = "0"*64
        else:
            changed["template"]["validation_policies"]["confirmation"]["required_recipe_parameters"]["fourier_convergence:v1"]["tolerance"] = .01
        with pytest.raises(ValueError, match="preserve its profiles"):
            conditional_design(changed, {})
    workspace.study_executions.activate(execution["id"])
    workspace.study_executions._reconcile(execution["id"])
    queued = workspace.store.list("trial")
    assert len(queued) == 8 and all(row["status"] == "queued" for row in queued)
    assert sum(row["algorithm"] == "evaluate_asset" for row in queued) == 7
    assert [(row["algorithm"], row["seed"]) for row in queued if row["algorithm"] != "evaluate_asset"] == [("dqn", 10)]
    assert len(workspace.store.list("execution_grant")) == 1
    assert workspace.resources.assessment(campaign["id"])["allocated_seconds"] == 691200
    # The scheduler thread is never started: this checks the production admission
    # contract without substituting a short run for the two-million-action study.


def test_staged_selection_counts_child_work_and_unknown_cost_cannot_pick_a_winner():
    from dqn_meent.replication import profile_config
    from dqn_meent.study_rules import selection, staged_selection
    from optimization_framework.evaluation.registry import problems
    problem = problems.resolve("meent_grating", asdict(profile_config("P").physics)).model_dump(mode="json")
    rows = []
    for name, local_time, full_time in (("P", 1, 100), ("C", 10, 20)):
        for seed in (10, 11, 12):
            training = asdict(profile_config(name).training)
            training.pop("seed")
            rows.append({"method": {"algorithm": "dqn", "training": training, "schedule_steps": 2_000_000,
                "completion": {"unit": "optimizer_decisions", "count": 2_000_000}}, "method_id": name,
                "problem": problem, "seed": seed, "evidence_complete": True, "amendments": [],
                "result": {"elapsed_seconds": local_time}, "full_worker_seconds": full_time,
                "validation": [{"recipe_id": "fourier_convergence:v1", "parameters": {"orders": [160, 320], "tolerance": .0001},
                    "measured_pass": True, "results": [{"producer": "trusted_service", "verdict": "passed", "measurements": {
                        "complete": True, "observations": [{"fourier_order": order, "efficiency": .8} for order in (160, 320)]}}]}]})
    evidence = {"experiments": rows}
    assert selection(evidence, {})["selected_profile"] == "P"
    assert staged_selection(evidence, {})["selected_profile"] == "C"
    rows[0]["full_worker_seconds"] = None
    result = staged_selection(evidence, {})
    assert result["selected_method_ids"] == [] and result["needs_attention"]
    assert result["outcome"] == "inconclusive_selection"


def test_reference_catalog_and_cross_campaign_import_preserve_identity(tmp_path):
    from fastapi.testclient import TestClient
    from optimization_framework.api.app import create_app
    from optimization_framework.assets.references import input_candidates
    from optimization_framework.contracts.templates import InputRequirement
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Reference library", tasks=[TaskInput(name="Grating")]))
    client = TestClient(app)
    entries = client.get("/api/v1/study-templates").json()["templates"]
    entry = next(row for row in entries if row["template"]["id"] == "meent-production-replication:v1")
    assert all(not candidates for candidates in entry["input_candidates"].values())
    reference_set = entry["reference_sets"][0]
    assert all("candidate" not in item for item in reference_set["manifest_preview"]["solutions"])
    payload = {"provider": reference_set["provider"], "reference_set_id": reference_set["manifest_preview"]["id"],
        "manifest_digest": reference_set["manifest_digest"]}
    outcome = client.post("/api/v1/commands", json={"id": "import_api", "campaign_id": campaign["id"],
        "expected_revision": campaign["version"], "operation": "asset.import_reference_set", "payload": payload}).json()["outcome"]
    another = workspace.create_campaign(CampaignInput(name="Another campaign", tasks=[TaskInput(name="Another grating")]))
    repeated = client.post("/api/v1/commands", json={"id": "import_another_campaign", "campaign_id": another["id"],
        "expected_revision": another["version"], "operation": "asset.import_reference_set", "payload": payload}).json()["outcome"]
    assert repeated["asset_bindings"] == outcome["asset_bindings"] and len(workspace.store.list("asset")) == 7
    updated = next(row for row in client.get("/api/v1/study-templates").json()["templates"] if row["template"]["id"] == entry["template"]["id"])
    assert all(any(asset["id"] == outcome["asset_bindings"][slot] for asset in candidates)
        for slot, candidates in updated["input_candidates"].items())
    assert all(asset["cost_provenance"] == "unknown" for candidates in updated["input_candidates"].values() for asset in candidates)
    paper = workspace.store.get(outcome["asset_bindings"]["paper"], "asset")
    workspace.store.put("trial", {"id": "protected_producer", "campaign_id": campaign["id"], "protected_cohort_id": "unreleased"})
    workspace.assets.publish(Asset(id="protected_duplicate", campaign_id=campaign["id"], kind="solution", title="Protected candidate",
        producer_id="protected_producer", payload=paper["payload"], authority="test", created_at="test"))
    requirements = {"paper": InputRequirement(**entry["template"]["input_requirements"]["paper"])}
    assert "protected_duplicate" not in {asset["id"] for asset in input_candidates(workspace.assets, requirements)["paper"]}
    # Publication stores the captured manifest itself, not an absolute sibling path.
    for artifact in paper["artifacts"]:
        from optimization_framework.contracts.experiments import ArtifactReference
        workspace.assets.artifacts.verify(ArtifactReference(**artifact))
