"""Installed MEENT evaluator for the FLRL two-dimensional beam deflector."""
from __future__ import annotations

import math
from typing import Any

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.problems import (
    CandidateSchema, Evaluation, Objective, ProblemDefinition, ProblemInstance,
)


PROBLEM_ID = "meent_2d_dual_polarization_deflector"
DEFINITION_VERSION = "flrl-2d-v1"
EVALUATOR_VERSION = "meent-0.13.2-flrl-2d-v1"

CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "wavelength_nm", "target_angle_deg", "period_x_nm", "period_y_nm",
        "thickness_nm", "grid_x", "grid_y", "incident_n", "exit_n",
        "incident_angle_deg", "target_order_x", "target_order_y",
        "silicon_index_source", "silicon_n_1050", "reference_meent_version",
    ],
    "properties": {
        "wavelength_nm": {"type": "number", "exclusiveMinimum": 0},
        "target_angle_deg": {"type": "number"},
        "period_x_nm": {"type": "number", "exclusiveMinimum": 0},
        "period_y_nm": {"type": "number", "exclusiveMinimum": 0},
        "thickness_nm": {"type": "number", "exclusiveMinimum": 0},
        "grid_x": {"type": "integer", "minimum": 2},
        "grid_y": {"type": "integer", "minimum": 2},
        "incident_n": {"type": "number", "exclusiveMinimum": 0},
        "exit_n": {"type": "number", "exclusiveMinimum": 0},
        "incident_angle_deg": {"type": "number"},
        "target_order_x": {"type": "integer"},
        "target_order_y": {"type": "integer"},
        "silicon_index_source": {"type": "string"},
        "silicon_n_1050": {"type": "number", "exclusiveMinimum": 0},
        "reference_meent_version": {"type": "string"},
    },
}
FIDELITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rcwa_order_x", "rcwa_order_y"],
    "properties": {
        "rcwa_order_x": {"type": "integer", "minimum": 1},
        "rcwa_order_y": {"type": "integer", "minimum": 1},
    },
}
OBJECTIVES = [
    Objective(name=name, direction="maximize", units="fraction of incident power")
    for name in (
        "mean_plus1_transmission", "te_plus1_transmission",
        "tm_plus1_transmission", "min_plus1_transmission",
    )
]


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


class Meent2DProblem:
    def describe(self) -> ProblemDefinition:
        return ProblemDefinition(
            id=PROBLEM_ID, version=DEFINITION_VERSION,
            name="FLRL 2D silicon/air dual-polarization beam deflector",
            evaluator_id="meent_rcwa_2d", evaluator_version=EVALUATOR_VERSION,
            configuration_schema=CONFIGURATION_SCHEMA, fidelity_schema=FIDELITY_SCHEMA,
            capabilities=["binary", "binary_forward", "scalar_objective"],
            resources={"cpu_threads": 1},
        )

    def resolve(self, configuration: dict, fidelity: dict | None = None) -> ProblemInstance:
        config = dict(configuration)
        settings = dict(fidelity or {})
        # TaskInput also supplies its legacy binary ``physics`` projection,
        # which includes the fidelity values alongside the configuration.
        embedded = {key: config.pop(key) for key in FIDELITY_SCHEMA["required"] if key in config}
        if any(key in settings and settings[key] != value for key, value in embedded.items()):
            raise ValueError("Embedded RCWA fidelity differs from the explicit fidelity")
        settings = {**embedded, **settings}
        if set(config) != set(CONFIGURATION_SCHEMA["required"]):
            raise ValueError("2D MEENT configuration must contain exactly the declared fields")
        if set(settings) != set(FIDELITY_SCHEMA["required"]):
            raise ValueError("2D MEENT fidelity requires both RCWA orders")
        for name in ("wavelength_nm", "period_x_nm", "period_y_nm", "thickness_nm",
                     "incident_n", "exit_n", "silicon_n_1050"):
            config[name] = _number(config[name], name, positive=True)
        for name in ("target_angle_deg", "incident_angle_deg"):
            config[name] = _number(config[name], name)
        for name in ("grid_x", "grid_y"):
            config[name] = _integer(config[name], name, minimum=2)
        for name in ("target_order_x", "target_order_y"):
            config[name] = _integer(config[name], name)
        for name in ("silicon_index_source", "reference_meent_version"):
            if not isinstance(config[name], str) or not config[name].strip():
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("rcwa_order_x", "rcwa_order_y"):
            settings[name] = _integer(settings[name], name, minimum=1)
        if settings["rcwa_order_x"] < config["target_order_x"] or settings["rcwa_order_y"] < config["target_order_y"]:
            raise ValueError("The target diffraction order lies outside the RCWA truncation")
        if config["grid_x"] * config["grid_y"] > 1_000_000:
            raise ValueError("Candidate exceeds the supported binary design dimension")
        definition = self.describe()
        return ProblemInstance(
            definition_id=definition.id, definition_version=definition.version,
            evaluator_id=definition.evaluator_id, evaluator_version=definition.evaluator_version,
            configuration=config,
            candidate_schema=CandidateSchema(representation="binary", dimensions=config["grid_x"] * config["grid_y"]),
            primary_objective=OBJECTIVES[0], extra_metrics=OBJECTIVES[1:],
            public_descriptor={
                "wavelength_nm": config["wavelength_nm"],
                "target_angle_deg": config["target_angle_deg"],
                "grid_x": config["grid_x"], "grid_y": config["grid_y"],
            },
            capabilities=definition.capabilities, fidelity=settings,
            scientific_identity=content_hash({"definition": [definition.id, definition.version], "configuration": config}),
        )

    def evaluator(self, instance: ProblemInstance) -> "Meent2DEvaluator":
        return Meent2DEvaluator(instance)

    def implementation_fixture(self, configuration: dict, dimensions: int) -> ProblemInstance:
        if configuration["grid_x"] * configuration["grid_y"] != dimensions:
            raise ValueError("Optimizer dimension differs from the 2D design grid")
        return self.resolve(configuration, {"rcwa_order_x": 1, "rcwa_order_y": 1})


