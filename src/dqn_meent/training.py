"""Reproducible experiment loop, logs, and trusted-local resumable checkpoints."""
from dataclasses import asdict
from pathlib import Path
import csv
import json
import random
import time

import numpy as np
import torch

from .config import ExperimentConfig
from .dqn import QNetwork, ReplayBuffer, greedy_action, optimize_batch
from .environment import MetagratingEnv


LOG_FIELDS = ["step", "episode", "episode_step", "epsilon", "reward", "efficiency",
              "best_efficiency", "loss", "evaluations", "solver_calls", "cache_hits", "elapsed_seconds"]


def epsilon_at_step(step: int, config) -> float:
    """Zero-based decision index; decay over the original full-run schedule."""
    duration = max(1, round(config.total_steps * config.exploration_fraction))
    fraction = min(max(step, 0) / duration, 1.0)
    return config.epsilon_start + fraction * (config.epsilon_end - config.epsilon_start)


def _canonical(config_dict):
    # JSON canonicalization normalizes tuples restored from JSON as lists.
    return json.dumps(config_dict, sort_keys=True, allow_nan=False)


def _atomic_json(path: Path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _restore_logs(path: Path, source: Path, completed_step: int):
    """Discard any rows written after the most recent durable checkpoint."""
    rows = []
    if source.exists():
        with source.open(newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != LOG_FIELDS:
                raise ValueError("Cannot resume: CSV log schema differs")
            for row in reader:
                if int(row["step"]) <= completed_step:
                    rows.append(row)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def train(config: ExperimentConfig, output_dir: Path,
          resume: Path | None = None) -> dict:
    """Train on MEENT calls and return a JSON-serializable run summary.

    Resumption requires the same full config, including ``total_steps``. That
    value denotes the original training budget, not additional steps. Resume is
    intended for interruption recovery. Checkpoints are trusted local pickle
    files; never use one received from an untrusted source.
    """
    output_dir = Path(output_dir)
    resume = Path(resume) if resume is not None else None
    tc = config.training
    existing_files = output_dir.exists() and any(output_dir.iterdir())
    if existing_files and resume is None:
        raise FileExistsError(f"Output directory is not empty: {output_dir}")
    if existing_files and resume is not None:
        config_path = output_dir / "config.json"
        if not config_path.exists() or _canonical(json.loads(config_path.read_text())) != _canonical(config.to_dict()):
            raise ValueError("Cannot resume into an existing directory with a different or missing config")

    device = torch.device(tc.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable; use training.device='cpu'")
    torch.set_num_threads(tc.torch_threads)
    random.seed(tc.seed)
    np.random.seed(tc.seed)
    torch.manual_seed(tc.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(tc.seed)
    rng = np.random.default_rng(tc.seed)
    online = QNetwork(config.physics.n_cells + 1, config.physics.n_cells, tc.hidden_sizes).to(device)
    target = QNetwork(config.physics.n_cells + 1, config.physics.n_cells, tc.hidden_sizes).to(device)
    target.load_state_dict(online.state_dict())
    target.eval()
    for parameter in target.parameters():
        parameter.requires_grad_(False)
    optimizer = torch.optim.Adam(online.parameters(), lr=tc.learning_rate)
    replay = ReplayBuffer(tc.buffer_size, config.physics.n_cells + 1)
    env = MetagratingEnv(config.physics, horizon=tc.horizon, reward_mode=tc.reward_mode)
    step, episode, episode_step, episodes_completed, updates = 0, 1, 0, 0, 0
    elapsed_before, needs_reset = 0.0, False
    last_efficiency = None
    last_loss = None

    if resume is not None:
        # Full replay, NumPy, and Python RNG state requires weights_only=False.
        # map_location='cpu' preserves CPU RNG tensor format before .to(device).
        state = torch.load(resume, map_location="cpu", weights_only=False)
        if state.get("format_version") != 1:
            raise ValueError("Unsupported checkpoint format")
        if _canonical(state["config"]) != _canonical(config.to_dict()):
            raise ValueError("Resume config must match checkpoint, including original total_steps")
        online.load_state_dict(state["online"])
        target.load_state_dict(state["target"])
        optimizer.load_state_dict(state["optimizer"])
        replay.load_state_dict(state["replay"])
        env.load_state_dict(state["environment"])
        observation = np.asarray(state["observation"], dtype=np.float32).copy()
        step = state["step"]
        episode = state["episode"]
        episode_step = state["episode_step"]
        episodes_completed = state["episodes_completed"]
        updates = state["updates"]
        needs_reset = state["needs_reset"]
        elapsed_before = state["elapsed_seconds"]
        last_efficiency, last_loss = state["last_efficiency"], state["last_loss"]
        rng.bit_generator.state = state["rng"]
        random.setstate(state["python_rng"])
        np.random.set_state(state["numpy_rng"])
        torch.set_rng_state(state["torch_rng"])
        if state.get("cuda_rng") is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state["cuda_rng"])
    else:
        observation, info = env.reset(seed=tc.seed)
        last_efficiency = float(info["efficiency"])

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(output_dir / "config.json", config.to_dict())
    log_path = output_dir / "metrics.csv"
    if resume is not None:
        source_log = log_path if log_path.exists() else resume.parent / "metrics.csv"
        _restore_logs(log_path, source_log, step)
    else:
        with log_path.open("w", newline="") as stream:
            csv.DictWriter(stream, fieldnames=LOG_FIELDS).writeheader()
    started_at = time.perf_counter()

    def elapsed():
        return elapsed_before + time.perf_counter() - started_at

    def save_checkpoint():
        state = {
            "format_version": 1, "config": config.to_dict(), "step": step,
            "episode": episode, "episode_step": episode_step,
            "episodes_completed": episodes_completed, "updates": updates,
            "needs_reset": needs_reset, "observation": np.asarray(observation).copy(),
            "last_efficiency": last_efficiency, "last_loss": last_loss,
            "elapsed_seconds": elapsed(),
            "online": online.state_dict(), "target": target.state_dict(),
            "optimizer": optimizer.state_dict(), "replay": replay.state_dict(),
            "environment": env.state_dict(), "rng": rng.bit_generator.state,
            "python_rng": random.getstate(), "numpy_rng": np.random.get_state(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        }
        temporary = output_dir / "checkpoint.pt.tmp"
        torch.save(state, temporary)
        temporary.replace(output_dir / "checkpoint.pt")
        np.save(output_dir / "best_design.npy", np.asarray(env.best_design, dtype=np.uint8))
        _atomic_json(output_dir / "best_design.json", {
            "efficiency": float(env.best_efficiency), "step_at_save": step,
            "physics": asdict(config.physics), "period_nm": config.physics.period_nm,
            "design": np.asarray(env.best_design, dtype=np.uint8).tolist(),
        })

    with log_path.open("a", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS)
        while step < tc.total_steps:
            if needs_reset:
                observation, _ = env.reset()
                episode += 1
                episode_step = 0
                needs_reset = False
            epsilon = epsilon_at_step(step, tc)
            if rng.random() < epsilon:
                action = int(rng.integers(config.physics.n_cells))
            else:
                action = greedy_action(online, observation, rng=rng, device=device)
            next_observation, reward, terminated, truncated, info = env.step(action)
            replay.add(observation, action, reward, next_observation, terminated)
            observation = next_observation
            step += 1
            episode_step += 1
            last_efficiency = float(info["efficiency"])
            needs_reset = bool(terminated or truncated)
            episodes_completed += int(needs_reset)
            loss = None
            if (step >= tc.learning_starts and len(replay) >= tc.batch_size
                    and step % tc.train_frequency == 0):
                loss = optimize_batch(online, target, optimizer,
                                      replay.sample(tc.batch_size, rng, device),
                                      tc.gamma, tc.double_dqn)
                updates += 1
                last_loss = loss
            if step % tc.target_update_interval == 0:
                target.load_state_dict(online.state_dict())
            writer.writerow({
                "step": step, "episode": episode, "episode_step": episode_step,
                "epsilon": epsilon, "reward": float(reward),
                "efficiency": last_efficiency, "best_efficiency": float(env.best_efficiency),
                "loss": "" if loss is None else loss,
                "evaluations": int(info["evaluations"]),
                "solver_calls": int(info["solver_calls"]), "cache_hits": int(info["cache_hits"]),
                "elapsed_seconds": elapsed(),
            })
            # A flushed CSV can be ahead of a checkpoint after interruption;
            # _restore_logs removes that suffix during recovery.
            stream.flush()
            if step % tc.checkpoint_interval == 0:
                save_checkpoint()
        save_checkpoint()
    summary = {
        "steps": step, "episodes_completed": episodes_completed, "updates": updates,
        "best_efficiency": float(env.best_efficiency), "last_efficiency": last_efficiency,
        "last_loss": last_loss, "evaluations": int(env.solver.evaluations),
        "solver_calls": int(env.solver.solver_calls),
        "cache_hits": int(env.solver.cache_hits), "elapsed_seconds": elapsed(),
        "seed": tc.seed, "device": str(device), "double_dqn": tc.double_dqn,
        "resumed_from": str(resume) if resume is not None else None,
        "checkpoint": str(output_dir / "checkpoint.pt"),
        "best_design": str(output_dir / "best_design.npy"),
        "metrics": str(log_path),
    }
    _atomic_json(output_dir / "summary.json", summary)
    env.close()
    return summary
