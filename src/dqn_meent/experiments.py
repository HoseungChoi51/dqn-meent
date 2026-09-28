"""Independent baselines, high-order reevaluation, and inspectable plots."""
from dataclasses import replace
from pathlib import Path
import csv
import json
import time

import numpy as np

from .config import ExperimentConfig
from .physics import ForwardSolver


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def baseline(config, output_dir, method="random", budget=1000, seed=0):
    """Run the preserved baseline profile through the framework lifecycle."""
    if method not in {"random", "hillclimb"}:
        raise ValueError("Require random/hillclimb method")
    from .lifecycle import execute
    return execute(config, output_dir, method=method, budget=budget, seed=seed)


def evaluate_design(config, design, orders, tolerance=0.005):
    orders = sorted(set(orders))
    if len(orders) < 2 or min(orders) < 1:
        raise ValueError("Provide at least two distinct positive Fourier orders")
    rows = []
    for order in orders:
        solver = ForwardSolver(replace(config.physics, fourier_order=order, cache_size=0))
        started = time.perf_counter()
        result = solver.evaluate(design)
        rows.append(dict(fourier_order=order, harmonics=2 * order + 1,
                         elapsed_seconds=time.perf_counter() - started, **result.to_dict()))
    delta = abs(rows[-1]["efficiency"] - rows[-2]["efficiency"])
    return dict(physics=config.to_dict()["physics"], period_nm=config.physics.period_nm,
                results=rows, last_two_absolute_difference=delta,
                convergence_tolerance=tolerance, last_two_within_tolerance=delta <= tolerance,
                note="A last-two-order check is a diagnostic, not a proof of convergence or independent solver validation.")


def evaluate_run(run_dir, orders, tolerance=0.005, policy=True):
    run = Path(run_dir)
    config = ExperimentConfig.load(run / "config.json")
    best = np.load(run / "best_design.npy", allow_pickle=False)
    report = {"best_discovered": evaluate_design(config, best, orders, tolerance)}
    if policy and (run / "checkpoint.pt").exists():
        from .dqn import greedy_action, load_policy
        from .environment import MetagratingEnv
        model, saved_config = load_policy(run / "checkpoint.pt", device="cpu")
        env = MetagratingEnv(saved_config.physics, horizon=saved_config.training.horizon,
                             reward_mode=saved_config.training.reward_mode,
                             episode_mode=saved_config.training.episode_mode)
        obs, _ = env.reset(seed=saved_config.training.seed)
        done = False
        trajectory = []
        while not done:
            action = greedy_action(model, obs, device="cpu")
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            trajectory.append(dict(step=len(trajectory) + 1, action=int(action),
                                   efficiency=info["efficiency"], reward=float(reward)))
        np.save(run / "policy_best_design.npy", env.best_design)
        report["greedy_policy"] = dict(trajectory=trajectory, final_efficiency=env.efficiency,
                                       best_low_order_efficiency=env.best_efficiency,
                                       best_design_high_order=evaluate_design(config, env.best_design, orders, tolerance))
    write_json(run / "evaluation.json", report)
    return report


def plot_runs(run_dirs, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
    for directory in run_dirs:
        run = Path(directory)
        with (run / "metrics.csv").open() as f:
            rows = list(csv.DictReader(f))
        for ax, x, label in zip(axes, ("step", "solver_calls"), ("Search steps / baseline evaluation requests", "Actual RCWA calls (cache misses)")):
            ax.plot([float(r[x]) for r in rows], [float(r["best_efficiency"]) for r in rows], label=run.name)
            ax.set_xlabel(label)
            ax.set_ylabel("Best discovered +1 transmitted efficiency")
            ax.set_ylim(0, 1)
            ax.grid(alpha=.2)
            ax.legend()
    fig.suptitle("Search diagnostics — training-order efficiencies, not converged performance")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def plot_design(run_dir, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    run = Path(run_dir)
    config = ExperimentConfig.load(run / "config.json")
    design = np.load(run / "best_design.npy", allow_pickle=False)
    fig, ax = plt.subplots(figsize=(11, 2.5), constrained_layout=True)
    ax.imshow(design[None, :], cmap="Greys", vmin=0, vmax=1, aspect="auto",
              extent=[0, config.physics.period_nm, 0, config.physics.thickness_nm])
    ax.set_xlabel("Position within one period (nm)")
    ax.set_ylabel("Thickness (nm)")
    ax.set_title(f"{run.name}: best binary design (black = Si, white = air)")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
