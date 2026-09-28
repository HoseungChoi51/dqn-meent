"""Editable research intent, distinct from an immutable runnable experiment."""
from typing import Any

from pydantic import Field, model_validator

from .base import Contract
from .reproduction import ReproductionIntent


class DraftSaveInput(Contract):
    draft_id: str | None = None
    expected_draft_revision: int | None = Field(default=None, ge=1)
    study_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    procedure: dict[str, Any]
    follow_proposal_implementation: bool = False
    required_capabilities: list[str] = Field(default_factory=list, max_length=50)
    reproduction: ReproductionIntent | None = None

    @model_validator(mode="after")
    def revision_is_explicit(self):
        if bool(self.draft_id) != (self.expected_draft_revision is not None):
            raise ValueError("Editing a draft requires its current revision; a new draft has no prior revision")
        return self


class DraftLaunchInput(Contract):
    draft_id: str
    expected_draft_revision: int = Field(ge=1)
    expected_readiness_hash: str = Field(min_length=64, max_length=64)
