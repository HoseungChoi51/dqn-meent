"""Real-MEENT numerical workers, exact continuations, and honest control/results."""
from dataclasses import asdict
import json
import pickle

import numpy as np
import pytest

from dqn_meent.config import PhysicsConfig
from dqn_meent.physics import ForwardSolver
from dqn_meent.workspace.optimizers import ALGORITHMS
from dqn_meent.workspace.worker import _fingerprint, atomic_json, run


def prepare(directory, algorithm="random", max_steps=8, **overrides):
    directory.mkdir(parents=True, exist_ok=True)
    spec = {"id": "trial-example", "campaign_id": "campaign-example", "charter_version": 1,
            "task_id": "task-example", "algorithm": algorithm, "algorithm_config": {},
            "seed": 123, "max_steps": max_steps, "schedule_steps": 16, "wall_seconds": 60,
            "physics": asdict(PhysicsConfig(n_cells=4, fourier_order=1, cache_size=100)),
            "training": {"hidden_sizes": [8], "horizon": 3, "batch_size": 2,
                         "buffer_size": 32, "learning_starts": 2, "target_update_interval": 2},
            "validation_orders": [1, 2, 3], "validation_tolerance": .01, "archive_size": 3}
    spec.update(overrides)
    atomic_json(directory / "spec.json", spec)
    return spec


def metrics(directory):
    return [json.loads(line) for line in (directory / "metrics.jsonl").read_text().splitlines()]


def scientific_rows(directory):
    keys = ("step", "efficiency", "best_efficiency", "best_design", "solver_calls", "cache_hits",
            "evaluations", "diagnostics")
    return [{key: row[key] for key in keys} for row in metrics(directory)]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_every_optimizer_runs_real_meent_and_resumes_exactly(tmp_path, algorithm):
    options = {"algorithm_config": {"warmup": 3, "candidate_pool": 12}} if algorithm == "surrogate" else {}
    if algorithm == "population":
        options = {"algorithm_config": {"population_size": 3}}
    full, resumed = tmp_path / "full", tmp_path / "resumed"
    prepare(full, algorithm, max_steps=12, **options)
    expected = run(full)
    assert expected["status"] == "completed", expected.get("reason")
    assert expected["step"] == expected["evaluations"] == 12
    assert expected["solver_calls"] + expected["cache_hits"] == expected["evaluations"]
    assert np.isfinite(expected["best_efficiency"])
    assert 0 <= expected["best_efficiency"] <= 1

    prepare(resumed, algorithm, max_steps=5, **options)
    partial = run(resumed)
    assert partial["status"] == "completed"
    atomic_json(resumed / "control.json", {"command": "run", "max_steps": 12, "revision": 1})
    continued = run(resumed)
    assert continued["status"] == "completed", continued.get("reason")
    assert continued["step"] == 12
    assert continued["elapsed_seconds"] >= partial["elapsed_seconds"]
    assert scientific_rows(full) == scientific_rows(resumed)
    assert continued["best_design"] == expected["best_design"]
    assert continued["archive"] == expected["archive"]
    assert continued["schedule_steps"] == 16
    assert continued["resume_supported"]
    if algorithm == "dqn":
        assert continued["diagnostics"]["schedule_steps"] == 16
        assert continued["diagnostics"]["updates"] > 0
    if algorithm == "surrogate":
        assert continued["diagnostics"]["guided_proposals"] > 0


def test_pause_is_cooperative_and_resume_keeps_observations(tmp_path):
    directory = tmp_path / "pause"
    prepare(directory)

    class PausingSolver(ForwardSolver):
        def evaluate(self, design):
            result = super().evaluate(design)
            if self.evaluations == 3:
                atomic_json(directory / "control.json", {"command": "pause", "revision": 1})
            return result

    paused = run(directory, solver_factory=PausingSolver)
    assert paused["status"] == "paused"
    assert paused["reason"] == "researcher_pause"
    assert paused["step"] == paused["evaluations"] == 3
    assert paused["checkpoint_available"]
    assert len(metrics(directory)) == 3
    atomic_json(directory / "control.json", {"command": "run", "revision": 2})
    completed = run(directory)
    assert completed["step"] == 8
    assert completed["status"] == "completed"
    assert len(metrics(directory)) == 8


def test_stop_before_first_evaluation_spends_no_solver_calls(tmp_path):
    prepare(tmp_path)
    atomic_json(tmp_path / "control.json", {"command": "stop", "revision": 1})
    stopped = run(tmp_path)
    assert stopped["status"] == "stopped"
    assert stopped["evaluations"] == stopped["solver_calls"] == stopped["step"] == 0
    assert stopped["best_efficiency"] is None
    assert stopped["checkpoint_available"]
    # Re-launching with an unchanged stop command cannot silently restart it.
    assert run(tmp_path)["status"] == "stopped"


def test_stale_control_cannot_override_durable_researcher_stop(tmp_path):
    prepare(tmp_path)
    atomic_json(tmp_path / "control.json", {"command": "stop", "revision": 3})
    assert run(tmp_path)["status"] == "stopped"
    atomic_json(tmp_path / "control.json", {"command": "run", "revision": 2})
    stopped = run(tmp_path)
    assert stopped["status"] == "stopped"
    assert stopped["solver_calls"] == 0
    atomic_json(tmp_path / "control.json", {"command": "run", "revision": 3})
    assert run(tmp_path)["status"] == "stopped"
    atomic_json(tmp_path / "control.json", {"command": "run", "revision": 4})
    assert run(tmp_path)["status"] == "completed"


