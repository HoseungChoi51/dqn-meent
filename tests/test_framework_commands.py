"""A command's allocation and outcome are one transaction, even under retries."""
from framework_fixtures import researcher_idea
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, CampaignUpdate, TaskInput
from optimization_framework.execution.service import Workspace


def prepare(tmp_path, autonomy="guided"):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Commands", autonomy=autonomy, compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={})]))
    task = workspace.current_tasks(campaign["id"])[0]
    command = Command(id="command_once", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="trial.create", payload={"task_id": task["id"], "algorithm": "coordinate", "max_steps": 5, "wall_seconds": 10})
    return workspace, campaign, command


def test_concurrent_retries_reserve_and_launch_only_one_experiment(tmp_path):
    workspace, campaign, command = prepare(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: workspace.commands.execute(command), range(2)))
    assert results[0] == results[1]
    assert len(workspace.store.list("trial")) == len(workspace.store.list("work_command")) == 1
    assert workspace.allocated_seconds(campaign["id"]) == 10
    workspace.update_campaign(campaign["id"], CampaignUpdate(name="New name"))
    assert workspace.commands.execute(command) == results[0]
    with pytest.raises(ValueError, match="identity"):
        workspace.commands.execute(command.model_copy(update={"payload": {**command.payload, "seed": 9}}))


def test_failure_before_commit_leaves_no_experiment_or_allocation(tmp_path, monkeypatch):
    workspace, campaign, command = prepare(tmp_path)
    original = workspace.commands._apply
    def fail(request, actor):
        original(request, actor)
        raise RuntimeError("Could not commit the command outcome")
    monkeypatch.setattr(workspace.commands, "_apply", fail)
    with pytest.raises(RuntimeError):
        workspace.commands.execute(command)
    assert workspace.store.list("trial") == workspace.store.list("work_command") == []
    assert workspace.allocated_seconds(campaign["id"]) == 0


def test_manager_commands_pin_current_guidance_and_resource_authority(tmp_path):
    workspace, campaign, command = prepare(tmp_path, "delegated")
    with pytest.raises(ValueError, match="pin guidance"):
        workspace.commands.execute(command, actor="manager")
    with pytest.raises(ValueError, match="pin guidance"):
        workspace.commands.execute(command, actor="manager")
    assert len(workspace.store.list("command_rejection")) == 1
    command = command.model_copy(update={"expected_guidance_revision": 0,
        "expected_authority_hash": workspace.commands.authority_hash(campaign)})
    outcome = workspace.commands.execute(command, actor="manager")
    assert outcome["actor"] == "manager"
    workspace.update_campaign(campaign["id"], CampaignUpdate(autonomy="guided"))
    with pytest.raises(ValueError, match="revision"):
        workspace.commands.execute(command.model_copy(update={"id": "another_command"}), actor="manager")


