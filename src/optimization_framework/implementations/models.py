"""Versioned contracts shared by the library, manager, and experiment workers."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from optimization_framework.contracts.capabilities import OptimizerCapabilities
from optimization_framework.contracts.evaluators import EvaluatorManifest
from optimization_framework.research.model_policy import ModelPolicy


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BehaviorCheck(Contract):
    """Protected black-box observations, frozen before a candidate is built."""
    name: str = Field(min_length=1, max_length=200)
    n_cells: int = Field(default=8, ge=1, le=1000000)
    seed: int = Field(default=0, ge=0, le=2**32-1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    efficiencies: list[float] = Field(default_factory=lambda: [.2, .8, .1, .9], min_length=2, max_length=64)
    assertion: Literal["binary", "feasible", "one_bit_from_incumbent", "one_coordinate_from_incumbent", "exact_designs", "unique_proposals"] = "binary"
    expected_designs: list[list[float]] = Field(default_factory=list, max_length=64)


class MechanismCheck(Contract):
    """Frozen invariants on exported optimizer state; no generated test execution."""
    name: str = Field(min_length=1, max_length=200)
    pointer: str = Field(min_length=1, max_length=1000)
    assertion: Literal["finite", "nonnegative", "unit_norm", "positive_semidefinite", "tangent", "rank_at_most"]
    reference_pointer: str | None = None
    maximum_rank: int | None = Field(default=None, ge=0)
    tolerance: float = Field(default=1e-8, gt=0, le=1e-3)


class ImplementationSpec(Contract):
    name: str = Field(min_length=1, max_length=300)
    mechanism: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
    capabilities: list[str] = Field(default_factory=lambda: ["binary_forward"])
    problem_id: str = "meent_grating"
    problem_configuration: dict[str, Any] = Field(default_factory=dict)
    supports_constraints: bool = False
    supports_failure_observations: bool = False
    execution_capabilities: OptimizerCapabilities = Field(default_factory=OptimizerCapabilities)
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)
    parameter_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "additionalProperties": False, "properties": {}})
    n_cells_min: int = Field(default=2, ge=1, le=1000000)
    n_cells_max: int = Field(default=1024, ge=1, le=1000000)
    max_checkpoint_bytes: int = Field(default=256 * 1024**2, ge=1024, le=4 * 1024**3)
    behavior_checks: list[BehaviorCheck] = Field(default_factory=list, max_length=20)
    mechanism_checks: list[MechanismCheck] = Field(default_factory=list, max_length=20)
    provenance: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    exposed_conditions: list[str] = Field(default_factory=list, max_length=10000)

    @model_serializer(mode="wrap")
    def retain_legacy_identity(self, handler):
        result = handler(self)
        if not self.mechanism_checks:
            result.pop("mechanism_checks", None)
        return result

    @model_validator(mode="after")
    def boundaries(self):
        if self.n_cells_min > self.n_cells_max:
            raise ValueError("Candidate dimension bounds are reversed")
        for name, version in self.dependencies.items():
            import re
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
                raise ValueError("Dependencies require a package name and exact version")
            if name.lower().replace('_', '-') in {"dqn-meent", "meent"}:
                raise ValueError("The evaluator is supplied by the trusted worker, not candidate dependencies")
        for check in self.behavior_checks:
            if not self.n_cells_min <= check.n_cells <= self.n_cells_max:
                raise ValueError("Behavior check is outside the supported cell-count scope")
            check_parameters({**self.parameters, **check.parameters}, self.parameter_schema)
            if check.assertion == "exact_designs" and (len(check.expected_designs) != len(check.efficiencies) or
                    any(len(row) != check.n_cells for row in check.expected_designs)):
                raise ValueError("Reference designs must provide one candidate of the declared size per observation")
        check_parameters(self.parameters, self.parameter_schema)
        if any(not text.strip() or len(text) > 4000 for text in self.acceptance_criteria):
            raise ValueError("Acceptance criteria must be nonempty and at most 4000 characters each")
        return self


class BoundOptimizerSpec(ImplementationSpec):
    """Explicit extension; legacy optimizer specifications keep their exact shape."""
    kind: Literal["optimizer"] = "optimizer"
    evaluator_version_id: str = Field(min_length=1)


class SourceFile(Contract):
    path: str
    content: str

    @model_validator(mode="after")
    def safe_path(self):
        path = PurePosixPath(self.path)
        if path.is_absolute() or not path.parts or any(p in {"..", "."} for p in path.parts) or "\\" in self.path:
            raise ValueError("Package files must use relative paths without traversal")
        if self.path != str(path) or len(self.content.encode()) > 262144:
            raise ValueError("Invalid path or oversized source file")
        return self


class Package(Contract):
    contract: Literal["ask_tell", "optimizer_v1"] = "ask_tell"
    entrypoint: str = "optimizer:create_optimizer"
    files: list[SourceFile] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_package(self):
        import re
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", self.entrypoint):
            raise ValueError("Entry point must be module:factory")
        if len({f.path for f in self.files}) != len(self.files) or sum(len(f.content.encode()) for f in self.files) > 1048576:
            raise ValueError("Duplicate files or package exceeds 1 MiB")
        return self


class EvaluatorProbe(Contract):
    """A contract exercise with no assertion of numerical truth."""
    name: str = Field(min_length=1, max_length=200)
    candidate: list[float] = Field(min_length=1, max_length=1000000)
    configuration: dict[str, Any] = Field(default_factory=dict)
    fidelity: dict[str, Any] = Field(default_factory=dict)


class EvaluatorCase(EvaluatorProbe):
    """An oracle supplied independently of the candidate build, with its basis."""
    basis: str = Field(min_length=1, max_length=4000)
    objectives: dict[str, float]
    absolute_tolerance: float = Field(default=1e-10, ge=0)
    relative_tolerance: float = Field(default=0, ge=0)


class EvaluatorSpec(Contract):
    kind: Literal["evaluator"] = "evaluator"
    contract: Literal["evaluator_v1"] = "evaluator_v1"
    name: str = Field(min_length=1, max_length=300)
    mechanism: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
    manifest: EvaluatorManifest
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=20)
    correctness_cases: list[EvaluatorCase] = Field(default_factory=list, max_length=100)
    validation_mode: Literal["numerical", "contract_only"] = "numerical"
    contract_cases: list[EvaluatorProbe] = Field(default_factory=list, max_length=100)
    operation_timeout_seconds: float = Field(default=10, gt=0, le=3600)
    max_checkpoint_bytes: int = Field(default=256 * 1024**2, ge=1024, le=4 * 1024**3)
    provenance: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    exposed_conditions: list[str] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def boundaries(self):
        import re
        for name, version in self.dependencies.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
                raise ValueError("Dependencies require a package name and exact version")
            if name.lower().replace('_', '-') == "dqn-meent":
                raise ValueError("Generated evaluators cannot import the workspace application")
        expected = {value.name for value in [self.manifest.primary_objective, *self.manifest.extra_metrics]}
        for case in [*self.correctness_cases, *self.contract_cases]:
            instance = self.manifest.resolve("correctness", case.configuration, case.fidelity)
            instance.candidate_schema.canonicalize(case.candidate)
            if isinstance(case, EvaluatorCase) and set(case.objectives) != expected:
                raise ValueError("Correctness fixtures must specify every declared objective and metric")
        if len({case.name for case in self.correctness_cases}) != len(self.correctness_cases):
            raise ValueError("Correctness fixture names must be distinct")
        if len({case.name for case in self.contract_cases}) != len(self.contract_cases):
            raise ValueError("Contract probe names must be distinct")
        if self.validation_mode == "contract_only" and (self.correctness_cases or not self.contract_cases):
            raise ValueError("Contract-only validation requires probes without numerical reference answers; supplied oracles must be checked in numerical mode")
        if any(not text.strip() or len(text) > 4000 for text in self.acceptance_criteria):
            raise ValueError("Acceptance criteria must be nonempty and at most 4000 characters each")
        return self

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        result = handler(self)
        # Published numerical specifications keep their original content identity.
        if self.validation_mode == "numerical":
            result.pop("validation_mode", None)
        if not self.contract_cases:
            result.pop("contract_cases", None)
        return result


class EvaluatorPackage(Package):
    kind: Literal["evaluator"] = "evaluator"
    contract: Literal["evaluator_v1"] = "evaluator_v1"
    entrypoint: str = "evaluator:create_evaluator"


def parse_package(value):
    if isinstance(value, (Package, EvaluatorPackage)):
        return value
    return (EvaluatorPackage if value.get("kind") == "evaluator" else Package).model_validate(value)


class BuildResult(Contract):
    explanation: str
    package: EvaluatorPackage | Package | None
    blocker: str | None


class ReviewResult(Contract):
    passed: bool
    criteria: list[str]
    findings: list[str]


class JobRequest(Contract):
    workspace_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=200)
    hypothesis_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    spec: EvaluatorSpec | BoundOptimizerSpec | ImplementationSpec
    package: EvaluatorPackage | Package | None = None
    max_attempts: int = Field(default=3, ge=1, le=3)
    max_calls: int = Field(default=12, ge=1, le=20)
    compute_seconds: float = Field(default=120, gt=0, le=86400)
    api_budget_usd: float = Field(default=0, ge=0, le=10000)
    grant_id: str = Field(min_length=1, max_length=200)
    model_policy: ModelPolicy | None = None
    agent_parent_id: str | None = None

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        result = handler(self)
        # Preserve the identity of requests accepted before model policies.
        if self.model_policy is None:
            result.pop("model_policy", None)
        if self.agent_parent_id is None:
            result.pop("agent_parent_id", None)
        return result

    @model_validator(mode="before")
    @classmethod
    def resolve_package_kind(cls, values):
        # Both package schemas have defaults. An untagged files-only package
        # must use the commissioned spec, not the arbitrary order of a union.
        if isinstance(values, dict) and isinstance(values.get("package"), dict):
            spec = values.get("spec")
            evaluator = isinstance(spec, EvaluatorSpec) or isinstance(spec, dict) and spec.get("kind") == "evaluator"
            package_type = EvaluatorPackage if evaluator else Package
            values = {**values, "package": package_type.model_validate(values["package"])}
        return values

    @model_validator(mode="after")
    def matching_kind(self):
        if self.package is not None and isinstance(self.spec, EvaluatorSpec) != isinstance(self.package, EvaluatorPackage):
            raise ValueError("Package kind must match the commissioned executable specification")
        return self


class EvaluatorCheckSpec(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["evaluator"] = "evaluator"
    validation_mode: Literal["numerical", "contract_only"] = "numerical"
    correctness_cases: list[EvaluatorCase] = Field(default_factory=list, max_length=100)
    contract_cases: list[EvaluatorProbe] = Field(default_factory=list, max_length=100)
    rationale: str = Field(min_length=1, max_length=5000)


class OptimizerCheckSpec(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["optimizer"] = "optimizer"
    behavior_checks: list[BehaviorCheck] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=5000)


class RevalidationRequest(Contract):
    """A mechanical check grant; it cannot supply source, dependencies or a model."""
    operation: Literal["revalidation"] = "revalidation"
    workspace_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=200)
    hypothesis_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1)
    checks: EvaluatorCheckSpec | OptimizerCheckSpec
    compute_seconds: float = Field(default=120, gt=0, le=86400)
    api_budget_usd: Literal[0] = 0
    max_calls: Literal[0] = 0
    grant_id: str = Field(min_length=1, max_length=200)


def parse_job_request(value):
    if isinstance(value, (JobRequest, RevalidationRequest)):
        return value
    return (RevalidationRequest if value.get("operation") == "revalidation" else JobRequest).model_validate(value)


class JobControl(Contract):
    action: Literal["cancel", "resume", "close_uncertain"]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ValidationRequest(Contract):
    request: JobRequest


class LibraryUnavailable(ValueError):
    pass


class CapabilityUnavailable(ValueError):
    pass


def check_parameters(parameters: dict, schema: dict):
    """A deliberately small, explicit JSON parameter schema; no ignored keywords."""
    if set(schema) - {"type", "properties", "required", "additionalProperties"} or schema.get("type", "object") != "object":
        raise ValueError("Parameter schemas support object properties, required, and additionalProperties")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not isinstance(schema.get("required", []), list) or any(not isinstance(key, str) for key in schema.get("required", [])) or type(schema.get("additionalProperties", False)) is not bool:
        raise ValueError("Invalid object parameter schema")
    for rule in properties.values():
        if not isinstance(rule, dict) or set(rule) - {"type", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "enum", "description", "title", "default"}:
            raise ValueError("Unsupported parameter constraint")
        if rule.get("type") not in {None, "integer", "number", "boolean", "string", "array", "object"}:
            raise ValueError("Unsupported parameter type")
        if "enum" in rule and not isinstance(rule["enum"], list):
            raise ValueError("Parameter enum must be a list")
        if any(type(rule[key]) not in (int, float) for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum") if key in rule):
            raise ValueError("Parameter bounds must be numeric")
    if schema.get("additionalProperties", False) is False and set(parameters) - set(properties):
        raise ValueError("Unsupported implementation parameters: " + ", ".join(sorted(set(parameters) - set(properties))))
    if set(schema.get("required", [])) - set(parameters):
        raise ValueError("Required implementation parameters are missing")
    for key, value in parameters.items():
        rule = properties.get(key, {})
        if set(rule) - {"type", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "enum", "description", "title", "default"}:
            raise ValueError("Unsupported parameter constraint")
        kind = rule.get("type")
        valid = {"integer": type(value) is int, "number": type(value) in (int, float),
                 "boolean": type(value) is bool, "string": isinstance(value, str),
                 "array": isinstance(value, list), "object": isinstance(value, dict)}
        if kind and not valid.get(kind, False):
            raise ValueError(f"Invalid type for parameter {key}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"Unsupported value for parameter {key}")
        if any(key in rule for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")) and type(value) not in (int, float):
            raise ValueError(f"Numeric bounds require a numeric parameter: {key}")
        if ("minimum" in rule and value < rule["minimum"]) or ("maximum" in rule and value > rule["maximum"]):
            raise ValueError(f"Parameter {key} is outside its validated bounds")
        if ("exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]) or ("exclusiveMaximum" in rule and value >= rule["exclusiveMaximum"]):
            raise ValueError(f"Parameter {key} is outside its validated bounds")
