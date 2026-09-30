import json

from fastapi.testclient import TestClient
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
from types import SimpleNamespace

from optimization_framework.analysis.tensorboard_view import ScalarExporter
from optimization_framework.api.app import compact_trial, create_app
from optimization_framework.campaigns.memory import CampaignMemory
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.storage.sqlite import Store


class _Store:
    def list(self, kind):
        return [{"id": "trial_one", "campaign_id": "campaign_one", "algorithm": "optimizer", "seed": 7}] if kind == "trial" else []


class _Workspace:
    def __init__(self, directory):
        self.directory = directory
        self.store = _Store()

    def job_dir(self, trial_id):
        return self.directory / "trials" / trial_id


def test_scalar_exporter_tracks_new_observations_without_repeating_old_ones(tmp_path):
    workspace = _Workspace(tmp_path)
    journal = workspace.job_dir("trial_one") / "metrics.jsonl"
    journal.parent.mkdir(parents=True)
    first = {"updated_at": "2026-10-01T00:00:00+00:00", "evaluations": 1, "objective": 0.2,
             "best_objective": 0.2, "elapsed_seconds": 2.4, "solver_calls": 2, "cache_hits": 0}
    second = {**first, "evaluations": 2, "objective": 0.1, "best_objective": 0.2,
              "elapsed_seconds": 4.1, "solver_calls": 4}
    journal.write_text(json.dumps(first) + "\n")
    exporter = ScalarExporter(workspace)
    exporter.sync_once()
    with journal.open("a") as stream:
        stream.write(json.dumps(second) + "\n")
    exporter.sync_once()
    exporter.sync_once()
    exporter.stop()
    run = exporter.directory / "campaign_one" / "optimizer" / "seed-7-trial_one"
    accumulator = EventAccumulator(str(run))
    accumulator.Reload()
    assert [(point.step, round(point.value, 3)) for point in accumulator.Scalars("efficiency/best_by_evaluation")] == [(1, 0.2), (2, 0.2)]
    assert [(point.step, round(point.value, 3)) for point in accumulator.Scalars("efficiency/best_by_worker_second")] == [(2, 0.2), (4, 0.2)]


def test_tensorboard_mount_and_compact_state_keep_design_access(tmp_path):
    app = create_app(tmp_path / "workspace", start_workers=False)
    client = TestClient(app)
    assert client.get("/tensorboard/").status_code == 200
    assert client.get("/tensorboard/data/plugins_listing").status_code == 200
    trial = {"id": "trial_one", "pid": 123, "progress": {"best_candidate": [[1]], "best_design": [[1]],
             "archive": [{"candidate": [[1]]}], "best_objective": 0.2},
             "result": {"best_design": [[1]], "best_objective": 0.2}}
    compact = compact_trial(trial)
    assert compact["progress"]["best_design"] == [[1]]
    assert compact["result"]["best_objective"] == 0.2
    assert "archive" not in compact["progress"]
    assert "best_candidate" not in compact["progress"]
    assert "best_design" not in compact["result"]


def test_manager_journal_appends_only_new_events_and_repairs_partial_line(tmp_path):
    store = Store(tmp_path)
    memory = CampaignMemory(SimpleNamespace(directory=tmp_path, store=store))
    path = tmp_path / "journal.jsonl"
    store.put("campaign", {"id": "campaign_one"}, "campaign.created")
    memory._append_journal(path, "campaign_one", 1)
    memory._append_journal(path, "campaign_one", 1)
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == [1]
    with path.open("ab") as stream:
        stream.write(b'{"id":')
    store.put("task", {"id": "task_one", "campaign_id": "campaign_one"}, "task.created")
    memory._append_journal(path, "campaign_one", 2)
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == [1, 2]


def test_state_cache_invalidates_on_committed_event(tmp_path):
    app = create_app(tmp_path / "workspace", start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Cache check", compute_budget_seconds=100,
        validation_reserve_seconds=10, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={})]))
    client = TestClient(app)
    path = f"/api/state?campaign_id={campaign['id']}"
    before = client.get(path).json()
    workspace.store.put("manager_note", {"id": "note_one", "campaign_id": campaign["id"],
        "kind": "finding", "content": "New evidence"}, "manager.note")
    after = client.get(path).json()
    assert after["event_cursor"] > before["event_cursor"]
