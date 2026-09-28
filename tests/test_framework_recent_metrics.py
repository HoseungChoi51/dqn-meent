"""A live monitor reads the journal tail without loading historical archives."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from optimization_framework.api.app import create_app
from optimization_framework.execution.metrics import recent_metrics
from optimization_framework.execution.service import Workspace
from optimization_framework.execution.worker import append_json


def journal(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.store.put("trial", {"id": "trial"})
    path = workspace.job_dir("trial") / "metrics.jsonl"
    path.parent.mkdir(parents=True)
    return workspace, path


def test_recent_records_preserve_recovery_order_and_torn_tail_semantics(tmp_path):
    workspace, path = journal(tmp_path)
    rows = [{"step": step, "attempt_id": attempt, "note": "재시작 λ", "archive": [{"candidate": [step]}]}
        for attempt, step in [("first", 1), ("first", 2), ("first", 3), ("recovery", 2), ("recovery", 3)]]
    path.write_text("\r\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n{"step":')
    assert workspace.metrics("trial", limit=3) == rows[-3:]
    assert recent_metrics(path, 20, block_bytes=11) == workspace.metrics("trial") == rows
    assert workspace.metrics("trial", limit=2, fields=("step", "missing")) == [{"step": 2}, {"step": 3}]
    # Actual worker recovery saves the torn bytes, then appends a new valid record.
    recovered = {"step": 4, "attempt_id": "recovery"}
    append_json(path, recovered)
    assert workspace.metrics("trial", limit=2) == [rows[-1], recovered]
    assert list(path.parent.glob("metrics.jsonl.torn-*"))
    # The ordinary reader also accepts a valid final JSON record without a newline.
    path.write_text(json.dumps(rows[0]) + '\nnot-json\n\n' + json.dumps(rows[1]))
    assert recent_metrics(path, 2, block_bytes=13) == workspace.metrics("trial")[-2:] == rows[:2]


def test_recent_view_observes_appends_replacements_truncation_and_removal(tmp_path):
    workspace, path = journal(tmp_path)
    assert workspace.metrics("trial", limit=20) == []
    path.write_text('{"step":1}\n')
    assert workspace.metrics("trial", limit=1) == [{"step": 1}]
    with path.open("a") as stream:
        stream.write('{"step":2}\n')
    assert workspace.metrics("trial", limit=1) == [{"step": 2}]
    replacement = path.with_suffix(".replacement")
    replacement.write_text('{"step":9}\n')
    replacement.replace(path)
    assert workspace.metrics("trial", limit=1) == [{"step": 9}]
    path.write_text("")
    assert workspace.metrics("trial", limit=20) == []
    path.unlink()
    assert workspace.metrics("trial", limit=20) == []


def test_monitor_reads_only_trailing_blocks_and_parses_only_requested_records(tmp_path, monkeypatch):
    workspace, path = journal(tmp_path)
    with path.open("w") as stream:
        for step in range(5000):
            stream.write(json.dumps({"step": step, "archive": "x" * 2000}) + "\n")
    original_open, original_loads = Path.open, json.loads
    reads, parsed = [], []

    class TrackedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def seek(self, *args):
            return self.stream.seek(*args)

        def read(self, count):
            assert 0 < count <= 65536
            result = self.stream.read(count)
            reads.append(len(result))
            return result

    def tracked_open(current, *args, **kwargs):
        stream = original_open(current, *args, **kwargs)
        if current == path:
            assert args == ("rb",), "The monitor must not read the whole text journal"
            return TrackedStream(stream)
        return stream

    def tracked_loads(raw, *args, **kwargs):
        value = original_loads(raw, *args, **kwargs)
        if isinstance(value, dict) and "step" in value:
            parsed.append(value["step"])
        return value

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(json, "loads", tracked_loads)
    rows = workspace.metrics("trial", limit=20)
    assert [row["step"] for row in rows] == list(range(4980, 5000))
    assert len(parsed) == 20
    assert sum(reads) <= 65536 < path.stat().st_size / 100


def test_metrics_http_limit_is_optional_bounded_and_preserves_full_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    workspace.store.put("trial", {"id": "trial"})
    path = workspace.job_dir("trial") / "metrics.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"step": step, "archive": [{"candidate": [step]}]} for step in range(30)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with TestClient(app) as client:
        assert client.get("/api/trials/trial/metrics").json() == rows
        assert client.get("/api/trials/trial/metrics?limit=20").json() == rows[-20:]
        assert client.get("/api/trials/trial/metrics?limit=1000").json() == rows
        for limit in (0, -1, 1001, "1.5", "invalid"):
            assert client.get("/api/trials/trial/metrics", params={"limit": limit}).status_code == 422
        assert client.get("/api/trials/missing/metrics?limit=20").status_code == 404


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_internal_recent_limit_rejects_invalid_sizes(tmp_path, limit):
    workspace, _ = journal(tmp_path)
    with pytest.raises(ValueError, match="integer from 1 through 1000"):
        workspace.metrics("trial", limit=limit)
