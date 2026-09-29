"""Reference source stays reusable without granting executable authority."""
import hashlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from framework_fixtures import researcher_idea
from test_framework_implementation_commands import prepare
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.implementations.references import ReferenceInput


def reference_payload(hypothesis_id):
    return {"hypothesis_id": hypothesis_id, "name": "Authors' reference PPO",
        "repository_url": "https://github.com/example/reference", "revision": "a" * 40,
        "entrypoints": ["main.py"], "files": {"main.py": "# Recorded source; never execute on import.\n"},
        "integration_notes": "Adapt the evaluation loop and verify optimizer state restoration."}


def test_reference_capture_is_idempotent_inspectable_and_does_not_enable_trials(tmp_path, monkeypatch):
    app, workspace, _, campaign, hypothesis = prepare(tmp_path, monkeypatch)
    hypothesis["algorithm"] = "authors_ppo"
    workspace.store.put("hypothesis", hypothesis)
    command = {"id": "record_reference", "campaign_id": campaign["id"], "expected_revision": campaign["version"],
        "operation": "implementation.reference", "payload": reference_payload(hypothesis["id"])}
    before = workspace.store.get(hypothesis["id"])
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    with TestClient(app) as client:
        result = client.post("/api/v1/commands", json=command)
        assert result.status_code == 200, result.text
        assert client.post("/api/v1/commands", json=command).json() == result.json()
        reference_id = result.json()["outcome"]["reference_id"]
        source = client.get("/api/v1/implementation-references/" + reference_id).json()
        assert source["files"] == command["payload"]["files"]
        assert source["file_hashes"]["main.py"] == hashlib.sha256(source["files"]["main.py"].encode()).hexdigest()
        state = client.get("/api/state", params={"campaign_id": campaign["id"]}).json()
        readiness = next(h for h in state["hypotheses"] if h["id"] == hypothesis["id"])["implementation_readiness"]
        assert readiness["state"] == "reference_available" and not readiness["runnable"]
        assert readiness["references"][0]["id"] == reference_id
        assert "files" not in readiness["references"][0]
        catalog = client.get("/api/implementations", params={"campaign_id": campaign["id"]}).json()
        assert catalog["references"][0]["id"] == reference_id
        assert not catalog["versions"]
    assert len(workspace.store.list("implementation_reference", campaign["id"])) == 1
    assert workspace.store.get(hypothesis["id"]) == before
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == guidance
    assert workspace.implementations.compute_committed(campaign["id"]) == 0
    other_method = researcher_idea(workspace, campaign["id"])
    other_method["algorithm"] = "new_hybrid"
    assert workspace.implementations.readiness(other_method)["state"] == "missing"


def test_reference_binding_rejects_another_campaign(tmp_path, monkeypatch):
    app, workspace, _, campaign, hypothesis = prepare(tmp_path, monkeypatch)
    other = workspace.create_campaign(CampaignInput(name="Other", tasks=[TaskInput(name="Other problem", problem_id="bounded_continuous")]))
    with TestClient(app) as client:
        result = client.post("/api/v1/commands", json={"id": "wrong_campaign_reference", "campaign_id": other["id"],
            "expected_revision": other["version"], "operation": "implementation.reference", "payload": reference_payload(hypothesis["id"])})
        assert result.status_code >= 400
    assert not workspace.store.list("implementation_reference")


@pytest.mark.parametrize("changes", [
    {"entrypoints": ["uncaptured.py"]}, {"files": {"../main.py": "source"}},
    {"repository_url": "javascript:alert(1)"}, {"repository_url": "https://user:secret@example.com/repo"},
    {"files": {"main.py": "x" * 500001}},
])
def test_reference_capture_validates_source_provenance(changes):
    with pytest.raises(ValidationError):
        ReferenceInput.model_validate({**reference_payload("hypothesis"), **changes})
