"""Validated, JSON-serializable experiment configuration (lengths in nm)."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import math


@dataclass(frozen=True)
class PhysicsConfig:
    n_cells: int = 64
    wavelength_nm: float = 1100.0
    deflection_angle_deg: float = 50.0
    thickness_nm: float = 325.0
    n_incident: float = 1.45
    n_exit: float = 1.0
    silicon_n: float = 3.551726470588235
    silicon_k: float = 0.0
    material: str = "constant"
    fourier_order: int = 15
    cache_size: int = 50000
    energy_tolerance: float = 1e-6

    def __post_init__(self):
        if self.n_cells < 2 or self.fourier_order < 1 or self.cache_size < 0:
            raise ValueError("Require n_cells>=2, fourier_order>=1, cache_size>=0")
        if not 0 < self.deflection_angle_deg < 90:
            raise ValueError("deflection_angle_deg must be between 0 and 90")
        if not all(math.isfinite(x) and x > 0 for x in
                   (self.wavelength_nm, self.thickness_nm, self.n_incident,
                    self.n_exit, self.silicon_n, self.energy_tolerance)):
            raise ValueError("Lengths, real indices, tolerance must be finite and positive")
        if not math.isfinite(self.silicon_k) or self.silicon_k < 0:
            raise ValueError("silicon_k must be a nonnegative extinction coefficient")
        if self.material not in {"constant", "meent_green"}:
            raise ValueError("material must be constant or meent_green")

    @property
    def period_nm(self):
        return self.wavelength_nm / (self.n_exit * math.sin(math.radians(self.deflection_angle_deg)))


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

    def __post_init__(self):
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


@dataclass(frozen=True)
class ExperimentConfig:
    physics: PhysicsConfig = field(default_factory=PhysicsConfig)
    training: TrainConfig = field(default_factory=TrainConfig)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        unknown = set(data) - {"physics", "training"}
        if unknown:
            raise ValueError(f"Unknown config sections: {unknown}")
        return cls(PhysicsConfig(**data.get("physics", {})), TrainConfig(**data.get("training", {})))

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))