class Meent2DEvaluator:
    def __init__(self, instance: ProblemInstance):
        import meent
        import torch

        if meent.__version__ != "0.13.2":
            raise ValueError("This evaluator requires MEENT 0.13.2")
        self.instance = instance
        self.torch = torch
        self.cache = {}
        self.mee = meent.call_mee(
            backend=2, pol=0,
            n_top=instance.configuration["incident_n"],
            n_bot=instance.configuration["exit_n"],
            theta=torch.tensor(math.radians(instance.configuration["incident_angle_deg"]), dtype=torch.float64),
            phi=torch.tensor(0.0, dtype=torch.float64),
            fto=[instance.fidelity["rcwa_order_x"], instance.fidelity["rcwa_order_y"]],
            wavelength=instance.configuration["wavelength_nm"],
            period=torch.tensor([instance.configuration["period_x_nm"], instance.configuration["period_y_nm"]], dtype=torch.float64),
            thickness=torch.tensor([instance.configuration["thickness_nm"]], dtype=torch.float64),
            type_complex=torch.complex128, device=0,
        )

    def evaluate(self, candidate: list[int | float]) -> Evaluation:
        values = self.instance.candidate_schema.canonicalize(candidate)
        config = self.instance.configuration
        cell = self.torch.tensor(values, dtype=self.torch.float64).reshape(1, config["grid_y"], config["grid_x"])
        self.mee.ucell = 1.0 + cell * (config["silicon_n_1050"] - 1.0)
        y_order = config["target_order_y"]
        x_order = config["target_order_x"]
        efficiencies = []
        energy_totals = []
        with self.torch.no_grad():
            for pol in (0, 1):
                self.mee.pol = pol
                result = self.mee.conv_solve().res
                transmitted = result.de_ti
                reflected = result.de_ri
                if transmitted.ndim != 2 or transmitted.shape != (
                    2 * self.instance.fidelity["rcwa_order_y"] + 1,
                    2 * self.instance.fidelity["rcwa_order_x"] + 1,
                ):
                    raise ValueError("MEENT returned an unexpected 2D diffraction-order grid")
                efficiency = float(transmitted[transmitted.shape[0] // 2 + y_order,
                                                transmitted.shape[1] // 2 + x_order])
                energy = float(transmitted.sum() + reflected.sum())
                if not math.isfinite(efficiency) or not math.isfinite(energy):
                    raise ValueError("MEENT returned a nonfinite diffraction efficiency")
                if efficiency < -1e-6 or efficiency > 1 + 1e-6 or abs(energy - 1) > 1e-3:
                    raise ValueError("MEENT returned an invalid diffraction or energy budget")
                efficiencies.append(efficiency)
                energy_totals.append(energy)
        te, tm = efficiencies
        return Evaluation(
            objectives={
                "mean_plus1_transmission": (te + tm) / 2,
                "te_plus1_transmission": te,
                "tm_plus1_transmission": tm,
                "min_plus1_transmission": min(te, tm),
            },
            metadata={"meent": {"version": "0.13.2", "energy_totals": energy_totals}},
            solver_executions=2, cache_hit=False,
        )

    def checkpoint(self) -> dict:
        from copy import deepcopy
        return {"evaluation_identity": self.instance.evaluation_identity, "binary_cache": deepcopy(self.cache)}

    def restore(self, state: dict) -> None:
        if state.get("evaluation_identity") != self.instance.evaluation_identity:
            raise ValueError("MEENT 2D checkpoint belongs to another problem or fidelity")
        from copy import deepcopy
        self.cache = deepcopy(state.get("binary_cache", {}))

    def relaxed_gradient(self, request: dict) -> dict:
        """Auxiliary physical solve: never supplies a binary campaign score."""
        from dqn_meent.fourier import FourierGeometry
        import numpy as np

        if set(request) != {"coefficients", "modes_x", "modes_y", "beta", "material_map"}:
            raise ValueError("Invalid Fourier gradient request")
        config = self.instance.configuration
        nx = _integer(request['modes_x'], 'modes_x', minimum=1)
        ny = _integer(request['modes_y'], 'modes_y')
        geometry = FourierGeometry(nx, ny, config['grid_x'], config['grid_y'])
        geometry.field(request['coefficients'])
        beta = _number(request['beta'], 'beta', positive=True)
        if beta > 100 or request['material_map'] not in {'index', 'permittivity'}:
            raise ValueError('Unsupported relaxation strength or material map')
        torch = self.torch
        with torch.enable_grad():
            coefficients = torch.tensor(request['coefficients'], dtype=torch.float64, requires_grad=True)
            field = torch.tensor(np.array(geometry.basis), dtype=torch.float64) @ coefficients
            density = torch.sigmoid(beta * field).reshape(1, config['grid_y'], config['grid_x'])
            self.mee.ucell = (1 + density * (config['silicon_n_1050'] ** 2 - 1)).sqrt() if request['material_map'] == 'permittivity' else 1 + density * (config['silicon_n_1050'] - 1)
            scores = []
            for pol in (0, 1):
                self.mee.pol = pol
                result = self.mee.conv_solve().res
                scores.append(result.de_ti[self.instance.fidelity['rcwa_order_y'] + config['target_order_y'],
                                          self.instance.fidelity['rcwa_order_x'] + config['target_order_x']])
            mean = (scores[0] + scores[1]) / 2
            gradient, = torch.autograd.grad(mean, coefficients)
            if not torch.isfinite(gradient).all() or not torch.isfinite(mean):
                raise ValueError('MEENT returned nonfinite relaxed gradients')
            output = {'gradient': gradient.detach().tolist(), 'mean': float(mean.detach()),
                'te': float(scores[0].detach()), 'tm': float(scores[1].detach()), 'beta': beta,
                'material_map': request['material_map'], 'forward_solver_executions': 2, 'backward_calls': 1}
        self.mee.ucell = self.mee.ucell.detach()
        return output

    def evaluate_proposal(self, candidate, metadata):
        """Account for hard scoring and an optional gradient at the same geometry.

        Only binary objectives enter the archive. Relaxed scores/gradients are
        auxiliary observation metadata; each forward polarization is charged.
        """
        from dqn_meent.fourier import FourierGeometry

        values = self.instance.candidate_schema.canonicalize(candidate)
        request = metadata.get('flrl_gradient')
        if request is not None:
            config = self.instance.configuration
            geometry = FourierGeometry(request['modes_x'], request['modes_y'], config['grid_x'], config['grid_y'])
            if geometry.mask(request['coefficients']) != values:
                raise ValueError('Gradient request coefficients do not produce the proposed binary mask')
        key = content_hash([self.instance.evaluation_identity, values])
        if key in self.cache:
            result = Evaluation(**self.cache[key]).model_copy(deep=True, update={"solver_executions": 0, "cache_hit": True})
        else:
            result = self.evaluate(values)
            self.cache[key] = result.model_dump(mode='json')
            if len(self.cache) > 256:
                self.cache.pop(next(iter(self.cache)))
        if request is not None:
            result = result.model_copy(update={"metadata": {**result.metadata, 'flrl_gradient': self.relaxed_gradient(request)},
                                               "solver_executions": result.solver_executions + 2})
        return result
