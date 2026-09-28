"""Bounded real CLI interruption and resume, without model calls or live services."""
import argparse
from dataclasses import replace
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from dqn_meent.config import ExperimentConfig, PhysicsConfig, TrainConfig
from dqn_meent.recorded import execute
from optimization_framework.cli import Session
from optimization_framework.execution.worker import read_journal
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import read_json, Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    config = ExperimentConfig(physics=PhysicsConfig(n_cells=4, fourier_order=1, material="constant", silicon_n=3.5, silicon_k=0),
        training=TrainConfig(total_steps=500, horizon=3, seed=123, batch_size=2, buffer_size=64, learning_starts=2,
            checkpoint_interval=10, hidden_sizes=(8, 8), torch_threads=1))
    atomic_json(directory / "config.json", config.to_dict())
    output = directory / "interrupted"
    command = [sys.executable, "-m", "dqn_meent.cli", "train", "--config", str(directory / "config.json"),
        "--output", str(output), "--wall-seconds", "30"]
    env = {**os.environ, "GRATING_LLM_DISABLED": "true", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    with (directory / "interruption.stdout").open("wb") as stdout, (directory / "interruption.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                manifest = read_json(output / "cli-run.json", {})
                if manifest.get("trial_id"):
                    progress = read_json(output / ".workspace/trials" / manifest["trial_id"] / "progress.json", {})
                    if progress.get("checkpoint_available") and progress.get("evaluations", 0) >= 10:
                        process.send_signal(signal.SIGINT)
                        break
                if process.poll() is not None:
                    raise AssertionError("The CLI exited before the requested interruption")
                time.sleep(.01)
            else:
                raise AssertionError("No checkpoint appeared within the qualification allowance")
            assert process.wait(timeout=30) == 130
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=30)
    manifest = read_json(output / "cli-run.json")
    store = Store(output / ".workspace")
    trial = store.get(manifest["trial_id"], "trial")
    assert trial["status"] == "paused", trial
    original = read_journal(output / "observations.jsonl")
    with Session(directory=store.directory) as session:
        session.command("campaign.update", manifest["campaign_id"], {"name": "Reviewed between CLI sessions"}, identity="rename_between_sessions")
    # A different export directory must still reconcile the original admission,
    # including the charter version from before the intervening researcher edit.
    resumed_output = directory / "resumed"
    resumed = execute(config, resumed_output, wall_seconds=30, resume=output / "checkpoint.pt")
    reference = execute(config, directory / "reference", wall_seconds=30)
    for key in ("steps", "updates", "episodes_completed", "best_efficiency", "last_efficiency", "last_loss"):
        assert resumed[key] == reference[key], (key, resumed[key], reference[key])
    assert read_journal(resumed_output / "observations.jsonl")[:len(original)] == original
    assert len(store.list("trial")) == 1 and len(store.list("execution_attempt")) == 2
    assert resumed["evaluation_requests"] >= reference["evaluation_requests"]
    assert not store.list("research_run")
    atomic_json(directory / "qualification.json", {"status": "passed", "paused_trial": trial, "resumed": resumed,
        "reference": reference, "original_observations_preserved": len(original), "cost_events": len(store.list("cost_event")),
        "command_ids": [row["id"] for row in store.list("work_command")], "model_calls": 0})
    print(directory / "qualification.json")


if __name__ == "__main__":
    main()
