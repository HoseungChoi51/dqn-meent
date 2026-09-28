"""Common lifecycle for bundled methods and the historical ask/tell contract."""
from __future__ import annotations

from copy import deepcopy
import numpy as np

from optimization_framework.contracts.problems import CandidateSchema, Objective, Proposal


class AskTellAdapter:
    """Apply an explicit objective sign transform while retaining raw observations."""
    def __init__(self, factory, *, failures=False):
        self.factory = factory
        self.supports_failure_observations = failures
        self.pending = []
        self.sequence = 0

    def initialize(self, problem_descriptor, parameters, seed, declared_assets):
        self.schema = CandidateSchema(**problem_descriptor["candidate_schema"])
        self.objective = Objective(**problem_descriptor["primary_objective"])
        self.optimizer = self.factory(problem_descriptor, parameters, seed, declared_assets)

    def propose(self, max_candidates=1):
        if self.pending:
            raise ValueError("The previous proposal batch must be observed before proposing again")
        if max_candidates < 1:
            raise ValueError("Proposal capacity must be positive")
        self.sequence += 1
        candidate = self.optimizer.ask()
        if hasattr(candidate, "tolist"):
            candidate = candidate.tolist()
        self.pending = [Proposal(id=f"proposal_{self.sequence}", candidate=candidate)]
        return self.pending.copy()

    def observe(self, observations):
        if [o.proposal_id for o in observations] != [p.id for p in self.pending]:
            raise ValueError("Observations must match the outstanding proposals in order")
        for observation in observations:
            if observation.status != "ok":
                raise ValueError(f"This optimizer cannot consume {observation.status}: {observation.error}")
            candidate = np.asarray(observation.candidate, dtype=np.uint8 if self.schema.representation == "binary" else float)
            self.optimizer.tell(candidate, self.objective.utility(observation.objectives[self.objective.name]))
        self.pending = []

    def checkpoint(self):
        return {"contract_version": 1, "sequence": self.sequence,
                "pending": [p.model_dump() for p in self.pending], "state": self.optimizer.state_dict()}

    def restore(self, manifest):
        if manifest["contract_version"] != 1:
            raise ValueError("Unsupported optimizer checkpoint contract")
        self.sequence = manifest["sequence"]
        self.pending = [Proposal(**p) for p in manifest["pending"]]
        self.optimizer.load_state_dict(manifest["state"])

    def inspect(self):
        return self.optimizer.diagnostics()

    def export_artifacts(self):
        return self.optimizer.export_artifacts() if hasattr(self.optimizer, "export_artifacts") else []

    def close(self):
        if hasattr(self.optimizer, "close"):
            self.optimizer.close()


class BoundedSearch:
    """Seeded reference search with optional coordinate improvement and restarts."""
    def __init__(self, schema, parameters, seed, *, coordinate=False):
        self.schema = schema
        self.rng = np.random.default_rng(seed)
        self.parameters = dict(parameters)
        self.coordinate = coordinate
        self.best = None
        self.best_score = None
        self.count = 0
        self.radius = float(parameters.get("radius", .2))
        if not 0 < self.radius <= 1:
            raise ValueError("radius must be in (0, 1]")

    def ask(self):
        for _ in range(10000):
            if self.schema.representation == "discrete":
                candidate = self.rng.choice(self.schema.values, size=self.schema.dimensions)
            elif self.schema.representation == "binary":
                candidate = self.rng.integers(0, 2, size=self.schema.dimensions, dtype=np.uint8)
            else:
                bounds = np.asarray(self.schema.bounds)
                candidate = self.rng.uniform(bounds[:, 0], bounds[:, 1])
                if self.coordinate and self.best is not None and self.count % (2 * self.schema.dimensions + 1):
                    candidate = self.best.copy()
                    index = int(self.rng.integers(self.schema.dimensions))
                    lo, hi = bounds[index]
                    scale = self.radius * (hi-lo) / (1 + self.count / (10 * self.schema.dimensions))
                    candidate[index] = np.clip(candidate[index] + self.rng.normal(0, scale), lo, hi)
            try:
                self.schema.canonicalize(candidate)
                return candidate
            except ValueError:
                continue
        raise ValueError("Could not produce a feasible candidate within the declared proposal limit")

    def tell(self, candidate, utility):
        self.count += 1
        if self.best_score is None or utility > self.best_score:
            self.best_score = float(utility)
            self.best = candidate.copy()

    def state_dict(self):
        return deepcopy(self.__dict__)

    def load_state_dict(self, state):
        self.__dict__.update(deepcopy(state))

    def diagnostics(self):
        return {"decisions": self.count, "phase": "search"}
