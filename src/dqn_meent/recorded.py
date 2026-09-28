"""MEENT CLI conveniences over the same durable workspace commands as the UI."""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
from pathlib import Path
import shutil
import uuid

from optimization_framework.cli import Session
from optimization_framework.contracts.base import canonical_json
from optimization_framework.contracts.base import content_hash
from optimization_framework.evaluation.registry import problems
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import read_json


@contextmanager
def output_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".cli.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another CLI invocation is using this output directory") from exc
        yield


def parameters(config, method):
    if method == "hillclimb":
        return {"neighborhood": "random", "initialization": "ones", "accept_equal": True,
                "restart_patience": 2 * config.physics.n_cells}
    if method == "random":
        return {"initial_design": [1] * config.physics.n_cells}
    return {}


def execute(config, output_dir, *, method="dqn", budget=None, seed=None, resume=None,
            workspace=None, workspace_url=None, campaign_id=None, task_id=None, wall_seconds=3600):
    output = Path(output_dir).resolve()
    wall_seconds = float(wall_seconds)
    seed = config.training.seed if seed is None else seed
    count = config.training.total_steps if method == "dqn" else budget
    if type(count) is not int or count < 1:
        raise ValueError("A positive integral work budget is required")
    frozen = {"config": config.to_dict(), "method": method, "seed": seed, "count": count, "wall_seconds": wall_seconds}
    with output_lock(output):
        manifest = read_json(output / "cli-run.json")
        if resume is not None:
            source = Path(resume).resolve().parent
            original = read_json(source / "cli-run.json")
            if not original:
                raise ValueError("This historical run has no workspace identity; import its evidence before starting a new experiment")
            pointer = read_json(Path(resume))
            if not pointer or pointer.get("format") != "framework-checkpoint-reference-v1":
                raise ValueError("Resume requires a recorded framework checkpoint reference")
            if canonical_json(original["procedure"]) != canonical_json(frozen):
                raise ValueError("Resume config and limits must match the frozen procedure")
            if manifest and manifest["id"] != original["id"]:
                raise ValueError("Resume destination belongs to a different experiment")
            if source != output:
                if any(path.name != ".cli.lock" for path in output.iterdir()) and manifest is None:
                    raise FileExistsError("Resume destination must be empty or belong to the same recorded experiment")
                if (source / "command-receipts").is_dir() and not (output / "command-receipts").exists():
                    shutil.copytree(source / "command-receipts", output / "command-receipts")
            manifest = original
        elif manifest and (output / "summary.json").exists():
            raise FileExistsError(f"Output already contains a recorded run: {output}; use --resume for interrupted work")
        elif not manifest and any(path.name != ".cli.lock" for path in output.iterdir()):
            raise FileExistsError(f"Output directory is not empty: {output}")
        if manifest and canonical_json(manifest["procedure"]) != canonical_json(frozen):
            raise ValueError("The pending CLI request has a different frozen config or allocation")
        if not manifest:
            identity = "cli_" + uuid.uuid4().hex
            manifest = {"format": "workspace-cli-run-v1", "id": identity, "procedure": frozen,
                "campaign_id": campaign_id or "campaign_" + identity, "task_id": task_id,
                "existing_campaign": bool(campaign_id), "connection": {"url": workspace_url,
                    "directory": str(Path(workspace or output / ".workspace").resolve()) if not workspace_url else None}}
            atomic_json(output / "cli-run.json", manifest)
        connection = manifest["connection"]
        if (workspace and str(Path(workspace).resolve()) != connection["directory"]) or (workspace_url and workspace_url.rstrip("/") != (connection["url"] or "").rstrip("/")):
            raise ValueError("The saved CLI run belongs to a different workspace")
        with Session(directory=connection["directory"], url=connection["url"], journal=output / "command-receipts") as session:
            if manifest.get("workspace_id", session.workspace_id) != session.workspace_id:
                raise ValueError("The saved CLI workspace identity changed; reconnect to its owning service")
            manifest["workspace_id"] = session.workspace_id
            atomic_json(output / "cli-run.json", manifest)
            if not manifest["existing_campaign"]:
                original_campaign = read_json(session.journal / (manifest["id"] + "_campaign.json"))
                campaign_request = original_campaign["request"]["payload"] if original_campaign else {
                    "name": output.name, "objective": "Evaluate the explicitly configured optimizer on this MEENT instance.",
                    "autonomy": "manual", "llm_budget_usd": 0, "compute_budget_seconds": wall_seconds + 360,
                    "validation_reserve_seconds": 360, "tasks": [{"name": "CLI configuration", "physics": asdict(config.physics)}]}
                session.command("campaign.create", manifest["campaign_id"], campaign_request,
                    identity=manifest["id"] + "_campaign")
            state = session.state(manifest["campaign_id"])
            expected = problems.resolve("meent_grating", asdict(config.physics)).model_dump(mode="json")
            tasks = [task for task in state["tasks"] if task.get("problem") == expected and not task.get("archived")
                     and (not manifest["task_id"] or task["id"] == manifest["task_id"])]
            if len(tasks) != 1:
                raise ValueError("Select one current task matching this config with --task; revise the campaign explicitly if needed")
            manifest["task_id"] = tasks[0]["id"]
            allowance = count
            tc = config.training
            if method == "dqn":
                allowance += (count + tc.horizon - 1) // tc.horizon + tc.checkpoint_interval + (tc.checkpoint_interval + tc.horizon - 1) // tc.horizon
            request = {"task_id": manifest["task_id"], "algorithm": method, "algorithm_config": parameters(config, method),
                "training": asdict(tc), "seed": seed, "max_steps": allowance, "schedule_steps": count, "wall_seconds": wall_seconds,
                "completion": {"unit": "optimizer_decisions" if method == "dqn" else "evaluation_requests", "count": count},
                "recovery": {"every_observations": tc.checkpoint_interval, "every_seconds": 30}}
            outcome = session.command("trial.create", manifest["campaign_id"], request, identity=manifest["id"] + "_experiment")
            manifest["trial_id"] = outcome["trial_id"]
            atomic_json(output / "cli-run.json", manifest)
            trial = session.trial(manifest["campaign_id"], manifest["trial_id"])
            if resume is not None and trial["status"] in {"paused", "interrupted", "stopped", "failed"}:
                if pointer.get("checkpoint_id") != (trial.get("progress") or {}).get("checkpoint_id"):
                    raise ValueError("The checkpoint reference is no longer the latest committed checkpoint; inspect the workspace's current run")
                session.command("trial.control", manifest["campaign_id"], {"trial_id": trial["id"], "action": "resume",
                    "expected_control_revision": trial["control_revision"]},
                    identity=manifest["id"] + "_resume_" + str(trial["control_revision"]))
            try:
                trial = session.wait_trial(manifest["campaign_id"], trial["id"])
            except KeyboardInterrupt:
                trial = session.trial(manifest["campaign_id"], trial["id"])
                if trial["status"] in {"queued", "running"}:
                    session.command("trial.control", manifest["campaign_id"], {"trial_id": trial["id"], "action": "pause",
                        "expected_control_revision": trial["control_revision"]})
                    trial = session.wait_trial(manifest["campaign_id"], trial["id"])
                _export(session, manifest, trial, output, config, resume)
                raise
            return _export(session, manifest, trial, output, config, resume)


