"""Executable declarations used before admitting completion and diagnostic work."""
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Contract
from .assets import AssetKind
from .experiments import CompletionCondition


CompletionUnit = Literal["evaluation_requests", "optimizer_decisions"]


class ArtifactFormat(Contract):
    kind: AssetKind
    format: str = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def accepts(self, declaration):
        """Metadata predicates refine a format without interpreting expressions."""
        value = declaration.model_dump() if isinstance(declaration, ArtifactFormat) else declaration
        return (value.get("kind") == self.kind and value.get("format") == self.format
                and all(value.get("metadata", {}).get(key) == expected for key, expected in self.metadata.items()))

    def matches(self, asset):
        return self.accepts({**asset.get("payload", {}), "kind": asset.get("kind")})


class OptimizerCapabilities(Contract):
    completion_units: list[CompletionUnit] = Field(default_factory=lambda: ["evaluation_requests"], min_length=1, max_length=2)
    exports: list[ArtifactFormat] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.completion_units)) != len(self.completion_units) or "evaluation_requests" not in self.completion_units:
            raise ValueError("Declare distinct completion units including worker-owned evaluation requests")
        if len({item.digest() for item in self.exports}) != len(self.exports):
            raise ValueError("Declare each exported artifact format once")
        return self


class InferenceDescriptor(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+:v[1-9][0-9]*$")
    title: str = Field(min_length=1, max_length=200)
    artifact: ArtifactFormat
    representations: list[Literal["binary", "discrete", "continuous"]] = Field(min_length=1)
    constraints: bool = False
    adaptation: Literal["forbidden"] = "forbidden"
    parameter_schema: dict[str, Any]
    capabilities: OptimizerCapabilities


class InferenceProcedure(Contract):
    max_steps: int = Field(ge=1, le=10000000, strict=True)
    schedule_steps: int = Field(ge=1, le=10000000, strict=True)
    completion: CompletionCondition

    @model_validator(mode="after")
    def bounded_requests(self):
        if self.completion.unit == "evaluation_requests" and self.completion.count > self.max_steps:
            raise ValueError("Inference completion exceeds its declared evaluation request limit")
        return self
