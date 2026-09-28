"""Reproduction is a new command-owned execution of one exact historical snapshot."""
from copy import deepcopy
import shutil

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.drafts import DraftSaveInput
from optimization_framework.contracts.requests import ControlInput, TrialInput, CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.storage import history
from test_framework_bundles import export, inspect, publish
from test_framework_provenance import campaign, finish as finish_original
from test_framework_supervision import finish


def imported(tmp_path):
    source, owner, task = campaign(tmp_path / "source")
    trial = source.create_trial(TrialInput(campaign_id=owner, task_id=task,
        algorithm="coordinate", seed=7, max_steps=4, wall_seconds=15))
    finish_original(source, trial)
    original = source.store.get(trial["id"], "trial")
    _, archive = export(source, owner, original["output_asset_ids"])
    portable = tmp_path / "evidence.zip"
    shutil.copyfile(archive, portable)
    source.directory.rename(tmp_path / "original-unavailable")
    destination, current, task = campaign(tmp_path / "destination")
    publish(destination, current, inspect(destination, current, portable))
    reference = history.find(destination.store, original["id"], "trial")[0]["reference"]
    return destination, current, task, reference, original


def command(workspace, owner, operation, payload, key):
    request = Command(id=key, campaign_id=owner, expected_revision=workspace.store.get(owner, "campaign")["version"],
        operation=operation, payload=payload)
    return request, workspace.commands.execute(request)


def draft(workspace, owner, task, reference):
    request, result = command(workspace, owner, "reproduction.draft",
        {"reference": reference, "task_id": task, "wall_seconds": 15}, "reproduce_draft")
    assert workspace.commands.execute(request) == result
    return workspace.store.get(result["outcome"]["draft_id"], "experiment_draft")


def launch(workspace, owner, saved):
    ready = workspace.drafts.readiness(saved["id"])
    assert ready["ready"], ready["blockers"]
    return command(workspace, owner, "draft.launch", {"draft_id": saved["id"],
        "expected_draft_revision": saved["revision"], "expected_readiness_hash": ready["readiness_hash"]}, "launch_reproduction")


