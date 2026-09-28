"""Compatibility CLI projections over the framework's sole numerical runner."""
from dataclasses import asdict
import csv
from pathlib import Path
import shutil
import uuid

import numpy as np

from optimization_framework.contracts.base import canonical_json, content_hash
from optimization_framework.contracts.experiments import ExperimentSpec, ImplementationVersion
from optimization_framework.evaluation.registry import problems
from optimization_framework.execution.source import runtime_identity, snapshot, scientific_hash
from optimization_framework.execution.worker import run, iter_journal
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import now, read_json


LOG_FIELDS = ["step", "episode", "episode_step", "epsilon", "reward", "efficiency",
              "best_efficiency", "loss", "evaluations", "solver_calls", "cache_hits", "elapsed_seconds"]


def execute(config, output_dir, *, method="dqn", budget=None, seed=None, resume=None):
    output = Path(output_dir).resolve()
    tc = config.training
    seed = tc.seed if seed is None else seed
    count = tc.total_steps if method == "dqn" else budget
    if type(count) is not int or count < 1:
        raise ValueError("A positive integral work budget is required")
    existing = output.exists() and any(output.iterdir())
    if resume is None and existing:
        raise FileExistsError(f"Output directory is not empty: {output}")
    if resume is not None:
        source = Path(resume).resolve().parent
        try:
            import json
            pointer = json.loads(Path(resume).read_text())
        except (UnicodeError, ValueError):
            raise ValueError("Legacy checkpoints require their recorded historical runtime; use their policy as an explicitly declared asset in a new experiment") from None
        if not isinstance(pointer, dict) or pointer.get("format") != "framework-checkpoint-reference-v1":
            raise ValueError("Unsupported CLI checkpoint reference")
        if canonical_json(read_json(source / "config.json")) != canonical_json(config.to_dict()):
            raise ValueError("Resume config must match the frozen config, including the original total_steps")
        if source != output:
            if existing:
                raise FileExistsError("Resume destination must be empty or the original run directory")
            output.mkdir(parents=True, exist_ok=True)
            for name in ("artifacts", "checkpoints", "code", "outputs", "inputs"):
                if (source / name).is_dir():
                    shutil.copytree(source / name, output / name)
            for name in ("spec.json", "config.json", "requests.jsonl", "observations.jsonl", "metrics.jsonl", "attempts.jsonl", "costs.jsonl", "progress.json"):
                if (source / name).exists():
                    shutil.copy2(source / name, output / name)
        latest = read_json(output / "checkpoints/latest.json")
        if not latest or latest["id"] != pointer["checkpoint_id"]:
            raise ValueError("Resume reference is not the latest committed checkpoint")
        spec = read_json(output / "spec.json")
        if spec["algorithm"] != method or spec["seed"] != seed:
            raise ValueError("Resume method and seed must match the frozen procedure")
        # This CLI runs in the current interpreter, while scheduled workers
        # start from the saved source. Refuse incompatible current CLI code.
        if spec["scientific_source_hash"] != scientific_hash():
            raise ValueError("CLI source changed; resume using the recorded source/runtime")
        if spec["scientific_environment"] != runtime_identity():
            raise ValueError("CLI numerical runtime changed; resume using the recorded runtime")
        control = read_json(output / "control.json", {})
        atomic_json(output / "control.json", {"command": "run", "revision": control.get("revision", 0) + 1})
    else:
        output.mkdir(parents=True, exist_ok=True)
        problem = problems.resolve("meent_grating", asdict(config.physics))
        parameters = {}
        if method == "hillclimb":
            parameters = {"neighborhood": "random", "initialization": "ones", "accept_equal": True,
                          "restart_patience": 2 * config.physics.n_cells}
        elif method == "random":
            parameters = {"initial_design": [1] * config.physics.n_cells}
        code_hash = snapshot(output)
        experiment_id = "cli_" + uuid.uuid4().hex
        # Requests include episode resets; one bounded checkpoint replay is
        # allocated in advance. Scientific completion remains an action count.
        allowance = count
        if method == "dqn":
            allowance += (count + tc.horizon - 1) // tc.horizon
            allowance += tc.checkpoint_interval + (tc.checkpoint_interval + tc.horizon - 1) // tc.horizon
        spec = {"id": experiment_id, "campaign_id": "cli", "study_id": "cli_" + content_hash(config.to_dict()),
                "problem": problem.model_dump(mode="json"), "algorithm": method, "algorithm_config": parameters,
                "training": asdict(tc), "seed": seed, "schedule_steps": count, "max_steps": allowance,
                "wall_seconds": 86400, "source_hash": code_hash, "scientific_source_hash": scientific_hash(),
                "scientific_environment": runtime_identity(),
                "completion": {"unit": "optimizer_decisions" if method == "dqn" else "evaluation_requests", "count": count},
                "recovery": {"every_observations": tc.checkpoint_interval, "every_seconds": 30}}
        frozen = ExperimentSpec(id=experiment_id + "_spec", campaign_id=spec["campaign_id"], study_id=spec["study_id"],
            problem=problem, implementation=ImplementationVersion(id="builtin:" + method + ":" + code_hash,
                name=method, source_digest=code_hash, runtime_digest=content_hash(spec["scientific_environment"]), representations=["binary"]),
            parameters={key: spec[key] for key in ("algorithm", "algorithm_config", "training")},
            seed=seed, schedule={"steps": count}, completion=spec["completion"], recovery=spec["recovery"],
            initial_wall_seconds=spec["wall_seconds"], created_at=now())
        spec.update(experiment_spec=frozen.model_dump(mode="json"), experiment_spec_hash=frozen.digest())
        atomic_json(output / "spec.json", spec)
        atomic_json(output / "config.json", config.to_dict())
    result = run(output)
    return export_summary(config, output, result, method=method, seed=seed, resume=resume)


