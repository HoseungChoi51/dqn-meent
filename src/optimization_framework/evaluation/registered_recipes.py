"""Reviewed recipe extensions composed by data-only evaluator manifests.

Packages declare identifiers, never executable expressions or module paths.
The implementation runs from captured framework/extension source and requests
ordinary worker-owned evaluations through the generated evaluator's host.
"""
from copy import deepcopy
from importlib import import_module, metadata

from pydantic import Field

from optimization_framework.contracts.base import Contract
from optimization_framework.contracts.validation import ValidationKind


GROUP = "optimization_framework.recipes"
BUILTINS = {
    "candidate_reevaluation:v1": "optimization_framework.evaluation.registered_recipes:CandidateReevaluation",
    "fidelity_comparison:v1": "optimization_framework.evaluation.registered_recipes:FidelityComparison",
}


class RecipeDescriptor(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*:v[1-9][0-9]*$")
    title: str
    description: str
    parameter_schema: dict
    assertion_kind: ValidationKind | None = None


class RecipeRegistry:
    def __init__(self, entries=None):
        self._entries = None if entries is None else dict(entries)

    def entries(self):
        if self._entries is not None:
            return dict(self._entries)
        entries = dict(BUILTINS)
        for entry in metadata.entry_points(group=GROUP):
            if entry.name in entries:
                raise ValueError(f"Duplicate registered recipe {entry.name!r}")
            entries[entry.name] = entry.value
        return entries

    def get(self, identity):
        entry = self.entries().get(identity)
        if entry is None:
            raise ValueError(f"Recipe {identity!r} is unavailable; install a reviewed compatible recipe")
        module, factory = entry.split(":")
        recipe = getattr(import_module(module), factory)()
        if any(not callable(getattr(recipe, name, None)) for name in ("describe", "parameters", "plan", "summarize")):
            raise ValueError("A reviewed recipe must describe, validate parameters, plan and summarize its procedure")
        descriptor = RecipeDescriptor.model_validate(recipe.describe())
        if descriptor.id != identity:
            raise ValueError("Recipe identity differs from its registered version")
        return recipe, descriptor

    def schemas(self, identities):
        result = {}
        for identity in identities:
            try:
                _, descriptor = self.get(identity)
                result[identity] = {**descriptor.parameter_schema, "title": descriptor.title,
                    "description": descriptor.description, "assertion_kind": descriptor.assertion_kind, "available": True}
            except ValueError as exc:
                # An unavailable extension is a capability requirement, not a
                # reason to lose an otherwise usable problem declaration.
                result[identity] = {"title": identity, "available": False, "unavailable_reason": str(exc)}
        return result


recipes = RecipeRegistry()


def parameter_schema(model):
    schema = model.model_json_schema()
    schema["properties"].pop("schema_version", None)
    return schema


class ReevaluationParameters(Contract):
    repeats: int = Field(default=1, ge=1, le=100, title="Evaluations per candidate")
    fidelity: dict = Field(default_factory=dict, title="Fidelity settings (JSON)",
        description="Overrides for the problem's declared fidelity settings; an empty object keeps the source fidelity.")


class CandidateReevaluation:
    identity = "candidate_reevaluation:v1"

    def describe(self):
        return RecipeDescriptor(id=self.identity, title="Reevaluate observed candidates",
            description="Record new measurements at a declared fidelity; no numerical correctness or convergence assertion.",
            parameter_schema=parameter_schema(ReevaluationParameters))

    def parameters(self, adapter, problem, parameters):
        values = ReevaluationParameters.model_validate(parameters)
        adapter.resolve(problem.configuration, {**problem.fidelity, **values.fidelity})
        return values.model_dump(mode="json", exclude={"schema_version"})

    def plan(self, adapter, problem, parameters, subjects):
        values = ReevaluationParameters.model_validate(self.parameters(adapter, problem, parameters))
        if not subjects:
            raise ValueError("Select observed candidates for reevaluation")
        instance = adapter.resolve(problem.configuration, {**problem.fidelity, **values.fidelity})
        subjects = [instance.candidate_schema.canonicalize(candidate) for candidate in subjects]
        return {"subjects": subjects, "cases": [{"problem": instance.model_dump(mode="json"), "candidate": candidate,
            "subject_index": index, "repeat": repeat} for index, candidate in enumerate(subjects) for repeat in range(values.repeats)]}

    def summarize(self, recipe, observations):
        return {"complete": len(observations) == len(recipe["cases"]), "subjects": [
            {"candidate": candidate, "measurements": [deepcopy(observation) for case, observation in zip(recipe["cases"], observations)
                if case["subject_index"] == index]}
            for index, candidate in enumerate(recipe["subjects"])],
            "limitations": "Measurements from the bound evaluator; no independent numerical correctness assertion."}


class FidelityParameters(Contract):
    fidelities: list[dict] = Field(min_length=2, max_length=16, title="Fidelity settings sequence (JSON)",
        description="Ordered JSON objects overriding source fidelity. The assertion compares the final two settings only.")
    absolute_tolerance: float = Field(default=1e-6, ge=0, title="Absolute tolerance")
    relative_tolerance: float = Field(default=0, ge=0, title="Relative tolerance")


class FidelityComparison:
    identity = "fidelity_comparison:v1"

    def describe(self):
        return RecipeDescriptor(id=self.identity, title="Compare declared fidelities",
            description="Compare the primary objective at the final two declared fidelity settings. This does not prove evaluator correctness or general convergence.",
            parameter_schema=parameter_schema(FidelityParameters), assertion_kind="solution_fidelity")

    def parameters(self, adapter, problem, parameters):
        values = FidelityParameters.model_validate(parameters)
        instances = [adapter.resolve(problem.configuration, {**problem.fidelity, **fidelity}) for fidelity in values.fidelities]
        if len({instance.evaluation_identity for instance in instances}) != len(instances):
            raise ValueError("Fidelity comparison requires distinct resolved fidelity settings")
        if not adapter.manifest.deterministic:
            raise ValueError("This fidelity assertion requires a deterministic evaluator; use reevaluation for stochastic measurements")
        return values.model_dump(mode="json", exclude={"schema_version"})

    def plan(self, adapter, problem, parameters, subjects):
        values = FidelityParameters.model_validate(self.parameters(adapter, problem, parameters))
        if not subjects:
            raise ValueError("Select observed candidates for fidelity comparison")
        instances = [adapter.resolve(problem.configuration, {**problem.fidelity, **fidelity}) for fidelity in values.fidelities]
        subjects = [problem.candidate_schema.canonicalize(candidate) for candidate in subjects]
        return {"subjects": subjects, "objective": problem.primary_objective.model_dump(mode="json"),
            "cases": [{"problem": instance.model_dump(mode="json"), "candidate": candidate,
                "subject_index": index, "fidelity_index": fidelity_index}
                for index, candidate in enumerate(subjects) for fidelity_index, instance in enumerate(instances)],
            "validation_rule": {"kind": "solution_fidelity", "subject": "candidate",
                "evidence_requirements": ["Primary objective agreement at the final two declared fidelity settings"]}}

    def summarize(self, recipe, observations):
        values = FidelityParameters.model_validate(recipe["parameters"])
        name = recipe["objective"]["name"]
        findings = []
        for index, candidate in enumerate(recipe["subjects"]):
            measured = [observation for case, observation in zip(recipe["cases"], observations) if case["subject_index"] == index]
            complete = len(measured) == len(values.fidelities)
            difference = abs(measured[-1]["objectives"][name] - measured[-2]["objectives"][name]) if complete else None
            tolerance = values.absolute_tolerance + values.relative_tolerance * abs(measured[-1]["objectives"][name]) if complete else None
            findings.append({"candidate": candidate, "complete": complete, "difference": difference, "tolerance": tolerance,
                "verdict": ("passed" if difference <= tolerance else "failed") if complete else "inconclusive",
                "measurements": deepcopy(measured),
                "rationale": "Agreement is assessed only at the final two declared fidelities; evaluator correctness and general convergence remain separate requirements."})
        return {"complete": len(observations) == len(recipe["cases"]), "subjects": findings}
