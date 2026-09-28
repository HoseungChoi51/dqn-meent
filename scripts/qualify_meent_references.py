"""Reevaluate all seven preserved references in a bounded, separately named study.

The common scheduler owns all work. This verifies the reference-input workflow
and numerical fidelity; it cannot produce a production DQN replication claim.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from dqn_meent.study_templates import production
from optimization_framework.assets.references import registered
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import now


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def definition():
    original = production()
    references = [group for group in original["groups"] if group["scope"] == "reference"]
    methods = {slot: deepcopy(original["methods"][slot]) for group in references for slot in group["slots"]}
    methods.update({"P": {"procedure": {"algorithm": "hillclimb", "max_steps": 2, "wall_seconds": 10}},
        "HC": {"procedure": {"algorithm": "random", "max_steps": 2, "wall_seconds": 10}},
        "selected": {"select_from": ["P"]}})
    check = deepcopy(original["validation_policies"]["reference"])
    check["validation_wall_seconds"] = 45
    return {"id": "meent-reference-fidelity-qualification:v1", "name": "Seven-reference fidelity qualification",
        "goal": "Qualify explicit reference inputs, recorded checks and preserved numerical results", "qualification_only": True,
        "input_requirements": original["input_requirements"], "methods": methods,
        "groups": [*references,
            {"id": "development", "scope": "development", "slots": ["P"], "seeds": [10], "priority": 60},
            {"id": "controls", "scope": "confirmation", "slots": ["HC"], "seeds": [100], "priority": 40},
            {"id": "confirmation", "scope": "confirmation", "slots": ["selected"], "seeds": [100], "priority": 80, "admission": "nomination"}],
        "selection": {"rule_id": "median_objective:v1", "parameters": {"seeds": [10]}},
        "analysis": {"rule_id": "paired_improvement:v1", "parameters": {"minimum_wins": 1}},
        "development_seconds": 120, "total_seconds": 300, "worker_seconds": 600, "max_workers": 2,
        "validation_policies": {"reference": check}, "validation_priority": 95}


def qualify(directory, sibling):
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a fresh isolated directory; inspect an existing run instead of replacing its evidence")
    manifest = registered("meent_grating")[0]
    original_report = sibling / "runs/replication-20260920-1100nm-70deg/reference-results.json"
    historical = {row["label"]: row for row in json.loads(original_report.read_text())}
    sources = []
    for solution in manifest.solutions:
        for source in solution.sources:
            path = sibling / source.path
            if checksum(path) != source.sha256:
                raise ValueError(f"Historical source changed: {source.path}")
            if path.suffix == ".npy" and np.load(path, allow_pickle=False).tolist() != solution.candidate:
                raise ValueError(f"Captured candidate differs from original array: {source.path}")
            sources.append(source.model_dump(mode="json"))
    workspace = Workspace(directory, max_workers=2)
    destination = directory / "qualification-report.json"
    report = {"scope": "Seven preserved candidates through a separately named software-qualification study",
        "status": "preparing", "created_at": now(), "model_calls": 0, "manifest_digest": manifest.digest(),
        "historical_sources": sources, "historical_report_sha256": checksum(original_report),
        "comparison_tolerance": 1e-8, "historical_cost": "unknown", "historical_exposure": "partial"}
    atomic_json(destination, report)
    campaign = workspace.create_campaign(CampaignInput(name="Seven-reference fidelity qualification", compute_budget_seconds=600,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Preserved 1100 nm / 70 degree condition", configuration=manifest.configuration)]))
    imported = workspace.commands.execute(Command(id="import_references", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="asset.import_reference_set", payload={"provider": "meent_grating", "reference_set_id": manifest.id,
            "manifest_digest": manifest.digest()}))["outcome"]
    execution = workspace.study_executions.freeze(campaign["id"], {"template": definition(),
        "task_ids": [workspace.current_tasks(campaign["id"])[0]["id"]], "asset_bindings": imported["asset_bindings"]})
    design = workspace.store.get(execution["design_id"], "confirmation_design")
    report.update(campaign_id=campaign["id"], execution_id=execution["id"], protocol_id=execution["protocol_id"],
        design=design, source=workspace.store.get(design["source_id"], "execution_source"), status="running")
    atomic_json(destination, report)
    workspace.study_executions.activate(execution["id"])
    workspace.start()
    try:
        observation_deadline = time.monotonic()+320
        while time.monotonic() < observation_deadline:
            current = workspace.store.get(execution["id"], "study_execution")
            if current["status"] in {"complete", "incomplete", "closed"}:
                break
            time.sleep(.25)
        else:
            raise TimeoutError(f"Observation timed out for {execution['id']}; inspect its existing attempts before any retry")
        state = workspace.study_executions.assess(execution["id"])
        report["execution_status"] = current["status"]
        report["manager_issues"] = workspace.store.list("manager_issue", campaign["id"])
        if not state["complete"]:
            report["assessment"] = state
            raise ValueError("The declared software qualification did not produce complete evidence")
        release = workspace.confirmations.release(execution["protocol_id"], authority="software_qualification")
        report["release_id"] = release["id"]
        report["confirmation_report"] = workspace.store.get(release["report_id"], "confirmation_report")
        measurements = []
        for solution in manifest.solutions:
            cell = next(row for row in state["cells"] if row["slot_id"] == solution.slot)
            trial = workspace.store.get(cell["trial_id"], "trial")
            if trial["result"]["evaluations"] != 1 or trial["result"]["best_candidate"] != solution.candidate:
                raise ValueError("Reference evaluation changed the candidate or repeated the frozen request")
            check = next(item for item in cell["evidence"]["validation"] if item["recipe_id"] == "fourier_convergence:v1")
            observed = {row["fourier_order"]: row["efficiency"] for row in check["results"][-1]["measurements"]["observations"]}
            expected = {row["order"]: row["efficiency"] for row in historical[solution.title]["results"]}
            differences = {order: abs(observed[order]-expected[order]) for order in (40, 160, 320, 480)}
            measurements.append({"slot": solution.slot, "reference_role": solution.role, "trial_id": trial["id"],
                "asset_id": imported["asset_bindings"][solution.slot], "candidate_digest": solution.candidate_digest,
                "observed": observed, "historical": expected, "absolute_differences": differences,
                "measured_pass": check["measured_pass"], "full_attributed_cost": cell["evidence"]["full_cost"],
                "matches_historical": all(value <= report["comparison_tolerance"] for value in differences.values())})
        report.update(measurements=measurements, actual_costs=workspace.assets.actual_costs(campaign["id"]),
            status="passed" if all(row["measured_pass"] and row["matches_historical"] for row in measurements) else "numerical_mismatch")
        return report
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_at"] = now()
        atomic_json(destination, report)
        workspace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--sibling", type=Path, required=True)
    arguments = parser.parse_args()
    report = qualify(arguments.directory.resolve(), arguments.sibling.resolve())
    print(json.dumps({"status": report["status"], "execution_id": report["execution_id"],
        "references": len(report["measurements"]), "maximum_difference": max(value for row in report["measurements"] for value in row["absolute_differences"].values()),
        "actual_costs": report["actual_costs"]}, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 1)
