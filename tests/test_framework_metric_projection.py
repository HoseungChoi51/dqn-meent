"""Metric projections reuse unchanged evidence and observe subsequent writes."""
import json

from optimization_framework.execution.metrics import MetricProjectionCache
from optimization_framework.execution.service import Workspace


def test_projection_reuses_scalar_columns_without_hiding_full_records(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    workspace.store.put("trial", {"id": "trial"})
    path = workspace.job_dir("trial") / "metrics.jsonl"
    path.parent.mkdir(parents=True)
    raw = {"best_objective": 3., "evaluations": 1, "archive": [{"candidate": [1, 2], "objective": 3.}]}
    path.write_text(json.dumps(raw) + "\n")
    fields = ("best_objective", "evaluations", "unknown_worker_cost")
    first = workspace.metrics("trial", fields=fields)
    assert first == [{"best_objective": 3., "evaluations": 1}]
    assert workspace.metrics("trial") == [raw]
    first[0]["best_objective"] = 100
    original_open = type(path).open
    def guarded_open(self, *args, **kwargs):
        assert self != path, "Unchanged projection reread the full journal"
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(type(path), "open", guarded_open)
    assert workspace.metrics("trial", fields=fields) == [{"best_objective": 3., "evaluations": 1}]


def test_projection_tracks_append_partial_line_replacement_and_removal(tmp_path):
    cache = MetricProjectionCache()
    path = tmp_path / "metrics.jsonl"
    fields = ("best_objective",)
    assert cache.read(path, fields) == []
    path.write_text('{"best_objective": 3}\n{"best_objective":')
    assert cache.read(path, fields) == [{"best_objective": 3}]
    with path.open("a") as out:
        out.write(' 2}\n')
    assert cache.read(path, fields) == [{"best_objective": 3}, {"best_objective": 2}]
    replacement = tmp_path / "new.jsonl"
    replacement.write_text('{"best_objective": 9}\n')
    replacement.replace(path)
    assert cache.read(path, fields) == [{"best_objective": 9}]
    path.write_text("")
    assert cache.read(path, fields) == []
    path.unlink()
    assert cache.read(path, fields) == []


def test_projection_cache_bounds_and_changed_during_read(tmp_path, monkeypatch):
    cache = MetricProjectionCache(max_points=2, max_files=1)
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text('{"x": 1}\n{"x": 2}\n')
    second.write_text('{"x": 3}\n')
    assert cache.read(first, ["x"]) == [{"x": 1}, {"x": 2}]
    cache.read(second, ["x"])
    assert cache._points == 1 and len(cache._entries) == 1
    assert (first, ("x",)) not in cache._entries
    with second.open("a") as out:
        out.write('{"x": 4}\n{"x": 5}\n')
    assert cache.read(second, ["x"]) == [{"x": 3}, {"x": 4}, {"x": 5}]
    assert not cache._entries
    versions = iter([(1,), (2,)])
    monkeypatch.setattr(cache, "_version", lambda path: next(versions))
    cache.read(first, ["x"])
    assert not cache._entries
