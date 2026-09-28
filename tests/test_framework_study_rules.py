"""Development selection and confirmation consume frozen, measured evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from optimization_framework.analysis.studies import nominate, selection_assessment
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput, StudyInput, TrialInput, RecipeInput
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run


def finish(workspace, identity):
    trial = workspace.store.get(identity, "trial")
    result = run(workspace.job_dir(identity))
    assert result["scientific_complete"], result
    trial.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return workspace.store.get(identity, "trial")


def prepare(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Select then confirm", compute_budget_seconds=500, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Known quadratic", problem_id="bounded_continuous")]))
    study = workspace.create_study(campaign["id"], StudyInput(goal="Select by declared fresh development seeds",
        selection={"rule_id": "median_objective:v1", "parameters": {"seeds": [10, 11]}},
        validation_policy={"required_recipes": ["analytic_fixtures:v1"], "validation_wall_seconds": 5}))
    return workspace, campaign["id"], workspace.current_tasks(campaign["id"])[0], study


def develop(workspace, campaign_id, task):
    trials = []
    for algorithm in ("coordinate", "random"):
        for seed in (10, 11):
            trial = workspace.create_trial(TrialInput(campaign_id=campaign_id, task_id=task["id"], algorithm=algorithm,
                seed=seed, max_steps=6, wall_seconds=5))
            trials.append(finish(workspace, trial["id"]))
            check = workspace.run_recipe(trial["id"], RecipeInput(recipe_id="analytic_fixtures:v1", wall_seconds=5))
            finish(workspace, check["id"])
    return trials


def test_nomination_drives_real_confirmation_and_preserves_evidence_after_reassessment(tmp_path):
    workspace, campaign_id, task, development = prepare(tmp_path)
    before = selection_assessment(workspace.store, development["id"])
    trials = develop(workspace, campaign_id, task)
    with pytest.raises(ValueError, match="Development evidence changed"):
        nominate(workspace, development["id"], expected_evidence_hash=before["evidence_hash"])
    assert not workspace.store.list("nomination")
    preview = selection_assessment(workspace.store, development["id"])
    command = Command(id="select_once", campaign_id=campaign_id, operation="study.nominate",
        expected_revision=workspace.store.get(campaign_id)["version"], payload={"study_id": development["id"], "expected_evidence_hash": preview["evidence_hash"]})
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: workspace.commands.execute(command), range(2)))
    assert outcomes[0] == outcomes[1]
    nomination = workspace.store.get(outcomes[0]["outcome"]["nomination_id"], "nomination")
    assert nomination["evidence_hash"] == content_hash(nomination["evidence"])
    assert len(nomination["evidence"]["experiments"]) == 4
    for row in nomination["evidence"]["experiments"]:
        checks = [trial for trial in workspace.store.list("trial") if trial.get("parent_trial_id") == row["trial_id"]]
        assert row["full_cost"]["quantities"]["evaluation_requests"]["total"] == row["result"]["evaluations"] + sum(check["result"]["evaluations"] for check in checks)
    by_algorithm = {algorithm: [trial["result"]["best_objective"] for trial in trials if trial["algorithm"] == algorithm]
                    for algorithm in ("coordinate", "random")}
    winner = min(by_algorithm, key=lambda algorithm: sum(by_algorithm[algorithm]) / 2)
    assert next(iter(nomination["methods"].values()))["algorithm"] == winner
    control = next(trial for trial in trials if trial["algorithm"] != winner)
    # Further development is separate evidence and cannot silently select again.
    later = workspace.create_trial(TrialInput(campaign_id=campaign_id, task_id=task["id"], algorithm="coordinate",
        algorithm_config={"radius": .01}, seed=12, max_steps=3, wall_seconds=5))
    finish(workspace, later["id"])
    assert nominate(workspace, development["id"]) == nomination
    study = workspace.create_study(campaign_id, StudyInput(goal="Confirm selected procedure against its control", scope="confirmation",
        confirmation_kind="seed_replication", nomination_id=nomination["id"], prototype_trial_ids=[control["id"]], seeds=[100, 101],
        analysis={"rule_id": "paired_improvement:v1", "parameters": {"minimum_wins": 1}},
        validation_policy={"required_recipes": ["analytic_fixtures:v1"], "validation_wall_seconds": 5}))
    protocol_id = study["confirmation"]["id"]
    allocated = workspace.confirmations.schedule(workspace, protocol_id)["created_trial_ids"]
    assert len(allocated) == 4
    for identity in allocated:
        finish(workspace, identity)
    for identity in workspace.confirmations.schedule_checks(workspace, protocol_id)["created_trial_ids"]:
        finish(workspace, identity)
    release = workspace.confirmations.release(protocol_id)
    report = workspace.store.get(release["report_id"], "confirmation_report")
    assert report["claim_level"] == "protocol_conclusion"
    evidence = report["evidence"]["analysis_evidence"]
    assert evidence["nomination"]["id"] == nomination["id"]
    assert evidence["nomination_review"]["supported"]
    cells = evidence["experiments"]
    expected_wins = sum(next(row["result"]["best_objective"] for row in cells if row["seed"] == seed and row["method"]["algorithm"] == winner)
        < next(row["result"]["best_objective"] for row in cells if row["seed"] == seed and row["method"]["algorithm"] != winner) for seed in (100, 101))
    assert report["conclusion"]["winning_cells"] == expected_wins
    assert report["outcome"] == ("declared_improvement_criterion_met" if expected_wins else "no_demonstrated_improvement")
    workspace.confirmations.reconcile_reports()
    assert len(workspace.store.list("confirmation_report")) == 1
    # A countercheck on the development evidence invalidates support without
    # rewriting either the original nomination or the released result.
    from optimization_framework.contracts.validation import ValidationResult
    chosen = nomination["prototypes"][nomination["selected_method_ids"][0]]
    requirement = next(row for row in workspace.store.list("validation_requirement") if row["scope"]["parent_trial_id"] == chosen)
    previous = workspace.validations.assess(requirement["id"])["results"][-1]
    workspace.validations.record_result(ValidationResult(**{**{key: value for key, value in previous.items() if key != "content_hash"},
        "id": "independent_countercheck", "verdict": "failed", "rationale": "New evaluator evidence invalidates the development measurement"}))
    workspace.confirmations.reconcile_reports()
    reports = workspace.store.list("confirmation_report")
    assert len(reports) == 2
    assert reports[-1]["outcome"] == "inconclusive_execution"
    assert not reports[-1]["evidence"]["analysis_evidence"]["nomination_review"]["supported"]
    assert workspace.store.get(report["id"]) == report
    assert workspace.store.get(nomination["id"]) == nomination
    assert workspace.confirmations.release(protocol_id) == release


def test_missing_validation_duplicate_seed_and_wrong_domain_do_not_select(tmp_path):
    workspace, campaign_id, task, study = prepare(tmp_path)
    trial = workspace.create_trial(TrialInput(campaign_id=campaign_id, task_id=task["id"], algorithm="coordinate", seed=10, max_steps=2, wall_seconds=5))
    finish(workspace, trial["id"])
    with pytest.raises(ValueError, match="No method"):
        nominate(workspace, study["id"])
    duplicate = workspace.create_trial(TrialInput(campaign_id=campaign_id, task_id=task["id"], algorithm="coordinate", seed=10, max_steps=2, wall_seconds=5))
    finish(workspace, duplicate["id"])
    with pytest.raises(ValueError, match="duplicate"):
        nominate(workspace, study["id"])
    assert not workspace.store.list("nomination")
    with pytest.raises(ValueError, match="does not support"):
        workspace.create_study(campaign_id, StudyInput(goal="Wrong domain", selection={"provider": "meent_grating", "rule_id": "replication_selection:v1"}))


def test_changed_installed_rule_uses_frozen_source_and_damaged_archive_is_rejected(tmp_path, monkeypatch):
    workspace, campaign_id, task, study = prepare(tmp_path)
    develop(workspace, campaign_id, task)
    from optimization_framework.analysis.rules import Rule
    monkeypatch.setattr(Rule, "digest", lambda self: "changed-installed-source")
    selected = nominate(workspace, study["id"])
    assert selected["selected_method_ids"]
    source = workspace.directory / "sources" / study["selection"]["execution_source_id"]
    changed = source / "code/optimization_framework/analysis/standard_rules.py"
    changed.write_text(changed.read_text() + "\n# Damaged archived rule\n")
    with pytest.raises(ValueError, match="source archive is unavailable or changed"):
        selection_assessment(workspace.store, study["id"])
    assert workspace.store.get(selected["id"]) == selected


def test_terminal_diagnostic_waits_for_atomic_evidence_publication(tmp_path, monkeypatch):
    from optimization_framework.evaluation.diagnostics import reconcile, assessment
    from optimization_framework.evaluation import jobs
    workspace, campaign_id, task, _ = prepare(tmp_path)
    trial = workspace.create_trial(TrialInput(campaign_id=campaign_id, task_id=task["id"], algorithm="coordinate", max_steps=2, wall_seconds=5,
        diagnostics=[{"at_counts": [1], "export_optimizer": False, "recipes": [{"recipe_id": "analytic_fixtures:v1", "wall_seconds": 5}]}]))
    finish(workspace, trial["id"])
    reconcile(workspace)
    child = next(row for row in workspace.store.list("trial") if row.get("diagnostic_grant_id"))
    result = run(workspace.job_dir(child["id"]))
    child.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", child)
    assert not assessment(workspace.store, trial)["complete"]
    original = jobs.finish
    def interrupted(*args):
        original(*args)
        raise ValueError("Interrupted publication after validation")
    monkeypatch.setattr(jobs, "finish", interrupted)
    with pytest.raises(ValueError, match="Interrupted publication"):
        workspace.capture_evidence(child)
    assert not workspace.store.list("validation_result")
    assert "asset_capture_attempt" not in workspace.store.get(child["id"])
    assert not assessment(workspace.store, trial)["complete"]
    monkeypatch.setattr(jobs, "finish", original)
    workspace.capture_evidence(workspace.store.get(child["id"]))
    assert assessment(workspace.store, trial)["complete"]
    assert workspace.validations.assess(child["validation_requirement_ids"][0])["measured_pass"]


def test_meent_selection_and_verdict_match_preserved_sibling_fixtures():
    from dqn_meent.replication import select_profile, decide
    fixture = json.loads((Path(__file__).parent / "fixtures/consolidation-rule-reference.json").read_text())
    for case in fixture["selection"]:
        assert select_profile(case["rows"]) == case["expected"], case["name"]
    for case in fixture["verdict"]:
        assert decide(case["rows"], case["selected"], case["references"]) == case["expected"], case["name"]
