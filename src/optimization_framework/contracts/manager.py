"""Versioned campaign memory, independent of provider conversation state."""
from typing import Literal

from pydantic import Field

from .base import Contract


class StudyContext(Contract):
    id: str
    goal: str
    scope: str


class DelegationContext(Contract):
    autonomy: Literal["manual", "guided", "delegated"]
    per_experiment_seconds: float = Field(gt=0)
    authority_hash: str


class ResourceContext(Contract):
    compute_cap_seconds: float = Field(gt=0)
    compute_committed_seconds: float = Field(ge=0)
    validation_reserve_seconds: float = Field(ge=0)
    implementation_cap_seconds: float = Field(ge=0)
    implementation_committed_seconds: float = Field(ge=0)
    api_cap_usd: float = Field(ge=0)


class FindingContext(Contract):
    id: str
    classification: Literal["observation", "provisional_interpretation", "researcher_endorsement", "counterevidence"]
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    study_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReuseContext(Contract):
    id: str
    asset_id: str
    study_id: str
    decision: Literal["reuse", "reference", "decline"]
    intended_use: str
    rationale: str


class IssueContext(Contract):
    id: str
    code: str
    message: str
    affected_ids: list[str] = Field(default_factory=list)


class NextActionContext(Contract):
    id: str
    title: str
    status: str
    evidence_ids: list[str] = Field(default_factory=list)


class CampaignContext(Contract):
    campaign_id: str
    revision: int = Field(ge=1)
    charter_version: int = Field(ge=1)
    guidance_revision: int = Field(ge=0)
    objective: str
    active_studies: list[StudyContext]
    delegation: DelegationContext
    resources: ResourceContext
    findings: list[FindingContext] = Field(default_factory=list)
    counterevidence: list[FindingContext] = Field(default_factory=list)
    reuse_decisions: list[ReuseContext] = Field(default_factory=list)
    finalist_selections: list[dict] = Field(default_factory=list)
    pending_issues: list[IssueContext] = Field(default_factory=list)
    next_actions: list[NextActionContext] = Field(default_factory=list)
    narrative_guidance: str = Field(min_length=1, max_length=49152)
    source_ids: list[str] = Field(default_factory=list)
    discovery: dict = Field(default_factory=dict)


class ContextImportInput(Contract):
    document: CampaignContext
    reason: str = Field(default="Researcher imported a structured campaign context edit", max_length=2000)
