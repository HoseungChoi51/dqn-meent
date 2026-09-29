"""Service-owned, programmatic H12 fixtures. Candidate code receives inputs, not oracles."""
from __future__ import annotations

import hashlib
import math
import re

import numpy as np


MODES = ((2, 1), (4, 2), (6, 3), (8, 4))
SHAPE = (256, 128)


def _decoder_oracle(mode, coefficients):
    nx, ny = mode
    if len(coefficients) != (ny + 1) * (2 * nx + 1):
        raise ValueError("Invalid Fourier fixture dimension")
    x = (np.arange(SHAPE[0], dtype=np.float64) + .5) / SHAPE[0]
    y = (np.arange(SHAPE[1], dtype=np.float64) + .5) / SHAPE[1]
    level = np.zeros(SHAPE, dtype=np.float64)
    index = 0
    for n in range(ny + 1):
        ycos = np.cos(2 * math.pi * n * y)[None, :]
        level += coefficients[index] * ycos
        index += 1
        for p in range(1, nx + 1):
            level += coefficients[index] * np.cos(2 * math.pi * p * x)[:, None] * ycos
            level += coefficients[index + 1] * np.sin(2 * math.pi * p * x)[:, None] * ycos
            index += 2
    return (level >= 0).astype(np.uint8)


def _candidate_mask(optimizer, mode, coefficients):
    result = optimizer.diagnostic("decode_fourier_mask", {"mode": list(mode), "coefficients": coefficients})
    if not isinstance(result, dict) or not isinstance(result.get("mask"), list):
        raise ValueError("Decoder diagnostic must return {'mask': binary flat list}")
    values = np.asarray(result["mask"])
    if values.shape != (SHAPE[0] * SHAPE[1],) or values.dtype.kind not in "biu" or np.any((values != 0) & (values != 1)):
        raise ValueError("Decoder diagnostic returned a nonbinary or incorrectly sized mask")
    return values.astype(np.uint8).reshape(SHAPE)


def fourier_decoder(optimizer):
    cases = 0
    digests = []
    for mode in MODES:
        dimension = (mode[1] + 1) * (2 * mode[0] + 1)
        zero = [0.0] * dimension
        positive, negative = zero.copy(), zero.copy()
        positive[0], negative[0] = 1.0, -1.0
        mixed = zero.copy()
        mixed[0], mixed[1], mixed[2], mixed[2 * mode[0] + 1] = .173, .41, -.29, .23
        for coefficients in (zero, positive, negative, mixed,
                             [7.5 * value for value in mixed], [-value for value in mixed]):
            actual = _candidate_mask(optimizer, mode, coefficients)
            expected = _decoder_oracle(mode, coefficients)
            if not np.array_equal(actual, expected):
                raise ValueError(f"Fourier decoder differs from independent full-mask oracle at mode {mode}")
            if not np.array_equal(actual, actual[:, ::-1]):
                raise ValueError(f"Fourier decoder violates y reflection at mode {mode}")
            digests.append(hashlib.sha256(actual.tobytes()).hexdigest())
            cases += 1
    return {"modes": [list(mode) for mode in MODES], "cases": cases, "mask_digests": digests}


def _refactor(optimizer, matrix, rank):
    result = optimizer.diagnostic("covariance_refactor", {"matrix": matrix.tolist(), "rank": rank, "floor": .05, "cap": 4.0})
    if not isinstance(result, dict):
        raise ValueError("Covariance diagnostic must return a factorization")
    diagonal = np.asarray(result.get("diagonal"), dtype=np.float64)
    factors = np.asarray(result.get("low_rank"), dtype=np.float64)
    k = matrix.shape[0]
    if diagonal.shape != (k,) or factors.ndim != 2 or factors.shape[0] != k or factors.shape[1] > rank:
        raise ValueError("Covariance diagnostic returned an invalid factor shape")
    if not np.all(np.isfinite(diagonal)) or not np.all(np.isfinite(factors)) or np.min(diagonal) < .05 - 1e-10:
        raise ValueError("Covariance factors are nonfinite or below the floor")
    actual = np.diag(diagonal) + factors @ factors.T
    spectrum = np.linalg.eigvalsh((actual + actual.T) / 2)
    if spectrum[0] < .05 - 1e-8 or spectrum[-1] > 4 + 1e-8:
        raise ValueError("Actual reconstructed covariance violates the spectral floor or cap")
    return actual, result, spectrum


