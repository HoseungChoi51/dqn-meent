"""Bridge standalone x-major mask optimizers to the campaign's y-major MEENT grid."""
from __future__ import annotations

import numpy as np

from optimization_framework.contracts.problems import Proposal


LIBRARY_VERSION = "0.1.0"
METHODS = {"motif_surgery", "nested_fourier", "phenotype_de"}


def library_available():
    try:
        import mask_optimizers
    except ImportError:
        return False
    return mask_optimizers.__version__ == LIBRARY_VERSION


class MaskLibraryAdapter:
    """Keep the standalone optimizer's checkpoint and proposal identity intact."""

    def __init__(self, name, instance, parameters, seed):
        if name not in METHODS:
            raise ValueError("Method is not enabled for the campaign mask adapter")
        if not library_available():
            raise ValueError(f"Install mask-optimizers {LIBRARY_VERSION} to run this method")
        from mask_optimizers import MaskSpace, create_optimizer

        self.x = instance.configuration["grid_x"]
        self.y = instance.configuration["grid_y"]
        self.schema = instance.candidate_schema
        self.objective = instance.primary_objective
        self.optimizer = create_optimizer(name, MaskSpace((self.x, self.y), decoder="flrl_endpoints_v1"), parameters, seed)
        self.pending = None

    def _to_campaign(self, candidate):
        return np.asarray(candidate, dtype=np.uint8).reshape(self.x, self.y).T.reshape(-1).tolist()

    def _to_library(self, candidate):
        values = self.schema.canonicalize(candidate)
        return np.asarray(values, dtype=np.uint8).reshape(self.y, self.x).T.reshape(-1).tolist()

    def propose(self, max_candidates=1):
        if self.pending is not None:
            raise ValueError("Observe the outstanding mask proposal first")
        proposal = self.optimizer.propose(max_candidates)[0]
        result = Proposal(id=proposal.id, candidate=self._to_campaign(proposal.candidate), metadata=proposal.metadata)
        self.pending = result
        return [result]

    def observe(self, observations):
        if self.pending is None or len(observations) != 1 or observations[0].proposal_id != self.pending.id:
            raise ValueError("Observation does not match the outstanding mask proposal")
        observation = observations[0]
        if observation.status != "ok":
            raise ValueError(f"Mask evaluation failed: {observation.error}")
        if self.schema.canonicalize(observation.candidate) != self.pending.candidate:
            raise ValueError("Observed mask differs from the proposed mask")
        from mask_optimizers import Observation
        self.optimizer.observe([Observation(observation.proposal_id, self._to_library(observation.candidate),
            self.objective.utility(observation.objectives[self.objective.name]))])
        self.pending = None

    def checkpoint(self):
        return {"library_version": LIBRARY_VERSION, "optimizer": self.optimizer.checkpoint(),
                "pending": self.pending.model_dump(mode="json") if self.pending else None}

    def restore(self, checkpoint):
        if checkpoint["library_version"] != LIBRARY_VERSION:
            raise ValueError("Mask optimizer library version changed")
        self.optimizer.restore(checkpoint["optimizer"])
        self.pending = Proposal(**checkpoint["pending"]) if checkpoint["pending"] else None

    def inspect(self):
        return {**self.optimizer.inspect(), "library": "mask-optimizers", "library_version": LIBRARY_VERSION,
                "campaign_flatten_order": "y-major C order"}

    def export_artifacts(self):
        return self.optimizer.export_artifacts()

    def close(self):
        self.optimizer.close()