def _export(session, manifest, trial, output, config, resume):
    session.export_trial(trial, output)
    atomic_json(output / "config.json", config.to_dict())
    from .lifecycle import export_summary
    summary = export_summary(config, output, trial.get("result") or trial.get("progress") or {},
        method=manifest["procedure"]["method"], seed=manifest["procedure"]["seed"], resume=resume)
    summary.update(workspace_id=session.workspace_id, campaign_id=trial["campaign_id"], trial_id=trial["id"],
        status=trial["status"], evidence_bundle=str(output / "evidence.zip"))
    atomic_json(output / "summary.json", summary)
    return summary


def _convergence(session, trial, orders, tolerance, wall_seconds, identity, directory, config):
    outcome = session.command("trial.validate", trial["campaign_id"], {"trial_id": trial["id"], "orders": orders,
        "max_designs": 1, "tolerance": tolerance, "wall_seconds": wall_seconds}, identity=identity)
    job = session.wait_trial(trial["campaign_id"], outcome["trial_id"])
    session.export_trial(job, directory)
    result = job.get("result") or {}
    subjects = result.get("recipe_result", {}).get("subjects", [])
    if job["status"] != "completed" or not subjects or not subjects[0]["complete"]:
        raise RuntimeError(f"Recorded validation {job['id']} did not complete: {job.get('reason', job['status'])}")
    subject = subjects[0]
    return {"physics": asdict(config.physics), "period_nm": config.physics.period_nm,
        "results": [{**row, "harmonics": 2 * row["fourier_order"] + 1,
                     "elapsed_seconds": row["costs"].get("evaluation_seconds")} for row in subject["observations"]],
        "last_two_absolute_difference": subject["last_two_absolute_difference"],
        "convergence_tolerance": tolerance, "last_two_within_tolerance": subject["converged"],
        "trial_id": job["id"], "validation_requirement_ids": job.get("validation_requirement_ids", []),
        "note": "A recorded last-two-order diagnostic; this is not independent solver validation."}


