"""Generated evaluator host; request and cost authority stay in the worker."""
import hashlib

from optimization_framework.contracts.evaluators import EvaluatorOutput
from optimization_framework.contracts.problems import Evaluation, ProblemInstance
from optimization_framework.implementations.legacy_runtime import CandidateError
from optimization_framework.implementations.runtime import MAX_CHECKPOINT, PackageProcess


class PackageEvaluator(PackageProcess):
    def __init__(self, package_dir, runtime_root, runtime, entrypoint, instance, *, timeout=10,
                 progress=None, max_checkpoint_bytes=MAX_CHECKPOINT, assets_dir=None):
        self.instance = instance if isinstance(instance, ProblemInstance) else ProblemInstance.model_validate(instance)
        self.count = 0
        super().__init__(package_dir, runtime_root, runtime, entrypoint,
            {"problem": self.instance.model_dump(mode="json")}, contract="evaluator_v1",
            timeout=timeout, progress=progress, max_checkpoint_bytes=max_checkpoint_bytes, assets_dir=assets_dir)

    def evaluate(self, candidate):
        candidate = self.instance.candidate_schema.canonicalize(candidate)
        self.count += 1
        try:
            value = self._request("evaluate", candidate=candidate)
            result = EvaluatorOutput.model_validate(value)
            expected = {objective.name for objective in [self.instance.primary_objective, *self.instance.extra_metrics]}
            if set(result.objectives) != expected:
                raise CandidateError("Evaluator returned objectives inconsistent with the frozen manifest")
            constraints = {constraint.name: constraint.satisfied(candidate) for constraint in self.instance.candidate_schema.constraints}
            if result.constraints is not None and result.constraints != constraints:
                raise CandidateError("Evaluator returned constraints inconsistent with the frozen manifest")
            # Each host invocation is one measured evaluator execution. Package
            # assertions about cache hits or physical cost cannot alter the ledger.
            return Evaluation(objectives=result.objectives, constraints=constraints,
                metadata={"generated_evaluator": result.metadata}, solver_executions=1, cache_hit=False)
        except BaseException:
            self.close()
            raise

    def checkpoint(self):
        return {"runtime_digest": self.runtime_digest, "evaluation_identity": self.instance.evaluation_identity,
                "checkpoint": self._request("checkpoint"), "count": self.count, "contract": "evaluator_v1"}

    def restore(self, state):
        if (state.get("runtime_digest") != self.runtime_digest or
                state.get("evaluation_identity") != self.instance.evaluation_identity or state.get("contract") != "evaluator_v1"):
            raise ValueError("Evaluator checkpoint identity changed")
        raw = state["checkpoint"]
        if not isinstance(raw, bytes) or len(raw) > self.max_checkpoint_bytes:
            raise CandidateError("Invalid evaluator checkpoint payload")
        self._request("restore", binary=raw, binary_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.count = state["count"]
