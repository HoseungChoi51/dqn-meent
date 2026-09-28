"""Installed historical reference sets; importing does not grant experimental use."""
from typing import Any

from pydantic import Field, model_validator

from .base import Contract, content_hash


class SourceIdentity(Contract):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReferenceSolution(Contract):
    slot: str = Field(min_length=1)
    title: str
    candidate: list[Any] = Field(min_length=1)
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: str
    seed: int = Field(ge=0, lt=2**32)
    sources: list[SourceIdentity] = Field(min_length=1)

    @model_validator(mode="after")
    def intact(self):
        if content_hash(self.candidate) != self.candidate_digest:
            raise ValueError("The captured reference candidate differs from its manifest digest")
        return self


class ReferenceSet(Contract):
    id: str
    title: str
    description: str
    problem_id: str
    configuration: dict[str, Any]
    scientific_identity: str
    origin_repository: str
    captured_at: str
    solutions: list[ReferenceSolution] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self):
        if len({item.slot for item in self.solutions}) != len(self.solutions):
            raise ValueError("A reference set cannot repeat an input slot")
        return self


class ReferenceImportInput(Contract):
    provider: str
    reference_set_id: str
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
