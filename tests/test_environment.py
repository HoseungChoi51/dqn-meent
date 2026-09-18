import numpy as np
import pytest

from dqn_meent.config import PhysicsConfig
from dqn_meent.environment import MetagratingEnv


@pytest.fixture
def config():
    return PhysicsConfig(n_cells=8, fourier_order=3, material="constant")


def test_flip_clock_and_true_terminal(config):
    env = MetagratingEnv(config, horizon=2)
    observation, info = env.reset(seed=17)
    np.testing.assert_array_equal(observation, np.ones(9))
    assert env.observation_space.contains(observation)
    observation, reward, terminated, truncated, info = env.step(2)
    assert observation[2] == -1 and observation[-1] == 0.5
    assert reward == pytest.approx(info["efficiency"]**3)
    assert not terminated and not truncated
    observation, _, terminated, truncated, _ = env.step(2)
    assert observation[2] == 1 and observation[-1] == 0
    assert terminated and not truncated
    with pytest.raises(RuntimeError, match="reset"):
        env.step(1)


def test_observations_design_and_best_info_do_not_alias(config):
    env = MetagratingEnv(config, horizon=4)
    observation, info = env.reset()
    observation[:] = -1
    info["best_design"][:] = 0
    external_design = env.design
    external_design[:] = 0
    np.testing.assert_array_equal(env.design, np.ones(8))
    np.testing.assert_array_equal(env.best_design, np.ones(8))


def test_difference_return_telescopes_and_best_survives_reset(config):
    env = MetagratingEnv(config, horizon=4, reward_mode="difference")
    _, info = env.reset()
    initial = info["efficiency"]
    total = 0
    for action in [0, 2, 3, 5]:
        _, reward, _, _, info = env.step(action)
        total += reward
    assert total == pytest.approx(info["efficiency"]-initial)
    best, design = env.best_efficiency, env.best_design
    env.reset()
    assert env.best_efficiency == best
    np.testing.assert_array_equal(env.best_design, design)


def test_restore_continues_episode_rng_cache_and_best(config):
    original = MetagratingEnv(config, horizon=4)
    original.reset(seed=12)
    original.step(0)
    original.step(2)
    restored = MetagratingEnv(config, horizon=4)
    restored.load_state_dict(original.state_dict())
    assert original.action_space.sample() == restored.action_space.sample()
    for action in [2, 0]:
        a, b = original.step(action), restored.step(action)
        np.testing.assert_array_equal(a[0], b[0])
        assert a[1:4] == b[1:4]
        for key in ("efficiency", "best_efficiency", "solver_calls", "cache_hits", "evaluations"):
            assert a[4][key] == b[4][key]
        np.testing.assert_array_equal(a[4]["best_design"], b[4]["best_design"])
    assert restored.solver.cache_hits >= 2


@pytest.mark.parametrize("action", [-1, 8, 0.5, True])
def test_invalid_action_leaves_episode_unchanged(config, action):
    env = MetagratingEnv(config)
    env.reset()
    with pytest.raises(ValueError):
        env.step(action)
    assert env.step_count == 0
    assert env.solver.solver_calls == 1