def export_summary(config, output, result, *, method, seed, resume=None):
    """Produce historical filenames from recorded worker evidence, without work."""
    output = Path(output)
    tc = config.training
    project_metrics(output, method)
    manifest = read_json(output / "checkpoints/latest.json")
    if manifest:
        atomic_json(output / "checkpoint.pt", {"format": "framework-checkpoint-reference-v1", "checkpoint_id": manifest["id"]})
    if result.get("best_candidate") is not None:
        np.save(output / "best_design.npy", np.asarray(result["best_candidate"], dtype=np.uint8))
        atomic_json(output / "best_design.json", {"design": result["best_candidate"], "efficiency": result["best_objective"],
            "physics": asdict(config.physics), "period_nm": config.physics.period_nm, "step_at_save": result["step"]})
    diagnostics = result.get("diagnostics", {})
    summary = {**result, "steps": diagnostics.get("decisions", result.get("step", 0)),
               "updates": diagnostics.get("updates", 0), "episodes_completed": diagnostics.get("episodes_completed", 0),
               "last_loss": diagnostics.get("loss"), "last_efficiency": result.get("objective"),
               "method": method, "seed": seed, "device": tc.device, "double_dqn": tc.double_dqn,
               "completed": result.get("scientific_complete", False), "evaluation_requests": result.get("evaluations", 0),
               "resumed_from": str(resume) if resume is not None else None,
               "checkpoint": str(output / "checkpoint.pt"), "best_design": str(output / "best_design.npy"),
               "metrics": str(output / "metrics.csv")}
    atomic_json(output / "summary.json", summary)
    if result["status"] == "failed":
        raise RuntimeError(result["reason"])
    return summary


def project_metrics(output, method):
    """CSV is a replaceable projection; journals retain every physical attempt."""
    rows = {}
    for record in iter_journal(output / "metrics.jsonl"):
        diagnostics = record["diagnostics"]
        step = diagnostics.get("decisions", 0) if method == "dqn" else record["step"]
        if step == 0 or (method == "dqn" and diagnostics.get("reward") is None):
            continue
        rows[step] = {"step": step, "episode": diagnostics.get("episode", ""), "episode_step": diagnostics.get("episode_step", ""),
            "epsilon": diagnostics.get("epsilon", ""), "reward": diagnostics.get("reward", ""),
            "efficiency": record["objective"], "best_efficiency": record["best_objective"],
            "loss": diagnostics.get("transition_loss") if diagnostics.get("transition_loss") is not None else "",
            "evaluations": record["evaluations"], "solver_calls": record["solver_calls"], "cache_hits": record["cache_hits"],
            "elapsed_seconds": record["elapsed_seconds"]}
    with (output / "metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
