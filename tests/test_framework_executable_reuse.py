"""Executable reuse is a scoped decision, independently of building or running."""
from framework_fixtures import researcher_idea
import copy

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.implementations.models import Package
from optimization_framework.implementations.reuse import assess
from optimization_framework.implementations.service import ImplementationService
from test_framework_evaluators import MockReviewer, package, request, specification
from test_framework_packages import SOURCE, spec
from test_framework_revalidation import Client, no_model


def setup(tmp_path, *, evaluator=False):
    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    specification_value = specification() if evaluator else spec()
    source = package() if evaluator else Package(files=[{"path": "optimizer.py", "content": SOURCE}])
    built = service.run_job(service.submit(request(spec=specification_value, package=source))["id"])
    assert built["status"] == "completed", built
    version = service.version(built["version_id"])
    workspace = Workspace(tmp_path / "workspace", implementation_client=Client(service))
    task = (TaskInput(name="Declared quadratic", problem_id=specification_value.manifest.id, evaluator_manifest=specification_value.manifest)
        if evaluator else TaskInput(name="Quadratic", problem_id="bounded_continuous", configuration={"dimensions": 2}))
    campaign = workspace.create_campaign(CampaignInput(name="Deliberate executable reuse", tasks=[task],
        implementation_compute_budget_seconds=60, validation_reserve_seconds=0))
    target = workspace.current_tasks(campaign["id"])[0] if evaluator else researcher_idea(workspace, campaign["id"])
    return workspace, service, campaign, target, version


def command(campaign, target, version, decision, *, key=None):
    return Command(id=key or decision, campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="implementation.reuse", payload={"study_id": campaign["active_study_id"], "version_id": version["id"],
            "task_id" if version.get("kind") == "evaluator" else "hypothesis_id": target["id"],
            "decision": decision, "rationale": "The current study needs this behavior" if decision == "reuse" else "Develop a specialized alternative for this problem"})


def test_decline_then_reuse_keep_exact_scope_and_do_not_launch_or_rebuild(tmp_path):
    workspace, service, campaign, hypothesis, version = setup(tmp_path)
    service.adapter_factory = no_model
    jobs = service.store.list("implementation_job")
    decline = workspace.commands.execute(command(campaign, hypothesis, version, "decline"))
    declined = workspace.store.get(decline["outcome"]["reuse_decision_id"], "reuse_decision")
    assert declined["decision"] == "decline" and declined["intended_use"] == "procedure"
    assert declined["consequences"]["version_id"] == version["id"] and declined["study_id"] == campaign["active_study_id"]
    assert not workspace.store.get(hypothesis["id"], "hypothesis").get("implementation_version_id")
    reuse_command = command(campaign, hypothesis, version, "reuse")
    receipt = workspace.commands.execute(reuse_command)
    decision = workspace.store.get(receipt["outcome"]["reuse_decision_id"], "reuse_decision")
    assert decision["authority"] == "researcher" and decision["consequences"]["artifact_digest"] == version["artifact_digest"]
    assert decision["consequences"]["applicability"]["eligible"]
    attached = workspace.store.get(hypothesis["id"], "hypothesis")
    assert attached["implementation_version_id"] == version["id"]
    costs = workspace.assets.attributed_costs([decision["asset_id"]])["quantities"]["evaluation_requests"]
    assert costs["known"] == 4 and costs["total"] is None and costs["unknown_provenance"] == 1
    assert service.store.list("implementation_job") == jobs and not workspace.store.list("trial")
    assert workspace.commands.execute(reuse_command) == receipt
    # Lose the delivery acknowledgement after the decision and attachment commit.
    effect = workspace.store.get(receipt["outcome"]["effect_id"], "outbox")
    effect["status"] = "pending"
    workspace.store.put("outbox", effect)
    restarted = Workspace(workspace.store.directory, implementation_client=Client(service))
    restarted.dispatch_outbox()
    assert restarted.store.get(effect["id"], "outbox")["status"] == "completed"
    assert restarted.store.list("reuse_decision") == [declined, decision]
    assert restarted.store.get(hypothesis["id"], "hypothesis") == attached
    assert service.store.list("implementation_job") == jobs


def test_evaluator_reuse_checks_the_full_manifest_and_freezes_its_binding(tmp_path):
    workspace, service, campaign, task, version = setup(tmp_path, evaluator=True)
    expected = assess(workspace.implementations, campaign["id"], version, task_id=task["id"])
    assert expected["eligible"], expected
    receipt = workspace.commands.execute(command(campaign, task, version, "reuse"))
    assert workspace.store.get(receipt["outcome"]["effect_id"], "outbox")["status"] == "completed"
    binding = workspace.evaluators.binding(task)
    assert binding["version_id"] == version["id"]
    altered = copy.deepcopy(version)
    altered["spec"]["manifest"]["primary_objective"]["units"] = "a different scientific quantity"
    assert not assess(workspace.implementations, campaign["id"], altered, task_id=task["id"])["eligible"]
    assert workspace.evaluators.binding(task) == binding
    decision = workspace.store.get(receipt["outcome"]["reuse_decision_id"], "reuse_decision")
    assert decision["consequences"]["task_id"] == task["id"]
    assert not workspace.store.list("trial") and not workspace.store.list("implementation_grant")


def test_fresh_revocation_blocks_reuse_before_an_effect_is_queued(tmp_path):
    workspace, service, campaign, hypothesis, version = setup(tmp_path)
    workspace.implementations.catalog(refresh=True)
    assert assess(workspace.implementations, campaign["id"], version, hypothesis_id=hypothesis["id"])["eligible"]
    service.revoke(version["id"], "Independent evidence invalidated this version")
    with pytest.raises(ValueError, match="Independent evidence invalidated"):
        workspace.commands.execute(command(campaign, hypothesis, version, "reuse"))
    assert not workspace.store.list("outbox") and not workspace.store.list("reuse_decision")
    assert not workspace.store.list("implementation_grant") and not workspace.store.list("trial")
    decline = workspace.commands.execute(command(campaign, hypothesis, version, "decline"))
    decision = workspace.store.get(decline["outcome"]["reuse_decision_id"], "reuse_decision")
    assert not decision["consequences"]["applicability"]["eligible"]
    assert workspace.store.get(hypothesis["id"], "hypothesis").get("implementation_version_id") is None


def test_declining_reuse_allows_a_new_specialized_implementation(tmp_path):
    workspace, service, campaign, hypothesis, version = setup(tmp_path)
    declined = workspace.commands.execute(command(campaign, hypothesis, version, "decline"))
    specialized = spec().model_copy(update={"name": "Specialized quadratic search", "mechanism": "A separately commissioned coordinate search for this study's quadratic instance"})
    receipt = workspace.commands.execute(Command(id="new-specialized", campaign_id=campaign["id"], expected_revision=campaign["version"],
        operation="implementation.commission", payload={"hypothesis_id": hypothesis["id"], "spec": specialized.model_dump(mode="json"),
            "package": Package(files=[{"path": "optimizer.py", "content": SOURCE}]).model_dump(mode="json"),
            "compute_seconds": 30, "max_calls": 1}))
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    result = service.run_job(grant["job_id"])
    assert result["status"] == "completed", result
    workspace.implementations.reconcile()
    assert result["version_id"] != version["id"]
    assert workspace.store.get(hypothesis["id"], "hypothesis")["implementation_version_id"] == result["version_id"]
    assert workspace.store.get(declined["outcome"]["reuse_decision_id"], "reuse_decision")["decision"] == "decline"
    assert len(service.store.list("implementation_job")) == 2 and not workspace.store.list("trial")