def test_wall_budget_is_checked_before_new_solver_work(tmp_path):
    prepare(tmp_path, wall_seconds=1e-9)
    completed = run(tmp_path)
    assert completed["status"] == "completed"
    assert completed["reason"] == "wall_budget_exhausted"
    assert completed["solver_calls"] == 0


def test_validation_uses_requested_real_orders_and_reports_convergence(tmp_path):
    designs = [[1, 0, 1, 0], [1, 1, 0, 0]]
    spec = prepare(tmp_path, algorithm="validate", max_steps=8,
                   algorithm_config={"designs": [{"design": design} for design in designs]})
    result = run(tmp_path)
    assert result["status"] == "completed", result.get("reason")
    assert result["reason"] == "validation_complete"
    assert result["evaluations"] == 6
    assert result["solver_calls"] == 6
    assert len(result["validation"]) == 2
    for design, validation in zip(designs, result["validation"], strict=True):
        assert validation["complete"]
        assert [observation["fourier_order"] for observation in validation["observations"]] == [1, 2, 3]
        physics = dict(spec["physics"], fourier_order=3)
        independently_computed = ForwardSolver(PhysicsConfig(**physics)).evaluate(design)
        assert validation["efficiency"] == independently_computed.efficiency
        assert validation["converged"] == (validation["delta"] <= .01)


def test_partial_validation_does_not_claim_convergence_and_resumes(tmp_path):
    prepare(tmp_path, algorithm="validate", max_steps=1, algorithm_config={"designs": [[1, 0, 0, 1]]})
    partial = run(tmp_path)
    assert partial["step"] == 1
    assert not partial["validation"][0]["converged"]
    assert partial["validation"][0]["numerical_status"] == "insufficient_orders"
    atomic_json(tmp_path / "control.json", {"command": "run", "max_steps": 3, "revision": 1})
    completed = run(tmp_path)
    assert completed["validation"][0]["complete"]
    assert completed["evaluations"] == 3


def test_resume_refuses_changed_physics_without_losing_costs(tmp_path):
    spec = prepare(tmp_path, max_steps=3)
    before = run(tmp_path)
    spec["physics"]["wavelength_nm"] = 1050.
    atomic_json(tmp_path / "spec.json", spec)
    after = run(tmp_path)
    assert after["status"] == "failed"
    assert "incompatible" in after["reason"]
    assert after["solver_calls"] == before["solver_calls"]
    assert not after["resume_supported"]


def test_numerical_failure_is_visible_and_accounted(tmp_path):
    prepare(tmp_path)

    class BrokenSolver(ForwardSolver):
        def evaluate(self, design):
            result = super().evaluate(design)
            if self.evaluations == 3:
                raise FloatingPointError("Injected numerical failure")
            return result

    result = run(tmp_path, solver_factory=BrokenSolver)
    assert result["status"] == "failed"
    assert result["step"] == 2
    assert result["evaluations"] == 3
    assert result["solver_calls"] + result["cache_hits"] == 3
    assert result["best_efficiency"] is not None
    assert "Injected numerical failure" in result["reason"]
    assert not result["resume_supported"]
    assert (tmp_path / "error.txt").exists()
    repeated = run(tmp_path)
    assert repeated["status"] == "failed"
    assert repeated["evaluations"] == 3
    assert "create a new trial" in repeated["reason"]


def test_forced_interruption_is_explicit_and_counts_against_budget(tmp_path):
    spec = prepare(tmp_path, max_steps=3)
    run(tmp_path)
    atomic_json(tmp_path / "inflight.json", {"request_id": "simulated-forced-kill", "spec_hash": _fingerprint(spec),
                                              "step": 4, "solver_call": True})
    atomic_json(tmp_path / "control.json", {"command": "run", "max_steps": 8, "revision": 1})
    recovered = run(tmp_path)
    assert recovered["status"] == "completed", recovered["reason"]
    assert recovered["step"] == recovered["evaluations"] == 7
    assert recovered["interrupted_requests"] == recovered["unconfirmed_solver_calls"] == 1
    assert recovered["budget_requests"] == 8
    assert not (tmp_path / "inflight.json").exists()
    repeated = run(tmp_path)
    assert repeated["interrupted_requests"] == 1
    assert repeated["evaluations"] == 7


def test_metrics_recovery_discards_uncheckpointed_suffix_and_repairs_last_row(tmp_path):
    prepare(tmp_path, max_steps=3)
    run(tmp_path)
    lines = (tmp_path / "metrics.jsonl").read_text().splitlines()
    (tmp_path / "metrics.jsonl").write_text("\n".join(lines[:2]) + '\n{"step":99}\ninvalid\n')
    atomic_json(tmp_path / "control.json", {"command": "run", "max_steps": 4, "revision": 1})
    result = run(tmp_path)
    assert result["step"] == 4
    assert [item["step"] for item in metrics(tmp_path)] == [1, 2, 3, 4]


def test_archive_contains_distinct_binary_designs(tmp_path):
    prepare(tmp_path, max_steps=30, archive_size=4)
    result = run(tmp_path)
    designs = [tuple(item["design"]) for item in result["archive"]]
    assert len(designs) == len(set(designs)) == 4
    assert all(set(design) <= {0, 1} for design in designs)
    assert [item["efficiency"] for item in result["archive"]] == sorted(
        [item["efficiency"] for item in result["archive"]], reverse=True)


def test_checkpoint_holds_solver_cache_and_schedule(tmp_path):
    prepare(tmp_path, algorithm="dqn", max_steps=4)
    run(tmp_path)
    with (tmp_path / "checkpoint.pkl").open("rb") as stream:
        state = pickle.load(stream)
    assert state["optimizer"]["training"].total_steps == 16
    assert state["solvers"][1]["evaluations"] == 4
    assert len(state["solvers"][1]["cache"]) > 0
