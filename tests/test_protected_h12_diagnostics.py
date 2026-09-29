"""The protected fixture must catch materially wrong decoder and spectral cap behavior."""
import math

import numpy as np
import pytest

from optimization_framework.implementations import protected_diagnostics as diagnostics


class FixtureCandidate:
    def __init__(self, *, bad_decoder=False, skip_cap=False):
        self.bad_decoder = bad_decoder
        self.skip_cap = skip_cap

    def diagnostic(self, operation, payload):
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
