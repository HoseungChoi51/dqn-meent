"""Declarative study expansion: named procedures, cells and fixed transitions."""
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Contract
from .diagnostics import DiagnosticSchedule
from .experiments import CompletionCondition, RecoveryPolicy
from .study_rules import RuleRequest


class PrefixInput(Contract):
    kind: Literal["same_seed_prefix:v1"] = "same_seed_prefix:v1"
    group: str
    slot: str
    unit: Literal["evaluation_requests", "optimizer_decisions"] = "evaluation_requests"
    count: int = Field(ge=1)
    asset_kind: Literal["solution"] = "solution"


class DeclaredInput(Contract):
    kind: Literal["declared_asset:v1"] = "declared_asset:v1"
    slot: str = Field(min_length=1)


class InputRequirement(Contract):
    title: str
    asset_kind: Literal["solution", "policy", "dataset"] = "solution"
    candidate_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Procedure(Contract):
    algorithm: str
    implementation_version_id: str | None = None
    algorithm_config: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(ge=1, le=10000000)
    schedule_steps: int | None = Field(default=None, ge=1, le=10000000)
    completion: CompletionCondition | None = None
    recovery: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    diagnostics: list[DiagnosticSchedule] = Field(default_factory=list, max_length=20)
    input_binding: PrefixInput | DeclaredInput | None = None
    wall_seconds: float = Field(default=60, gt=0, le=86400)


class MethodSlot(Contract):
    procedure: Procedure | None = None
    select_from: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_one_resolution(self):
        if bool(self.procedure) == bool(self.select_from):
            raise ValueError("A method slot needs a literal procedure or a declared selection roster")
        if len(set(self.select_from)) != len(self.select_from):
            raise ValueError("A selection roster cannot repeat a slot")
        return self


class CellGroup(Contract):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    scope: Literal["development", "confirmation", "reference"]
    slots: list[str] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    task_ids: list[str] = Field(default_factory=list)
    admission: Literal["activation", "nomination"] = "activation"
    priority: int = Field(default=0, ge=-100, le=100)
    reference_role: str | None = None
    cell_order: Literal["slot_then_seed", "seed_then_slot"] = "slot_then_seed"

    @model_validator(mode="after")
    def distinct_cells(self):
        if len(set(self.slots)) != len(self.slots) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("A cell group cannot repeat a slot or seed")
        if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in self.seeds):
            raise ValueError("Cell seeds must be unsigned 32-bit integers")
        if self.scope == "development" and self.admission != "activation":
            raise ValueError("Development evidence cannot depend on its own nomination")
        if self.reference_role and self.scope != "reference":
            raise ValueError("A reference role belongs only to a reference cell group")
        return self


class StudyTemplateVersion(Contract):
    id: str = Field(min_length=1)
    name: str
    goal: str
    qualification_only: bool = False
    methods: dict[str, MethodSlot]
    input_requirements: dict[str, InputRequirement] = Field(default_factory=dict)
    groups: list[CellGroup] = Field(min_length=1)
    selection: RuleRequest
    analysis: RuleRequest | None = None
    confirmation_kind: Literal["seed_replication", "unseen_instance"] = "seed_replication"
    development_seconds: float = Field(gt=0)
    total_seconds: float = Field(gt=0)
    worker_seconds: float = Field(gt=0)
    max_workers: int = Field(ge=1, le=16)
    diagnostic_priority: int = Field(default=50, ge=-100, le=100)
    validation_priority: int = Field(default=60, ge=-100, le=100)
    validation_policies: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_expansion(self):
        if self.development_seconds >= self.total_seconds:
            raise ValueError("Development must close before the overall execution deadline")
        if len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Cell group identifiers must be unique")
        if set(self.validation_policies) - {"development", "confirmation", "reference"}:
            raise ValueError("Validation policies must name declared study scopes")
        scopes = {group.scope for group in self.groups}
        if not {"development", "confirmation"} <= scopes:
            raise ValueError("A staged template needs development and confirmation cells")
        development = {slot for group in self.groups if group.scope == "development" for slot in group.slots}
        declared_inputs = {slot.procedure.input_binding.slot for slot in self.methods.values()
                           if slot.procedure and isinstance(slot.procedure.input_binding, DeclaredInput)}
        if declared_inputs != set(self.input_requirements):
            raise ValueError("Each declared input needs one named requirement, with no unused requirements")
        for name, slot in self.methods.items():
            if set(slot.select_from) - development or name in slot.select_from:
                raise ValueError("A selected slot must resolve from predeclared development procedures")
            if any(not self.methods.get(key) or not self.methods[key].procedure for key in slot.select_from):
                raise ValueError("Selection cannot depend on another unresolved method slot")
        for group in self.groups:
            if set(group.slots) - set(self.methods):
                raise ValueError("A cell group names an undeclared method slot")
            if any(self.methods[key].select_from for key in group.slots) and group.admission != "nomination":
                raise ValueError("Selected-method cells must await nomination")
            for slot_id in group.slots:
                procedure = self.methods[slot_id].procedure
                binding = procedure.input_binding if procedure else None
                if isinstance(binding, PrefixInput):
                    source = next((row for row in self.groups if row.id == binding.group), None)
                    if not source or binding.slot not in source.slots or not set(group.seeds) <= set(source.seeds):
                        raise ValueError("A prefix binding needs its declared producer at every matching seed")
                    producer = self.methods[binding.slot].procedure
                    if not producer or producer.input_binding or source.admission != "activation":
                        raise ValueError("Prefix producers must be literal independent procedures admitted at activation")
                    if not any(schedule.unit == binding.unit and binding.count in schedule.at_counts for schedule in producer.diagnostics):
                        raise ValueError("The producer must declare the exact bound prefix milestone")
                    if group.scope == "development" and source.scope != "development":
                        raise ValueError("Protected confirmation inputs cannot feed development selection")
        return self


class TemplateFreezeInput(Contract):
    template: StudyTemplateVersion
    task_ids: list[str] = Field(min_length=1)
    asset_bindings: dict[str, str] = Field(default_factory=dict)


class ExecutionInput(Contract):
    execution_id: str
