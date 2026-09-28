"""MEENT scientific recipes; the framework owns their execution and costs."""
from dataclasses import asdict
import io
import math

import numpy as np

from optimization_framework.contracts.problems import Evaluation, ProblemInstance
from .config import PhysicsConfig


def plan(adapter, instance, recipe_id, parameters, subjects):
    subjects = [instance.candidate_schema.canonicalize(candidate) for candidate in subjects]
    cases = []
    if recipe_id in {"fourier_convergence:v1", "reevaluate:v1"}:
        if recipe_id == "fourier_convergence:v1":
            from optimization_framework.contracts.requests import ValidationInput
            orders = ValidationInput(orders=parameters["orders"], tolerance=parameters["tolerance"]).orders
        else:
            orders = parameters.get("orders", [])
            if set(parameters) - {"orders"} or not isinstance(orders, list) or not 1 <= len(orders) <= 12 or any(type(order) is not int or not 1 <= order <= 480 for order in orders):
                raise ValueError("Reevaluation requires one through twelve integer Fourier orders from 1 to 480")
            orders = sorted(set(orders))
        for index, candidate in enumerate(subjects):
            for order in orders:
                problem = adapter.resolve({**instance.configuration, "cache_size": 0}, {"fourier_order": order})
                cases.append({"problem": problem.model_dump(mode="json"), "candidate": candidate,
                              "subject_index": index, "fourier_order": order})
    elif recipe_id == "sensitivity:v1":
        parameter = parameters.get("parameter")
        if parameter not in {"wavelength_nm", "deflection_angle_deg", "thickness_nm"}:
            raise ValueError("Sensitivity parameter must be wavelength_nm, deflection_angle_deg, or thickness_nm")
        values = parameters.get("values", [])
        if not 1 <= len(values) <= 25 or any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
            raise ValueError("Sensitivity requires 1 through 25 finite parameter values")
        for index, candidate in enumerate(subjects):
            for value in values:
                problem = adapter.resolve({**instance.configuration, parameter: value, "cache_size": 0}, instance.fidelity)
                cases.append({"problem": problem.model_dump(mode="json"), "candidate": candidate,
                              "subject_index": index, "parameter": parameter, "value": value})
    elif recipe_id == "fields:v1":
        unknown = set(parameters) - {"fourier_order", "nx", "nz_pattern", "air_nm", "glass_nm"}
        if unknown:
            raise ValueError(f"Unknown field settings: {sorted(unknown)}")
        sampling = {"nx": 256, "nz_pattern": 80, "air_nm": 1400., "glass_nm": 100., **parameters}
        order = sampling.pop("fourier_order", instance.fidelity["fourier_order"])
        for name, maximum in (("nx", 2048), ("nz_pattern", 512)):
            if type(sampling[name]) is not int or not 2 <= sampling[name] <= maximum:
                raise ValueError(f"{name} must be an integer from 2 through {maximum}")
        for name in ("air_nm", "glass_nm"):
            if type(sampling[name]) not in {float, int} or not 0 < sampling[name] <= 10000:
                raise ValueError(f"{name} must be positive and at most 10000 nm")
        problem = adapter.resolve(instance.configuration, {"fourier_order": order})
        # Bound the complete grid, including the homogeneous observation layers.
        total_rows = sampling["nz_pattern"] * (1 + (sampling["air_nm"] + sampling["glass_nm"]) / instance.configuration["thickness_nm"])
        if total_rows * sampling["nx"] > 4_000_000:
            raise ValueError("Field sampling exceeds the local 4-million-point allowance")
        cases = [{"problem": problem.model_dump(mode="json"), "candidate": candidate,
                  "subject_index": index, "operation": "fields:v1", "sampling": sampling}
                 for index, candidate in enumerate(subjects)]
    else:
        raise ValueError(f"Unknown MEENT recipe: {recipe_id}")
    result = {"cases": cases, "subjects": subjects}
    if recipe_id == "fourier_convergence:v1":
        result["validation_rule"] = {"kind": "solution_fidelity", "subject": "solution",
            "evidence_requirements": ["All declared Fourier orders completed", "Last-two absolute difference within the frozen tolerance"]}
    return result


def summarize(recipe, observations):
    subjects = []
    for index, candidate in enumerate(recipe["subjects"]):
        pairs = [(case, observation) for case, observation in zip(recipe["cases"], observations) if case["subject_index"] == index]
        rows = [{"fidelity": observation["fidelity"], "fourier_order": observation["fidelity"]["fourier_order"],
                 **observation["objectives"], "observation_id": observation["id"], "costs": observation["costs"],
                 "metadata": observation["metadata"], **({"parameter": case["parameter"], "value": case["value"]} if "parameter" in case else {})}
                for case, observation in pairs]
        complete = len(rows) == sum(case["subject_index"] == index for case in recipe["cases"])
        subject = {"design_index": index, "design": candidate, "observations": rows, "complete": complete}
        if recipe["recipe_id"] == "fourier_convergence:v1":
            tolerance = recipe["parameters"]["tolerance"]
            delta = abs(rows[-1]["efficiency"] - rows[-2]["efficiency"]) if len(rows) > 1 else None
            converged = bool(complete and delta is not None and delta <= tolerance)
            subject.update(converged=converged, last_two_absolute_difference=delta, delta=delta,
                efficiency=rows[-1]["efficiency"] if rows else None, fourier_order=rows[-1]["fourier_order"] if rows else None,
                tolerance=tolerance, numerical_status="converged" if converged else "unconverged" if complete else "insufficient_orders",
                verdict="passed" if converged else "failed" if complete else "inconclusive",
                rationale="Last-two Fourier orders agree within tolerance." if converged else "Fourier convergence has not been established at the declared tolerance.")
        subjects.append(subject)
    kind = {"fourier_convergence:v1": "solution_fidelity", "reevaluate:v1": "fixed_fidelity_diagnostic", "fields:v1": "field_analysis", "sensitivity:v1": "solution_sensitivity"}[recipe["recipe_id"]]
    return {"kind": kind, "recipe_id": recipe["recipe_id"], "subjects": subjects,
            "note": "Domain diagnostics are separate from implementation correctness and scientific confirmation."}


class FieldEvaluator:
    def __init__(self, case):
        self.instance = ProblemInstance(**case["problem"])
        self.sampling = dict(case["sampling"])
        self.outputs = []

    def evaluate(self, candidate):
        from .fields import sample_fields
        candidate = self.instance.candidate_schema.canonicalize(candidate)
        config = PhysicsConfig(**self.instance.configuration, **self.instance.fidelity)
        result = sample_fields(config, candidate, **self.sampling)
        stream = io.BytesIO()
        np.savez_compressed(stream, **result)
        self.outputs = [{"kind": "fields", "format": "meent-fields-v2", "data": stream.getvalue(),
                         "metadata": {"physics": asdict(config), "sampling": self.sampling, "candidate": candidate}}]
        return Evaluation(objectives={key: result[key] for key in ("efficiency", "reflectance", "transmittance")},
                          metadata={"meent": {"exit_flux": result["exit_flux"], "field_schema": 2}}, solver_executions=1)

    def export_artifacts(self):
        result, self.outputs = self.outputs, []
        return result

    def checkpoint(self):
        return {"identity": self.instance.evaluation_identity, "sampling": self.sampling}

    def restore(self, state):
        if state != self.checkpoint():
            raise ValueError("Field checkpoint settings changed")
