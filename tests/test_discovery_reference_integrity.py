"""A malformed source link cannot poison an otherwise valid research handoff."""
from copy import deepcopy

import pytest

from test_framework_discovery import setup, start
from optimization_framework.research.discovery import knowledge
from optimization_framework.research.discovery.models import DiscoveryArtifact, DiscoveryTaskBrief
from optimization_framework.research.discovery.references import correct_saved_sources


def evidence_fixture(workspace, session):
    store = workspace.store
    author = next(t for t in workspace.discovery.tasks(session) if t["brief"]["role"] == "campaign_manager")
    common = {"campaign_id": session["campaign_id"]}
    source = store.put_immutable("source", {**common, "id": "source_exact", "title": "Primary evidence"})
    capture = store.put_immutable("source_capture", {**common, "id": "capture_exact", "source_id": source["id"],
        "passage_ids": ["passage_exact"]})
    passage = store.put_immutable("source_passage", {**common, "id": "passage_exact", "source_id": source["id"],
        "capture_id": capture["id"], "text": "Complete recorded scientific passage."})
    artifact = store.put_immutable("discovery_artifact", {**common, "id": "packet_with_typo", "session_id": session["id"],
        "task_id": author["id"], "kind": "synthesis", "title": "Common evidence packet", "stale": False,
        "content": {"supporting_passages": [{"source_id": "source_mistyped", "capture_id": capture["id"],
            "passage_id": passage["id"]}], "conclusions": "Representation does not establish effectiveness."}})
    return author, source, capture, passage, artifact


def test_freeform_synthesis_checks_links_before_publication_and_requires_actual_passage(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task, source, capture, passage, original = evidence_fixture(workspace, session)
    task["attempt_id"] = "source_read_attempt"
    workspace.store.put_immutable("discovery_attempt", {"id": task["attempt_id"], "context_snapshot": {"passage": passage}})
    artifact = DiscoveryArtifact(kind="synthesis", title="New common packet", content=deepcopy(original["content"]))
    with pytest.raises(ValueError, match="Use the exact source_id source_exact"):
        knowledge.validate(workspace.discovery, session, task, artifact)
    artifact.content["supporting_passages"][0]["source_id"] = source["id"]
    assert knowledge.validate(workspace.discovery, session, task, artifact) == artifact.content

    task["attempt_id"] = "index_only_attempt"
    workspace.store.put_immutable("discovery_attempt", {"id": task["attempt_id"], "context_snapshot": {
        "source_id": source["id"], "capture_id": capture["id"], "passage_ids": [passage["id"]]}})
    with pytest.raises(ValueError, match="actually supplied"):
        knowledge.validate(workspace.discovery, session, task, artifact)
    artifact = artifact.model_copy(update={"content": {"dossier_id": "missing_saved_dossier"}})
    with pytest.raises(ValueError, match="Unknown evidence identifier: missing_saved_dossier"):
        knowledge.validate(workspace.discovery, session, task, artifact)


def test_correction_preserves_original_and_unblocks_same_independent_assignments(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, source, capture, passage, original = evidence_fixture(workspace, session)
    tasks = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key=f"reader_{i}", role=f"specialist_{i}",
        stage="generate", objective="Continue from the same packet", evidence_ids=[original["id"]]) for i in range(3)],
        batch_id="blocked_readers")
    for task in tasks:
        with pytest.raises(ValueError, match="Unknown evidence identifier: source_mistyped") as error:
            workspace.discovery._context(session, task)
        task.update(status="waiting", wait_reason="problem_scope", error=str(error.value))
        workspace.store.put("discovery_task", task)
    policy = deepcopy(workspace.store.get(session["id"])["policy"])
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    issue = workspace.memory.issue(campaign["id"], "discovery_scope", tasks[0]["error"], affected=session["id"])
    correction = correct_saved_sources(workspace.discovery, session, original["id"], reason="Repair a transcribed source identifier.")
    assert correction["patches"] == [{"pointer": "/content/supporting_passages/0/source_id", "before": "source_mistyped",
        "after": source["id"], "capture_id": capture["id"], "passage_ids": [passage["id"]]}]
    assert correct_saved_sources(workspace.discovery, session, original["id"], reason="Repeated maintenance call") == correction
    assert workspace.store.get(original["id"]) == original
    with pytest.raises(ValueError, match="Unknown evidence identifier: source_mistyped"):
        workspace.discovery._evidence(session, "source_mistyped")  # No permissive global alias.

    workspace.discovery.recover()
    workspace.discovery._reconcile_task_issues(session)
    resolved = workspace.store.get(issue["id"])
    assert resolved["status"] == "resolved" and resolved["evidence"] == [correction["id"]]
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == guidance

    contexts = []
    for task in tasks:
        saved = workspace.store.get(task["id"])
        assert saved["status"] == "queued" and saved["wait_reason"] is None and "error" not in saved
        assert saved["brief"] == task["brief"] and saved["dependencies"] == task["dependencies"]
        context = workspace.discovery._context(session, saved)
        contexts.append(context)
        packet = next(row for row in context["discovery"]["evidence"] if row["id"] == original["id"])
        assert packet["content"]["supporting_passages"][0]["source_id"] == source["id"]
        assert packet["content"]["conclusions"] == original["content"]["conclusions"]
        assert packet["original_record_hash"] == original["content_hash"]
        assert packet["reference_correction"] == correction
        assert passage in context["discovery"]["evidence"]
    assert len({context["discovery"]["shared_context_hash"] for context in contexts}) == 1
    assert not workspace.store.list("research_run") and not workspace.store.list("discovery_attempt")
    assert workspace.store.get(session["id"])["policy"] == policy
    result = workspace.discovery.tools.execute({"id": "read_corrected", "campaign_id": campaign["id"],
        "session_id": session["id"], "task_id": tasks[0]["id"], "call": {"tool": "evidence.read", "arguments": {
            "record_id": original["id"], "pointer": "/content/supporting_passages/0"}}})
    assert result["record"]["source_id"] == source["id"]
    workspace.discovery.project_pending()
    exported = workspace.directory / "campaigns" / campaign["id"] / "discovery" / "records.jsonl"
    assert '"kind": "discovery_reference_correction"' in exported.read_text()
    assert workspace.store.get(original["id"]) == original


