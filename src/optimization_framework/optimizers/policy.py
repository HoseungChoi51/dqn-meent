"""Read-only inference from a declared policy; the worker owns all evaluation."""
from copy import deepcopy

import numpy as np

from optimization_framework.contracts.experiments import ArtifactReference
from .config import TrainConfig


POLICY_FORMAT = {"kind": "policy", "format": "numpy-state-dict-v1", "metadata": {"implementation": "dqn"}}


def legacy_inference(rollout):
    """Compatibility projection; never reserialize an existing frozen schedule."""
    return "dqn_policy:v1", {"epsilon": rollout.epsilon, "horizon": rollout.horizon, "tie_break": "first"}


class DQNInference:
    def describe(self):
        return {"id": "dqn_policy:v1", "title": "Frozen DQN policy episode", "artifact": POLICY_FORMAT,
            "representations": ["binary"], "constraints": False,
            "parameter_schema": {"type": "object", "additionalProperties": False, "properties": {
                "epsilon": {"type": "number", "minimum": 0, "maximum": 1, "default": 0, "title": "Exploration probability"},
                "horizon": {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 128, "title": "Episode decisions"},
                "tie_break": {"type": "string", "enum": ["first", "random"], "default": "first", "title": "Action ties"}}},
            "capabilities": {"completion_units": ["evaluation_requests", "optimizer_decisions"]}}

    def prepare(self, instance, parameters, *, asset=None):
        horizon = 128
        if asset is not None:
            metadata = asset["payload"].get("metadata", {})
            if len(asset["artifacts"]) != 1 or metadata.get("n_actions") != instance.candidate_schema.dimensions:
                raise ValueError("Policy artifacts or dimensions are incompatible with this target problem")
            profile = TrainConfig(**metadata["training"])
            if metadata.get("observation_size") != instance.candidate_schema.dimensions + int(profile.episode_mode == "finite"):
                raise ValueError("Policy observation dimensions differ from its frozen training profile")
            horizon = profile.horizon
        return {"epsilon": parameters.get("epsilon", 0.), "horizon": parameters.get("horizon", horizon),
                "tie_break": parameters.get("tie_break", "first")}

    def procedure(self, instance, parameters):
        horizon = parameters["horizon"]
        return {"max_steps": horizon + 1, "schedule_steps": horizon,
                "completion": {"unit": "optimizer_decisions", "count": horizon}}

    def create(self, instance, parameters, seed, asset, artifact_store):
        from .lifecycle import AskTellAdapter
        optimizer = AskTellAdapter(lambda descriptor, config, seed, assets: FrozenPolicy(instance, config, seed, assets[0], artifact_store))
        optimizer.initialize(instance.descriptor(), parameters, seed, [asset])
        return optimizer


class FrozenPolicy:
    def __init__(self, instance, parameters, seed, asset, artifact_store):
        import torch
        from .dqn_network import QNetwork
        metadata = asset["payload"]["metadata"]
        self.profile = TrainConfig(**metadata["training"])
        self.n_cells = instance.candidate_schema.dimensions
        self.asset_digest = asset["content_hash"]
        self.parameters = dict(parameters)
        self.rng = np.random.default_rng(seed)
        self.horizon = int(parameters.get("horizon", self.profile.horizon))
        self.epsilon = float(parameters.get("epsilon", 0))
        self.tie_break = parameters.get("tie_break", "first")
        if self.horizon < 1 or not 0 <= self.epsilon <= 1 or self.tie_break not in {"first", "random"}:
            raise ValueError("Invalid frozen-policy horizon, epsilon or tie policy")
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            self.model = QNetwork(metadata["observation_size"], self.n_cells, self.profile.hidden_sizes,
                                  self.profile.activation, self.profile.initialization)
        with artifact_store.open(ArtifactReference(**asset["artifacts"][0])) as stream, np.load(stream, allow_pickle=False) as arrays:
            weights = {key: torch.from_numpy(arrays[key].copy()) for key in arrays.files}
        if any(not torch.isfinite(value).all() for value in weights.values()):
            raise ValueError("Policy has nonfinite weights")
        self.model.load_state_dict(weights)
        self.model.eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        self.current, self.score = None, None
        self.action = None
        self.decisions = self.episode_step = self.count = 0
        self.discounted_return = 0.

    def ask(self):
        from .dqn_network import greedy_action
        if self.current is None or self.episode_step >= self.horizon:
            self.action = None
            return np.ones(self.n_cells, dtype=np.uint8)
        observation = self.current.astype(np.float32) * 2 - 1
        if self.profile.episode_mode == "finite":
            observation = np.r_[observation, (self.horizon - self.episode_step) / self.horizon].astype(np.float32)
        if self.rng.random() < self.epsilon:
            self.action = int(self.rng.integers(self.n_cells))
        else:
            self.action = greedy_action(self.model, observation, self.rng if self.tie_break == "random" else None, "cpu")
        candidate = self.current.copy()
        candidate[self.action] ^= 1
        return candidate

    def tell(self, candidate, utility):
        previous = self.score
        self.current, self.score = candidate.copy(), utility
        self.count += 1
        if self.action is None:
            self.episode_step = 0
        else:
            reward = utility ** 3 if self.profile.reward_mode == "paper" else utility - previous
            self.discounted_return += self.profile.gamma ** self.episode_step * reward
            self.episode_step += 1
            self.decisions += 1

    def diagnostics(self):
        return {"phase": "frozen_policy_evaluation", "decisions": self.decisions, "episode_step": self.episode_step,
                "action": self.action, "epsilon": self.epsilon, "updates": 0, "adaptation": "forbidden",
                "discounted_return_sum": self.discounted_return, "policy_digest": self.asset_digest}

    def state_dict(self):
        return deepcopy({key: value for key, value in self.__dict__.items() if key != "model"})

    def load_state_dict(self, state):
        if state["asset_digest"] != self.asset_digest or state["parameters"] != self.parameters:
            raise ValueError("Policy or inference procedure changed since checkpoint")
        self.__dict__.update(deepcopy(state))

    def export_artifacts(self):
        return [{"kind": "learner_diagnostics", "media_type": "application/json", "data": self.diagnostics()}]