def test_versioned_http_command_returns_durable_outcome(tmp_path, monkeypatch):
    from optimization_framework.api.app import create_app
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="HTTP", compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={})]))
    task = workspace.current_tasks(campaign["id"])[0]
    payload = {"id": "http_command", "campaign_id": campaign["id"], "expected_revision": campaign["version"],
        "operation": "trial.create", "payload": {"task_id": task["id"], "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5}}
    with TestClient(app) as client:
        first = client.post("/api/v1/commands", json=payload)
        second = client.post("/api/v1/commands", json=payload)
        assert first.status_code == second.status_code == 200, first.text
        assert first.json() == second.json()
        assert client.get("/api/v1/commands/http_command").json() == first.json()


def commission(workspace, campaign, identity="build_once"):
    from optimization_framework.implementations.models import ImplementationSpec
    updated = workspace.update_campaign(campaign["id"], CampaignUpdate(implementation_compute_budget_seconds=20))
    hypothesis = researcher_idea(workspace, campaign["id"])
    return Command(id=identity, campaign_id=campaign["id"], expected_revision=updated["version"], operation="implementation.commission",
        payload={"hypothesis_id": hypothesis["id"], "compute_seconds": 10,
            "spec": ImplementationSpec(name="Coordinate variant", mechanism="A bounded specialized search",
                problem_id="bounded_continuous", capabilities=["continuous"], acceptance_criteria=["Feasible proposals"]).model_dump()})


def test_build_dispatch_survives_lost_acknowledgement_without_duplicate_job(tmp_path):
    workspace, campaign, _ = prepare(tmp_path)
    command = commission(workspace, campaign)
    class Client:
        jobs = {}
        deliveries = 0
        scans = 0
        def versions(self):
            self.scans += 1
            return []
        def submit(self, request):
            assert not workspace.store.in_transaction
            assert workspace.store.get(request["grant_id"], "implementation_grant")
            self.deliveries += 1
            job = self.jobs.setdefault(request["idempotency_key"], {"id": "job_once", "status": "queued"})
            if self.deliveries == 1:
                raise ValueError("Acknowledgement lost after accepting the job")
            return job
    client = Client()
    workspace.implementations.client = client
    first = workspace.commands.execute(command)
    assert len(client.jobs) == 1
    assert workspace.store.list("outbox")[0]["status"] == "pending"
    assert workspace.implementations.compute_committed(campaign["id"]) == 10
    restarted = Workspace(tmp_path, implementation_client=client)
    restarted.dispatch_outbox()
    assert restarted.commands.execute(command) == first
    assert len(client.jobs) == 1 and client.deliveries == 2
    assert client.scans == 1
    assert restarted.store.list("outbox")[0]["status"] == "completed"


def test_rolled_back_build_never_contacts_the_implementation_service(tmp_path, monkeypatch):
    workspace, campaign, _ = prepare(tmp_path)
    command = commission(workspace, campaign)
    monkeypatch.setattr(workspace.implementations.client, "submit", lambda request: pytest.fail("Uncommitted grant was dispatched"))
    apply = workspace.commands._apply
    def fail(request, actor):
        apply(request, actor)
        raise RuntimeError("Cannot commit outcome")
    monkeypatch.setattr(workspace.commands, "_apply", fail)
    with pytest.raises(RuntimeError):
        workspace.commands.execute(command)
    workspace.dispatch_outbox()
    assert workspace.store.list("implementation_grant") == workspace.store.list("outbox") == []


def test_manager_turn_retries_do_not_repeat_guidance_or_reset_status(tmp_path, monkeypatch):
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.contracts.requests import ResearchInput
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    workspace, campaign, _ = prepare(tmp_path)
    manager = CampaignManager(workspace)
    monkeypatch.setattr(manager, "_thread", lambda record: None)
    request = ResearchInput(campaign_id=campaign["id"], message="Review the evidence")
    first = manager.start(request, command_id="turn_once")
    revision = workspace.memory.state(campaign["id"])["guidance_revision"]
    assert manager.start(request, command_id="turn_once") == first
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == revision
    assert workspace.store.get("turn_once", "manager_command")["status"] == "dispatched"
    with pytest.raises(ValueError, match="identity"):
        manager.start(request.model_copy(update={"message": "A different request"}), command_id="turn_once")


def test_delegated_action_is_atomic_idempotent_and_keeps_manager_authority(tmp_path):
    from optimization_framework.campaigns.manager import CampaignManager
    workspace, campaign, command = prepare(tmp_path, "delegated")
    manager = CampaignManager(workspace)
    action = {"id": "proposal_once", "campaign_id": campaign["id"], "charter_version": campaign["version"],
        "guidance_revision": 0, "authority_hash": workspace.commands.authority_hash(campaign), "kind": "command",
        "command_operation": command.operation, "command_payload": command.payload}
    first = manager.execute_action(action, actor="manager")
    workspace.update_campaign(campaign["id"], CampaignUpdate(name="Renamed"))
    assert manager.execute_action(action, actor="manager") == first
    assert workspace.store.get("action_proposal_once", "work_command")["actor"] == "manager"
    assert len(workspace.store.list("trial")) == 1
    with pytest.raises(ValueError, match="another proposal"):
        manager.execute_action({**action, "command_payload": {**command.payload, "seed": 5}}, actor="manager")