@pytest.mark.parametrize("conflict", ["existing_source", "wrong_capture", "wrong_campaign", "no_passage"])
def test_source_correction_rejects_ambiguous_or_unverified_links(setup, conflict):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, _, _, passage, original = evidence_fixture(workspace, session)
    changed = deepcopy(original)
    changed.pop("content_hash")
    changed["id"] = "conflicting_packet"
    citation = changed["content"]["supporting_passages"][0]
    if conflict == "existing_source":
        workspace.store.put_immutable("source", {"id": "source_mistyped", "campaign_id": campaign["id"]})
    elif conflict == "wrong_capture":
        workspace.store.put_immutable("source_capture", {"id": "other_capture", "campaign_id": campaign["id"],
            "source_id": "source_exact", "passage_ids": [passage["id"]]})
        citation["capture_id"] = "other_capture"
    elif conflict == "wrong_campaign":
        workspace.store.put_immutable("source_capture", {"id": "foreign_capture", "campaign_id": "another_campaign",
            "source_id": "source_exact", "passage_ids": [passage["id"]]})
        citation["capture_id"] = "foreign_capture"
    else:
        citation.pop("passage_id")
    workspace.store.put_immutable("discovery_artifact", changed)
    with pytest.raises(ValueError):
        correct_saved_sources(workspace.discovery, session, changed["id"], reason="Try a metadata repair")
    assert not workspace.store.list("discovery_reference_correction")


@pytest.mark.parametrize("state", ["sent", "cancelled", "changed_guidance", "stopped_session"])
def test_scope_recovery_does_not_restart_sent_cancelled_or_stale_work(setup, state):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    task.update(status="waiting", wait_reason="problem_scope")
    if state == "sent":
        task.update(run_id="already_dispatched", attempt_id="prior_attempt")
    elif state == "cancelled":
        task["status"] = "cancelled"
    elif state == "changed_guidance":
        task["guidance_revision"] -= 1
    else:
        session["status"] = "stopped"
        workspace.store.put("discovery_session", session)
    workspace.store.put("discovery_task", task)
    workspace.discovery.recover()
    assert workspace.store.get(task["id"]) == task
