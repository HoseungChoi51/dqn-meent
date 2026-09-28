"""Deterministic bounded quadratic and Rosenbrock minimization instances."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract, content_hash
from optimization_framework.contracts.problems import (CandidateSchema, Constraint, Evaluation,
    Objective, ProblemDefinition, ProblemInstance)


class BenchmarkConfig(Contract):
    function: Literal["quadratic", "rosenbrock"] = "quadratic"
    dimensions: int = Field(default=2, ge=1, le=10000)
    bounds: list[tuple[float, float]] = Field(default_factory=list)
    center: list[float] = Field(default_factory=list)
    weights: list[float] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def dimensions_match(self):
        if self.function == "rosenbrock" and self.dimensions < 2:
            raise ValueError("Rosenbrock requires at least two dimensions")
        for values in (self.bounds, self.center, self.weights):
            if values and len(values) != self.dimensions:
                raise ValueError("Configuration vectors must match dimensions")
        if any(x <= 0 for x in self.weights):
            raise ValueError("Quadratic weights must be positive")
        return self


class ContinuousProblem:
    def study_templates(self):
        from .templates import registered
        return registered()

    def describe(self):
        return ProblemDefinition(id="bounded_continuous", version="1", name="Bounded continuous benchmarks",
            evaluator_id="analytic_scalar", evaluator_version="1", configuration_schema=BenchmarkConfig.model_json_schema(),
            capabilities=["continuous", "scalar_objective", "linear_constraints"],
            validation_recipes=["analytic_fixtures:v1"], renderer="continuous",
            recipe_schemas={"analytic_fixtures:v1": {"title": "Known-value evaluator fixtures", "assertion_kind": "evaluator_correctness", "type": "object", "properties": {
                "tolerance": {"title": "Absolute tolerance", "type": "number", "default": 1e-12, "exclusiveMinimum": 0, "maximum": 1e-6}}}})

    def resolve(self, configuration, fidelity=None):
        if fidelity:
            raise ValueError("Analytic benchmarks do not have approximate fidelity settings")
        config = BenchmarkConfig(**configuration)
        values = config.model_dump(mode="json", exclude={"schema_version"})
        values["bounds"] = values["bounds"] or [[-5., 5.]] * config.dimensions
        values["center"] = values["center"] or [0.] * config.dimensions
        values["weights"] = values["weights"] or [1.] * config.dimensions
        schema = CandidateSchema(representation="continuous", dimensions=config.dimensions,
            bounds=values["bounds"], constraints=config.constraints)
        definition = self.describe()
        objective = Objective(name="value", direction="minimize")
        return ProblemInstance(definition_id=definition.id, definition_version=definition.version,
            evaluator_id=definition.evaluator_id, evaluator_version=definition.evaluator_version,
            configuration=values, candidate_schema=schema, primary_objective=objective,
            public_descriptor={"function": config.function, "dimensions": config.dimensions},
            capabilities=definition.capabilities, fidelity={},
            scientific_identity=content_hash({"definition": definition.id, "configuration": values, "objective": objective.model_dump()}))

    def evaluator(self, instance):
        return AnalyticEvaluator(instance)

    def implementation_fixture(self, configuration, dimensions):
        return self.resolve({**configuration, "dimensions": dimensions})

    def plan_recipe(self, instance, recipe_id, parameters, subjects):
        if recipe_id != "analytic_fixtures:v1":
            raise ValueError("Unknown analytic benchmark recipe")
        if set(parameters) - {"tolerance"}:
            raise ValueError("Analytic fixtures accept only an absolute tolerance")
        import math
        tolerance = parameters.get("tolerance", 1e-12)
        if type(tolerance) not in {int, float} or not math.isfinite(tolerance) or not 0 < tolerance <= 1e-6:
            raise ValueError("Fixture tolerance must be positive and at most 1e-6")
        config = instance.configuration
        if config["function"] == "quadratic":
            fixtures = [(config["center"], 0.)]
            for index in range(min(4, len(config["center"]))):
                for offset in (-1., 1.):
                    candidate = list(config["center"])
                    candidate[index] += offset
                    fixtures.append((candidate, config["weights"][index]))
        else:
            size = instance.candidate_schema.dimensions
            fixtures = [([1.] * size, 0.), ([0.] * size, float(size - 1))]
        cases = []
        for candidate, expected in fixtures:
            try:
                candidate = instance.candidate_schema.canonicalize(candidate)
            except ValueError:
                continue
            cases.append({"candidate": candidate, "problem": instance.model_dump(mode="json"),
                          "expected_value": expected, "tolerance": tolerance})
        if not cases:
            raise ValueError("No registered analytic fixture is feasible in this instance; a compatible evaluator check is required")
        return {"cases": cases, "subjects": [], "validation_rule": {"kind": "evaluator_correctness", "subject": "evaluator",
            "evidence_requirements": ["Every admissible registered known-value fixture satisfies the declared tolerance"]}}

    def summarize_recipe(self, recipe, observations):
        rows = [{"candidate": case["candidate"], "expected_value": case["expected_value"],
                 "observed_value": observed["objectives"]["value"],
                 "absolute_error": abs(observed["objectives"]["value"] - case["expected_value"]),
                 "tolerance": case["tolerance"], "observation_id": observed["id"]}
                for case, observed in zip(recipe["cases"], observations)]
        complete = len(rows) == len(recipe["cases"])
        passed = complete and all(row["absolute_error"] <= row["tolerance"] for row in rows)
        return {"kind": "evaluator_correctness", "recipe_id": recipe["recipe_id"], "complete": complete, "measurements": rows,
                "verdict": "passed" if passed else "failed" if complete else "inconclusive",
                "rationale": "Registered analytic fixtures agree." if passed else "The declared analytic fixture checks have not passed.",
                "limitations": "These finite fixtures check evaluator behavior; they do not establish optimizer effectiveness."}


class AnalyticEvaluator:
    def __init__(self, instance):
        self.instance = instance
        self.requests = self.solves = self.hits = 0
        self.cache = {}

    def evaluate(self, candidate):
        x = self.instance.candidate_schema.canonicalize(candidate)
        key = content_hash(x)
        self.requests += 1
        hit = key in self.cache
        if hit:
            self.hits += 1
            value = self.cache[key]
        else:
            self.solves += 1
            config = self.instance.configuration
            if config["function"] == "quadratic":
                value = sum(w * (v - c)**2 for w, v, c in zip(config["weights"], x, config["center"], strict=True))
            else:
                value = sum(100 * (b - a*a)**2 + (1-a)**2 for a, b in zip(x[:-1], x[1:]))
            self.cache[key] = value
        return Evaluation(objectives={"value": value}, solver_executions=int(not hit), cache_hit=hit,
                          constraints={c.name: True for c in self.instance.candidate_schema.constraints})

    def checkpoint(self):
        return {"identity": self.instance.evaluation_identity, "requests": self.requests,
                "solves": self.solves, "hits": self.hits, "cache": self.cache.copy()}

    def restore(self, state):
        if state["identity"] != self.instance.evaluation_identity:
            raise ValueError("Evaluator checkpoint belongs to a different instance or version")
        self.requests, self.solves, self.hits = state["requests"], state["solves"], state["hits"]
        self.cache = state["cache"].copy()
