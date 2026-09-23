"""Resumable ask/tell optimizers for the binary grating workspace.

These are intentionally transparent reference implementations. Each ``ask``
produces one feasible binary device; the trusted worker owns its evaluation.
No optimizer receives a filesystem path, test data, or the evaluation ledger.
"""
from collections import deque
from copy import deepcopy
import math

import numpy as np

from ..config import TrainConfig
from ..physics import validate_design


ALGORITHMS = ("random", "hillclimb", "dqn", "annealing", "block_tabu",
              "population", "surrogate")


def _positive(config, name, default, *, integer=False):
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    if not math.isfinite(value) or value <= 0 or (integer and int(value) != value):
        raise ValueError(f"{name} must be a positive {'integer' if integer else 'number'}")
    return int(value) if integer else float(value)


class BinaryOptimizer:
    def __init__(self, n_cells, seed, config=None):
        self.n_cells = n_cells
        self.config = dict(config or {})
        self.rng = np.random.default_rng(seed)
        self.count = 0
        self.best_design = None
        self.best_efficiency = None
        if "initial_design" in self.config:
            self.initial_design = validate_design(self.config["initial_design"], n_cells)
        else:
            self.initial_design = None

    def random_design(self):
        return self.rng.integers(0, 2, self.n_cells, dtype=np.uint8)

    def ask(self):
        if self.count == 0 and self.initial_design is not None:
            return self.initial_design.copy()
        return self.random_design()

    def tell(self, design, efficiency):
        if not math.isfinite(efficiency):
            raise FloatingPointError("Optimizer received a nonfinite objective")
        self.count += 1
        if self.best_efficiency is None or efficiency > self.best_efficiency:
            self.best_efficiency = float(efficiency)
            self.best_design = np.asarray(design, dtype=np.uint8).copy()

    def state_dict(self):
        return deepcopy(self.__dict__)

    def load_state_dict(self, state):
        self.__dict__.update(deepcopy(state))

    def diagnostics(self):
        return {}


