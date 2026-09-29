"""The protected fixture must catch materially wrong decoder and spectral cap behavior."""
import math
import hashlib
import json

import numpy as np
import pytest

from optimization_framework.implementations import protected_diagnostics as diagnostics
from optimization_framework.implementations.models import digest
from optimization_framework.implementations.runtime import PackageOptimizer
from optimization_framework.contracts.problems import CandidateSchema, Proposal


class FixtureCandidate:
    def __init__(self, *, bad_decoder=False, skip_cap=False):
        self.bad_decoder = bad_decoder
        self.skip_cap = skip_cap

    def diagnostic(self, operation, payload):
        if operation == "cache_identity":
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            return {"key": hashlib.sha256(encoded).hexdigest()}
        if operation == "decode_fourier_mask":
            mask = diagnostics._decoder_oracle(tuple(payload["mode"]), payload["coefficients"])
            if self.bad_decoder:
                mask = 1 - mask
            return {"mask": mask.ravel().tolist()}
        matrix = np.asarray(payload["matrix"])
        eig, basis = np.linalg.eigh(matrix)
        kept = basis[:, -1] * math.sqrt(max(eig[-1] - .05, 0))
        residual = matrix - np.outer(kept, kept)
        diagonal = np.maximum(np.diag(residual), .05)
        factors = kept[:, None]
        combined = np.diag(diagonal) + factors @ factors.T
        highest = np.linalg.eigvalsh(combined)[-1]
        shrink = 1.0 if self.skip_cap or highest <= 4 else 3.95 / (highest - .05)
        return {"diagonal": (.05 + shrink * (diagonal - .05)).tolist(),
                "low_rank": (math.sqrt(shrink) * factors).tolist()}


def test_programmatic_full_mask_oracle_catches_material_sign_error():
    result = diagnostics.fourier_decoder(FixtureCandidate())
    assert result["cases"] == 24
    with pytest.raises(ValueError, match="independent full-mask oracle"):
        diagnostics.fourier_decoder(FixtureCandidate(bad_decoder=True))


def test_actual_reconstructed_spectrum_catches_unshrunk_refactor():
    result = diagnostics.covariance_refactor(FixtureCandidate())
    assert result["unshrunk_max"] == pytest.approx(5.475)
    assert result["post_shrink_max"] == pytest.approx(4)
    with pytest.raises(ValueError, match="Actual reconstructed covariance"):
        diagnostics.covariance_refactor(FixtureCandidate(skip_cap=True))


def test_cache_identity_changes_with_physics_solver_fidelity_and_mask():
    assert diagnostics.cache_identity(FixtureCandidate())["context_variations"] == 3


def test_synthetic_adapter_uses_pinned_identity_and_declared_metrics():
    worker = PackageOptimizer.__new__(PackageOptimizer)
    worker.contract = "optimizer_v1"
    worker.candidate_schema = CandidateSchema(representation="binary", dimensions=2)
    worker.pending = [Proposal(id="proposal-1", candidate=[0, 1])]
    worker.count = 0
    context = {"fidelity": {"rcwa_order_x": 10, "rcwa_order_y": 5},
               "evaluator_id": "meent_rcwa_2d", "evaluator_version": "meent-0.13.2-flrl-2d-v1"}
    worker.context = {"parameters": {"evaluation_context": context}, "problem": {
        "scientific_identity": "fixture-physics", "primary_objective": {"name": "mean_plus1_transmission", "direction": "maximize"},
        "extra_metrics": [{"name": name} for name in
                          ("te_plus1_transmission", "tm_plus1_transmission", "min_plus1_transmission")]}}
    observed = []
    worker.observe = lambda batch: observed.extend(batch)
    worker.tell([0, 1], .4)
    assert observed[0].evaluator_identity == digest({"instance": "fixture-physics", "fidelity": context["fidelity"],
                                                     "evaluator": [context["evaluator_id"], context["evaluator_version"]]})
    assert set(observed[0].objectives) == {"mean_plus1_transmission", "te_plus1_transmission",
                                            "tm_plus1_transmission", "min_plus1_transmission"}