def covariance_refactor(optimizer):
    # The two-dimensional subspace embedded in the supported k=9 chart gives
    # an unshrunk rank-one factorization with lambda_max=5.475.
    k = 9
    u = np.zeros(k); u[:2] = 1 / math.sqrt(2)
    v = np.zeros(k); v[0], v[1] = 1 / math.sqrt(2), -1 / math.sqrt(2)
    matrix = .05 * np.eye(k) + 3.95 * np.outer(u, u) + 2.95 * np.outer(v, v)
    actual, _, spectrum = _refactor(optimizer, matrix, 1)
    unshrunk = np.diag([1.525, 1.525] + [.05] * (k - 2)) + 3.95 * np.outer(u, u)
    highest = float(np.linalg.eigvalsh(unshrunk)[-1])
    assert abs(highest - 5.475) < 1e-10
    shrink = (4 - .05) / (highest - .05)
    expected = .05 * np.eye(k) + shrink * (unshrunk - .05 * np.eye(k))
    if not np.allclose(actual, expected, atol=1e-8, rtol=1e-8):
        raise ValueError("Covariance refactor differs from the frozen H12 spectral correction")
    # Repeated eigenspaces must have a deterministic canonical basis, not an
    # arbitrary solver-dependent sign or rotation.
    tied = np.diag([4.0, 4.0, 3.0] + [.05] * (k - 3))
    _, first, _ = _refactor(optimizer, tied, 1)
    _, second, _ = _refactor(optimizer, tied, 1)
    if first != second:
        raise ValueError("Degenerate covariance eigenspace factorization is nondeterministic")
    return {"dimension": k, "unshrunk_max": highest, "shrink_factor": shrink,
            "post_shrink_min": float(spectrum[0]), "post_shrink_max": float(spectrum[-1]),
            "degenerate_replay": True}


def cache_identity(optimizer):
    """The exact cache key must bind mask bytes and all evaluator context."""
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[:81, :33] = 1
    mask = mask.ravel().tolist()
    context = {"scientific_identity": "fixture-physics-a", "evaluator_version": "fixture-solver-a",
               "fidelity": {"rcwa_order_x": 10, "rcwa_order_y": 5}, "objective": "mean_TE_TM"}

    def key(bits, settings):
        value = optimizer.diagnostic("cache_identity", {"mask": bits, "context": settings})
        result = value.get("key") if isinstance(value, dict) else None
        if not isinstance(result, str) or not re.fullmatch(r"[0-9a-f]{64}", result):
            raise ValueError("Cache diagnostic must return a SHA256 identity")
        return result

    first = key(mask, context)
    if key(mask, context) != first:
        raise ValueError("Identical mask and evaluator context changed cache identity")
    changed_mask = mask.copy()
    changed_mask[123] ^= 1
    if key(changed_mask, context) == first:
        raise ValueError("Different physical masks collide in cache identity")
    for field, value in (("scientific_identity", "fixture-physics-b"),
                         ("evaluator_version", "fixture-solver-b")):
        changed = {**context, field: value}
        if key(mask, changed) == first:
            raise ValueError(f"Cache identity ignores {field}")
    changed = {**context, "fidelity": {"rcwa_order_x": 6, "rcwa_order_y": 3}}
    if key(mask, changed) == first:
        raise ValueError("Cache identity ignores RCWA fidelity")
    return {"mask_cells": len(mask), "context_variations": 3, "mask_variations": 1}


def replay_context(create):
    """Compare live proposals across a checkpoint at the frozen evaluator identity."""
    from optimization_framework.contracts.problems import Observation
    from optimization_framework.implementations.models import digest
    n = SHAPE[0] * SHAPE[1]
    first, resumed = create(n, 731), None
    try:
        if first.contract != "optimizer_v1":
            raise ValueError("H12 replay requires optimizer_v1")
        objective = first.context["problem"]["primary_objective"]["name"]
        evaluator = first.context["parameters"].get("evaluation_context")
        if not isinstance(evaluator, dict) or not isinstance(evaluator.get("fidelity"), dict):
            raise ValueError("H12 replay needs a frozen evaluation_context in optimizer parameters")
        identity = digest({"instance": first.context["problem"]["scientific_identity"],
                           "fidelity": evaluator["fidelity"],
                           "evaluator": [evaluator["evaluator_id"], evaluator["evaluator_version"]]})

        def step(worker, index):
            proposals = worker.propose(1)
            proposal = proposals[0]
            candidate = worker.candidate_schema.canonicalize(proposal.candidate)
            observation = Observation(id=f"fixture_{index}", experiment_id="h12_replay",
                attempt_id="h12_replay", request_id=f"fixture_{index}", proposal_id=proposal.id,
                candidate=candidate, status="ok",
                objectives={name: .5 if index < 8 else (index % 3) / 3
                            for name in (objective, "te_plus1_transmission", "tm_plus1_transmission", "min_plus1_transmission")},
                evaluator_identity=identity, fidelity=evaluator["fidelity"])
            worker.observe([observation])
            return candidate

        for index in range(8):
            step(first, index)  # An all-equal population exercises no-information ties.
        state = first.state_dict()
        resumed = create(n, 731)
        resumed.load_state_dict(state)
        for index in range(8, 16):
            if step(first, index) != step(resumed, index):
                raise ValueError("Checkpoint continuation changed the mask sequence")
        return {"cells": n, "pre_checkpoint": 8, "replayed": 8,
                "tie_observations": 8, "frozen_evaluator_identity": identity}
    finally:
        first.close()
        if resumed is not None:
            resumed.close()
