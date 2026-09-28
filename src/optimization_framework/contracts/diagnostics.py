"""Declared milestones capture evidence without changing the optimizer's inputs."""
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .base import Contract


class ScheduledRecipe(Contract):
    recipe_id: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    subject_limit: int = Field(default=1, ge=1, le=10)
    wall_seconds: float = Field(default=120, gt=0, le=86400)


class DerivedSeed(Contract):
    """A bounded affine declaration, with no interpreted agent expression."""
    kind: Literal["affine:v1"] = "affine:v1"
    offset: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    parent_seed_factor: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    milestone_factor: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    episode_factor: int = Field(default=1, ge=0, le=2**32 - 1, strict=True)

    def resolve(self, parent_seed, milestone, episode_index):
        if any(type(value) is not int or value < 0 for value in (parent_seed, milestone, episode_index)):
            raise ValueError("Diagnostic seed inputs must be nonnegative integers")
        seed = self.offset + self.parent_seed_factor * parent_seed + self.milestone_factor * milestone + self.episode_factor * episode_index
        if not 0 <= seed < 2**32:
            raise ValueError("The declared diagnostic seed derivation exceeds the unsigned 32-bit range")
        return seed


class PolicyRollout(Contract):
    seed: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)] | DerivedSeed
    epsilon: float = Field(default=0, ge=0, le=1)
    horizon: int = Field(default=128, ge=1, le=1000000)
    wall_seconds: float = Field(default=60, gt=0, le=86400)
    recipes: list[ScheduledRecipe] = Field(default_factory=list, max_length=10)

    def resolved_seed(self, parent_seed, milestone, episode_index):
        return self.seed.resolve(parent_seed, milestone, episode_index) if isinstance(self.seed, DerivedSeed) else self.seed


class ArtifactInference(Contract):
    kind: Literal["artifact_inference:v1"] = "artifact_inference:v1"
    adapter_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+:v[1-9][0-9]*$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    seed: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)] | DerivedSeed
    wall_seconds: float = Field(default=60, gt=0, le=86400)
    recipes: list[ScheduledRecipe] = Field(default_factory=list, max_length=10)

    def resolved_seed(self, parent_seed, milestone, episode_index):
        return self.seed.resolve(parent_seed, milestone, episode_index) if isinstance(self.seed, DerivedSeed) else self.seed


class DiagnosticSchedule(Contract):
    unit: Literal["evaluation_requests", "optimizer_decisions"] = "evaluation_requests"
    at_counts: list[int] = Field(min_length=1, max_length=1000)
    export_optimizer: bool = True
    recipes: list[ScheduledRecipe] = Field(default_factory=list, max_length=10)
    # The legacy declaration keeps its serialization/digest. New declarations
    # explicitly name a versioned adapter and its own parameter schema.
    rollouts: list[ArtifactInference | PolicyRollout] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def valid_milestones(self):
        if any(type(value) is not int or value <= 0 for value in self.at_counts) or self.at_counts != sorted(set(self.at_counts)):
            raise ValueError("Diagnostic milestones must be distinct, increasing positive counts")
        if self.rollouts and not self.export_optimizer:
            raise ValueError("Artifact inference requires a captured optimizer export")
        return self

    def allocation(self):
        """Known upper bound, reserved with the parent before work is accepted."""
        return sum(recipe.wall_seconds for recipe in self.recipes) + sum(
            rollout.wall_seconds + sum(recipe.wall_seconds for recipe in rollout.recipes) for rollout in self.rollouts)
