"""Frozen single-condition experiment definitions and decision rules."""
from dataclasses import replace
import statistics

from .config import ExperimentConfig, PhysicsConfig, TrainConfig


PROFILE_ORDER = ("P", "C", "S", "T", "H", "TH", "D", "A", "I", "G", "L")
DEVELOPMENT_SEEDS = (10, 11, 12)
CONFIRMATION_SEEDS = (100, 101, 102, 103, 104)
GAIN = .005
TOLERANCE = .0001
PUBLIC_COMMIT = "0fdb2887e45f82c6ae9613144f0019bf1e2e4965"


def profile_config(profile, seed=0, smoke=False):
    physics = PhysicsConfig(deflection_angle_deg=70, fourier_order=40)
    paper = TrainConfig(
        total_steps=2_000_000, horizon=128, seed=seed, learning_rate=.001,
        gamma=.99, batch_size=512, buffer_size=1_000_000, learning_starts=5000,
        train_frequency=2, target_update_interval=20_000, exploration_fraction=.5,
        hidden_sizes=(128, 128), double_dqn=False, reward_mode="paper",
        checkpoint_interval=100_000, episode_mode="continuing", activation="leaky_relu",
        initialization="orthogonal", gradient_clip_norm=None, target_update_tau=.1,
        epsilon_schedule="paper", greedy_tie_break="first", random_streams="python_numpy",
        learning_starts_strict=True,
    )
    modifications = {
        "P": {},
        "T": dict(target_update_interval=2000, target_update_tau=1.),
        "H": dict(episode_mode="finite"),
        "TH": dict(target_update_interval=2000, target_update_tau=1., episode_mode="finite"),
        "D": dict(double_dqn=True),
        "A": dict(activation="relu"),
        "I": dict(initialization="default"),
        "G": dict(gradient_clip_norm=10.),
        "L": dict(learning_rate=.0001),
        "S": dict(double_dqn=True, learning_rate=.0001, gradient_clip_norm=10.),
        "C": dict(double_dqn=True, episode_mode="finite", activation="relu",
                  initialization="default", gradient_clip_norm=10., target_update_tau=1.,
                  target_update_interval=2000, epsilon_schedule="linear", greedy_tie_break="random",
                  random_streams="numpy", learning_starts_strict=False),
    }
    training = replace(paper, **modifications[profile])
    if smoke:
        training = replace(training, total_steps=256, buffer_size=512, learning_starts=16,
                           batch_size=16, checkpoint_interval=128,
                           target_update_interval=40 if training.target_update_tau < 1 else 20)
    return ExperimentConfig(physics, training)


def checkpoint_steps(total_steps):
    if total_steps < 100_000:
        return sorted({total_steps // 2, total_steps})
    return [30_000, *range(100_000, total_steps + 1, 100_000)]


def select_profile(rows, seeds=DEVELOPMENT_SEEDS):
    """Only complete, converged development results may select a profile."""
    candidates = []
    for profile in PROFILE_ORDER:
        group = [r for r in rows if r.get("profile") == profile and r.get("phase") == "development"
                 and r.get("status") == "completed" and r.get("converged")]
        by_seed = {r["seed"]: r for r in group}
        if set(by_seed) != set(seeds):
            continue
        efficiencies = [by_seed[s]["efficiency_F320"] for s in seeds]
        candidates.append(dict(profile=profile, median=statistics.median(efficiencies),
                               maximum=max(efficiencies),
                               elapsed_seconds=sum(by_seed[s]["elapsed_seconds"] for s in seeds)))
    if not candidates:
        return None
    top = max(c["median"] for c in candidates)
    tied = [c for c in candidates if c["median"] >= top - .0005]
    winner = sorted(tied, key=lambda c: (-c["maximum"], c["elapsed_seconds"], c["profile"]))[0]
    return dict(selected_profile=winner["profile"], candidates=candidates, selection=winner,
                rule="median F320; ties within 0.05 pp: max, elapsed time, profile ID")


def decide(rows, selected, reference_results, seeds=CONFIRMATION_SEEDS):
    controls = [r for r in rows if r.get("kind") in {"hc", "refine"}]
    confirmations = [r for r in rows if r.get("phase") == "confirmation" and r.get("profile") == selected]
    complete_controls = {(r.get("kind"), r.get("seed")) for r in controls
                         if r.get("status") == "completed" and r.get("converged")}
    complete_rl = {r.get("seed") for r in confirmations
                   if r.get("status") == "completed" and r.get("converged")}
    references_valid = bool(reference_results) and all(r.get("converged") for r in reference_results)
    valid_hc = [r["efficiency_F480"] for r in controls if r.get("converged") and "efficiency_F480" in r]
    valid_hc += [r["efficiency_F480"] for r in reference_results
                 if r.get("method") != "paper" and r.get("converged")]
    benchmark = max(valid_hc) if valid_hc else None
    wins = [r["seed"] for r in confirmations if benchmark is not None and r.get("converged")
            and r.get("status") == "completed" and r["efficiency_F480"] > benchmark + GAIN]
    complete = (selected is not None and references_valid and complete_rl == set(seeds)
                and complete_controls == {(m, s) for m in ("hc", "refine") for s in seeds})
    if not complete:
        verdict = "inconclusive_execution"
    elif len(wins) >= 3:
        verdict = "reproducible_rl_advantage"
    elif wins:
        verdict = "isolated_improvement"
    else:
        verdict = "no_demonstrated_reproducible_advantage"
    return dict(verdict=verdict, selected_profile=selected, complete=complete,
                hc_best=benchmark, required_gain=GAIN, winning_seeds=wins,
                complete_rl_seeds=sorted(complete_rl), complete_hc_runs=len(complete_controls),
                scope="Only this condition, these DQN variants, MEENT physics, and the bounded effort.")
