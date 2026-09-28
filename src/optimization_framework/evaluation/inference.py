"""Reviewed inference extensions; no candidate-supplied module is imported here.

An adapter describes accepted artifacts, normalizes its parameters, compiles a
bounded procedure and creates a common-lifecycle optimizer. Evaluations, grants,
source capture and scheduling remain worker/workspace responsibilities.
"""
from importlib import import_module, metadata

from optimization_framework.contracts.capabilities import InferenceDescriptor, InferenceProcedure
from optimization_framework.contracts.diagnostics import ArtifactInference, PolicyRollout
from optimization_framework.contracts.problems import ProblemInstance
from optimization_framework.implementations.models import check_parameters


GROUP = "optimization_framework.inference"
BUILTINS = {"dqn_policy:v1": "optimization_framework.optimizers.policy:DQNInference"}


class InferenceRegistry:
    def __init__(self, entries=None):
        self._entries = None if entries is None else dict(entries)

    def entries(self):
        if self._entries is not None:
            return dict(self._entries)
        entries = dict(BUILTINS)
        for entry in metadata.entry_points(group=GROUP):
            if entry.name in entries:
                raise ValueError(f"Duplicate inference adapter {entry.name!r}")
            entries[entry.name] = entry.value
        return entries

    def get(self, identity):
        entry = self.entries().get(identity)
        if entry is None:
            raise ValueError(f"Inference adapter {identity!r} is unavailable; install a reviewed compatible adapter")
        module, factory = entry.split(":")
        adapter = getattr(import_module(module), factory)()
        descriptor = InferenceDescriptor.model_validate(adapter.describe())
        if descriptor.id != identity:
            raise ValueError("Inference adapter identity differs from its registered version")
        return adapter, descriptor

    def catalog(self):
        return [self.get(identity)[1].model_dump(mode="json") for identity in sorted(self.entries())]


adapters = InferenceRegistry()


def resolve(rollout):
    if isinstance(rollout, PolicyRollout):
        from optimization_framework.optimizers.policy import legacy_inference
        return legacy_inference(rollout)
    return rollout.adapter_id, rollout.parameters


def prepare(identity, problem, parameters, *, asset=None, registry=None):
    problem = problem if isinstance(problem, ProblemInstance) else ProblemInstance(**problem)
    adapter, descriptor = (registry or adapters).get(identity)
    if problem.candidate_schema.representation not in descriptor.representations or (problem.candidate_schema.constraints and not descriptor.constraints):
        raise ValueError(f"{descriptor.title} does not support this problem's representation or constraints")
    if asset is not None and not descriptor.artifact.matches(asset):
        raise ValueError(f"The declared asset is incompatible with {descriptor.title}")
    check_parameters(parameters, descriptor.parameter_schema)
    normalized = adapter.prepare(problem, dict(parameters), asset=asset)
    check_parameters(normalized, descriptor.parameter_schema)
    limits = InferenceProcedure(**adapter.procedure(problem, normalized))
    if limits.completion.unit not in descriptor.capabilities.completion_units:
        raise ValueError("Inference procedure uses an undeclared completion counter")
    return adapter, descriptor, normalized


def compile_inference(problem, rollout, assets):
    """Run from captured source when a committed milestone is dispatched."""
    if isinstance(rollout, dict):
        rollout = ArtifactInference(**rollout) if rollout.get("kind") == "artifact_inference:v1" else PolicyRollout(**rollout)
    identity, parameters = resolve(rollout)
    _, descriptor = adapters.get(identity)
    compatible = [asset for asset in assets if descriptor.artifact.matches(asset)]
    if len(compatible) != 1:
        raise ValueError(f"The declared {descriptor.title} diagnostic needs exactly one compatible exported artifact")
    asset = compatible[0]
    adapter, descriptor, parameters = prepare(identity, problem, parameters, asset=asset)
    instance = problem if isinstance(problem, ProblemInstance) else ProblemInstance(**problem)
    limits = InferenceProcedure(**adapter.procedure(instance, parameters))
    result = {"algorithm": "artifact_inference", "algorithm_config": {"adapter_id": identity, "parameters": parameters},
              "max_steps": limits.max_steps, "schedule_steps": limits.schedule_steps,
              "completion": limits.completion.model_dump(exclude={"schema_version"}), "asset_id": asset["id"],
              "adapter": descriptor.model_dump(mode="json")}
    if isinstance(rollout, PolicyRollout):
        # Existing records retain their public method and parameter identities.
        result.update(algorithm="frozen_policy", algorithm_config=parameters)
    return result


def create(instance, config, seed, assets, artifact_store, registry=None):
    if len(assets) != 1:
        raise ValueError("Artifact inference requires exactly one declared input asset")
    adapter, _, parameters = prepare(config["adapter_id"], instance, config.get("parameters", {}), asset=assets[0], registry=registry)
    return adapter.create(instance, parameters, seed, assets[0], artifact_store)


def run_job(workspace, parent, request):
    """Researcher-requested inference is a separate diagnostic allocation."""
    from optimization_framework.contracts.requests import TrialInput
    if parent.get("recipe") or parent["algorithm"] in {"recipe", "validate"}:
        raise ValueError("Select the optimization experiment that supplies this inference problem")
    assets = workspace.assets.inputs(parent["campaign_id"], parent["study_id"], [request.asset_id], request.reuse_decision_ids)
    compiled = workspace.compile_inference(parent, {"kind": "artifact_inference:v1", "adapter_id": request.adapter_id,
        "parameters": request.parameters, "seed": request.seed, "wall_seconds": request.wall_seconds}, assets)
    trial = TrialInput(campaign_id=parent["campaign_id"], task_id=parent["task_id"], seed=request.seed,
        **{key: compiled[key] for key in ("algorithm", "algorithm_config", "max_steps", "schedule_steps", "completion")},
        initial_assets=[request.asset_id], reuse_decision_ids=request.reuse_decision_ids, wall_seconds=request.wall_seconds,
        question="Independent inference from an explicitly selected frozen policy; no training or adaptation.")
    return workspace.create_trial(trial, validation={"parent_trial_id": parent["id"], "study_id": parent["study_id"],
        "analysis_kind": "artifact_inference", "source_trial_id": parent["id"],
        **({"independent_countercheck": True} if parent.get("study_execution_id") else {})})
