"""Requests select installed analysis rules, never arbitrary executable code."""
from typing import Any
from pydantic import Field

from .base import Contract


class RuleRequest(Contract):
    provider: str = "framework"
    rule_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class NominateInput(Contract):
    study_id: str
    expected_evidence_hash: str | None = None
