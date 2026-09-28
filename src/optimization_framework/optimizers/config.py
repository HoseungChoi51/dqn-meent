"""Validated learner parameters shared by all problem adapters."""
from dataclasses import asdict, dataclass, fields
import math

@dataclass(frozen=True)
class TrainConfig:
    total_steps: int = 10000
    horizon: int = 128
    seed: int = 0
    learning_rate: float = 1e-4
    gamma: float = 0.99
    batch_size: int = 64
    buffer_size: int = 100000
    learning_starts: int = 512
    train_frequency: int = 1
    target_update_interval: int = 250
    epsilon_start: float = 0.9
    epsilon_end: float = 0.01
    exploration_fraction: float = 0.8
    hidden_sizes: tuple[int, ...] = (128, 128)
    double_dqn: bool = True
    reward_mode: str = "paper"
    device: str = "cpu"
    checkpoint_interval: int = 2000
    torch_threads: int = 1
    episode_mode: str = "finite"
    activation: str = "relu"
    initialization: str = "default"
    gradient_clip_norm: float | None = 10.0
    target_update_tau: float = 1.0
    epsilon_schedule: str = "linear"
    greedy_tie_break: str = "random"
    random_streams: str = "numpy"
    learning_starts_strict: bool = False

    def __post_init__(self):
        for name in ("total_steps", "horizon", "seed", "batch_size", "buffer_size", "learning_starts",
                     "train_frequency", "target_update_interval", "checkpoint_interval", "torch_threads"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if not 0 <= self.seed <= 2**32 - 1:
            raise ValueError("seed must be an unsigned 32-bit integer")
        for name in ("learning_rate", "gamma", "epsilon_start", "epsilon_end", "exploration_fraction", "target_update_tau"):
            if type(getattr(self, name)) not in {float, int} or not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number")
        for name in ("double_dqn", "learning_starts_strict"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.hidden_sizes, (tuple, list)) or any(type(n) is not int for n in self.hidden_sizes):
            raise ValueError("hidden_sizes must contain integers")
        for name in ("total_steps", "horizon", "batch_size", "buffer_size",
                     "train_frequency", "target_update_interval", "checkpoint_interval", "torch_threads"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.learning_starts < 0 or self.buffer_size < self.batch_size:
            raise ValueError("Invalid replay size or learning_starts")
        if not 0 <= self.gamma <= 1 or not 0 <= self.epsilon_end <= self.epsilon_start <= 1:
            raise ValueError("Invalid discount or epsilon schedule")
        if not 0 < self.exploration_fraction <= 1 or self.learning_rate <= 0:
            raise ValueError("Invalid exploration_fraction or learning_rate")
        if not self.hidden_sizes or any(n < 1 for n in self.hidden_sizes):
            raise ValueError("hidden_sizes must be positive")
        if self.reward_mode not in {"paper", "difference"}:
            raise ValueError("reward_mode must be paper or difference")
        if self.reward_mode == "difference" and self.gamma != 1:
            raise ValueError("difference reward requires gamma=1 for telescoping return")
        for name, choices in {
            "episode_mode": {"finite", "continuing"},
            "activation": {"relu", "leaky_relu"},
            "initialization": {"default", "orthogonal"},
            "epsilon_schedule": {"linear", "paper"},
            "greedy_tie_break": {"random", "first"},
            "random_streams": {"numpy", "python_numpy"},
        }.items():
            if getattr(self, name) not in choices:
                raise ValueError(f"Invalid {name}: {getattr(self, name)}")
        if not math.isfinite(self.target_update_tau) or not 0 < self.target_update_tau <= 1:
            raise ValueError("target_update_tau must be in (0,1]")
        if self.gradient_clip_norm is not None and (
            type(self.gradient_clip_norm) not in {float, int} or not math.isfinite(self.gradient_clip_norm) or self.gradient_clip_norm <= 0
        ):
            raise ValueError("gradient_clip_norm must be positive or null")
        if self.episode_mode == "continuing" and (self.gamma >= 1 or self.reward_mode != "paper"):
            raise ValueError("continuing mode requires discounted paper reward")


def normalize_training(values):
    """Validate before normalizing JSON's equivalent integer/float spellings."""
    config = TrainConfig(**values)
    result = asdict(config)
    for field in fields(config):
        if field.type in (float, float | None) and result[field.name] is not None:
            result[field.name] = float(result[field.name])
    return result
