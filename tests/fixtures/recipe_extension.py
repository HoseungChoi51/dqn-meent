"""Reviewed measurement recipe fixture, registered by an integration test only."""
from statistics import mean

from pydantic import Field

from optimization_framework.contracts.base import Contract
from optimization_framework.evaluation.registered_recipes import RecipeDescriptor, parameter_schema


class Parameters(Contract):
    repeats: int = Field(default=2, ge=1, le=10)


class Adapter:
    def describe(self):
        return RecipeDescriptor(id="fixture_measurements:v1", title="Repeated measurement summary",
            description="Record the observed mean and range; no statistical or correctness assertion.",
            parameter_schema=parameter_schema(Parameters))

    def parameters(self, adapter, problem, parameters):
        return Parameters.model_validate(parameters).model_dump(mode="json", exclude={"schema_version"})

    def plan(self, adapter, problem, parameters, subjects):
        values = self.parameters(adapter, problem, parameters)
        return {"subjects": subjects, "objective": problem.primary_objective.name,
            "cases": [{"candidate": candidate, "problem": problem.model_dump(mode="json"), "subject_index": index}
                for index, candidate in enumerate(subjects) for _ in range(values["repeats"])]}

    def summarize(self, plan, observations):
        subjects = []
        for index, candidate in enumerate(plan["subjects"]):
            values = [observation["objectives"][plan["objective"]]
                for case, observation in zip(plan["cases"], observations) if case["subject_index"] == index]
            subjects.append({"candidate": candidate, "count": len(values), "mean": mean(values) if values else None,
                "range": [min(values), max(values)] if values else None})
        return {"complete": len(observations) == len(plan["cases"]), "subjects": subjects}