class HillClimber(BinaryOptimizer):
    """First-improvement search; restart after a full unsuccessful neighborhood."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current = None
        self.current_score = None
        self.remaining = []
        self.restarts = 0
        self.stagnation = 0
        self.restart_patience = _positive(self.config, "restart_patience", self.n_cells, integer=True)
        self.pending_restart = False

    def ask(self):
        self.pending_restart = self.current is None or not self.remaining or self.stagnation >= self.restart_patience
        if self.pending_restart:
            if self.current is None:
                return super().ask()
            self.restarts += 1
            return self.random_design()
        design = self.current.copy()
        design[self.remaining.pop()] ^= 1
        return design

    def tell(self, design, efficiency):
        super().tell(design, efficiency)
        if self.pending_restart or efficiency > self.current_score:
            self.current = design.copy()
            self.current_score = efficiency
            self.remaining = self.rng.permutation(self.n_cells).tolist()
            self.stagnation = 0
        else:
            self.stagnation += 1

    def diagnostics(self):
        return {"restarts": self.restarts, "stagnation": self.stagnation}


class Annealing(BinaryOptimizer):
    def __init__(self, *args, schedule_steps, **kwargs):
        super().__init__(*args, **kwargs)
        self.current = None
        self.current_score = None
        self.schedule_steps = schedule_steps
        self.temperature = _positive(self.config, "temperature", .05)
        self.min_temperature = _positive(self.config, "min_temperature", .0001)
        if self.min_temperature > self.temperature:
            raise ValueError("min_temperature must not exceed temperature")
        self.max_block_size = min(self.n_cells, _positive(self.config, "max_block_size", 4, integer=True))
        self.accepted = 0

    def temperature_now(self):
        fraction = min(self.count / max(1, self.schedule_steps), 1.)
        return self.temperature * (self.min_temperature / self.temperature) ** fraction

    def ask(self):
        if self.current is None:
            return super().ask()
        design = self.current.copy()
        size = int(self.rng.integers(1, self.max_block_size + 1))
        start = int(self.rng.integers(self.n_cells))
        design[(start + np.arange(size)) % self.n_cells] ^= 1
        return design

    def tell(self, design, efficiency):
        delta = efficiency - self.current_score if self.current_score is not None else 0.
        accepted = self.current is None or delta >= 0 or self.rng.random() < math.exp(delta / self.temperature_now())
        super().tell(design, efficiency)
        if accepted:
            self.current, self.current_score = design.copy(), efficiency
            self.accepted += 1

    def diagnostics(self):
        return {"temperature": self.temperature_now(), "accepted": self.accepted}


class BlockTabu(BinaryOptimizer):
    """Adaptive contiguous block moves with an exact-design tabu list.

    Improving moves replace the current design. Stagnation increases the block
    size; after a declared patience the search restarts. The tabu list prevents
    repeated proposals while retaining an aspiration criterion in ``tell``.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_block_size = min(self.n_cells, _positive(self.config, "max_block_size", 8, integer=True))
        self.restart_patience = _positive(self.config, "restart_patience", 4 * self.n_cells, integer=True)
        self.tabu = deque(maxlen=_positive(self.config, "tabu_tenure", 4 * self.n_cells, integer=True))
        self.current, self.current_score = None, None
        self.stagnation, self.restarts, self.block_size = 0, 0, 1
        self.pending_restart = False

    def ask(self):
        self.pending_restart = self.current is None or self.stagnation >= self.restart_patience
        if self.pending_restart:
            if self.current is None:
                return super().ask()
            self.restarts += 1
            return self.random_design()
        self.block_size = min(self.max_block_size, 1 + self.stagnation // self.n_cells)
        # Bounded proposals avoid an infinite loop on tiny exhausted spaces.
        for _ in range(2 * self.n_cells):
            design = self.current.copy()
            start = int(self.rng.integers(self.n_cells))
            design[(start + np.arange(self.block_size)) % self.n_cells] ^= 1
            if design.tobytes() not in self.tabu:
                return design
        return self.random_design()

    def tell(self, design, efficiency):
        super().tell(design, efficiency)
        self.tabu.append(design.tobytes())
        if self.pending_restart or efficiency > self.current_score:
            self.current, self.current_score = design.copy(), efficiency
            self.stagnation = 0
        else:
            self.stagnation += 1

    def diagnostics(self):
        return {"block_size": self.block_size, "stagnation": self.stagnation, "restarts": self.restarts}


class Population(BinaryOptimizer):
    """Steady-state elitist binary GA with contiguous-fragment crossover."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.population_size = _positive(self.config, "population_size", 20, integer=True)
        if self.population_size < 2:
            raise ValueError("population_size must be at least 2")
        self.population_size = min(self.population_size, 2 ** self.n_cells)
        self.mutation_rate = float(self.config.get("mutation_rate", 1 / self.n_cells))
        if not 0 < self.mutation_rate <= 1:
            raise ValueError("mutation_rate must be in (0, 1]")
        self.population = []

    def ask(self):
        if len(self.population) < self.population_size:
            return super().ask()
        def parent():
            choices = self.rng.choice(len(self.population), min(3, len(self.population)), replace=False)
            return max((self.population[int(i)] for i in choices), key=lambda pair: pair[0])[1]
        left, right = parent(), parent()
        split = int(self.rng.integers(1, self.n_cells))
        design = np.r_[left[:split], right[split:]].astype(np.uint8)
        mask = self.rng.random(self.n_cells) < self.mutation_rate
        if not mask.any():
            mask[self.rng.integers(self.n_cells)] = True
        design[mask] ^= 1
        return design

    def tell(self, design, efficiency):
        super().tell(design, efficiency)
        if not any(np.array_equal(design, item[1]) for item in self.population):
            self.population.append((efficiency, design.copy()))
            self.population.sort(key=lambda pair: pair[0], reverse=True)
            self.population = self.population[:self.population_size]

    def diagnostics(self):
        return {"population_size": len(self.population)}


class Surrogate(BinaryOptimizer):
    """Ridge-kernel search with uncertainty and prospective local/global proposals.

    This is a transparent small-data surrogate baseline, not an implementation
    of BOCS. It models centered bits and adjacent pair interactions. Its startup
    phase is explicitly reported so a supervisor can identify an untested claim.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.warmup = _positive(self.config, "warmup", max(16, self.n_cells), integer=True)
        self.candidate_pool = _positive(self.config, "candidate_pool", 128, integer=True)
        self.ridge = _positive(self.config, "ridge", .1)
        self.exploration = _positive(self.config, "exploration", .2)
        self.max_samples = _positive(self.config, "max_samples", 256, integer=True)
        self.designs, self.scores = [], []
        self.guided_proposals = 0

    def features(self, designs):
        bits = np.asarray(designs, dtype=float) * 2 - 1
        return np.c_[bits, bits * np.roll(bits, 1, axis=1)] / math.sqrt(2 * self.n_cells)

    def ask(self):
        if len(self.designs) < self.warmup:
            return super().ask()
        from scipy.linalg import cho_factor, cho_solve

        pool = [self.random_design() for _ in range(self.candidate_pool)]
        for i in range(self.candidate_pool // 2):
            design = self.best_design.copy()
            size = int(self.rng.integers(1, min(8, self.n_cells) + 1))
            design[self.rng.choice(self.n_cells, size=size, replace=False)] ^= 1
            pool[i] = design
        known = {item.tobytes() for item in self.designs}
        novel = [item for item in pool if item.tobytes() not in known]
        if novel:
            pool = novel
        features = self.features(self.designs[-self.max_samples:])
        targets = np.asarray(self.scores[-self.max_samples:])
        proposals = self.features(pool)
        kernel = features @ features.T
        kernel.flat[::len(kernel) + 1] += self.ridge
        factor = cho_factor(kernel, lower=True, check_finite=True)
        cross = proposals @ features.T
        centered = targets - targets.mean()
        means = targets.mean() + cross @ cho_solve(factor, centered)
        variance = np.maximum(0., np.sum(proposals * proposals, axis=1) -
                              np.sum(cross * cho_solve(factor, cross.T).T, axis=1))
        scale = max(float(targets.std()), .01)
        acquisition = means + self.exploration * scale * np.sqrt(variance)
        self.guided_proposals += 1
        return pool[int(np.argmax(acquisition))].copy()

    def tell(self, design, efficiency):
        super().tell(design, efficiency)
        if not any(np.array_equal(design, previous) for previous in self.designs):
            self.designs.append(design.copy())
            self.scores.append(efficiency)

    def diagnostics(self):
        return {"phase": "warmup" if len(self.designs) < self.warmup else "guided",
                "warmup": self.warmup, "unique_samples": len(self.designs),
                "guided_proposals": self.guided_proposals}


class DQN(BinaryOptimizer):
    """Existing Double DQN semantics, expressed as single-evaluation steps."""
    def __init__(self, *args, schedule_steps, training=None, **kwargs):
        super().__init__(*args, **kwargs)
        import torch
        from ..dqn import QNetwork, ReplayBuffer

        settings = dict(training or {})
        settings.update({key: value for key, value in self.config.items() if key != "initial_design"})
        settings.update(total_steps=schedule_steps)
        self.training = TrainConfig(**settings)
        tc = self.training
        self.device = torch.device(tc.device)
        if self.device.type != "cpu" and not torch.cuda.is_available():
            raise ValueError("Requested DQN device is unavailable")
        torch.set_num_threads(tc.torch_threads)
        # The worker overrides tc.seed with the trial seed before construction.
        torch.manual_seed(tc.seed)
        self.online = QNetwork(self.n_cells + 1, self.n_cells, tc.hidden_sizes).to(self.device)
        self.target = QNetwork(self.n_cells + 1, self.n_cells, tc.hidden_sizes).to(self.device)
        self.target.load_state_dict(self.online.state_dict())
        self.target.eval()
        for parameter in self.target.parameters():
            parameter.requires_grad_(False)
        self.optimizer = torch.optim.Adam(self.online.parameters(), lr=tc.learning_rate)
        self.replay = ReplayBuffer(tc.buffer_size, self.n_cells + 1)
        self.current, self.current_score = None, None
        self.observation = None
        self.episode_step, self.decisions, self.updates = 0, 0, 0
        self.action, self.loss = None, None
        self.epsilon = tc.epsilon_start

    def _observation(self):
        return np.r_[self.current.astype(np.float32) * 2 - 1,
                     (self.training.horizon - self.episode_step) / self.training.horizon].astype(np.float32)

    def ask(self):
        from ..dqn import greedy_action
        from ..training import epsilon_at_step

        if self.current is None or self.episode_step >= self.training.horizon:
            self.action = None
            return (self.initial_design.copy() if self.initial_design is not None else
                    np.ones(self.n_cells, dtype=np.uint8))
        self.epsilon = epsilon_at_step(self.decisions, self.training)
        self.action = (int(self.rng.integers(self.n_cells)) if self.rng.random() < self.epsilon else
                       greedy_action(self.online, self.observation, self.rng, self.device))
        design = self.current.copy()
        design[self.action] ^= 1
        return design

    def tell(self, design, efficiency):
        from ..dqn import optimize_batch

        super().tell(design, efficiency)
        previous_score = self.current_score
        self.current, self.current_score = design.copy(), efficiency
        if self.action is None:
            self.episode_step = 0
            self.observation = self._observation()
            return
        self.episode_step += 1
        self.decisions += 1
        next_observation = self._observation()
        reward = efficiency ** 3 if self.training.reward_mode == "paper" else efficiency - previous_score
        self.replay.add(self.observation, self.action, reward, next_observation,
                        self.episode_step >= self.training.horizon)
        self.observation = next_observation
        tc = self.training
        if (self.decisions >= tc.learning_starts and len(self.replay) >= tc.batch_size and
                self.decisions % tc.train_frequency == 0):
            self.loss = optimize_batch(self.online, self.target, self.optimizer,
                                       self.replay.sample(tc.batch_size, self.rng, self.device),
                                       tc.gamma, tc.double_dqn)
            self.updates += 1
        if self.decisions % tc.target_update_interval == 0:
            self.target.load_state_dict(self.online.state_dict())

    def state_dict(self):
        import torch
        excluded = {"online", "target", "optimizer", "replay"}
        state = {key: deepcopy(value) for key, value in self.__dict__.items() if key not in excluded}
        state.update(online=self.online.state_dict(), target=self.target.state_dict(),
                     optimizer=self.optimizer.state_dict(), replay=self.replay.state_dict(),
                     torch_rng=torch.get_rng_state(),
                     cuda_rng=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None)
        return state

    def load_state_dict(self, state):
        import torch
        excluded = {"online", "target", "optimizer", "replay", "torch_rng", "cuda_rng"}
        self.__dict__.update({key: deepcopy(value) for key, value in state.items() if key not in excluded})
        self.online.load_state_dict(state["online"])
        self.target.load_state_dict(state["target"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.replay.load_state_dict(state["replay"])
        torch.set_rng_state(state["torch_rng"])
        if state.get("cuda_rng") is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state["cuda_rng"])

    def diagnostics(self):
        return {"epsilon": self.epsilon, "decisions": self.decisions, "updates": self.updates,
                "loss": self.loss, "phase": "warmup" if self.decisions < self.training.learning_starts else "learning",
                "schedule_steps": self.training.total_steps}


def make_optimizer(name, n_cells, seed, schedule_steps, config=None, training=None):
    if name == "custom":
        from .custom_optimizer import CustomOptimizer
        return CustomOptimizer(n_cells, seed, config)
    constructors = {"random": BinaryOptimizer, "hillclimb": HillClimber,
                    "annealing": Annealing, "block_tabu": BlockTabu,
                    "population": Population, "surrogate": Surrogate, "dqn": DQN}
    if name not in constructors:
        raise ValueError(f"Unknown optimizer {name!r}")
    extra = {"schedule_steps": schedule_steps} if name in {"annealing", "dqn"} else {}
    if name == "dqn":
        extra["training"] = dict(training or {}, seed=seed)
        # algorithm_config may tune hyperparameters, but cannot replace the seed.
        config = {key: value for key, value in dict(config or {}).items() if key != "seed"}
    return constructors[name](n_cells, seed, config, **extra)
