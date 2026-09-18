"""Gymnasium binary-cell inverse design environment with an observed clock."""
from copy import deepcopy

import gymnasium as gym
import numpy as np

from .config import PhysicsConfig
from .physics import ForwardResult, ForwardSolver, validate_design


class MetagratingEnv(gym.Env):
    """Flip one air/silicon cell per action and evaluate its +1 TM efficiency.

    Observation: n_cells entries in {-1,+1}, followed by remaining-horizon
    fraction. The clock makes the finite-horizon problem Markov. Its last action
    terminates the MDP (terminated=True, truncated=False), so the DQN target must
    not bootstrap beyond this horizon.

    `paper` reward is eta_next**3. `difference` is eta_next-eta_current; its
    undiscounted return telescopes to eta_final-eta_initial. These are distinct
    objectives; gamma=1 is required for that telescoping interpretation.
    """

    metadata = {"render_modes": []}

    def __init__(self, physics_config: PhysicsConfig, horizon=128, reward_mode="paper"):
        super().__init__()
        if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
            raise ValueError("horizon must be a positive integer")
        if reward_mode not in {"paper", "difference"}:
            raise ValueError("reward_mode must be paper or difference")
        self.physics_config = physics_config
        self.n_cells = physics_config.n_cells
        self.horizon = horizon
        self.reward_mode = reward_mode
        self.solver = ForwardSolver(physics_config)
        self.action_space = gym.spaces.Discrete(self.n_cells)
        self.observation_space = gym.spaces.Box(
            low=np.r_[np.full(self.n_cells, -1.0), 0.0].astype(np.float32),
            high=np.ones(self.n_cells + 1, dtype=np.float32),
            dtype=np.float32,
        )
        self._design = None
        self._best_design = None
        self.best_efficiency = float("-inf")
        self.efficiency = float("nan")
        self.step_count = 0
        self._terminated = False
        self._result = None

    @property
    def design(self):
        return None if self._design is None else self._design.copy()

    @property
    def best_design(self):
        return None if self._best_design is None else self._best_design.copy()

    def state_dict(self):
        """Checkpoint the episode, best candidate, random generators and cache."""
        return {
            "horizon": self.horizon,
            "reward_mode": self.reward_mode,
            "design": self.design,
            "best_design": self.best_design,
            "best_efficiency": self.best_efficiency,
            "efficiency": self.efficiency,
            "step_count": self.step_count,
            "terminated": self._terminated,
            "result": None if self._result is None else self._result.to_dict(),
            "np_random": deepcopy(self.np_random.bit_generator.state),
            "action_random": deepcopy(self.action_space.np_random.bit_generator.state),
            "solver": self.solver.state_dict(),
        }

    def load_state_dict(self, state):
        if state["horizon"] != self.horizon or state["reward_mode"] != self.reward_mode:
            raise ValueError("Cannot restore environment with different horizon/reward")
        self.solver.load_state_dict(state["solver"])
        self._design = (None if state["design"] is None else
                        validate_design(state["design"], self.n_cells))
        self._best_design = (None if state["best_design"] is None else
                             validate_design(state["best_design"], self.n_cells))
        self.best_efficiency = float(state["best_efficiency"])
        self.efficiency = float(state["efficiency"])
        self.step_count = int(state["step_count"])
        self._terminated = bool(state["terminated"])
        self._result = (None if state["result"] is None else
                        ForwardResult.from_dict(state["result"]))
        self.np_random.bit_generator.state = deepcopy(state["np_random"])
        self.action_space.np_random.bit_generator.state = deepcopy(state["action_random"])

    def _observation(self):
        observation = np.empty(self.n_cells + 1, dtype=np.float32)
        observation[:-1] = self._design.astype(np.float32) * 2 - 1
        observation[-1] = (self.horizon - self.step_count) / self.horizon
        return observation

    def _record(self, result):
        self._result = result
        self.efficiency = result.efficiency
        if self.efficiency > self.best_efficiency:
            self.best_efficiency = self.efficiency
            self._best_design = self._design.copy()

    def _info(self):
        return {
            "efficiency": self.efficiency,
            "best_efficiency": self.best_efficiency,
            "best_design": self.best_design,
            "step_count": self.step_count,
            "evaluations": self.solver.evaluations,
            "solver_calls": self.solver.solver_calls,
            "cache_hits": self.solver.cache_hits,
            "reflectance": self._result.reflectance,
            "transmittance": self._result.transmittance,
            "absorption": self._result.absorption,
        }

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.action_space.seed(seed)
        options = {} if options is None else options
        unknown = set(options) - {"design"}
        if unknown:
            raise ValueError(f"Unknown reset options: {unknown}")
        initial = options.get("design", np.ones(self.n_cells, dtype=np.uint8))
        self._design = validate_design(initial, self.n_cells)
        self.step_count = 0
        self._terminated = False
        self._record(self.solver.evaluate(self._design))
        return self._observation(), self._info()

    def step(self, action):
        if self._design is None or self._terminated:
            raise RuntimeError("Call reset() before stepping a new/terminated episode")
        if isinstance(action, (bool, np.bool_)) or not self.action_space.contains(action):
            raise ValueError(f"Action must be an integer in [0, {self.n_cells})")
        previous_efficiency = self.efficiency
        candidate = self._design.copy()
        candidate[int(action)] ^= 1
        # Evaluate before committing state, so solver failure leaves it unchanged.
        result = self.solver.evaluate(candidate)
        self._design = candidate
        self.step_count += 1
        self._record(result)
        reward = (self.efficiency**3 if self.reward_mode == "paper" else
                  self.efficiency - previous_efficiency)
        self._terminated = self.step_count >= self.horizon
        return self._observation(), float(reward), self._terminated, False, self._info()