def test_cross_directory_command_reproduces_and_exports_exact_reference_without_historical_costs(tmp_path, monkeypatch):
    workspace, owner, task, reference, original = imported(tmp_path)
    assert not workspace.store.list("trial")
    assert workspace.reproductions.sources(owner)[0]["reference"] == reference
    assert workspace.assets.actual_costs(owner)["event_count"] == 0
    def replaced(*args, **kwargs):
        pytest.fail("Installed preparation must not reinterpret the historical procedure")
    monkeypatch.setattr("optimization_framework.campaigns.drafts.prepare", replaced)
    saved = draft(workspace, owner, task, reference)
    request, result = launch(workspace, owner, saved)
    trial = workspace.store.get(result["outcome"]["trial_id"], "trial")
    assert trial["id"] != original["id"] and trial["campaign_id"] == owner
    assert trial["reproduction"]["reference"] == reference
    assert trial["experiment_spec"]["schedule"]["reproduction"] == trial["reproduction"]
    assert trial["execution_manifest"] == original["execution_manifest"]
    assert trial["isolation_policy"] and not trial["confirmatory"] and not trial["confirmation_protocol_id"]
    completed = finish(workspace, trial)
    assert completed["result"]["scientific_complete"], completed.get("reason")
    comparison = workspace.store.get(completed["reproduction_comparison_id"], "reproduction_comparison")
    assert comparison["outcome"] == "agreement", comparison
    assert comparison["measurements"]["best_candidate"]["agrees"]
    assert workspace.assets.attributed_costs(completed["output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 4
    assert workspace.assets.actual_costs(owner)["quantities"]["evaluation_requests"]["total"] == 4
    costs = workspace.store.list("cost_event")
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    assert restarted.commands.execute(request) == result
    restarted.capture_evidence(restarted.store.get(trial["id"], "trial"))
    assert restarted.store.list("cost_event") == costs
    assert restarted.store.list("reproduction_comparison") == [comparison]
    assert len(restarted.store.list("trial")) == 1
    assert history.exact(restarted.store, reference).data == original
    _, archive = export(restarted, owner, completed["output_asset_ids"], "export_reproduction")
    from optimization_framework.storage.bundles import Reader
    from optimization_framework.contracts.bundles import RecordKey
    with Reader(archive) as reader:
        records = reader.verify()
        assert reference in [record.reference.model_dump(mode="json") for record in records]
        assert any(record.reference.kind == "reproduction_comparison" and record.data == comparison for record in records)
        assert any(edge.role == "historical_reference" and edge.dependency == RecordKey(**reference).key for edge in reader.manifest.edges)


def test_reproduction_revision_preserves_science_and_snapshot_while_allowing_resource_changes(tmp_path):
    workspace, owner, task, reference, _ = imported(tmp_path)
    saved = draft(workspace, owner, task, reference)
    def revise(**values):
        return workspace.drafts.save(owner, DraftSaveInput(draft_id=saved["id"], expected_draft_revision=saved["revision"],
            title=saved["title"], procedure=values.pop("procedure", saved["procedure"]),
            reproduction=values.pop("reproduction", saved["reproduction"]), **values))
    with pytest.raises(ValueError, match="historical procedure"):
        revise(procedure={**saved["procedure"], "seed": 8})
    with pytest.raises(ValueError, match="retain its selected"):
        revise(reproduction=None)
    revised = revise(procedure={**saved["procedure"], "wall_seconds": 12, "max_steps": 5})
    assert revised["revision"] == 2 and revised["procedure"]["completion"] == saved["procedure"]["completion"]
    assert workspace.store.get(saved["revision_id"], "draft_revision")["reproduction"] == saved["reproduction"]


def test_missing_capture_and_incompatible_problem_remain_draft_blockers_before_allocation(tmp_path):
    workspace, owner, task, reference, original = imported(tmp_path)
    saved = draft(workspace, owner, task, reference)
    from optimization_framework.assets.captures import experiment
    directory, manifest, _ = experiment(workspace, reference)
    member = next(name for name in manifest["files"] if name.endswith(".py"))
    (directory / "code" / member).write_text("Changed source")
    readiness = workspace.drafts.readiness(saved["id"])
    assert not readiness["ready"] and "Materialized captured source changed" in str(readiness["blockers"])
    with pytest.raises(ValueError, match="not ready"):
        command(workspace, owner, "draft.launch", {"draft_id": saved["id"], "expected_draft_revision": 1,
            "expected_readiness_hash": readiness["readiness_hash"]}, "blocked_launch")
    assert not workspace.store.list("trial") and workspace.allocated_seconds(owner) == 0
    other = workspace.create_campaign(CampaignInput(name="Unrelated campaign", validation_reserve_seconds=0,
        tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    assert workspace.reproductions.sources(other["id"]) == []
    with pytest.raises(ValueError, match="Import this exact"):
        workspace.reproductions.save(other["id"], {"task_id": workspace.current_tasks(other["id"])[0]["id"], "reference": reference})
    assert history.exact(workspace.store, reference).data == original


def test_incomplete_attempt_keeps_inconclusive_comparison_after_budget_amendment_and_resume(tmp_path):
    workspace, owner, task, reference, _ = imported(tmp_path)
    saved = draft(workspace, owner, task, reference)
    saved = workspace.drafts.save(owner, DraftSaveInput(draft_id=saved["id"], expected_draft_revision=1,
        title=saved["title"], reproduction=saved["reproduction"], procedure={**saved["procedure"], "max_steps": 2}))
    _, result = launch(workspace, owner, saved)
    trial = finish(workspace, workspace.store.get(result["outcome"]["trial_id"], "trial"))
    first = workspace.store.get(trial["reproduction_comparison_id"], "reproduction_comparison")
    assert trial["status"] == "completed" and not trial["result"]["scientific_complete"]
    assert first["outcome"] == "inconclusive"
    frozen = deepcopy(trial["experiment_spec"])
    trial = workspace.control(trial["id"], ControlInput(action="extend", max_steps=4, rationale="Finish the frozen reproduction procedure"))
    trial = finish(workspace, trial)
    assert trial["experiment_spec"] == frozen
    second = workspace.store.get(trial["reproduction_comparison_id"], "reproduction_comparison")
    assert second["outcome"] == "agreement", second
    assert second["attempt"] == 2 and workspace.store.get(first["id"], "reproduction_comparison") == first
    assert set(trial["reproduction_comparison_ids"]) == {first["id"], second["id"]}


def test_generated_evaluator_reproduction_requires_current_validation_and_verified_legacy_relocation(tmp_path, monkeypatch):
    from test_framework_bundles import Client
    from test_framework_evaluators import specification
    from test_framework_revalidation import campaign_fixture, no_model
    from optimization_framework.implementations.service import ImplementationService
    from optimization_framework.implementations.runtime import prepare_runtime, verify_runtime
    from optimization_framework.implementations.runtime_resolution import resolve, receipt
    from optimization_framework.implementations.models import digest
    def old_runtime(*args, **kwargs):
        root, modern = prepare_runtime(*args, **kwargs)
        binding = verify_runtime(root, modern)
        legacy = {key: modern[key] for key in ("protocol", "python", "executable_hash", "dependencies", "files")}
        legacy.update(executable=binding["executable"], stdlib=binding["stdlib"],
            libraries={path: modern["libraries"][name] for name, paths in binding["libraries"].items() for path in paths})
        legacy["digest"] = digest(legacy)
        return root, legacy
    spec = specification()
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.implementations.service.prepare_runtime", old_runtime)
        source, library, campaign_record, task = campaign_fixture(tmp_path / "source", spec=spec)
    source.implementations.client = Client(library)
    original = source.create_trial(TrialInput(campaign_id=campaign_record["id"], task_id=task["id"],
        algorithm="coordinate", max_steps=3, wall_seconds=15))
    finish_original(source, original)
    original = source.store.get(original["id"], "trial")
    _, archive = export(source, campaign_record["id"], original["output_asset_ids"])
    portable = tmp_path / "legacy-evidence.zip"
    shutil.copyfile(archive, portable)
    (tmp_path / "source").rename(tmp_path / "original-unavailable")
    service = ImplementationService(tmp_path / "destination-library", adapter_factory=no_model)
    client = Client(service)
    client.resolve_runtime = lambda version, request: resolve(service, version, request)
    client.runtime_resolution = lambda owner, key: receipt(service, owner, key)
    workspace = Workspace(tmp_path / "destination", implementation_client=client)
    campaign_record = workspace.create_campaign(CampaignInput(name="Reproduce legacy evaluator", validation_reserve_seconds=0,
        implementation_compute_budget_seconds=60, tasks=[TaskInput(name="Original problem", problem_id=spec.manifest.id, evaluator_manifest=spec.manifest)]))
    owner, task = campaign_record["id"], workspace.current_tasks(campaign_record["id"])[0]
    publish(workspace, owner, inspect(workspace, owner, portable))
    reference = history.find(workspace.store, original["id"], "trial")[0]["reference"]
    saved = draft(workspace, owner, task["id"], reference)
    missing = workspace.drafts.readiness(saved["id"])
    assert not missing["ready"] and not workspace.store.list("trial")
    version_id = original["evaluator_version_id"]
    before = service.version(version_id)
    assert before["status"] == "validation_required"
    artifact = deepcopy(service.artifact(version_id, ready=False)["artifact"])
    _, resolved = command(workspace, owner, "implementation.resolve_runtime", {"version_id": version_id}, "resolve_legacy")
    assert workspace.store.get(resolved["outcome"]["effect_id"], "outbox")["status"] == "completed"
    conversion = service.store.list("runtime_resolution_receipt")[0]["conversion"]
    assert conversion["original_runtime_digest"] == before["runtime_digest"]
    assert service.version(version_id) == before
    _, submitted = command(workspace, owner, "implementation.revalidate", {"version_id": version_id,
        "checks": {"kind": "evaluator", "rationale": "Independent local validation of this exact imported version"}, "compute_seconds": 30}, "revalidate_import")
    grant = workspace.store.get(submitted["outcome"]["grant_id"], "implementation_grant")
    checked = service.run_job(grant["job_id"])
    assert checked["status"] == "completed", checked
    workspace.implementations.reconcile()
    command(workspace, owner, "evaluator.attach", {"task_id": task["id"], "version_id": version_id,
        "rationale": "Use the independently rechecked original evaluator"}, "attach_exact")
    _, started = launch(workspace, owner, saved)
    completed = finish(workspace, workspace.store.get(started["outcome"]["trial_id"], "trial"))
    comparison = workspace.store.get(completed["reproduction_comparison_id"], "reproduction_comparison")
    assert comparison["outcome"] == "agreement", comparison
    assert comparison["runtime_limitations"] == conversion["limitations"]
    assert service.artifact(version_id)["artifact"] == artifact
    assert service.version(version_id)["runtime_digest"] == before["runtime_digest"]
    assert workspace.assets.attributed_costs(completed["output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 43
    assert workspace.assets.actual_costs(owner)["quantities"]["evaluation_requests"]["total"] == 23
    assert len(service.store.list("implementation_job")) == 1
    # A later local conversion cannot silently replace a frozen legacy binding.
    from optimization_framework.implementations.runtime import bundle_runtime_root
    from optimization_framework.implementations.runtime_conversion import report
    from optimization_framework.storage.sqlite import atomic_json
    import json
    root = bundle_runtime_root(service.artifact(version_id))
    (root / "stdlib" / "new_local_marker.py").write_text("# A later local standard-library installation\n")
    from optimization_framework.implementations.runtime import tree_hashes
    resolved_manifest = json.loads((root / "resolved-runtime.json").read_text())
    resolved_manifest["stdlib_files"] = tree_hashes(root / "stdlib", include_bytecode=True)
    resolved_manifest["digest"] = digest({key: value for key, value in resolved_manifest.items() if key != "digest"})
    binding = json.loads((root / "binding.json").read_text())
    binding["runtime_digest"] = resolved_manifest["digest"]
    atomic_json(root / "binding.json", binding)
    atomic_json(root / "resolved-runtime.json", resolved_manifest)
    atomic_json(root / "conversion.json", report(artifact["runtime"], resolved_manifest))
    verify_runtime(root, artifact["runtime"])
    with pytest.raises(ValueError, match="resolved legacy runtime changed"):
        workspace._start_trial(completed)
    assert completed["attempt"] == 1


def test_original_input_reuse_requires_a_new_decision_and_retains_only_its_upstream_cost(tmp_path):
    from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice
    source, owner, task = campaign(tmp_path / "source")
    source.assets.record_cost(CostEvent(id="original_prefix", campaign_id=owner, source_id="original_prefix", ordinal=0,
        category="evaluation", quantities={"evaluation_requests": 7}, created_at="fixture"))
    asset = source.assets.publish(Asset(id="original_input", campaign_id=owner, kind="solution", title="A measured starting candidate",
        payload={"candidate": [0., 0.], "problem": source.store.get(task, "task")["problem"]},
        costs=[CostSlice(source_id="original_prefix", stop=1)], cost_provenance="complete", authority="researcher", created_at="fixture"))
    _, declared = command(source, owner, "asset.reuse", {"asset_id": asset["id"],
        "study_id": source.store.get(owner, "campaign")["active_study_id"], "decision": "reuse", "intended_use": "optimizer_input",
        "rationale": "Explicit original input"}, "original_reuse")
    original = source.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="evaluate_asset", max_steps=1, wall_seconds=15,
        initial_assets=[asset["id"]], reuse_decision_ids=[declared["outcome"]["reuse_decision_id"]]))
    finish_original(source, original)
    original = source.store.get(original["id"], "trial")
    _, archive = export(source, owner, original["output_asset_ids"])
    workspace, owner, task = campaign(tmp_path / "destination")
    publish(workspace, owner, inspect(workspace, owner, archive))
    reference = history.find(workspace.store, original["id"], "trial")[0]["reference"]
    saved = draft(workspace, owner, task, reference)
    readiness = workspace.drafts.readiness(saved["id"])
    assert not readiness["ready"] and any(row["code"] == "input_unavailable" for row in readiness["blockers"])
    assert readiness["reproduction_inputs"][0]["decisions"] == []
    _, chosen = command(workspace, owner, "asset.reuse", {"asset_id": asset["id"],
        "study_id": saved["study_id"], "decision": "reuse", "intended_use": "optimizer_input",
        "rationale": "Reproduce using exactly the originally declared candidate"}, "new_reuse")
    saved = workspace.drafts.save(owner, DraftSaveInput(draft_id=saved["id"], expected_draft_revision=1,
        title=saved["title"], reproduction=saved["reproduction"], procedure={**saved["procedure"],
            "reuse_decision_ids": [chosen["outcome"]["reuse_decision_id"]]}))
    _, started = launch(workspace, owner, saved)
    completed = finish(workspace, workspace.store.get(started["outcome"]["trial_id"], "trial"))
    assert workspace.store.get(completed["reproduction_comparison_id"], "reproduction_comparison")["outcome"] == "agreement"
    assert workspace.assets.actual_costs(owner)["quantities"]["evaluation_requests"]["total"] == 1
    assert workspace.assets.attributed_costs(completed["output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 8


def test_comparison_retains_disagreement_and_respects_frozen_tolerances(tmp_path):
    workspace, owner, task, reference, _ = imported(tmp_path)
    saved = draft(workspace, owner, task, reference)
    _, started = launch(workspace, owner, saved)
    completed = finish(workspace, workspace.store.get(started["outcome"]["trial_id"], "trial"))
    agreed = workspace.store.get(completed["reproduction_comparison_id"], "reproduction_comparison")
    # A separately supplied result counterexample tests the assessment boundary;
    # it is not presented as another physical execution or charged as one.
    counterexample = deepcopy(completed)
    counterexample["result"]["best_objective"] += 1
    changed = workspace.reproductions.compare(counterexample)
    assert changed["outcome"] == "disagreement" and not changed["measurements"]["primary_objective"]["agrees"]
    assert workspace.store.get(agreed["id"], "reproduction_comparison") == agreed
    from optimization_framework.campaigns.reproduction import close_values
    assert close_values([.1, {"x": 3}], [.1 + 1e-11, {"x": 3}], 1e-10, 0)
    assert not close_values([1, 2], [1, 2, 3], 100, 100)
    assert not close_values([True], [1], 100, 100)


def test_missing_execution_capability_is_a_readiness_requirement_and_manager_retains_the_reference(tmp_path, monkeypatch):
    workspace, owner, task, reference, _ = imported(tmp_path)
    saved = draft(workspace, owner, task, reference)
    from optimization_framework.execution.isolation import IsolationUnavailable
    def missing(name):
        raise IsolationUnavailable("Imported source execution requires bwrap")
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.execution.isolation._program", missing)
        readiness = workspace.drafts.readiness(saved["id"])
        assert not readiness["ready"] and "requires bwrap" in str(readiness["blockers"])
        assert not workspace.store.list("trial") and workspace.allocated_seconds(owner) == 0
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.research.engine import _safe_context
    reopened = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    context = CampaignManager(reopened).context(owner, "Continue the historical reproduction")
    assert context["historical_reproduction_sources"][0]["reference"] == reference
    assert "best_objective" not in context["historical_reproduction_sources"][0]
    assert context["experiment_drafts"][0]["draft"]["reproduction"]["reference"] == reference
    assert context["experiment_drafts"][0]["readiness"]["ready"]
    assert "reproduction.draft" in _safe_context(context)["application_commands"]
    assert _safe_context(context)["historical_reproduction_sources"] == context["historical_reproduction_sources"]
