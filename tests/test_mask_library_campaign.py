"""Campaign boundary checks for the separately installed mask optimizer library."""
import math

import numpy as np
import pytest

from dqn_meent.problem_2d import Meent2DProblem
from optimization_framework.contracts.problems import Observation
from optimization_framework.optimizers.registry import create
from optimization_framework.execution.worker import ExperimentWorker, read_journal, run
from optimization_framework.storage.artifacts import atomic_json


def problem(x=16, y=8):
    config = {"wavelength_nm": 1050, "target_angle_deg": 75,
        "period_x_nm": 1050 / math.sin(math.radians(75)), "period_y_nm": 525,
        "thickness_nm": 325, "grid_x": x, "grid_y": y,
        "incident_n": 1.45, "exit_n": 1.0, "incident_angle_deg": 0,
        "target_order_x": 1, "target_order_y": 0,
        "silicon_index_source": "FLRL Si_refractive_data.csv",
        "silicon_n_1050": 3.567390909090909, "reference_meent_version": "0.9.5"}
    return Meent2DProblem().resolve(config, {"rcwa_order_x": 1, "rcwa_order_y": 1})


def observed(proposal, instance, value):
    return Observation(id="observation", experiment_id="test", attempt_id="attempt", request_id="request",
        proposal_id=proposal.id, candidate=proposal.candidate, status="ok",
        objectives={instance.primary_objective.name: value}, evaluator_identity=instance.evaluation_identity)


@pytest.mark.parametrize("method", ["motif_surgery", "nested_fourier", "phenotype_de"])
def test_campaign_mask_order_and_checkpoint_replay(method):
    instance = problem()
    optimizer = create(method, instance, {}, seed=37, schedule_steps=20)
    for step in range(4):
        proposal = optimizer.propose()[0]
        mask = np.asarray(proposal.candidate).reshape(8, 16)
        np.testing.assert_array_equal(mask, mask[::-1])
        np.testing.assert_array_equal(np.asarray(optimizer.optimizer.pending.candidate).reshape(16, 8), mask.T)
        optimizer.observe([observed(proposal, instance, float(np.mean(mask[:, :4])) + step / 100)])
    checkpoint = optimizer.checkpoint()
    expected = optimizer.propose()[0]
    resumed = create(method, instance, {}, seed=37, schedule_steps=20)
    resumed.restore(checkpoint)
    assert resumed.propose()[0].model_dump() == expected.model_dump()


@pytest.mark.parametrize("method", ["motif_surgery", "nested_fourier", "phenotype_de"])
def test_full_size_mask_is_accepted_by_meent_adapter(method):
    instance = problem(256, 128)
    optimizer = create(method, instance, {}, seed=8, schedule_steps=1)
    candidate = optimizer.propose()[0].candidate
    assert len(candidate) == 256 * 128
    mask = np.asarray(candidate).reshape(128, 256)
    np.testing.assert_array_equal(mask, mask[::-1])
    assert instance.candidate_schema.canonicalize(candidate) == candidate


@pytest.mark.parametrize("method", ["motif_surgery", "nested_fourier", "phenotype_de"])
def test_real_meent_uses_campaign_mask_order(method):
    pytest.importorskip("meent")
    pytest.importorskip("torch")
    instance = problem()
    optimizer = create(method, instance, {}, seed=7, schedule_steps=1)
    proposal = optimizer.propose()[0]
    result = Meent2DProblem().evaluator(instance).evaluate(proposal.candidate)
    assert result.solver_executions == 2
    assert result.objectives["mean_plus1_transmission"] == pytest.approx(
        (result.objectives["te_plus1_transmission"] + result.objectives["tm_plus1_transmission"]) / 2)
    optimizer.observe([observed(proposal, instance, result.objectives["mean_plus1_transmission"])])
    assert optimizer.inspect()["evaluations"] == 1


@pytest.mark.parametrize("method", ["motif_surgery", "nested_fourier", "phenotype_de"])
def test_campaign_worker_checkpoint_and_physical_costs(tmp_path, method):
    pytest.importorskip("meent")
    pytest.importorskip("torch")
    instance = problem()
    spec = {"id": "mask_library_smoke", "campaign_id": "test", "study_id": "study",
        "seed": 23, "algorithm": method, "algorithm_config": {},
        "problem": instance.model_dump(mode="json"), "max_steps": 3,
        "wall_seconds": 30, "schedule_steps": 3,
        "recovery": {"every_observations": 1}}
    atomic_json(tmp_path / "spec.json", spec)
    result = run(tmp_path)
    assert result["status"] == "completed", result.get("reason")
    records = read_journal(tmp_path / "observations.jsonl")
    assert len(records) == 3
    assert result["solver_calls"] == sum(row["costs"]["solver_executions"] for row in records)
    assert result["best_objective"] == max(row["objectives"]["mean_plus1_transmission"] for row in records)
    restored = ExperimentWorker(tmp_path)
    assert restored.optimizer.inspect()["evaluations"] == 3
