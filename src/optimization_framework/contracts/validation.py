"""Requirements, measurements and waivers are separate immutable records."""
from typing import Any, Literal
from pydantic import Field

from .base import Contract


ValidationKind = Literal["implementation_correctness", "evaluator_correctness", "solution_fidelity", "learner_diagnostics", "scientific_confirmation"]


class ValidationRequirement(Contract):
    id: str
    campaign_id: str
    study_id: str
    subject_id: str
    subject_digest: str
    kind: ValidationKind
    recipe_id: str
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence_requirements: list[str] = Field(default_factory=list)
    authority: str
    created_at: str


class ValidationResult(Contract):
    id: str
    campaign_id: str
    requirement_id: str
    subject_digest: str
    recipe_id: str
    verdict: Literal["passed", "failed", "inconclusive", "error"]
    evidence_ids: list[str] = Field(min_length=1)
    measurements: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    producer: Literal["trusted_service", "researcher", "imported"]
    authority: str
    created_at: str


class Waiver(Contract):
    id: str
    campaign_id: str
    study_id: str
    requirement_id: str
    subject_digest: str
    recipe_id: str
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    authority: str
    authority_kind: Literal["researcher", "manager"]
    invalidation_conditions: list[Literal["subject_version_changed", "recipe_changed", "study_changed", "new_failed_result"]] = Field(
        default_factory=lambda: ["subject_version_changed", "recipe_changed", "study_changed", "new_failed_result"])
    created_at: str


class WaiverRevocation(Contract):
    id: str
    campaign_id: str
    waiver_id: str
    rationale: str = Field(min_length=1)
    authority: str
    created_at: str