def evaluate(run_dir, orders, tolerance=.005, policy=True, *, workspace=None, workspace_url=None, wall_seconds=120):
    """All numerical reevaluation is queued as a separately bounded recorded job."""
    from .config import ExperimentConfig
    from optimization_framework.contracts.requests import ValidationInput
    output = Path(run_dir).resolve()
    config = ExperimentConfig.load(output / "config.json")
    validated = ValidationInput(orders=orders, tolerance=tolerance, wall_seconds=wall_seconds, max_designs=1)
    orders = validated.orders
    with output_lock(output):
        manifest = read_json(output / "cli-run.json")
        if manifest is None:
            from .historical import import_for_evaluation
            manifest = import_for_evaluation(output, config, workspace=workspace, workspace_url=workspace_url)
        connection = manifest["connection"]
        if (workspace and str(Path(workspace).resolve()) != connection["directory"]) or (workspace_url and workspace_url.rstrip("/") != (connection["url"] or "").rstrip("/")):
            raise ValueError("Evaluation must connect to the workspace that owns the recorded run")
        identity = "cli_evaluate_" + content_hash([manifest["id"], orders, tolerance, wall_seconds])[:32]
        directory = output / "evaluations" / identity
        with Session(directory=connection["directory"], url=connection["url"], journal=output / "command-receipts") as session:
            if manifest["workspace_id"] != session.workspace_id:
                raise ValueError("The recorded evaluation workspace identity changed")
            parent = session.trial(manifest["campaign_id"], manifest["trial_id"])
            report = {"best_discovered": _convergence(session, parent, orders, tolerance, wall_seconds,
                identity + "_solution", directory / "solution", config)}
            candidates = [row for row in session.assets(manifest["campaign_id"]) if row["kind"] == "policy"
                          and row.get("producer_id") == parent["id"]]
            if manifest.get("policy_asset_ids"):
                candidates = [session.asset(identity) for identity in manifest["policy_asset_ids"]]
            if policy and candidates:
                if len(candidates) != 1:
                    raise ValueError("Select one exported policy; this run has multiple captured policy versions")
                asset = session.asset(candidates[0]["id"])
                study = parent["study_id"]
                reused = session.command("asset.reuse", parent["campaign_id"], {"asset_id": asset["id"], "study_id": study,
                    "decision": "reuse", "intended_use": "optimizer_input", "rationale": "Researcher requested a separate frozen-policy CLI evaluation"},
                    identity=identity + "_policy_reuse")
                outcome = session.command("inference.run", parent["campaign_id"], {"trial_id": parent["id"],
                    "asset_id": asset["id"], "adapter_id": "dqn_policy:v1",
                    "parameters": {"epsilon": 0, "horizon": config.training.horizon, "tie_break": "first"},
                    "seed": config.training.seed, "wall_seconds": wall_seconds,
                    "reuse_decision_ids": [reused["reuse_decision_id"]]},
                    identity=identity + "_policy")
                rollout = session.wait_trial(parent["campaign_id"], outcome["trial_id"])
                session.export_trial(rollout, directory / "policy")
                if rollout["status"] != "completed":
                    raise RuntimeError(f"Recorded policy evaluation {rollout['id']} did not complete")
                from optimization_framework.execution.worker import iter_journal
                metrics = list(iter_journal(directory / "policy/metrics.jsonl"))
                import numpy as np
                np.save(output / "policy_best_design.npy", np.asarray(rollout["result"]["best_candidate"], dtype=np.uint8))
                report["greedy_policy"] = {"trial_id": rollout["id"], "policy_asset_id": asset["id"],
                    "trajectory": [{"step": row["diagnostics"]["decisions"], "action": row["diagnostics"].get("action"),
                        "efficiency": row["objective"]} for row in metrics if row["diagnostics"].get("decisions", 0) > 0],
                    "final_efficiency": rollout["result"]["objective"], "best_low_order_efficiency": rollout["result"]["best_objective"],
                    "best_design_high_order": _convergence(session, rollout, orders, tolerance, wall_seconds,
                        identity + "_policy_solution", directory / "policy-solution", config)}
            elif policy:
                report["policy_status"] = "No reusable policy was exported by this experiment."
            report.update(workspace_id=session.workspace_id, source_trial_id=parent["id"],
                evidence_directory=str(directory), historical_import=manifest.get("historical_import"))
            atomic_json(output / "evaluation.json", report)
            return report
