"""An exact historical reference and a comparison rule frozen before execution."""
from typing import Literal

from pydantic import Field

from .base import Contract
from .bundles import RecordKey


class ResultComparisonRule(Contract):
    rule: Literal["result_v1"] = "result_v1"
    objective_absolute_tolerance: float = Field(default=1e-10, ge=0)
    objective_relative_tolerance: float = Field(default=1e-8, ge=0)
    compare_candidate: bool = True
    candidate_absolute_tolerance: float = Field(default=0, ge=0)
    candidate_relative_tolerance: float = Field(default=0, ge=0)


class ReproductionIntent(Contract):
    reference: RecordKey
    comparison: ResultComparisonRule = Field(default_factory=ResultComparisonRule)


class ReproductionDraftInput(ReproductionIntent):
    task_id: str
    study_id: str | None = None
    title: str = Field(default="Reproduce historical result", min_length=1, max_length=300)
    wall_seconds: float = Field(default=60, gt=0, le=86400)
    reuse_decision_ids: list[str] = Field(default_factory=list)


class ReproductionCompareInput(Contract):
    trial_id: str
