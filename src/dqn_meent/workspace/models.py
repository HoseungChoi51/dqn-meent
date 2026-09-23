"""Validated request contracts; scientific state is immutable per trial."""
from dataclasses import asdict
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from dqn_meent.config import PhysicsConfig


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TaskInput(Model):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    physics: dict[str, Any] = Field(default_factory=dict)
    split: Literal["development", "selection", "test"] = "development"

    @field_validator("physics")
    @classmethod
    def valid_physics(cls, value):
        try:
            cfg = PhysicsConfig(**value)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        if cfg.n_cells > 1024 or cfg.fourier_order > 256:
            raise ValueError("Local pilot supports at most 1024 cells and Fourier order 256")
        if not isinstance(cfg.n_cells, int) or not isinstance(cfg.fourier_order, int):
            raise ValueError("Cell count and Fourier order must be integers")
        return asdict(cfg)


class CampaignInput(Model):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="Find reliable, cost-effective binary grating optimizers.", max_length=20000)
    compute_budget_seconds: float = Field(default=3600, gt=0, le=604800)
    llm_budget_usd: float = Field(default=5, ge=0, le=10000)
    autonomy: Literal["manual", "guided", "delegated"] = "guided"
    tasks: list[TaskInput] = Field(min_length=1, max_length=100)
    delegated_trial_seconds: float = Field(default=60, gt=0, le=3600)
    validation_reserve_seconds: float = Field(default=120, ge=0)

    @model_validator(mode="after")
    def reserve_fits(self):
        if self.validation_reserve_seconds > self.compute_budget_seconds:
            raise ValueError("Validation reserve exceeds campaign compute budget")
        return self


class CampaignUpdate(Model):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=20000)
    compute_budget_seconds: float | None = Field(default=None, gt=0, le=604800)
    llm_budget_usd: float | None = Field(default=None, ge=0, le=10000)
    autonomy: Literal["manual", "guided", "delegated"] | None = None
    tasks: list[TaskInput] | None = Field(default=None, min_length=1, max_length=100)
    delegated_trial_seconds: float | None = Field(default=None, gt=0, le=3600)
    validation_reserve_seconds: float | None = Field(default=None, ge=0)


class TrialInput(Model):
    campaign_id: str
    task_id: str
    algorithm: str = Field(min_length=1, max_length=100)
    algorithm_config: dict[str, Any] = Field(default_factory=dict)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    max_steps: int = Field(default=512, ge=1, le=10000000)
    wall_seconds: float = Field(default=60, gt=0, le=86400)
    schedule_steps: int | None = Field(default=None, ge=1, le=10000000)
    hypothesis_id: str | None = None
    question: str = Field(default="Compare search progress under a bounded budget.", max_length=10000,
                          validation_alias=AliasChoices("question", "experiment_question"))
    priority: int = Field(default=0, ge=-100, le=100)
    confirmatory: bool = False
    training: dict[str, Any] = Field(default_factory=dict)


class ControlInput(Model):
    action: Literal["pause", "resume", "stop", "extend", "prioritize"]
    max_steps: int | None = Field(default=None, ge=1, le=10000000)
    wall_seconds: float | None = Field(default=None, gt=0, le=86400)
    priority: int | None = Field(default=None, ge=-100, le=100)


class ValidationInput(Model):
    orders: list[int] = Field(default_factory=lambda: [25, 40, 60, 80], min_length=2, max_length=12)
    max_designs: int = Field(default=3, ge=1, le=10)
    wall_seconds: float = Field(default=120, gt=0, le=86400)
    tolerance: float = Field(default=0.005, gt=0, le=0.1)

    @field_validator("orders")
    @classmethod
    def distinct_orders(cls, orders):
        if min(orders) < 1 or max(orders) > 256 or len(set(orders)) < 2:
            raise ValueError("Provide at least two distinct Fourier orders from 1 through 256")
        return sorted(set(orders))


class HypothesisInput(Model):
    campaign_id: str
    title: str = Field(min_length=1, max_length=300)
    mechanism: str = Field(default="", max_length=20000)
    rationale: str = Field(default="", max_length=20000)
    assumptions: list[Any] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=50)
    sources: list[Any] = Field(default_factory=list, max_length=50)
    parent_ids: list[str] = Field(default_factory=list, max_length=10)
    algorithm: str = "hillclimb"
    algorithm_config: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "investigating", "archived", "finalist"] = "proposed"
    source: str | None = Field(default=None, max_length=100000)


class ReviewInput(Model):
    text: str = Field(min_length=1, max_length=20000)

    @field_validator("text")
    @classmethod
    def nonempty_comment(cls, value):
        if not value.strip():
            raise ValueError("Write a comment before saving")
        return value


class DecisionInput(Model):
    choice: str = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=20000)


class ResearchInput(Model):
    campaign_id: str
    message: str = Field(min_length=1, max_length=20000)
    mode: Literal["discuss", "generate", "review", "compare", "evolve", "probe", "plan"] = "discuss"
    hypothesis_id: str | None = None
    feedback_review_ids: list[str] = Field(default_factory=list, max_length=100)
    max_calls: int = Field(default=6, ge=1, le=20)
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
