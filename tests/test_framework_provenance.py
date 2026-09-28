"""Long-lived studies use actual archived source, portable identities and runtimes."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput, RecipeInput, StudyInput
from optimization_framework.analysis.rules import resolve as resolve_rule
from optimization_framework.evaluation.confirmation import method_definition
from optimization_framework.execution import provenance
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import run


def campaign(directory):
    workspace = Workspace(directory)
    current = workspace.create_campaign(CampaignInput(name="Portable source", validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    return workspace, current["id"], workspace.current_tasks(current["id"])[0]["id"]


def finish(workspace, trial):
    result = run(workspace.job_dir(trial["id"]))
    assert result["scientific_complete"], result
    trial.update(status=result["status"], result=result, progress=result, attempt=1)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return result


def test_recipe_compiles_from_parent_source_after_installed_adapter_changes(tmp_path, monkeypatch):
    workspace, identity, task = campaign(tmp_path)
    parent = workspace.create_trial(TrialInput(campaign_id=identity, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=5))
    finish(workspace, parent)
    from optimization_framework.evaluation.registry import problems
    def wrong_compiler(*_):
        pytest.fail("The current adapter compiler must not reinterpret a frozen procedure")
    monkeypatch.setattr(problems.get("bounded_continuous"), "plan_recipe", wrong_compiler)
    monkeypatch.setattr("optimization_framework.execution.preparation.prepare", wrong_compiler)
    child = workspace.run_recipe(parent["id"], RecipeInput(recipe_id="analytic_fixtures:v1", wall_seconds=5))
    assert child["execution_manifest"] == parent["execution_manifest"]
    assert child["source_hash"] == parent["source_hash"]
    result = finish(workspace, child)
    assert result["recipe_result"]["verdict"] == "passed"
    assert workspace.validations.assess(child["validation_requirement_ids"][0])["measured_pass"]


def test_scientific_identity_excludes_service_changes_and_includes_adapter_code(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    manifest = provenance.capture(original, problem_ids=["bounded_continuous"])
    assert not any(name.startswith(("optimization_framework/api/", "optimization_framework/campaigns/")) for name in manifest["scientific_files"])
    changed = tmp_path / "changed"
    changed.mkdir()
    shutil.copytree(original / "code", changed / "code")
    interface = changed / "code/optimization_framework/api/app.py"
    interface.write_text(interface.read_text() + "\n# A new interface release\n")
    same_science = provenance.capture(changed, problem_ids=["bounded_continuous"])
    assert same_science["scientific_digest"] == manifest["scientific_digest"]
    assert content_hash(same_science) != content_hash(manifest)
    adapter = next(name for name in manifest["scientific_files"] if name.startswith("optimization_benchmarks/") and not name.endswith("__init__.py"))
    path = changed / "code" / adapter
    path.write_text(path.read_text() + "\n# Changed numerical adapter\n")
    assert provenance.capture(changed, problem_ids=["bounded_continuous"])["scientific_digest"] != manifest["scientific_digest"]


def test_archive_resolves_after_directory_move_and_never_substitutes_a_runtime(tmp_path, monkeypatch):
    workspace, _, _ = campaign(tmp_path / "one")
    record = provenance.archive(workspace.store, problem_ids=["bounded_continuous"], purpose="rule")
    identity = record["id"]
    destination = tmp_path / "two"
    shutil.copytree(tmp_path / "one", destination)
    copied = Workspace(destination)
    directory, manifest = provenance.resolve(copied.store, identity)
    assert directory.is_relative_to(destination)
    assert str(tmp_path) not in json.dumps(manifest)
    result = provenance.invoke_archive(copied.store, identity, "rule.evaluate", {
        "binding": {"provider": "framework", "rule_id": "median_objective:v1", "kind": "selection", "parameters": {"seeds": [1]},
            "source_digest": resolve_rule("framework", "median_objective:v1", "selection").digest()},
        "evidence": {"experiments": []}})
    assert result["outcome"] == "inconclusive_selection"
    original = provenance.runtime_manifest
    monkeypatch.setattr(provenance, "runtime_manifest", lambda *args: {**original(*args), "python": "different"})
    with pytest.raises(ValueError, match="compatible frozen execution runtime is unavailable"):
        provenance.resolve(copied.store, identity)


def test_custom_recovery_is_frozen_repeated_by_confirmation_and_checked_by_worker(tmp_path):
    workspace, identity, task = campaign(tmp_path)
    parent = workspace.create_trial(TrialInput(campaign_id=identity, task_id=task, algorithm="coordinate", max_steps=3, wall_seconds=5,
        recovery={"every_observations": 2, "every_seconds": 1, "chunk_bytes": 4096, "max_checkpoint_bytes": 1024**2}))
    study = workspace.create_study(identity, StudyInput(goal="Repeat the entire procedure", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[parent["id"]], seeds=[17]))
    children = workspace.confirmations.schedule(workspace, study["confirmation"]["id"])["created_trial_ids"]
    child = workspace.store.get(children[0], "trial")
    assert child["recovery"] == parent["recovery"]
    assert content_hash(method_definition(child)) == content_hash(method_definition(parent))
    finish(workspace, child)
    changed = deepcopy(child)
    changed["recovery"]["every_observations"] = 200
    (workspace.job_dir(child["id"]) / "spec.json").write_text(json.dumps(changed))
    result = run(workspace.job_dir(child["id"]))
    assert result["status"] == "failed" and "frozen scientific procedure" in result["reason"]


def test_legacy_rule_without_archive_requires_its_original_code(monkeypatch):
    from optimization_framework.analysis.rules import freeze, evaluate, Rule
    binding = freeze({"rule_id": "median_objective:v1"}, "selection", [])
    monkeypatch.setattr(Rule, "digest", lambda _: "changed")
    with pytest.raises(ValueError, match="frozen analysis rule source"):
        evaluate(binding, {"experiments": []})
