"""CLI uses the application's commands, scheduler lease and evidence exports."""
from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dqn_meent.config import ExperimentConfig, PhysicsConfig, TrainConfig
from dqn_meent.recorded import execute, evaluate
from optimization_framework.api.app import create_app
from optimization_framework.cli import Session
from optimization_framework.contracts.commands import Command
from optimization_framework.execution.service import Workspace
from optimization_framework.storage.sqlite import Store, read_json


def tiny_config():
    return ExperimentConfig(physics=PhysicsConfig(n_cells=4, fourier_order=1, material="constant", silicon_n=3.5, silicon_k=0),
        training=TrainConfig(total_steps=4, horizon=3, seed=123, batch_size=2, buffer_size=16,
            learning_starts=2, checkpoint_interval=2, hidden_sizes=(8, 8), torch_threads=1))


def test_cli_reports_a_stale_export_delivery_instead_of_waiting_forever(tmp_path, monkeypatch):
    from optimization_framework.contracts.assets import Asset
    from optimization_framework.contracts.requests import CampaignInput, TaskInput
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    with Session(directory=tmp_path) as session:
        workspace = session.workspace
        campaign = workspace.create_campaign(CampaignInput(name="CLI delivery", autonomy="manual",
            tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
        workspace.assets.publish(Asset(id="finding", campaign_id=campaign["id"], kind="finding", title="Prior observation",
            authority="researcher", created_at="historical"))
        with workspace.outbox_lock:
            outcome = session.command("bundle.export", campaign["id"], {"asset_ids": ["finding"]}, identity="export_before_direction_change")
            current = workspace.memory.sync(campaign["id"])
            workspace.memory.edit(campaign["id"], "Defer this export until its evidence is reviewed.", current["revision"])
        workspace.dispatch_outbox()
        with pytest.raises(ValueError, match="direction changed"):
            session.operation(outcome["operation_id"])
        assert workspace.store.get(outcome["effect_id"])["status"] == "failed"
        assert not list(workspace.directory.glob("bundles/*.zip"))


def test_local_cli_records_actual_numerics_and_projects_legacy_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    output = tmp_path / "baseline"
    result = execute(tiny_config(), output, method="random", budget=3, seed=7, wall_seconds=30)
    assert result["status"] == "completed" and result["evaluation_requests"] == 3
    store = Store(output / ".workspace")
    assert len(store.list("trial")) == 1 and len(store.list("experiment_spec")) == 1
    assert len(store.list("resource_reservation")) == 1 and len(store.list("execution_attempt")) == 1
    assert store.list("cost_event") and store.list("asset")
    assert {row["request"]["operation"] for row in store.list("work_command")} == {"campaign.create", "trial.create", "bundle.export"}
    assert not store.list("research_run")
    for name in ("metrics.csv", "checkpoint.pt", "config.json", "best_design.npy", "summary.json", "evidence.zip", "cli-run.json"):
        assert (output / name).is_file()
    with pytest.raises(FileExistsError):
        execute(tiny_config(), output, method="random", budget=3, seed=7, wall_seconds=30)
    # Reading a completed checkpoint retains the same job, attempts and costs.
    before = store.list("cost_event")
    resumed = execute(tiny_config(), output, method="random", budget=3, seed=7, wall_seconds=30, resume=output / "checkpoint.pt")
    assert resumed["trial_id"] == result["trial_id"] and store.list("cost_event") == before
    with pytest.raises(ValueError, match="frozen procedure"):
        execute(replace(tiny_config(), training=replace(tiny_config().training, total_steps=5)), output,
            method="random", budget=3, seed=7, wall_seconds=30, resume=output / "checkpoint.pt")
    checked = evaluate(output, [1, 2], policy=False, wall_seconds=30)
    assert [row["fourier_order"] for row in checked["best_discovered"]["results"]] == [1, 2]
    assert len(store.list("trial")) == 2 and store.list("validation_result")
    before = store.list("cost_event")
    assert evaluate(output, [1, 2], policy=False, wall_seconds=30) == checked
    assert store.list("cost_event") == before and len(store.list("trial")) == 2


def test_cli_dqn_and_policy_reevaluation_use_separate_costed_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    output = tmp_path / "dqn"
    result = execute(tiny_config(), output, wall_seconds=30)
    assert result["steps"] == 4 and result["updates"] == 3 and result["completed"]
    report = evaluate(output, [1, 2], wall_seconds=30)
    assert len(report["greedy_policy"]["trajectory"]) == 3
    store = Store(output / ".workspace")
    assert len(store.list("trial")) == 4
    assert {row["algorithm"] for row in store.list("trial")} == {"dqn", "artifact_inference", "validate"}
    assert len(store.list("reuse_decision")) == 1 and len(store.list("execution_attempt")) == 4
    assert not store.list("research_run")


def test_old_standalone_run_is_imported_as_history_before_new_measurements(tmp_path, monkeypatch):
    import numpy as np
    from optimization_framework.storage.artifacts import atomic_json
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    output = tmp_path / "historical"
    output.mkdir()
    atomic_json(output / "config.json", tiny_config().to_dict())
    atomic_json(output / "summary.json", {"best_efficiency": .3, "note": "Historical claim, not a new measurement"})
    np.save(output / "best_design.npy", np.array([1, 0, 1, 0], dtype=np.uint8))
    original = (output / "summary.json").read_bytes()
    report = evaluate(output, [1, 2], policy=False, wall_seconds=30)
    assert report["historical_import"]["plan"]["cost_provenance"] == "unknown"
    assert (output / "summary.json").read_bytes() == original
    store = Store(output / ".workspace")
    assert len(store.list("trial")) == 2
    historical = [row for row in store.list("archived_record") if row["reference"]["kind"] == "trial"]
    assert len(historical) == 1 and historical[0]["data"]["scientific_status"] == "retrospective"
    assert store.get(report["historical_import"]["capture"]["solution_asset_id"], "asset")["exposure_status"] == "unknown"
    before = store.list("cost_event")
    assert evaluate(output, [1, 2], policy=False, wall_seconds=30) == report
    assert store.list("cost_event") == before and len(store.list("archived_record")) == 4


def test_local_cli_refuses_an_existing_owner_without_admitting_work(tmp_path):
    owner = Workspace(tmp_path / "workspace")
    owner.start()
    try:
        with pytest.raises(RuntimeError, match="--workspace-url"):
            with Session(directory=owner.directory):
                pytest.fail("A second CLI scheduler acquired the lease")
        assert not owner.store.list("campaign") and not owner.store.list("work_command")
    finally:
        owner.close()


def test_connected_cli_reconciles_lost_acknowledgement_and_rejects_another_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path / "server", start_workers=False)
    with TestClient(app) as client:
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
        command = Command(id="cli_create", campaign_id="campaign_cli", operation="campaign.create", expected_revision=0,
            payload={"name": "Connected", "tasks": [{"name": "Quadratic", "problem_id": "bounded_continuous"}]})
        with Session(url="http://workspace.test", journal=tmp_path / "journal") as session:
            original = session.request
            writes = []
            def lost(method, path, **kwargs):
                response = original(method, path, **kwargs)
                if method == "POST":
                    writes.append(kwargs["json"])
                    raise httpx.ReadTimeout("Accepted response was lost")
                return response
            monkeypatch.setattr(session, "request", lost)
            with pytest.raises(httpx.ReadTimeout):
                session.submit(command)
            receipt = session.submit(command)
            assert receipt["outcome"]["campaign"]["id"] == command.campaign_id
            assert len(writes) == 1 and len(app.state.workspace.store.list("campaign")) == 1
            session.workspace_id = "a_different_workspace"
            with pytest.raises(ValueError, match="different workspace"):
                session.submit(command)
    saved = read_json(tmp_path / "journal/cli_create.json")
    assert saved["receipt"] == receipt
