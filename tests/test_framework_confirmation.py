"""Known-instance replication must not inherit the unseen-instance restriction."""
import pytest

from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, StudyInput, ControlInput
from optimization_framework.execution.service import Workspace


def prepare(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Confirmation", compute_budget_seconds=300, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Known condition", physics={"n_cells": 4, "fourier_order": 1})]))
    task = workspace.current_tasks(campaign["id"])[0]
    request = TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="hillclimb", seed=0, max_steps=4, wall_seconds=10)
    prototype = workspace.create_trial(request)
    return workspace, campaign, task, request, prototype


def freeze(workspace, campaign, prototype, kind, *, seeds=(17, 18)):
    return workspace.create_study(campaign["id"], StudyInput(goal="Confirm the fixed method under a declared protocol",
        scope="confirmation", prototype_trial_ids=[prototype["id"]], confirmation_kind=kind, seeds=list(seeds)))


def test_fresh_seed_replication_on_exposed_condition_has_its_own_protocol(tmp_path):
    workspace, campaign, task, request, prototype = prepare(tmp_path)
    study = freeze(workspace, campaign, prototype, "seed_replication")
    protocol = study["confirmation"]["id"]
    trial = workspace.create_trial(request.model_copy(update={"seed": 17, "confirmation_protocol_id": protocol}))
    assert trial["confirmation_kind"] == "seed_replication"
    assert trial["study_id"] == study["id"]
    assert trial["experiment_spec"]["extension_policy"] == "forbidden"
    with pytest.raises(ValueError, match="already allocated"):
        workspace.create_trial(request.model_copy(update={"seed": 17, "confirmation_protocol_id": protocol}))
    with pytest.raises(ValueError, match="frozen"):
        workspace.control(trial["id"], ControlInput(action="extend", max_steps=8, wall_seconds=20))


def test_unseen_confirmation_rejects_the_same_previously_exposed_condition(tmp_path):
    workspace, campaign, task, request, prototype = prepare(tmp_path)
    study = freeze(workspace, campaign, prototype, "unseen_instance")
    with pytest.raises(ValueError, match="already evaluated"):
        workspace.create_trial(request.model_copy(update={"seed": 17, "confirmation_protocol_id": study["confirmation"]["id"]}))


def test_method_schedule_and_seed_rosters_are_fixed_before_confirmation(tmp_path):
    workspace, campaign, task, request, prototype = prepare(tmp_path)
    study = freeze(workspace, campaign, prototype, "seed_replication")
    request = request.model_copy(update={"seed": 17, "confirmation_protocol_id": study["confirmation"]["id"]})
    with pytest.raises(ValueError, match="frozen method"):
        workspace.create_trial(request.model_copy(update={"schedule_steps": 9}))
    with pytest.raises(ValueError, match="seed|Seed"):
        workspace.create_trial(request.model_copy(update={"seed": 2}))
    with pytest.raises(ValueError, match="frozen confirmation protocol"):
        workspace.create_trial(request.model_copy(update={"confirmation_protocol_id": None}))


def test_replication_requires_fresh_seeds_even_on_a_known_instance(tmp_path):
    workspace, campaign, task, request, prototype = prepare(tmp_path)
    study = freeze(workspace, campaign, prototype, "seed_replication", seeds=(0, 1))
    with pytest.raises(ValueError, match="fresh seed"):
        workspace.create_trial(request.model_copy(update={"confirmation_protocol_id": study["confirmation"]["id"]}))


def test_frozen_policy_transfer_uses_exported_weights_without_learning_and_charges_pretraining(tmp_path):
    from optimization_framework.contracts.assets import ReuseDecision
    from optimization_framework.execution.worker import run
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Transfer", compute_budget_seconds=300, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Source", physics={"n_cells": 4, "fourier_order": 1}),
               TaskInput(name="Target", physics={"n_cells": 4, "fourier_order": 1, "wavelength_nm": 1200})]))
    source, target = workspace.current_tasks(campaign["id"])
    def execute(trial):
        result = run(workspace.job_dir(trial["id"]))
        assert result["status"] == "completed", result
        trial.update(result=result, progress=result, status="completed", attempt=1, execution_seconds=result["elapsed_seconds"])
        workspace.store.put("trial", trial)
        return workspace.capture_evidence(trial)
    trained = execute(workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=source["id"], algorithm="dqn",
        max_steps=12, schedule_steps=8, completion={"unit": "optimizer_decisions", "count": 8}, wall_seconds=20,
        training={"horizon": 3, "batch_size": 2, "buffer_size": 16, "learning_starts": 2, "hidden_sizes": [8, 8]})))
    policy = next(workspace.store.get(item, "asset") for item in trained["latest_output_asset_ids"] if workspace.store.get(item, "asset")["kind"] == "policy")
    def reuse(study_id, decision_id):
        workspace.assets.decide(ReuseDecision(id=decision_id, campaign_id=campaign["id"], study_id=study_id, asset_id=policy["id"],
            decision="reuse", intended_use="optimizer_input", rationale="Evaluate a frozen policy on a declared target",
            authority="researcher", created_at="test"))
    reuse(campaign["active_study_id"], "probe_use")
    request = TrialInput(campaign_id=campaign["id"], task_id=target["id"], algorithm="frozen_policy", max_steps=5, wall_seconds=20,
        initial_assets=[policy["id"]], reuse_decision_ids=["probe_use"])
    prototype = execute(workspace.create_trial(request))
    study = workspace.create_study(campaign["id"], StudyInput(goal="Measure the fixed learned policy on the target",
        scope="confirmation", task_ids=[target["id"]], prototype_trial_ids=[prototype["id"]],
        confirmation_kind="policy_transfer", seeds=[18], policy_asset_id=policy["id"]))
    reuse(study["id"], "confirm_use")
    transferred = execute(workspace.create_trial(request.model_copy(update={"seed": 18, "reuse_decision_ids": ["confirm_use"],
        "confirmation_protocol_id": study["confirmation"]["id"]})))
    assert transferred["confirmation_kind"] == "policy_transfer"
    assert transferred["result"]["diagnostics"]["updates"] == 0
    assert transferred["result"]["diagnostics"]["adaptation"] == "forbidden"
    assert workspace.store.get(policy["id"], "asset") == policy
    costs = workspace.assets.attributed_costs(transferred["latest_output_asset_ids"])
    assert costs["quantities"]["evaluation_requests"]["total"] == trained["result"]["evaluations"] + 5
