"""Algorithm semantics and a tiny real-MEENT, interruption/resume experiment."""
from dataclasses import replace
import csv
import json

import numpy as np
import pytest
import torch

from dqn_meent.config import ExperimentConfig, PhysicsConfig, TrainConfig
from dqn_meent.dqn import QNetwork, ReplayBuffer, compute_td_target, greedy_action, load_policy
from dqn_meent.training import epsilon_at_step, train


def test_double_dqn_uses_online_selection_and_target_evaluation():
    # Online chooses action 0 while the target network prefers action 1.
    online = torch.tensor([[9., 1.], [2., 7.], [3., 3.]], requires_grad=True)
    target = torch.tensor([[2., 10.], [6., 4.], [5., 8.]], requires_grad=True)
    rewards = torch.tensor([1., 2., -1.])
    terminated = torch.tensor([False, True, False])
    double = compute_td_target(rewards, terminated, online, target, .5, True)
    vanilla = compute_td_target(rewards, terminated, online, target, .5, False)
    # Terminal return is reward, and ties select first maximal online action.
    torch.testing.assert_close(double, torch.tensor([2., 2., 1.5]))
    torch.testing.assert_close(vanilla, torch.tensor([6., 2., 3.]))
    assert not double.requires_grad
    assert not vanilla.requires_grad


def test_terminal_bootstrap_does_not_propagate_nonfinite_q():
    result = compute_td_target(torch.tensor([3.]), torch.tensor([True]),
                               torch.tensor([[0., 1.]]),
                               torch.tensor([[float('nan'), float('inf')]]), .9)
    torch.testing.assert_close(result, torch.tensor([3.]))


def test_replay_roundtrip_preserves_wrapped_buffer():
    replay = ReplayBuffer(3, 2)
    for i in range(5):
        replay.add([i, i + 1], i % 2, float(i), [i + 1, i + 2], i % 2 == 0)
    restored = ReplayBuffer(3, 2)
    restored.load_state_dict(replay.state_dict())
    assert (restored.position, len(restored)) == (2, 3)
    left = replay.sample(3, np.random.default_rng(42))
    right = restored.sample(3, np.random.default_rng(42))
    for name in left:
        torch.testing.assert_close(left[name], right[name])


def test_greedy_random_ties_are_seeded_and_can_choose_each_action():
    model = QNetwork(4, 3, (4,))
    for parameter in model.parameters():
        torch.nn.init.zeros_(parameter)
    obs = np.zeros(4, dtype=np.float32)
    a = np.random.default_rng(41)
    b = np.random.default_rng(41)
    actions = [greedy_action(model, obs, a) for _ in range(40)]
    assert actions == [greedy_action(model, obs, b) for _ in range(40)]
    assert set(actions) == {0, 1, 2}
    assert greedy_action(model, obs) == 0


def test_epsilon_schedule_uses_original_budget():
    tc = TrainConfig(total_steps=100, exploration_fraction=.5,
                     epsilon_start=1., epsilon_end=.1)
    assert epsilon_at_step(0, tc) == 1.
    assert epsilon_at_step(25, tc) == pytest.approx(.55)
    assert epsilon_at_step(50, tc) == pytest.approx(.1)
    assert epsilon_at_step(200, tc) == pytest.approx(.1)


def tiny_config():
    return ExperimentConfig(
        physics=PhysicsConfig(n_cells=4, fourier_order=1, material="constant",
                              silicon_n=3.5, silicon_k=0., cache_size=100),
        training=TrainConfig(total_steps=8, horizon=3, seed=123,
                             batch_size=2, buffer_size=32, learning_starts=2,
                             target_update_interval=2, checkpoint_interval=4,
                             hidden_sizes=(8, 8), torch_threads=1),
    )


def test_real_meent_training_and_exact_cpu_resume(tmp_path, monkeypatch):
    from optimization_framework.execution.worker import ExperimentWorker, read_journal

    config = tiny_config()
    uninterrupted_dir = tmp_path / "full"
    uninterrupted = train(config, uninterrupted_dir)
    assert uninterrupted["steps"] == 8
    assert uninterrupted["updates"] == 7
    assert np.isfinite(uninterrupted["best_efficiency"])
    assert 0 <= uninterrupted["best_efficiency"] <= 1
    assert uninterrupted["solver_calls"] > 0
    with (uninterrupted_dir / "metrics.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 8
    assert [int(row["step"]) for row in rows] == list(range(1, 9))
    with pytest.raises(FileExistsError):
        train(config, uninterrupted_dir)

    original_step = ExperimentWorker.one_step
    def interrupted_step(worker):
        if worker.optimizer.inspect()["decisions"] == 5:
            raise RuntimeError("Simulated interruption after checkpoint")
        return original_step(worker)

    interrupted_dir = tmp_path / "interrupted"
    with monkeypatch.context() as patch:
        patch.setattr(ExperimentWorker, "one_step", interrupted_step)
        with pytest.raises(RuntimeError, match="Simulated interruption"):
            train(config, interrupted_dir)
    # The CSV is a logical projection; physical attempt evidence is never erased.
    with (interrupted_dir / "metrics.csv").open() as stream:
        assert len(list(csv.DictReader(stream))) == 5
    original_evidence = read_journal(interrupted_dir / "observations.jsonl")
    resumed = train(config, interrupted_dir, resume=interrupted_dir / "checkpoint.pt")
    for key in ("steps", "updates", "episodes_completed", "best_efficiency",
                "last_efficiency", "last_loss"):
        assert resumed[key] == uninterrupted[key]
    assert resumed["evaluations"] > uninterrupted["evaluations"]
    assert resumed["solver_calls"] >= uninterrupted["solver_calls"]
    assert read_journal(interrupted_dir / "observations.jsonl")[:len(original_evidence)] == original_evidence
    with (interrupted_dir / "metrics.csv").open() as stream:
        resumed_rows = list(csv.DictReader(stream))
    for before, after in zip(rows, resumed_rows, strict=True):
        costs = {"elapsed_seconds", "evaluations", "solver_calls", "cache_hits"}
        assert {k: v for k, v in before.items() if k not in costs} == {
            k: v for k, v in after.items() if k not in costs}
    full_model, loaded_config = load_policy(uninterrupted_dir / "checkpoint.pt")
    resumed_model, _ = load_policy(interrupted_dir / "checkpoint.pt")
    assert loaded_config.physics == config.physics
    for left, right in zip(full_model.parameters(), resumed_model.parameters(), strict=True):
        torch.testing.assert_close(left, right, rtol=0, atol=0)
    design = np.load(interrupted_dir / "best_design.npy")
    assert design.shape == (4,)
    assert set(design.tolist()) <= {0, 1}
    best = json.loads((interrupted_dir / "best_design.json").read_text())
    assert best["efficiency"] == resumed["best_efficiency"]
    with pytest.raises(ValueError, match="config"):
        train(replace(config, training=replace(config.training, total_steps=9)),
              tmp_path / "bad_resume", resume=interrupted_dir / "checkpoint.pt")
