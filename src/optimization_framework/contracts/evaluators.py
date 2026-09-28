"""Data-only problem manifests for separately commissioned evaluators."""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, StrictBool, model_serializer, model_validator

from .base import Contract, content_hash
from .problems import CandidateSchema, Objective, ProblemDefinition, ProblemInstance


def object_schema():
    return {"type": "object", "properties": {}, "additionalProperties": False}


class EvaluatorManifest(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    name: str = Field(min_length=1, max_length=300)
    candidate_schema: CandidateSchema
    primary_objective: Objective
    extra_metrics: list[Objective] = Field(default_factory=list, max_length=20)
    configuration_schema: dict[str, Any] = Field(default_factory=object_schema)
    configuration: dict[str, Any] = Field(default_factory=dict)
    fidelity_schema: dict[str, Any] = Field(default_factory=object_schema)
    fidelity: dict[str, Any] = Field(default_factory=dict)
    public_configuration_keys: list[str] = Field(default_factory=list, max_length=100)
    deterministic: bool = True
    recipe_ids: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]*:v[1-9][0-9]*$")]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def declared_domain(self):
        from optimization_framework.implementations.models import check_parameters
        check_parameters(self.configuration, self.configuration_schema)
        check_parameters(self.fidelity, self.fidelity_schema)
        objectives = [self.primary_objective, *self.extra_metrics]
        if any(not value.name.strip() or not value.units.strip() for value in objectives):
            raise ValueError("Objectives require names and units")
        if len({value.name for value in objectives}) != len(objectives):
            raise ValueError("Objective and metric names must be distinct")
        if set(self.public_configuration_keys) - self.configuration_schema.get("properties", {}).keys():
            raise ValueError("Public configuration keys must be declared in the schema")
        if len({value.name for value in self.candidate_schema.constraints}) != len(self.candidate_schema.constraints):
            raise ValueError("Constraint names must be distinct")
        if len(set(self.recipe_ids)) != len(self.recipe_ids):
            raise ValueError("Recipe declarations must be distinct registered versions")
        return self

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        result = handler(self)
        if not self.recipe_ids:
            result.pop("recipe_ids", None)
        return result

    @property
    def version(self):
        return content_hash(self.model_dump(mode="json"))

    @property
    def capabilities(self):
        return [self.candidate_schema.representation, "scalar_objective"]

    def describe(self, evaluator_version: str, *, recipe_registry=None):
        from optimization_framework.evaluation.registered_recipes import recipes
        return ProblemDefinition(id=self.id, version=self.version, name=self.name,
            evaluator_id="package:" + self.id, evaluator_version=evaluator_version,
            configuration_schema=self.configuration_schema, fidelity_schema=self.fidelity_schema,
            capabilities=self.capabilities, validation_recipes=self.recipe_ids,
            recipe_schemas=(recipe_registry or recipes).schemas(self.recipe_ids))

    def resolve(self, evaluator_version: str, configuration=None, fidelity=None):
        from optimization_framework.implementations.models import check_parameters
        configuration = {**self.configuration, **(configuration or {})}
        fidelity = {**self.fidelity, **(fidelity or {})}
        check_parameters(configuration, self.configuration_schema)
        check_parameters(fidelity, self.fidelity_schema)
        return ProblemInstance(definition_id=self.id, definition_version=self.version,
            evaluator_id="package:" + self.id, evaluator_version=evaluator_version,
            configuration=configuration, candidate_schema=self.candidate_schema,
            primary_objective=self.primary_objective, extra_metrics=self.extra_metrics,
            capabilities=self.capabilities, fidelity=fidelity,
            public_descriptor={key: configuration[key] for key in self.public_configuration_keys if key in configuration},
            scientific_identity=content_hash({"definition": [self.id, self.version], "configuration": configuration}))


class EvaluatorOutput(Contract):
    """Candidate code cannot supply cost, provenance, or observation authority."""
    objectives: dict[str, float]
    constraints: dict[str, StrictBool] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
