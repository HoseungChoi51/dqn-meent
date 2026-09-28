"""Bounded fresh fidelity evidence through the common scheduler and worker.

This evaluates one preserved paper design. It is neither a production replication
campaign nor evidence that a newly developed optimizer is superior. Historical
input cost stays unknown; newly performed solver work is measured separately.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from dqn_meent.replication import profile_config
from optimization_framework.contracts.assets import Asset, ReuseDecision
from optimization_framework.contracts.requests import CampaignInput, RecipeInput, TaskInput, TrialInput
from optimization_framework.contracts.resources import ExecutionGrant
from optimization_framework.execution.service import Workspace
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import now


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_for_evidence(workspace, trial_id, deadline):
    while time.monotonic() < deadline:
        trial = workspace.store.get(trial_id, "trial")
        if trial["status"] not in {"queued", "running", "pausing", "stopping"}:
            workspace.capture_evidence(trial)
            return workspace.store.get(trial_id, "trial")
        time.sleep(.2)
    raise TimeoutError(f"Observation timed out for {trial_id}; inspect this existing attempt before any retry")


def qualify(directory, sibling):
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a fresh isolated directory; existing qualification evidence must be inspected rather than replaced")
    supplement_path = sibling / "data/paper_2022_supplement.json"
    report_path = sibling / "runs/replication-20260920-1100nm-70deg/reference-results.json"
    case = next(row for row in json.loads(supplement_path.read_text())["conditions"]
                if row["wavelength_nm"] == 1100 and row["angle_deg"] == 70)
    candidate = [int(value) for value in "".join(case["design_rows"])]
    historical = next(row for row in json.loads(report_path.read_text()) if row["method"] == "paper")
    original_input = sibling / "runs/replication-20260920-1100nm-70deg/references/input/paper.npy"
    if np.load(original_input, allow_pickle=False).tolist() != candidate:
        raise ValueError("Historical numerical reference used a different candidate")
    workspace = Workspace(directory, max_workers=1)
    report = {"scope": "Fresh MEENT fidelity software qualification on one historical paper design",
        "created_at": now(), "model_calls": 0, "historical_cost": "unknown",
        "reference_sources": [{"path": str(path), "sha256": digest(path)} for path in (supplement_path, report_path, original_input)],
        "candidate": candidate, "orders": [40, 160, 320, 480], "comparison_tolerance": 1e-8,
        "scientific_tolerance": 1e-4, "status": "preparing"}
    destination = directory / "qualification-report.json"
    atomic_json(destination, report)
    campaign = workspace.create_campaign(CampaignInput(name="Bounded high-fidelity qualification", compute_budget_seconds=180,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Paper design at 1100 nm / 70 degrees",
            configuration=asdict(profile_config("P").physics))]))
    task = workspace.current_tasks(campaign["id"])[0]
    asset = workspace.assets.publish(Asset(id="paper_reference", campaign_id=campaign["id"], kind="solution",
        title="Preserved paper Table S3 candidate", payload={"candidate": candidate, "problem": task["problem"],
            "historical_sources": report["reference_sources"]}, cost_provenance="unknown", exposure_status="known",
        exposed_instance_ids=[task["problem"]["scientific_identity"]], authority="historical_reference", created_at=now()))
    reuse = workspace.assets.decide(ReuseDecision(id="reference_input", campaign_id=campaign["id"],
        study_id=campaign["active_study_id"], asset_id=asset["id"], decision="reuse", intended_use="optimizer_input",
        rationale="Evaluate exactly this historical candidate once, then run the frozen fidelity recipe; no search-improvement claim",
        authority="software_qualification", created_at=now()))
    started = time.time()
    grant = workspace.resources.create(ExecutionGrant(id="fidelity_qualification", campaign_id=campaign["id"],
        owner_id="bounded_fidelity_check", starts_at=started, deadline_at=started+180, worker_seconds=180,
        max_workers=1, authority="software_qualification"))
    parent = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="refinement",
        seed=0, initial_assets=[asset["id"]], reuse_decision_ids=[reuse["id"]], max_steps=1, wall_seconds=15,
        question="Publish a measured snapshot of the supplied reference without performing a refinement move"),
        execution={"execution_grant_id": grant["id"]})
    report.update(campaign_id=campaign["id"], parent_trial_id=parent["id"], execution_grant_id=grant["id"],
        source_hash=parent["source_hash"], execution_manifest=parent["execution_manifest"], status="running")
    atomic_json(destination, report)
    workspace.start()
    try:
        observation_deadline = time.monotonic()+190
        parent = wait_for_evidence(workspace, parent["id"], observation_deadline)
        if parent["status"] != "completed" or parent["result"].get("best_candidate") != candidate:
            raise ValueError("The reference snapshot did not complete with the declared candidate")
        check = workspace.run_recipe(parent["id"], RecipeInput(recipe_id="fourier_convergence:v1",
            parameters={"orders": report["orders"], "tolerance": report["scientific_tolerance"]}, wall_seconds=120, subject_limit=1))
        report["validation_trial_id"] = check["id"]
        atomic_json(destination, report)
        check = wait_for_evidence(workspace, check["id"], observation_deadline)
        report["validation_status"] = check["status"]
        report["validation_result"] = check.get("result")
        if check["status"] != "completed" or not check["result"].get("scientific_complete"):
            raise ValueError("The declared fidelity evaluations did not all complete")
        subject = check["result"]["recipe_result"]["subjects"][0]
        measured = {row["fourier_order"]: row["efficiency"] for row in subject["observations"]}
        expected = {row["order"]: row["efficiency"] for row in historical["results"]}
        differences = {order: abs(measured[order]-expected[order]) for order in report["orders"]}
        report.update(measured_efficiency=measured, historical_efficiency=expected, absolute_differences=differences,
            matches_historical=all(value <= report["comparison_tolerance"] for value in differences.values()),
            actual_costs=workspace.assets.actual_costs(campaign["id"]),
            attributed_costs=workspace.assets.attributed_costs(check["latest_output_asset_ids"]),
            validation_records=workspace.store.list("validation_result", campaign["id"]))
        report["status"] = "passed" if report["matches_historical"] else "numerical_mismatch"
        workspace.resources.release(grant["id"], rationale="Bounded qualification finished; no outcome campaign was launched")
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
    result = qualify(arguments.directory.resolve(), arguments.sibling.resolve())
    print(json.dumps({key: result[key] for key in ("status", "campaign_id", "parent_trial_id", "validation_trial_id", "absolute_differences")}, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)
