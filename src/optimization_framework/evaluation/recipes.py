"""Frozen evaluation plans consumed by the common experiment worker."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.problems import ProblemInstance, Proposal
from .registry import problems


def recipe_parameters(problem, recipe_id, parameters, *, registry=None):
    """Resolve manifest defaults before recording a scientific procedure."""
    if isinstance(problem, dict):
        problem = ProblemInstance(**problem)
    adapter = (registry or problems).get(problem.definition_id)
    definition = adapter.describe()
    if recipe_id not in definition.validation_recipes:
        raise ValueError(f"Problem {problem.definition_id} has no executable recipe {recipe_id}")
    schema = definition.recipe_schemas.get(recipe_id, {})
    if schema.get("available") is False:
        raise ValueError(schema["unavailable_reason"])
    defaults = {key: deepcopy(value["default"]) for key, value in schema.get("properties", {}).items() if "default" in value}
    for key, value in schema.get("properties", {}).items():
        if value.get("default_from_fidelity"):
            defaults[key] = deepcopy(problem.fidelity[value["default_from_fidelity"]])
    parameters = {**defaults, **deepcopy(parameters)}
    return adapter.recipe_parameters(problem, recipe_id, parameters) if hasattr(adapter, "recipe_parameters") else parameters


def validation_policy(tasks, policy, *, registry_resolver=None):
    """Freeze required assertions and their parameters, including adapter defaults."""
    policy = deepcopy(policy)
    from typing import get_args
    from optimization_framework.contracts.validation import ValidationKind
    kinds = policy.get("waivable_kinds", [])
    if (not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in get_args(ValidationKind) for kind in kinds)
            or len(set(kinds)) != len(kinds)):
        raise ValueError("Waivable kinds must be a list of distinct validation kinds")
    for key in ("manager_may_waive", "accept_imported_results"):
        if key in policy and not isinstance(policy[key], bool):
            raise ValueError(f"{key} must be an explicit boolean")
    required = policy.get("required_recipes", [])
    if not isinstance(required, list) or any(not isinstance(value, str) for value in required) or len(set(required)) != len(required):
        raise ValueError("Required recipes must be a list of distinct registered assertion identifiers")
    if not required:
        return policy
    parameters = policy.get("required_recipe_parameters", {})
    if not isinstance(parameters, dict) or set(parameters) - set(required):
        raise ValueError("Required recipe parameters must correspond to the frozen required checks")
    resolved = {}
    for recipe_id in required:
        for task in tasks:
            problem = ProblemInstance(**task["problem"])
            registry = registry_resolver(task) if registry_resolver else problems
            definition = registry.get(problem.definition_id).describe()
            if not definition.recipe_schemas.get(recipe_id, {}).get("assertion_kind"):
                raise ValueError(f"{recipe_id} is not a registered validation assertion for {problem.definition_id}")
            values = recipe_parameters(problem, recipe_id, parameters.get(recipe_id, {}), registry=registry)
            if recipe_id in resolved and resolved[recipe_id] != values:
                raise ValueError("Required checks have incompatible instance defaults; declare their parameters explicitly")
            resolved[recipe_id] = values
    from optimization_framework.contracts.requests import RecipeInput
    wall = RecipeInput(recipe_id=required[0], wall_seconds=policy.get("validation_wall_seconds", 120)).wall_seconds
    policy.update(required_recipe_parameters=resolved, validation_wall_seconds=wall)
    return policy


def compile_recipe(problem, recipe_id, parameters, subjects, *, registry=None):
    if isinstance(problem, dict):
        problem = ProblemInstance(**problem)
    registry = registry or problems
    adapter = registry.get(problem.definition_id)
    if recipe_id not in adapter.describe().validation_recipes or not hasattr(adapter, "plan_recipe"):
        raise ValueError(f"Problem {problem.definition_id} has no executable recipe {recipe_id}")
    parameters = recipe_parameters(problem, recipe_id, parameters, registry=registry)
    plan = adapter.plan_recipe(problem, recipe_id, parameters, subjects)
    if not plan or not plan.get("cases"):
        raise ValueError("A recipe must declare its evaluation cases before allocation")
    for case in plan["cases"]:
        instance = ProblemInstance(**case["problem"])
        # Resolve through the caller's captured manifest before allocating.
        verified = registry.resolve(instance.definition_id, instance.configuration, instance.fidelity)
        if verified.digest() != instance.digest():
            raise ValueError("Recipe instance differs from the registered problem contract")
        case["candidate"] = instance.candidate_schema.canonicalize(case["candidate"])
    return {"schema_version": 1, "problem_id": problem.definition_id, "recipe_id": recipe_id,
            "parameters": deepcopy(parameters), **plan}


class PlannedEvaluations:
    """A deterministic procedure, with no optimizer access to later results."""
    def __init__(self, recipe, registry=None):
        self.recipe = deepcopy(recipe)
        self.registry = registry or problems
        self.cursor = 0
        self.observations = []

    @property
    def instance(self):
        index = min(self.cursor, len(self.recipe["cases"]) - 1)
        return ProblemInstance(**self.recipe["cases"][index]["problem"])

    def propose(self, max_candidates=1):
        if max_candidates < 1 or self.cursor >= len(self.recipe["cases"]):
            raise ValueError("Evaluation plan exhausted")
        return [Proposal(id=f"case_{self.cursor}", candidate=self.recipe["cases"][self.cursor]["candidate"])]

    def observe(self, observations):
        if len(observations) != 1 or observations[0].proposal_id != f"case_{self.cursor}":
            raise ValueError("Recipe observation disagrees with the frozen case order")
        observation = observations[0]
        if observation.status != "ok":
            raise ValueError(f"Recipe evaluation failed: {observation.error}")
        self.observations.append(observation.model_dump(mode="json"))
        self.cursor += 1

    def checkpoint(self):
        return {"recipe_hash": content_hash(self.recipe), "observations": deepcopy(self.observations)}

    def restore(self, state):
        if state["recipe_hash"] != content_hash(self.recipe):
            raise ValueError("Recipe changed since checkpoint")
        self.observations = deepcopy(state["observations"])
        self.cursor = len(self.observations)

    def inspect(self):
        return {"decisions": self.cursor, "planned_cases": len(self.recipe["cases"]), "phase": "validation"}

    def export_artifacts(self):
        return [{"kind": "validation_evidence", "media_type": "application/json",
                 "data": {"recipe": self.recipe, "observations": self.observations, "summary": self.summary()}}]

    def summary(self):
        adapter = self.registry.get(self.recipe["problem_id"])
        return adapter.summarize_recipe(self.recipe, self.observations)


class RecipeEvaluator:
    def __init__(self, procedure):
        self.procedure = procedure
        self.registry = procedure.registry
        self.evaluators = {}
        self.outputs = []

    @staticmethod
    def case_key(case):
        return content_hash(case) if case.get("operation") else ProblemInstance(**case["problem"]).evaluation_identity

    @staticmethod
    def make_evaluator(case, registry=None):
        registry = registry or problems
        instance = ProblemInstance(**case["problem"])
        if case.get("operation"):
            return registry.get(instance.definition_id).recipe_evaluator(case)
        return registry.evaluator(instance)

    def evaluate(self, candidate):
        case = self.procedure.recipe["cases"][self.procedure.cursor]
        key = self.case_key(case)
        if key not in self.evaluators:
            self.evaluators[key] = self.make_evaluator(case, self.registry)
        evaluator = self.evaluators[key]
        result = evaluator.evaluate(candidate)
        if hasattr(evaluator, "export_artifacts"):
            self.outputs.extend(evaluator.export_artifacts())
        return result

    def drain_artifacts(self):
        outputs, self.outputs = self.outputs, []
        return outputs

    def checkpoint(self):
        return {key: evaluator.checkpoint() for key, evaluator in self.evaluators.items()}

    def restore(self, state):
        instances = {self.case_key(case): case for case in self.procedure.recipe["cases"]}
        if set(state) - set(instances):
            raise ValueError("Recipe checkpoint contains undeclared evaluator instances")
        self.close()
        try:
            for key, saved in state.items():
                self.evaluators[key] = self.make_evaluator(instances[key], self.registry)
                self.evaluators[key].restore(saved)
        except BaseException:
            self.close()
            raise

    def close(self):
        for evaluator in self.evaluators.values():
            if hasattr(evaluator, "close"):
                evaluator.close()
        self.evaluators.clear()
