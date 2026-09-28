"""Problem, proposal, and trusted evaluation contracts (scalar objectives, v1)."""
from __future__ import annotations

import math
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from .base import Contract, content_hash


class Objective(Contract):
    name: str
    direction: Literal["minimize", "maximize"]
    units: str = "dimensionless"

    def utility(self, value: float) -> float:
        """Frozen sign transform for maximization-only legacy optimizers."""
        if not math.isfinite(value):
            raise ValueError("An objective value must be finite")
        return value if self.direction == "maximize" else -value

    def better(self, left: float, right: float | None) -> bool:
        return right is None or self.utility(left) > self.utility(right)


class Constraint(Contract):
    """A public linear feasibility constraint; adapters can supply named checks."""
    name: str
    coefficients: list[float]
    relation: Literal["le", "ge", "eq"] = "le"
    bound: float
    tolerance: float = Field(default=1e-9, ge=0)

    def satisfied(self, values: list[float]) -> bool:
        total = sum(a * b for a, b in zip(self.coefficients, values, strict=True))
        if self.relation == "eq":
            return abs(total - self.bound) <= self.tolerance
        return total <= self.bound + self.tolerance if self.relation == "le" else total >= self.bound - self.tolerance


class CandidateSchema(Contract):
    representation: Literal["binary", "discrete", "continuous"]
    dimensions: int = Field(ge=1, le=1000000)
    bounds: list[tuple[float, float]] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_domain(self):
        if self.representation == "continuous":
            if len(self.bounds) != self.dimensions or any(a >= b for a, b in self.bounds):
                raise ValueError("Continuous candidates need one finite increasing bound per dimension")
        if self.representation == "discrete" and not self.values:
            raise ValueError("Discrete candidates require permitted values")
        if any(len(c.coefficients) != self.dimensions for c in self.constraints):
            raise ValueError("Constraint dimension does not match candidate schema")
        return self

    def canonicalize(self, candidate: Any) -> list[int | float]:
        if hasattr(candidate, "tolist"):
            candidate = candidate.tolist()
        if not isinstance(candidate, (list, tuple)) or len(candidate) != self.dimensions:
            raise ValueError(f"Candidate must be a vector of length {self.dimensions}")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in candidate):
            raise ValueError("Candidate entries must be finite numbers")
        if self.representation == "binary":
            if any(x not in (0, 1) for x in candidate):
                raise ValueError("Candidate entries must be binary 0 or 1")
            result = [int(x) for x in candidate]
        elif self.representation == "discrete":
            if any(x not in self.values for x in candidate):
                raise ValueError("Candidate contains a value outside the declared discrete domain")
            result = list(candidate)
        else:
            if any(x < lo or x > hi for x, (lo, hi) in zip(candidate, self.bounds, strict=True)):
                raise ValueError("Candidate is outside the declared bounds")
            result = [float(x) for x in candidate]
        failed = [c.name for c in self.constraints if not c.satisfied(result)]
        if failed:
            raise ValueError("Candidate violates constraints: " + ", ".join(failed))
        return result


class ProblemDefinition(Contract):
    id: str
    version: str
    name: str
    evaluator_id: str
    evaluator_version: str
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    fidelity_schema: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=lambda: {"cpu_threads": 1})
    validation_recipes: list[str] = Field(default_factory=list)
    recipe_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)
    renderer: str | None = None


class ProblemInstance(Contract):
    definition_id: str
    definition_version: str
    evaluator_id: str
    evaluator_version: str
    configuration: dict[str, Any]
    candidate_schema: CandidateSchema
    primary_objective: Objective
    extra_metrics: list[Objective] = Field(default_factory=list)
    public_descriptor: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    fidelity: dict[str, Any] = Field(default_factory=dict)
    scientific_identity: str

    @property
    def evaluation_identity(self) -> str:
        return content_hash({"instance": self.scientific_identity, "fidelity": self.fidelity,
                             "evaluator": [self.evaluator_id, self.evaluator_version]})

    def descriptor(self) -> dict:
        """Only declared public information is sent to optimizer packages."""
        return {"scientific_identity": self.scientific_identity,
                "candidate_schema": self.candidate_schema.model_dump(mode="json"),
                "primary_objective": self.primary_objective.model_dump(mode="json"),
                "capabilities": self.capabilities, "public": self.public_descriptor}


class Proposal(Contract):
    id: str
    candidate: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evaluation(Contract):
    objectives: dict[str, float]
    constraints: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    solver_executions: int = Field(default=1, ge=0)
    cache_hit: bool = False


class Observation(Contract):
    id: str
    experiment_id: str
    attempt_id: str
    request_id: str
    proposal_id: str
    candidate: Any
    status: Literal["ok", "invalid_candidate", "evaluation_failed", "uncertain"]
    objectives: dict[str, float] = Field(default_factory=dict)
    constraints: dict[str, bool] = Field(default_factory=dict)
    fidelity: dict[str, Any] = Field(default_factory=dict)
    evaluator_identity: str
    costs: dict[str, float | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class Evaluator(Protocol):
    def evaluate(self, candidate: list[int | float]) -> Evaluation: ...
    def checkpoint(self) -> dict: ...
    def restore(self, state: dict) -> None: ...


class ProblemAdapter(Protocol):
    def describe(self) -> ProblemDefinition: ...
    def resolve(self, configuration: dict, fidelity: dict | None = None) -> ProblemInstance: ...
    def evaluator(self, instance: ProblemInstance) -> Evaluator: ...
