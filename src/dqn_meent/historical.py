"""Inspectable retrospective import of standalone MEENT output directories.

Original files are hashed and archived. A historical solution becomes input only
through a separate researcher reuse command and a newly bounded evaluation.
"""
from dataclasses import asdict
import hashlib
from pathlib import Path

import numpy as np

from optimization_framework.cli import Session
from optimization_framework.contracts.assets import Asset
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.bundles import BundleBlob, BundleEdge, BundleRecord, CapturedFiles, EvidenceBundle, RecordKey
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.evaluation.registry import problems
from optimization_framework.storage.artifacts import LocalArtifactStore, atomic_json
from optimization_framework.storage.bundles import write
from optimization_framework.storage.sqlite import read_json


def inspect_run(directory, config):
    directory = Path(directory).resolve()
    names = {"config.json", "spec.json", "summary.json", "metrics.csv", "metrics.jsonl", "result.json", "progress.json",
        "requests.jsonl", "observations.jsonl", "costs.jsonl", "attempts.jsonl", "best_design.npy", "best_design.json",
        "checkpoint.pt", "outputs.json", "execution-manifest.json", "evaluation.json", "policy_best_design.npy", "archive.json"}
    roots = {"code", "runtime-locks", "artifacts", "checkpoints", "outputs", "inputs"}
    files = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if relative.as_posix() not in names and relative.parts[0] not in roots:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError("Historical captures cannot contain symbolic links")
        if path.is_file():
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            files[relative.as_posix()] = {"sha256": checksum, "bytes": path.stat().st_size}
    if not {"config.json", "best_design.npy"} <= set(files):
        raise ValueError("Historical evaluation needs the original config.json and best_design.npy")
    problem = problems.resolve("meent_grating", asdict(config.physics))
    candidate = problem.candidate_schema.canonicalize(np.load(directory / "best_design.npy", allow_pickle=False).tolist())
    identity = "historical_" + content_hash(files)
    return {"format": "standalone-meent-import-v1", "source_id": identity, "files": files,
        "problem": problem.model_dump(mode="json"), "candidate": candidate,
        "scientific_status": "retrospective", "cost_provenance": "unknown", "exposure_status": "unknown",
        "limitations": ["Captured records describe past work; they do not preregister a study.",
                       "Reported historical costs remain in the original files; complete upstream costs and exposure are unknown.",
                       "Import does not resume a historical checkpoint or approve optimizer-input reuse."]}


def capture_run(directory, plan, destination):
    directory = Path(directory)
    blobs, paths = {}, {}
    for name, item in plan["files"].items():
        checksum = item["sha256"]
        blobs[checksum] = BundleBlob(**item)
        paths[checksum] = directory / name
    source = plan["source_id"]
    records = []
    def record(kind, data, immutable=False):
        data = {**data, **({"content_hash": content_hash(data)} if immutable else {})}
        value = BundleRecord(reference=RecordKey(owner="workspace", source_id=source, kind=kind,
            id=data["id"], content_digest=content_hash(data)), data=data)
        records.append(value)
        return value
    campaign = record("campaign", {"id": "campaign_" + source, "name": "Historical standalone MEENT run",
        "objective": "Preserve the recorded experiment as retrospective evidence", "version": 0, "scientific_status": "retrospective"})
    study = record("study", {"id": "study_" + source, "campaign_id": campaign.data["id"], "scope": "historical",
        "goal": "Historical run; original prospective study is unavailable", "authority": "historical_importer"})
    original = read_json(directory / "spec.json", {})
    trial = record("trial", {"id": "trial_" + source, "campaign_id": campaign.data["id"], "study_id": study.data["id"],
        "status": "historical", "problem": plan["problem"], "task_split": "unknown", "scientific_status": "retrospective",
        "algorithm": original.get("algorithm", "unknown"), "original_spec": original,
        "import_manifest": plan, "created_at": original.get("created_at", "unknown")})
    reference = ArtifactReference(id="sha256:" + plan["files"]["best_design.npy"]["sha256"],
        **plan["files"]["best_design.npy"], media_type="application/x-npy")
    solution = record("asset", Asset(id="solution_" + source, campaign_id=campaign.data["id"], kind="solution",
        title="Historical best recorded solution", artifacts=[reference], payload={"candidate": plan["candidate"], "problem": plan["problem"]},
        producer_id=trial.data["id"], cost_provenance="unknown", exposure_status="unknown",
        applicability={"problem_id": "meent_grating", "candidate_schema": plan["problem"]["candidate_schema"]},
        authority="historical_importer", created_at=trial.data["created_at"]).model_dump(mode="json"), immutable=True)
    assets = [solution]
    outputs = read_json(directory / "outputs.json")
    if outputs:
        if outputs.get("id") != "outputs_" + content_hash({key: value for key, value in outputs.items() if key != "id"}):
            raise ValueError("Historical policy output manifest failed integrity verification")
        local = LocalArtifactStore(directory / "artifacts")
        for index, item in enumerate(outputs.get("outputs", [])):
            if item["kind"] != "policy":
                continue
            artifact = ArtifactReference(**item["reference"])
            local.verify(artifact)
            if artifact.sha256 not in blobs:
                raise ValueError("The historical policy is outside its captured artifact files")
            assets.append(record("asset", Asset(id="policy_" + source + "_" + str(index), campaign_id=campaign.data["id"],
                kind="policy", title="Historical exported policy", artifacts=[artifact], producer_id=trial.data["id"],
                payload={"format": item.get("format"), "metadata": item.get("metadata", {}), "checkpoint_id": outputs["checkpoint_id"]},
                applicability={"problem_id": "meent_grating", "candidate_schema": plan["problem"]["candidate_schema"]},
                authority="historical_importer", created_at=trial.data["created_at"]).model_dump(mode="json"), immutable=True))
    manifest = EvidenceBundle(roots=[asset.reference.key for asset in assets], records=[row.reference for row in records],
        blobs=list(blobs.values()), captures=[CapturedFiles(record_key=trial.reference.key, purpose="experiment",
            files={name: item["sha256"] for name, item in plan["files"].items()})],
        edges=[*[BundleEdge(source=asset.reference.key, dependency=trial.reference.key, role="historical_producer") for asset in assets],
               BundleEdge(source=trial.reference.key, dependency=study.reference.key, role="historical_scope"),
               BundleEdge(source=study.reference.key, dependency=campaign.reference.key, role="historical_campaign")])
    write(destination, manifest, records, lambda blob: paths[blob.sha256].open("rb"))
    return {"bundle_digest": manifest.digest, "solution_asset_id": solution.data["id"],
            "policy_asset_ids": [asset.data["id"] for asset in assets if asset.data["kind"] == "policy"]}


