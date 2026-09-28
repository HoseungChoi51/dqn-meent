"""Reusable evidence and inputs, with explicit exposure and cost provenance."""
from typing import Any, Literal
from pydantic import Field, model_validator

from .base import Contract
from .experiments import ArtifactReference


AssetKind = Literal["implementation", "policy", "solution", "solution_archive", "dataset", "protocol", "finding", "validation_evidence", "fields", "learner_diagnostics"]


class CostSlice(Contract):
    source_id: str
    start: int = Field(default=0, ge=0)
    stop: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.stop < self.start:
            raise ValueError("Cost intervals are half-open [start, stop), with stop >= start")
        return self


class CostEvent(Contract):
    id: str
    campaign_id: str
    source_id: str
    attempt_id: str | None = None
    ordinal: int = Field(ge=0)
    category: Literal["evaluation", "implementation", "model", "diagnostic", "overhead", "imported"]
    quantities: dict[str, float | None]
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["measured", "uncertain", "historical"] = "measured"
    created_at: str

    @model_validator(mode="after")
    def nonnegative(self):
        if any(value is not None and value < 0 for value in self.quantities.values()):
            raise ValueError("Resource expenditure cannot be negative")
        return self


class CostSnapshot(Contract):
    """A captured cumulative service receipt at an immutable cost boundary."""
    id: str
    campaign_id: str
    source_id: str
    stop: int = Field(ge=1)
    quantities: dict[str, float | None]
    evidence_ids: list[str] = Field(default_factory=list)
    receipt: dict[str, Any] = Field(default_factory=dict)
    work_cursor: str | None = None
    created_at: str

    @model_validator(mode="after")
    def nonnegative(self):
        if any(value is not None and value < 0 for value in self.quantities.values()):
            raise ValueError("Cumulative expenditure cannot be negative")
        return self


class CostReconciliation(Contract):
    """Additional evidence for a prefix total; original events remain intact."""
    id: str
    campaign_id: str
    source_id: str
    stop: int = Field(ge=1)
    event_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    quantities: dict[str, float]
    evidence_ids: list[str] = Field(min_length=1)
    authority: str
    rationale: str = Field(min_length=1)
    created_at: str

    @model_validator(mode="after")
    def nonnegative(self):
        if not self.quantities or any(value < 0 for value in self.quantities.values()):
            raise ValueError("A reconciliation requires nonnegative measured quantities")
        return self


class CostReconcileInput(Contract):
    asset_id: str
    source_id: str
    stop: int = Field(ge=1)
    quantities: dict[str, float]
    evidence_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4000)


class Asset(Contract):
    id: str
    campaign_id: str
    kind: AssetKind
    title: str
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    producer_id: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)
    costs: list[CostSlice] = Field(default_factory=list)
    cost_provenance: Literal["complete", "partial", "unknown"] = "unknown"
    exposure_status: Literal["known", "unknown"] = "unknown"
    exposed_instance_ids: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    validation_ids: list[str] = Field(default_factory=list)
    availability: Literal["available", "external", "unavailable"] = "available"
    authority: str
    created_at: str


class ReuseDecision(Contract):
    id: str
    campaign_id: str
    study_id: str
    asset_id: str
    decision: Literal["reuse", "decline", "reference"]
    intended_use: Literal["optimizer_input", "manager_evidence", "procedure"]
    rationale: str = Field(min_length=1)
    authority: str
    created_at: str
    consequences: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def distinguish_reference(self):
        if self.decision == "reference" and self.intended_use == "optimizer_input":
            raise ValueError("Reference evidence is not permission to expose an input to an optimizer")
        return self
