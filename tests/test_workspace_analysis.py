import json

import pytest

from dqn_meent.workspace.analysis import analyze_trials


def trial(name, algorithm="random", seed=0, task_id="dev", status="completed", efficiency=.5, **extra):
    return {"id": name, "algorithm": algorithm, "seed": seed, "task_id": task_id, "status": status,
            "charter_version": 1, "physics": {"n_cells": 8, "fourier_order": 3}, "max_steps": 20,
            "wall_seconds": 30, "progress": {"best_efficiency": efficiency, "solver_calls": 20,
            "elapsed_seconds": 10, "step": 20, "numerical_status": "screening"}, **extra}


def rows(value):
    return [{"solver_calls": 5, "elapsed_seconds": 2, "best_efficiency": value / 2},
            {"solver_calls": 20, "elapsed_seconds": 10, "best_efficiency": value}]


def test_comparisons_pair_task_seed_and_retain_actual_differences():
    trials = [trial("r0", seed=0, efficiency=.2), trial("r1", seed=1, efficiency=.3),
              trial("h0", "hillclimb", seed=0, efficiency=.5), trial("h1", "hillclimb", seed=1, efficiency=.7)]
    report = analyze_trials(trials, {t["id"]: rows(t["progress"]["best_efficiency"]) for t in trials}, [])
    comparison = next(p for p in report["paired_comparisons"] if p["budget_axis"] == "solver_calls")
    assert comparison["n_pairs"] == 2
    assert comparison["budget"] == 20
    # Alphabetical variant ordering is hillclimb minus random.
    assert comparison["difference"]["mean"] == pytest.approx(.35)
    assert comparison["difference"]["ci95"] == pytest.approx([.3, .4])
    assert report["summary"]["solver_calls"] == 80
    assert report["summary"]["execution_seconds"] == 40
    assert report["summary"]["best_converged_efficiency"] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("mismatch", [
    {"physics": {"n_cells": 8, "fourier_order": 8}},
    {"physics": {"n_cells": 8, "fourier_order": 3, "silicon_n": 3.8}},
    {"charter_version": 2}, {"task_split": "test"}, {"task_id": "different"},
])
def test_mismatched_conditions_never_form_pairs(mismatch):
    trials = [trial("r"), trial("h", "hillclimb", **mismatch)]
    report = analyze_trials(trials, {"r": rows(.5), "h": rows(.5)}, [])
    assert len(report["groups"]) == 2
    assert report["paired_comparisons"] == []


def test_stopped_trial_is_censored_and_never_carried_to_full_budget():
    stopped = trial("r", status="stopped", stopped_by="researcher",
                    progress={"best_efficiency": .4, "solver_calls": 5, "elapsed_seconds": 2, "step": 5})
    complete = trial("h", "hillclimb", efficiency=.9)
    metrics = {"r": [{"solver_calls": 5, "elapsed_seconds": 2, "best_efficiency": .4}], "h": rows(.9)}
    report = analyze_trials([stopped, complete], metrics, [])
    assert report["summary"]["censored_trials"] == 1
    comparison = next(p for p in report["paired_comparisons"] if p["budget_axis"] == "solver_calls")
    assert comparison["budget"] == 5
    assert comparison["difference"]["mean"] == pytest.approx(.05)
    assert not any(p["budget_axis"] == "full_allocation" for p in report["paired_comparisons"])
    threshold = next(t for t in report["thresholds"] if t["algorithm_id"].startswith("random:") and t["threshold"] == .5)
    assert threshold["right_censored"] == 1
    assert threshold["cost_among_hits"]["mean"] is None
    assert "not an uncensored" in threshold["interpretation"]


def test_unmatched_seeds_and_duplicate_seed_runs_are_not_cherry_picked():
    trials = [trial("r0", seed=0), trial("r0dup", seed=0, efficiency=.99),
              trial("h0", "hillclimb", seed=0), trial("r1", seed=1), trial("h2", "hillclimb", seed=2)]
    report = analyze_trials(trials, {t["id"]: rows(.5) for t in trials}, [])
    assert report["paired_comparisons"] == []
    assert report["summary"]["n_trials"] == 5
    random = next(a for a in report["groups"][0]["algorithms"] if a["algorithm"] == "random")
    assert random["completed_efficiency"]["ci95"] is None


def test_single_seed_reports_unknown_uncertainty_and_empty_report_has_no_fake_zero():
    report = analyze_trials([trial("r"), trial("h", "hillclimb", efficiency=.7)], {}, [])
    comparison = report["paired_comparisons"][0]
    assert comparison["difference"]["n"] == 1
    assert comparison["difference"]["ci95"] is None
    empty = analyze_trials([], {}, [])
    assert empty["summary"]["best_observed_screening_efficiency"] is None
    assert empty["groups"] == []
    assert empty["paired_comparisons"] == []


def test_hyperparameters_and_dqn_schedule_are_distinct_method_variants():
    trials = [trial("d1", "dqn", schedule_steps=20, training={"seed": 0}),
              trial("d2", "dqn", seed=1, schedule_steps=20, training={"seed": 1}),
              trial("d3", "dqn", schedule_steps=100), trial("r1", algorithm_config={"mutation": 1}),
              trial("r2", algorithm_config={"mutation": 2})]
    report = analyze_trials(trials, {}, [])
    algorithms = report["groups"][0]["algorithms"]
    assert len(algorithms) == 4
    assert sorted(a["n_trials"] for a in algorithms) == [1, 1, 1, 2]


def test_mixed_allocations_not_pooled_as_full_budget_performance():
    trials = [trial("r1"), trial("r2", seed=1, max_steps=100)]
    report = analyze_trials(trials, {}, [])
    algorithm = report["groups"][0]["algorithms"][0]
    assert algorithm["allocation_compatible"] is False
    assert algorithm["completed_efficiency"] is None


def test_physical_validation_is_separate_from_screening_and_reported_from_parent():
    parent = trial("r", efficiency=.95, validation={"validation": [
        {"converged": True, "efficiency": .2}, {"converged": False, "efficiency": .7}]})
    report = analyze_trials([parent], {}, [])
    assert report["summary"]["best_observed_screening_efficiency"] == .95
    assert report["summary"]["best_converged_efficiency"] == .2
    assert report["trials"][0]["validated_design_count"] == 1


def test_campaign_budget_kill_before_allocation_remains_partial():
    t = trial("r", status="budget_exhausted", stopped_by="budget", progress={"step": 4, "solver_calls": 4,
              "elapsed_seconds": 2, "best_efficiency": .1})
    report = analyze_trials([t], {}, [])
    assert report["trials"][0]["full_allocation"] is False
    assert report["trials"][0]["censored"] is True


def test_cross_task_comparison_weights_tasks_equally_not_number_of_seeds():
    trials = [trial("r0", efficiency=.1), trial("h0", "hillclimb", efficiency=.3),
              trial("r1", seed=1, efficiency=.1), trial("h1", "hillclimb", seed=1, efficiency=.3),
              trial("r_other", task_id="other", efficiency=.1), trial("h_other", "hillclimb", task_id="other", efficiency=.7)]
    result = analyze_trials(trials, {}, [])
    aggregate = next(c for c in result["aggregate_comparisons"] if c["budget_axis"] == "solver_calls")
    assert aggregate["n_tasks"] == 2 and aggregate["n_pairs"] == 3
    assert aggregate["mean_difference"] == pytest.approx(.4)
    assert aggregate["ci95"] is not None
