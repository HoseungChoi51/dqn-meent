"""Scientific recipes retain fidelity, artifacts, costs and recovery identity."""
import json

import numpy as np
import pytest

from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.evaluation.recipes import compile_recipe
from optimization_framework.evaluation.registry import problems
from optimization_framework.execution.worker import ExperimentWorker, read_journal, run
from optimization_framework.storage.artifacts import LocalArtifactStore, atomic_json


def prepare(path, recipe_id, parameters):
    instance = problems.resolve("meent_grating", {"n_cells": 4, "fourier_order": 1})
    recipe = compile_recipe(instance, recipe_id, parameters, [[0, 1, 0, 1]])
    spec = {"id": "recipe_test", "campaign_id": "campaign_test", "study_id": "study_test", "seed": 1,
            "algorithm": "recipe", "algorithm_config": {}, "problem": instance.model_dump(mode="json"),
            "recipe": recipe, "max_steps": len(recipe["cases"]), "wall_seconds": 60, "schedule_steps": len(recipe["cases"])}
    atomic_json(path / "spec.json", spec)
    return spec


def test_convergence_uses_common_runner_and_restores_per_fidelity_evaluators(tmp_path):
    prepare(tmp_path, "fourier_convergence:v1", {"orders": [1, 2], "tolerance": .005})
    first = ExperimentWorker(tmp_path)
    first.one_step()
    first.save_checkpoint()
    second = ExperimentWorker(tmp_path)
    result = second.run()
    assert result["status"] == "completed" and result["scientific_complete"]
    assert result["recipe_result"]["kind"] == "solution_fidelity"
    assert result["validation"][0]["complete"]
    observations = read_journal(tmp_path / "observations.jsonl")
    assert [o["fidelity"]["fourier_order"] for o in observations] == [1, 2]
    assert len({o["evaluator_identity"] for o in observations}) == 2
    assert result["evaluations"] == result["solver_calls"] == 2


def test_high_order_support_is_declared_before_costly_execution():
    instance = problems.resolve("meent_grating", {"n_cells": 4, "fourier_order": 1})
    recipe = compile_recipe(instance, "fourier_convergence:v1", {"orders": [320, 480], "tolerance": .001}, [[0, 1, 0, 1]])
    assert [case["problem"]["fidelity"]["fourier_order"] for case in recipe["cases"]] == [320, 480]
    with pytest.raises(ValueError):
        compile_recipe(instance, "fourier_convergence:v1", {"orders": [320, 481], "tolerance": .001}, [[0, 1, 0, 1]])


def test_field_artifact_uses_verified_blob_and_one_solver_charge(tmp_path):
    prepare(tmp_path, "fields:v1", {"nx": 16, "nz_pattern": 4, "air_nm": 100, "glass_nm": 50})
    result = run(tmp_path)
    assert result["status"] == "completed", result
    assert result["solver_calls"] == result["evaluations"] == 1
    observation = read_journal(tmp_path / "observations.jsonl")[0]
    assert observation["fidelity"] == {"fourier_order": 1}
    artifact = observation["metadata"]["artifacts"][0]
    assert artifact["kind"] == "fields"
    store = LocalArtifactStore(tmp_path / "artifacts")
    with store.open(ArtifactReference(**artifact["reference"])) as stream, np.load(stream, allow_pickle=False) as field:
        assert field["ex"].shape[1] == 16
        assert float(field["exit_flux"]) == pytest.approx(float(field["transmittance"]), abs=1e-8)
    outputs = json.loads((tmp_path / "outputs.json").read_text())
    assert [item["kind"] for item in outputs["outputs"]] == ["validation_evidence"]


def test_sensitivity_preserves_distinct_scientific_identities(tmp_path):
    spec = prepare(tmp_path, "sensitivity:v1", {"parameter": "thickness_nm", "values": [300, 325, 350]})
    assert len({case["problem"]["scientific_identity"] for case in spec["recipe"]["cases"]}) == 3
    result = run(tmp_path)
    assert result["status"] == "completed", result
    assert result["recipe_result"]["kind"] == "solution_sensitivity"
    assert result["solver_calls"] == 3
    assert [row["value"] for row in result["recipe_result"]["subjects"][0]["observations"]] == [300, 325, 350]
