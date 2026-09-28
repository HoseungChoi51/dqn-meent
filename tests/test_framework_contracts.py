"""Cross-domain scientific boundaries, portable artifacts, and repository durability."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from optimization_framework.contracts.problems import CandidateSchema, Constraint, Objective
from optimization_framework.evaluation.registry import problems
from optimization_framework.storage.artifacts import LocalArtifactStore
from optimization_framework.storage.sqlite import Store


def test_application_starts_with_numerical_domain_imports_blocked(tmp_path):
    code = '''
import importlib.abc, sys
class BlockNumerics(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'dqn_meent', 'meent', 'torch'}:
            raise AssertionError('Unexpected numerical import: ' + fullname)
sys.meta_path.insert(0, BlockNumerics())
from optimization_framework.api.app import create_app
app = create_app(sys.argv[1], start_workers=False)
assert app.title == 'Optimization Lab'
'''
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True,
                   env={**os.environ, "GRATING_LLM_DISABLED": "true"}, capture_output=True, text=True)


def test_continuous_minimization_has_raw_units_and_known_solution():
    instance = problems.resolve("bounded_continuous", {"center": [2., -1.], "weights": [1., 3.]})
    evaluator = problems.evaluator(instance)
    optimum = evaluator.evaluate([2., -1.])
    distant = evaluator.evaluate([-5., 5.])
    assert optimum.objectives["value"] == 0
    assert distant.objectives["value"] == 157
    assert instance.primary_objective.better(0, 157)
    assert instance.primary_objective.utility(157) == -157
    assert evaluator.evaluate([2., -1.]).cache_hit
    state = evaluator.checkpoint()
    restored = problems.evaluator(instance)
    restored.restore(state)
    assert restored.evaluate([-5., 5.]).solver_executions == 0


def test_rosenbrock_and_explicit_constraint_validation():
    instance = problems.resolve("bounded_continuous", {"function": "rosenbrock", "constraints": [
        {"name": "sum_cap", "coefficients": [1., 1.], "bound": 3.}]})
    assert problems.evaluator(instance).evaluate([1., 1.]).objectives["value"] == 0
    with pytest.raises(ValueError, match="sum_cap"):
        instance.candidate_schema.canonicalize([2., 2.])
    with pytest.raises(ValueError, match="bounds"):
        instance.candidate_schema.canonicalize([8., -4.])
    assert CandidateSchema(representation="discrete", dimensions=2, values=[-2, 3, 7]).canonicalize([7, -2]) == [7, -2]


def test_meent_fidelity_does_not_manufacture_new_scientific_instance():
    from dqn_meent.config import PhysicsConfig
    from dqn_meent.physics import ForwardSolver
    from dqn_meent.workspace.confirmation import physical_condition_key
    a = problems.resolve("meent_grating", {"n_cells": 4}, {"fourier_order": 1})
    b = problems.resolve("meent_grating", {"n_cells": 4}, {"fourier_order": 480})
    assert a.scientific_identity == b.scientific_identity == physical_condition_key({"n_cells": 4})
    assert a.evaluation_identity != b.evaluation_identity
    measured = problems.evaluator(a).evaluate([0, 1, 1, 0])
    reference = ForwardSolver(PhysicsConfig(n_cells=4, fourier_order=1)).evaluate([0, 1, 1, 0])
    assert measured.objectives["efficiency"] == reference.efficiency
    assert a.primary_objective.better(1., 0.)


def test_immutable_records_and_event_cursor_never_skip_a_backlog(tmp_path):
    store = Store(tmp_path)
    first = store.put_immutable("study", {"id": "study_1", "goal": "bounded optimization"})
    store.put_immutable("study", first)
    with pytest.raises(ValueError, match="immutable"):
        store.put("study", {"id": "study_1", "goal": "different science"})
    for n in range(7):
        store.event(None, "test", {"n": n})
    first_page = store.events(limit=3)
    second_page = store.events(after=first_page[-1]["id"], limit=3)
    final_page = store.events(after=second_page[-1]["id"], limit=3)
    assert [e["data"]["n"] for e in first_page + second_page + final_page] == list(range(7))


def test_checkpoint_larger_than_16_mib_is_chunked_verified_and_atomic(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    data = b"a" * (17 * 1024**2) + b"tail"
    manifest = store.publish_checkpoint(tmp_path / "checkpoints", io.BytesIO(data), {"step": 10}, chunk_bytes=4 * 1024**2)
    assert len(manifest["chunks"]) == 5
    with store.checkpoint_stream(manifest) as stream:
        assert stream.read() == data
    with pytest.raises(ValueError, match="allowance"):
        store.publish_checkpoint(tmp_path / "checkpoints", io.BytesIO(data), {"step": 20}, max_bytes=1024)
    assert json.loads((tmp_path / "checkpoints/latest.json").read_text())["id"] == manifest["id"]
    chunk = manifest["chunks"][-1]
    from optimization_framework.contracts.experiments import ArtifactReference
    store.resolve(ArtifactReference(**chunk)).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        store.checkpoint_stream(manifest)


def test_verified_external_blob_becomes_visibly_unavailable(tmp_path):
    external = tmp_path / "historical"
    external.write_bytes(b"history")
    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = store.register_external(external)
    with store.open(ref) as source:
        assert source.read() == b"history"
    external.unlink()
    with pytest.raises(FileNotFoundError):
        store.open(ref)
