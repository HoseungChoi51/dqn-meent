"""Small, explicit Double DQN components for discrete material flips."""
from pathlib import Path
from typing import Sequence
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


class QNetwork(nn.Module):
    """An MLP Q(s, a); each output corresponds to flipping one design cell."""

    def __init__(self, obs_dim: int, n_actions: int,
                 hidden_sizes: Sequence[int] = (128, 128),
                 activation: str = "relu", initialization: str = "default"):
        super().__init__()
        layers: list[nn.Module] = []
        previous = obs_dim
        for width in hidden_sizes:
            nonlinear = nn.ReLU() if activation == "relu" else nn.LeakyReLU(0.1)
            layers.extend((nn.Linear(previous, width), nonlinear))
            previous = width
        layers.append(nn.Linear(previous, n_actions))
        self.network = nn.Sequential(*layers)
        if initialization == "orthogonal":
            for layer in self.network:
                if isinstance(layer, nn.Linear):
                    nn.init.orthogonal_(layer.weight, np.sqrt(2))
                    nn.init.zeros_(layer.bias)

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        return self.network(observations)


class ReplayBuffer:
    """Uniform transition replay with compact, resumable state."""

    def __init__(self, capacity: int, obs_dim: int):
        if capacity < 1 or obs_dim < 1:
            raise ValueError("Replay dimensions must be positive")
        self.capacity = capacity
        self.obs_dim = obs_dim
        self.position = 0
        self.size = 0
        self.observations = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.next_observations = np.zeros_like(self.observations)
        self.actions = np.zeros(capacity, dtype=np.int64)
        self.rewards = np.zeros(capacity, dtype=np.float32)
        self.terminated = np.zeros(capacity, dtype=np.bool_)

    def __len__(self):
        return self.size

    def add(self, observation, action, reward, next_observation, terminated):
        i = self.position
        self.observations[i] = observation
        self.next_observations[i] = next_observation
        self.actions[i] = action
        self.rewards[i] = reward
        self.terminated[i] = terminated
        self.position = (i + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def sample(self, batch_size: int, rng: np.random.Generator,
               device: str | torch.device = "cpu", random_streams="numpy") -> dict[str, torch.Tensor]:
        if batch_size < 1 or self.size < batch_size:
            raise ValueError("Replay must contain at least batch_size transitions")
        if random_streams == "python_numpy":
            indices = np.asarray(random.sample(range(self.size), batch_size))
            if self.size == self.capacity:
                indices = (indices + self.position) % self.capacity
        else:
            indices = rng.choice(self.size, size=batch_size, replace=False)
        return {key: torch.as_tensor(getattr(self, key)[indices], device=device)
                for key in ("observations", "next_observations", "actions",
                            "rewards", "terminated")}

    def state_dict(self) -> dict:
        # Unused capacity is omitted, keeping early checkpoints small.
        state = {"capacity": self.capacity, "obs_dim": self.obs_dim,
                 "position": self.position, "size": self.size}
        state.update({key: getattr(self, key)[:self.size].copy()
                      for key in ("observations", "next_observations", "actions",
                                  "rewards", "terminated")})
        return state

    def load_state_dict(self, state: dict):
        if (state["capacity"], state["obs_dim"]) != (self.capacity, self.obs_dim):
            raise ValueError("Replay dimensions differ from checkpoint")
        self.position, self.size = int(state["position"]), int(state["size"])
        if not (0 <= self.size <= self.capacity and 0 <= self.position < self.capacity):
            raise ValueError("Invalid replay checkpoint counters")
        for key in ("observations", "next_observations", "actions", "rewards", "terminated"):
            getattr(self, key)[:self.size] = state[key]


@torch.no_grad()
def compute_td_target(rewards: torch.Tensor, terminated: torch.Tensor,
                      online_next_q: torch.Tensor, target_next_q: torch.Tensor,
                      gamma: float, double_dqn: bool = True) -> torch.Tensor:
    """Mask genuine terminal states; select online/evaluate target for Double DQN.

    ``argmax`` uses the first maximal index on ties. Behavior-policy ties are
    randomized separately. The target has no autograd history.
    """
    if double_dqn:
        next_actions = online_next_q.argmax(dim=1, keepdim=True)
        next_values = target_next_q.gather(1, next_actions).squeeze(1)
    else:
        next_values = target_next_q.max(dim=1).values
    # where also prevents NaN/inf terminal estimates from contaminating targets.
    bootstrap = torch.where(terminated.bool(), torch.zeros_like(next_values), next_values)
    return rewards + gamma * bootstrap


@torch.no_grad()
def greedy_action(model: nn.Module, observation: np.ndarray,
                  rng: np.random.Generator | None = None,
                  device: str | torch.device = "cpu") -> int:
    """Greedy action; use seeded random tie breaking during training."""
    obs = torch.as_tensor(observation, dtype=torch.float32, device=device).unsqueeze(0)
    q = model(obs)[0]
    if not torch.isfinite(q).all():
        raise FloatingPointError("Nonfinite policy Q values")
    if rng is None:
        return int(q.argmax().item())
    ties = torch.nonzero(q == q.max(), as_tuple=False).flatten().cpu().numpy()
    return int(rng.choice(ties))


def optimize_batch(online: nn.Module, target: nn.Module,
                   optimizer: torch.optim.Optimizer, batch: dict[str, torch.Tensor],
                   gamma: float, double_dqn: bool = True,
                   gradient_clip_norm: float | None = 10.0) -> float:
    with torch.no_grad():
        targets = compute_td_target(batch["rewards"], batch["terminated"],
                                    online(batch["next_observations"]),
                                    target(batch["next_observations"]), gamma, double_dqn)
    predictions = online(batch["observations"]).gather(1, batch["actions"][:, None]).squeeze(1)
    loss = F.smooth_l1_loss(predictions, targets)
    if not torch.isfinite(loss):
        raise FloatingPointError("Nonfinite DQN loss")
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    if gradient_clip_norm is not None:
        nn.utils.clip_grad_norm_(online.parameters(), max_norm=gradient_clip_norm, error_if_nonfinite=True)
    elif any(not torch.isfinite(p.grad).all() for p in online.parameters() if p.grad is not None):
        raise FloatingPointError("Nonfinite DQN gradient")
    optimizer.step()
    return float(loss.detach().cpu())




@torch.no_grad()
def update_target(online: nn.Module, target: nn.Module, tau: float):
    if tau == 1:
        target.load_state_dict(online.state_dict())
    else:
        for destination, source in zip(target.parameters(), online.parameters(), strict=True):
            destination.copy_((1 - tau) * destination + tau * source)