def import_for_evaluation(output, config, *, workspace=None, workspace_url=None):
    directory = output / "history-import"
    directory.mkdir(exist_ok=True)
    saved = read_json(directory / "evaluation-source.json")
    if saved:
        return saved
    plan = inspect_run(output, config)
    atomic_json(directory / "dry-run.json", plan)
    capture = capture_run(output, plan, directory / "evidence.zip")
    connection = {"directory": str(Path(workspace or output / ".workspace").resolve()) if not workspace_url else None, "url": workspace_url}
    identity = "import_" + plan["source_id"][:60]
    campaign_id = "campaign_" + identity
    with Session(**connection, journal=output / "command-receipts") as session:
        session.command("campaign.create", campaign_id, {"name": "Historical evaluation: " + output.name[:150],
            "objective": "Independently reevaluate explicitly imported historical evidence.", "autonomy": "manual", "llm_budget_usd": 0,
            "compute_budget_seconds": 600, "validation_reserve_seconds": 360,
            "tasks": [{"name": "Historical MEENT configuration", "physics": asdict(config.physics)}]}, identity=identity + "_campaign")
        receipt = session.import_bundle(directory / "evidence.zip", campaign_id, identity=identity)
        state = session.state(campaign_id)
        reuse = session.command("asset.reuse", campaign_id, {"asset_id": capture["solution_asset_id"],
            "study_id": state["campaign"]["active_study_id"], "decision": "reuse", "intended_use": "optimizer_input",
            "rationale": "Researcher requested reevaluation of this historical best solution; historical performance and costs remain unverified."},
            identity=identity + "_solution_reuse")
        result = session.command("trial.create", campaign_id, {"task_id": state["tasks"][0]["id"], "algorithm": "evaluate_asset",
            "max_steps": 1, "completion": {"count": 1}, "wall_seconds": 120,
            "initial_assets": [capture["solution_asset_id"]], "reuse_decision_ids": [reuse["reuse_decision_id"]],
            "question": "Measure the imported historical solution under the currently declared evaluator."}, identity=identity + "_measurement")
        trial = session.wait_trial(campaign_id, result["trial_id"])
        if trial["status"] != "completed":
            raise RuntimeError(f"Historical solution reevaluation {trial['id']} did not complete")
        result = {"id": identity, "campaign_id": campaign_id, "workspace_id": session.workspace_id, "trial_id": trial["id"],
            "connection": connection, "historical_import": {"plan": plan, "capture": capture, "receipt": receipt},
            "policy_asset_ids": capture["policy_asset_ids"]}
        atomic_json(directory / "evaluation-source.json", result)
        return result
