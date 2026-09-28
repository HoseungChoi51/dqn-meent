"""A reviewed test extension for a continuous constant-point policy format.

It is installed by the test's entry-point fixture, captured with the parent, and
executed in the ordinary worker. It has no DQN, binary-action or reset semantics.
"""
import json

from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.contracts.problems import Proposal


class Adapter:
    def describe(self):
        return {"id": "constant_point:v1", "title": "Constant point policy", "representations": ["continuous"], "constraints": True,
            "artifact": {"kind": "policy", "format": "constant-point:v1"},
            "parameter_schema": {"type": "object", "additionalProperties": False, "properties": {
                "repeats": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3}}},
            "capabilities": {"completion_units": ["evaluation_requests"]}}

    def prepare(self, instance, parameters, *, asset=None):
        if asset is not None and (len(asset["artifacts"]) != 1 or asset["payload"]["metadata"]["dimensions"] != instance.candidate_schema.dimensions):
            raise ValueError("Constant-point policy dimensions do not match")
        return {"repeats": parameters.get("repeats", 3)}

    def procedure(self, instance, parameters):
        count = parameters["repeats"]
        return {"max_steps": count, "schedule_steps": count, "completion": {"unit": "evaluation_requests", "count": count}}

    def create(self, instance, parameters, seed, asset, artifact_store):
        with artifact_store.open(ArtifactReference(**asset["artifacts"][0])) as stream:
            point = instance.candidate_schema.canonicalize(json.load(stream)["point"])
        return ConstantPolicy(point, asset["content_hash"])


class ConstantPolicy:
    supports_failure_observations = False

    def __init__(self, point, asset_digest):
        self.point, self.asset_digest = point, asset_digest
        self.count, self.pending = 0, None

    def propose(self, max_candidates=1):
        if self.pending is not None:
            raise ValueError("Observe the previous proposal first")
        self.pending = str(self.count)
        return [Proposal(id=self.pending, candidate=self.point)]

    def observe(self, observations):
        if [value.proposal_id for value in observations] != [self.pending]:
            raise ValueError("Observation identity mismatch")
        self.count += 1
        self.pending = None

    def inspect(self):
        return {"updates": 0, "adaptation": "forbidden", "observations": self.count}

    def checkpoint(self):
        return {"count": self.count, "pending": self.pending, "asset_digest": self.asset_digest}

    def restore(self, state):
        if state["asset_digest"] != self.asset_digest:
            raise ValueError("Inference input changed")
        self.count, self.pending = state["count"], state["pending"]

    def export_artifacts(self):
        return []
