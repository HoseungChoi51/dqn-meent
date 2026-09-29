"""Evidence-aware compaction preserves current science and historical receipts."""
from copy import deepcopy

import pytest

from test_framework_discovery import setup, start
from optimization_framework.research.context import DISCOVERY_LIMIT as LIMIT, size


def test_duplicate_candidate_cards_fit_without_losing_reviews_or_current_evidence(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    candidates = [{"id": f"candidate_{i}", "key": f"method_{i}", "title": f"Distinct method {i}",
        "mechanism": f"Method {i}: " + "scientific mechanism " * 2000,
        "assumptions": [f"Assumption {i}"], "predictions": [f"Prediction {i}"],
        "cheapest_test": f"Bounded multiseed comparison {i}", "implementation_needs": "Original requirement",
        "applicability": f"Problem match {i}", "failure_modes": f"Risk {i}", "startup_requirements": f"Startup {i}"}
        for i in range(3)]
    hypotheses = [{**deepcopy(candidate), "id": f"hypothesis_{i}", "candidate_id": candidate["id"],
        "rationale": candidate["applicability"], "risks": candidate["failure_modes"],
        "protocol": candidate["cheapest_test"], "startup_cost": "Researcher amended startup",
        "implementation_needs": "Researcher amended requirement", "reviews": [{"rationale": "Independent objection remains visible"}],
        "implementation_status": "missing"} for i, candidate in enumerate(candidates)]
    passage = {"id": "assigned_passage", "capture_id": "capture", "text": "Direct assigned evidence. " * 1000}
    snapshot["hypotheses"] = hypotheses
    snapshot["discovery"]["evidence"] = [*candidates, passage]
    task["brief"]["evidence_ids"] = [row["id"] for row in snapshot["discovery"]["evidence"]]
    snapshot["discovery"]["dependencies"] = [{"id": "completed_generator", "status": "completed",
        "artifact_ids": [row["id"] for row in candidates], "result": {"summary": "Three new mechanisms are ready", "dissent": ["Need quick tests"]}}]
    receipt = {"id": "prior_task_read", "campaign_id": campaign["id"], "task_id": task["id"],
        "request_id": "prior_read", "tool": "evidence.read", "created_at": "2026-09-28T00:00:00+00:00",
        "result": {"record": {"id": "completed_generator", "status": "failed"}}}
    saved_receipt = workspace.store.put_immutable("discovery_tool_receipt", receipt)
    task["last_tool_ids"] = ["prior_read"]
    original = deepcopy(snapshot)
    assert size(snapshot) > LIMIT

    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})

    assert size(context) <= LIMIT
    assert snapshot == original, "Compaction must not mutate the durable full snapshot."
    evidence = {row["id"]: row for row in context["discovery"]["evidence"]}
    for index, hypothesis in enumerate(context["hypotheses"]):
        reference = hypothesis["candidate_content_reference"]
        assert reference["candidate_id"] == candidates[index]["id"]
        assert "mechanism" in reference["fields"]
        assert evidence[reference["candidate_id"]]["mechanism"] == candidates[index]["mechanism"]
        for key, source in reference["renamed_fields"].items():
            assert key not in hypothesis
            assert evidence[reference["candidate_id"]][source] == hypotheses[index][key]
        assert hypothesis["startup_cost"] == "Researcher amended startup"
        assert hypothesis["implementation_needs"] == "Researcher amended requirement"
        assert hypothesis["reviews"] == hypotheses[index]["reviews"]
        assert hypothesis["implementation_status"] == "missing"
    assert evidence["assigned_passage"]["text"] == passage["text"]
    assert context["discovery"]["dependencies"][0]["status"] == "completed"
    assert context["discovery"]["dependencies"][0]["result"]["summary"] == "Three new mechanisms are ready"
    assert context["discovery"]["tool_results"][0]["result"]["record"]["status"] == "failed"
    assert "immutable snapshots" in context["discovery"]["tool_result_provenance"]
    assert workspace.store.get(receipt["id"], "discovery_tool_receipt") == saved_receipt


def test_asset_catalog_groups_keep_exact_readable_ids_and_assigned_assets(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    assets = [{"id": f"model_work_{index}", "campaign_id": campaign["id"],
        "title": "Model work for a campaign turn", "kind": "finding"} for index in range(80)]
    for asset in assets:
        workspace.store.put("asset", asset)
    snapshot = workspace.discovery._context(session, task)
    snapshot["applicable_assets"] = deepcopy(assets)
    snapshot["discovery"]["evidence"] = [deepcopy(assets[0])]
    task["brief"]["evidence_ids"] = [assets[0]["id"]]
    original = deepcopy(snapshot)

    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})

    assert snapshot == original
    assert [row["id"] for row in context["applicable_assets"]] == [assets[0]["id"]]
    groups = context["applicable_asset_groups"]["groups"]
    assert len(groups) == 1
    assert groups[0]["record_ids"] == [asset["id"] for asset in assets[1:]]
    assert groups[0]["metadata"] == {"title": "Model work for a campaign turn", "kind": "finding"}
    assert workspace.discovery._evidence(session, groups[0]["record_ids"][-1]) == assets[-1]
    assert size(context["applicable_asset_groups"]) < size(original["applicable_assets"]) - 2000


def save_context_run(workspace, session, task, snapshot, identity="paging_run"):
    run = {"id": identity, "campaign_id": task["campaign_id"], "discovery_session_id": session["id"],
        "discovery_task_id": task["id"], "context_snapshot": deepcopy(snapshot)}
    workspace.store.put("research_run", run)
    task["run_id"] = identity
    workspace.store.put("discovery_task", task)
    return run


def read_context(workspace, task, arguments):
    return workspace.discovery.tools.execute({"id": "local_context_read", "campaign_id": task["campaign_id"],
        "session_id": task["session_id"], "task_id": task["id"],
        "call": {"tool": "context.read", "arguments": arguments}})


def test_large_retrieval_catalog_and_proposal_history_are_paged_before_assigned_science(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    dossiers = [{"id": f"current_dossier_{i}", "kind": "problem_dossier", "content": {
        "analysis": f"Assigned science {i}: " + "z" * 14000, "dissent": ["Unresolved numerical convergence"]}}
        for i in range(5)]
    snapshot["discovery"]["evidence"] = deepcopy(dossiers)
    task["brief"]["evidence_ids"] = [row["id"] for row in dossiers]
    snapshot["discovery"]["retrieval_receipts"] = [{"id": f"historical_receipt_{i}",
        "request_id": f"request_{i}", "tool": "source.read", "status": "completed" if i else "failed",
        "error": None if i else "Source unavailable", "created_at": "2026-09-28T00:00:00Z",
        "result": {"capture": {"id": f"capture_{i}"}, "passages": [{
            "id": f"capture_{i}_passage_{j}_" + "a" * 70, "capture_id": f"capture_{i}",
            "text": f"Actual passage {i}/{j}"} for j in range(12)]}} for i in range(64)]
    snapshot["hypotheses"] = [{"id": f"hypothesis_{i}", "title": f"Existing proposal {i}",
        "reviews": [{"text": "Earlier review with unresolved objections " * 400}]} for i in range(4)]
    original = deepcopy(snapshot)
    run = save_context_run(workspace, session, task, snapshot)

    context = workspace.discovery._step_context(task, run)

    assert size(context) < LIMIT - 10 * 1024
    assert context["discovery"]["evidence"] == dossiers
    assert context["discovery"]["brief"] == snapshot["discovery"]["brief"]
    assert context["campaign"] == snapshot["campaign"]
    assert context["tasks"] == snapshot["tasks"]
    assert context["discovery"]["policy"] == snapshot["discovery"]["policy"]
    pages = context["discovery"]["context_pages"]
    assert "/discovery/retrieval_receipts" in pages
    assert "/hypotheses" in pages
    assert context["discovery"]["retrieval_receipts"] == []
    # A whole archive is paged; the failed retrieval remains readable exactly,
    # and no existing record or the frozen source snapshot is rewritten.
    actual = read_context(workspace, task, {**pages["/discovery/retrieval_receipts"]["read_arguments"],
        "pointer": "/discovery/retrieval_receipts/0"})
    assert actual["record"] == original["discovery"]["retrieval_receipts"][0]
    assert actual["record"]["error"] == "Source unavailable"
    assert snapshot == original and workspace.store.get(run["id"])["context_snapshot"] == original


def test_arbitrarily_growing_assigned_collection_supports_useful_multi_step_reads(setup):
    from optimization_framework.research.discovery.knowledge import supplied_passages
    from optimization_framework.contracts.base import content_hash
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    # Each entry fits ordinary record paging, but the collection does not.
    # This also covers a future record kind without a hard-coded body projector.
    records = [{"id": f"saved_finding_{i}", "kind": "finding", "content": "x" * 2000} for i in range(600)]
    passage = {"id": "selected_passage", "capture_id": "selected_capture", "text": "Exact complete source passage"}
    records.append(passage)
    trial = {"id": "trial_selected", "task_id": session["problem_task_id"], "algorithm": "coordinate",
        "algorithm_config": {"radius": .2}, "seed": 37, "result": {"best_objective": .81}}
    records.append(trial)
    snapshot["discovery"]["evidence"] = deepcopy(records)
    task["brief"]["evidence_ids"] = [passage["id"], trial["id"]]
    run = save_context_run(workspace, session, task, snapshot)
    first = workspace.discovery._step_context(task, run)
    descriptor = first["discovery"]["context_pages"]["/discovery/evidence"]
    assert descriptor["total"] == len(records)
    assert size(first) < LIMIT - 10 * 1024
    assert not list(supplied_passages(first)), "Paging cannot pretend that unread source text was supplied."
    assert first["discovery"]["evidence"][0]["result"] == trial["result"]
    assert first["discovery"]["evidence"][0]["algorithm_config"] == trial["algorithm_config"]

    # Continue from a navigation page, then read an exact full passage. Both
    # replies must stay useful in the next request even as the archive grows.
    for index, extra in enumerate(({"limit": 2}, {"pointer": "/discovery/evidence/600"})):
        arguments = {**descriptor["read_arguments"], **extra}
        result = read_context(workspace, task, arguments)
        receipt = {"id": f"receipt_context_{index}", "request_id": f"context_{index}", "campaign_id": campaign["id"],
            "session_id": session["id"], "task_id": task["id"], "tool": "context.read", "result": result}
        workspace.store.put_immutable("discovery_tool_receipt", receipt)
        task.update(step=index + 1, last_tool_ids=[receipt["request_id"]])
        context = workspace.discovery._step_context(task, run)
        assert size(context) < LIMIT - 10 * 1024
        current = next(row for row in context["discovery"]["tool_results"] if row.get("id") == receipt["id"])
        assert current["result"] == result
        if index == 0:
            assert result["record"]["items"] == records[:2]
            assert result["record"]["next_offset"] == 2
            assert result["record"]["record_hash"] == descriptor["snapshot_hash"]
        else:
            assert result["record"] == passage
            assert (passage["id"], content_hash([passage["capture_id"], passage["text"]])) in set(supplied_passages(context))
    assert workspace.store.get(run["id"])["context_snapshot"] == snapshot


def test_context_read_is_scoped_to_own_frozen_task_and_does_not_expose_run_metadata(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    tasks = workspace.discovery.tasks(session)
    for i, task in enumerate(tasks):
        task["brief"]["stage"] = "review"
        task["context_group_id"] = "same_independent_group"
        run = save_context_run(workspace, session, task, {"assigned": f"Only worker {i}"}, f"run_{i}")
        run["request"] = {"provider_snapshot": {"private_config": "Not context"}}
        workspace.store.put("research_run", run)
    task = tasks[0]
    assert read_context(workspace, task, {"record_id": "run_0"}) == {"record": {"assigned": "Only worker 0"}}
    with pytest.raises(ValueError, match="own saved research run"):
        read_context(workspace, task, {"record_id": "run_1"})
    with pytest.raises(ValueError, match="does not name a field"):
        read_context(workspace, task, {"record_id": "run_0", "pointer": "/request/provider_snapshot"})
    # Even a mismatched task/run binding cannot cross the session/campaign.
    run = workspace.store.get("run_0")
    run["discovery_session_id"] = "another_session"
    workspace.store.put("research_run", run)
    with pytest.raises(ValueError, match="task, session and campaign"):
        read_context(workspace, task, {"record_id": "run_0"})


def test_candidate_backlinks_do_not_expand_siblings_but_assigned_batches_do(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    manager = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    manager["dependencies"] = []
    base = {"campaign_id": campaign["id"], "session_id": session["id"], "task_id": manager["id"]}
    for depth in range(3):
        batch_id = f"batch_{depth}"
        members = []
        for index in range(8):
            record = {**base, "id": f"candidate_{depth}_{index}", "artifact_id": batch_id,
                "key": f"idea_{index}", "title": f"Idea {depth}/{index}", "mechanism": "Distinct mechanism",
                "parent_candidate_ids": [f"candidate_{depth-1}_{index}"] if depth else []}
            workspace.store.put("discovery_candidate", record)
            members.append({"key": record["key"], "title": record["title"], "mechanism": record["mechanism"],
                "parent_candidate_ids": record["parent_candidate_ids"]})
        workspace.store.put("discovery_artifact", {**base, "id": batch_id, "kind": "candidate_batch",
            "content": {"candidates": members}})
    manager["brief"]["evidence_ids"] = ["candidate_2_0"]
    evidence, _ = workspace.discovery._evidence_bundle(session, manager, [])
    assert {row["id"] for row in evidence if row["id"].startswith("candidate_")} == {
        "candidate_2_0", "candidate_1_0", "candidate_0_0"}
    manager["brief"]["evidence_ids"] = ["batch_2"]
    evidence, _ = workspace.discovery._evidence_bundle(session, manager, [])
    assert {f"candidate_2_{index}" for index in range(8)} <= {row["id"] for row in evidence}


@pytest.mark.parametrize("role", ["campaign_manager", "methodology_specialist"])
def test_growing_transitive_sources_keep_selected_science_and_current_tool_reply(setup, role):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    task["brief"]["role"] = role
    context = workspace.discovery._context(session, task)
    candidate = {"id": "candidate_primary", "key": "primary", "title": "Selected scientific method",
        "mechanism": "Primary mechanism", "applicability": "Current problem", "assumptions": ["Explicit assumption"]}
    review = {"id": "scientific_review", "kind": "review", "content": {"dissent": ["The apparent benefit may be a confound"]}}
    passage = {"id": "assigned_passage", "capture_id": "capture", "text": "Exact assigned scientific support"}
    trials = [{"id": f"trial_{i}", "task_id": session["problem_task_id"], "algorithm": "coordinate",
        "algorithm_config": {"radius": i / 10}, "seed": i, "result": {"best_objective": i / 3, "evaluations": 96}}
        for i in range(8)]
    roots = [candidate, review, passage, *trials]
    context["discovery"]["evidence"] = deepcopy(roots)
    task["brief"]["evidence_ids"] = [row["id"] for row in roots]
    for i in range(20):
        context["discovery"]["evidence"].extend([
            {"id": f"candidate_background_{i}", "key": f"background_{i}", "title": "Indirect comparator", "mechanism": "Different mechanism",
                "applicability": "Not the selected assignment. " * 1000},
            {"id": f"receipt_background_{i}", "request_id": f"read_{i}", "tool": "source.read", "status": "completed",
                "result": {"passages": [{"id": f"passage_{i}", "capture_id": "capture", "text": "Unassigned old document " * 1000}]}}])
    receipt = {"id": "current_reply", "campaign_id": campaign["id"], "task_id": task["id"], "request_id": "current_read",
        "tool": "source.read", "result": {"passages": [{"id": "current_passage", "capture_id": "current_capture", "text": "Latest requested evidence"}]}}
    workspace.store.put_immutable("discovery_tool_receipt", receipt)
    task["last_tool_ids"] = ["current_read"]
    original = deepcopy(context)
    projected = workspace.discovery._step_context(task, {"context_snapshot": context})
    assert size(projected) < LIMIT
    assert context == original
    evidence = {row["id"]: row for row in projected["discovery"]["evidence"]}
    assert evidence[candidate["id"]] == candidate
    assert evidence[review["id"]] == review
    assert evidence[passage["id"]] == passage
    for trial in trials:
        assert evidence[trial["id"]]["algorithm_config"] == trial["algorithm_config"]
        assert evidence[trial["id"]]["seed"] == trial["seed"]
        assert evidence[trial["id"]]["result"] == trial["result"]
    assert "content_omitted" in evidence["candidate_background_19"]
    assert "content_omitted" in evidence["receipt_background_19"]
    assert projected["discovery"]["tool_results"][0]["result"] == receipt["result"]


@pytest.mark.parametrize("sent", [False, True])
def test_context_recovery_refreshes_only_never_dispatched_managers(setup, sent):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    old = workspace.discovery._context(session, task)
    for dependency_id in task["dependencies"]:
        dependency = workspace.store.get(dependency_id)
        dependency["status"] = "superseded"
        workspace.store.put("discovery_task", dependency)
    run = {"id": "unsent_manager", "campaign_id": campaign["id"], "context_snapshot": old,
        "usage": {"calls": int(sent)}, "status": "waiting", "charter_version": campaign["version"],
        "guidance_revision": session["guidance_revision"]}
    task.update(run_id=run["id"], status="waiting", wait_reason="context_scope")
    if sent:
        task["attempt_id"] = "already_dispatched"
    workspace.store.put("research_run", run)
    workspace.store.put("discovery_task", task)
    workspace.discovery.recover()
    result = workspace.store.get(run["id"])
    assert result["usage"] == run["usage"]
    assert workspace.store.get(task["id"])["status"] == "queued"
    if sent:
        assert result["context_snapshot"] == old
    else:
        assert all(row["status"] == "superseded" for row in result["context_snapshot"]["discovery"]["dependencies"])


def test_context_recovery_does_not_resurrect_retired_task(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    task.update(status="superseded", wait_reason="context_scope")
    workspace.store.put("discovery_task", task)
    workspace.discovery.recover()
    assert workspace.store.get(task["id"]) == task


def test_current_replies_share_budget_and_a_followup_read_supplies_usable_content(setup):
    from optimization_framework.research.discovery.record_view import view_record
    from optimization_framework.research.discovery.knowledge import supplied_passages
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    review = {"id": "assigned_long_review", "kind": "review", "content": {"review": "r" * 150_000,
        "dissent": ["Preserve this unresolved confound"]}}
    workspace.store.put("discovery_artifact", {**review, "campaign_id": campaign["id"], "session_id": session["id"], "task_id": task["id"]})
    snapshot["discovery"]["evidence"] = [review]
    task["brief"]["evidence_ids"] = [review["id"]]
    task["last_tool_ids"] = []
    originals = []
    for i in range(6):
        receipt = {"id": f"receipt_current_{i}", "campaign_id": campaign["id"], "session_id": session["id"],
            "task_id": task["id"], "request_id": f"current_{i}", "tool": "evidence.read", "status": "completed",
            "result": {"passages": [{"id": f"passage_{i}", "capture_id": "capture",
                "text": "Exact current evidence. " * 1000}]}}
        originals.append(workspace.store.put_immutable("discovery_tool_receipt", receipt))
        task["last_tool_ids"].append(receipt["request_id"])
    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    assert size(context) <= LIMIT
    assert context["discovery"]["evidence"][0] == review
    assert len(context["discovery"]["tool_results"]) == 6
    assert not list(supplied_passages(context)), "An indexed passage is not supplied citation evidence."
    assert all(row["record_projection"] == "field_index" for row in context["discovery"]["tool_results"])

    # Follow an exact indexed pointer with the advertised feasible page budget.
    # This must yield actual new text on the next turn, not another index loop.
    page = view_record(originals[0], record_id=originals[0]["id"], pointer="/result/passages/0/text",
        max_bytes=context["discovery"]["bounded_read_guidance"]["max_bytes"])
    assert page["text_chunk"]
    reply = {"id": "receipt_followup", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": task["id"], "request_id": "followup", "tool": "evidence.read", "status": "completed", "result": {"record": page}}
    workspace.store.put_immutable("discovery_tool_receipt", reply)
    task["last_tool_ids"] = ["followup"]
    continuation = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    assert size(continuation) <= LIMIT
    latest = next(row for row in continuation["discovery"]["tool_results"] if row.get("id") == reply["id"])
    assert latest["result"]["record"]["text_chunk"] == page["text_chunk"]
    assert not list(supplied_passages(continuation))
    assert all(workspace.store.get(row["id"]) == row for row in originals)


def test_many_previous_steps_are_navigable_without_overflow_or_rewriting_history(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    evidence = {"id": "assigned_review", "kind": "review", "content": {"analysis": "a" * 10_000}}
    snapshot["discovery"]["evidence"] = [evidence]
    task["brief"]["evidence_ids"] = [evidence["id"]]
    originals = []
    for i in range(16):
        step = {"id": f"{task['id']}_step_{i}", "campaign_id": campaign["id"], "session_id": session["id"],
            "task_id": task["id"], "result": {"summary": f"Step {i}: " + "s" * 12_000,
                "dissent": [f"Unresolved concern {i}"], "questions_for_manager": []}}
        originals.append(workspace.store.put_immutable("discovery_step", step))
    task["step"] = 16
    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    assert size(context) <= LIMIT
    assert context["discovery"]["evidence"][0] == evidence
    assert len(context["discovery"]["previous_work"]) == 16
    assert all(row["saved_step"]["record_projection"] == "field_index" for row in context["discovery"]["previous_work"])
    assert {row["step_id"] for row in context["discovery"]["previous_work"]} == {row["id"] for row in originals}
    assert all(workspace.discovery._evidence(session, row["id"]) == row for row in originals)


def test_nearly_full_assigned_body_is_explicitly_paged_and_next_read_fits(setup):
    from optimization_framework.research.discovery.record_view import view_record
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    review = {"id": "oversized_assigned_review", "kind": "review", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": task["id"], "content": {"analysis": "Exact scientific explanation. " * 6500, "dissent": ["An unresolved confound"]}}
    workspace.store.put_immutable("discovery_artifact", review)
    snapshot["discovery"]["evidence"] = [deepcopy(review)]
    task["brief"]["evidence_ids"] = [review["id"]]
    first = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    projected = first["discovery"]["evidence"][0]
    assert projected["id"] == review["id"] and "content_omitted" in projected
    assert projected["saved_record"]["record_projection"] == "field_index"
    assert size(first) <= LIMIT - 8192
    cap = first["discovery"]["bounded_read_guidance"]["max_bytes"]
    assert cap >= 8192
    page = view_record(review, record_id=review["id"], pointer="/content/analysis", max_bytes=cap)
    assert page["text_chunk"]
    receipt = {"id": "next_scientific_read", "campaign_id": campaign["id"], "session_id": session["id"], "task_id": task["id"],
        "request_id": "requested_field", "tool": "evidence.read", "result": {"record": page}}
    workspace.store.put_immutable("discovery_tool_receipt", receipt)
    task["last_tool_ids"] = ["requested_field"]
    second = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    assert size(second) <= LIMIT
    assert second["discovery"]["tool_results"][0]["result"]["record"]["text_chunk"] == page["text_chunk"]
    assert workspace.store.get(review["id"])["content"] == review["content"]


def test_context_below_hard_ceiling_compacts_old_receipts_and_recovers_saved_task(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    run = {"id": "manager_needing_read_headroom", "campaign_id": campaign["id"], "context_snapshot": snapshot,
        "usage": {"calls": 1}, "status": "waiting", "charter_version": campaign["version"],
        "guidance_revision": session["guidance_revision"]}
    task.update(run_id=run["id"], status="waiting", step=1, wait_reason="context_scope", last_tool_ids=["new_read"])
    baseline = workspace.discovery._step_context(task, run)
    recent = {"id": "recent_small_reply", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": task["id"], "request_id": "new_read", "tool": "source.read", "status": "completed",
        "result": {"passages": [{"id": "current_passage", "capture_id": "capture", "text": "Exact new source evidence"}]}}
    older = {**recent, "id": "earlier_large_reply", "request_id": "old_read", "tool": "evidence.read",
        "result": {"record": {"analysis": ""}}}
    # Between the 170 KiB working target and 180 KiB hard ceiling: old code
    # skipped receipt compaction here, then rejected the task for lack of room.
    older["result"]["record"]["analysis"] = "x" * (LIMIT - 4096 - size(baseline) - size(older) - size(recent))
    originals = [workspace.store.put_immutable("discovery_tool_receipt", row) for row in (older, recent)]
    assert LIMIT - 10 * 1024 < size(baseline) + sum(size(row) for row in originals) < LIMIT
    workspace.store.put("research_run", run)
    workspace.store.put("discovery_task", task)

    workspace.discovery.recover()

    assert workspace.store.get(task["id"])["status"] == "queued"
    assert workspace.store.get(run["id"]) == run
    context = workspace.discovery._step_context(task, run)
    assert size(context) < LIMIT - 8192
    receipts = {row["id"]: row for row in context["discovery"]["tool_results"]}
    assert receipts[older["id"]]["result"] == {"omitted_from_context": True, "retrieve_record_id": older["id"]}
    assert receipts[recent["id"]]["result"] == recent["result"]
    assert all(workspace.store.get(row["id"]) == row for row in originals)


def test_large_capture_navigation_is_readable_without_repeating_all_passage_ids(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    snapshot = workspace.discovery._context(session, task)
    capture = {"id": "capture_long_article", "campaign_id": campaign["id"], "source_id": "article",
        "coverage": "partial_text", "limitations": ["Completeness is not independently verified"],
        "passage_ids": [f"capture_long_article_passage_{i}" for i in range(1000)]}
    saved = workspace.store.put_immutable("source_capture", capture)
    passage = {"id": capture["passage_ids"][7], "capture_id": capture["id"], "text": "Exact selected scientific evidence."}
    snapshot["discovery"]["source_captures"] = [capture]
    snapshot["discovery"]["evidence"] = [passage]
    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    index = context["discovery"]["source_captures"][0]
    assert size(index) < 1024
    assert index["coverage"] == capture["coverage"] and index["limitations"] == capture["limitations"]
    assert index["passage_index"]["count"] == 1000
    assert index["passage_index"]["read_arguments"] == {"record_id": capture["id"], "pointer": "/passage_ids"}
    assert context["discovery"]["evidence"][0] == passage
    assert workspace.store.get(capture["id"]) == saved
    assert snapshot["discovery"]["source_captures"][0] == capture


@pytest.mark.parametrize("role", ["campaign_manager", "methodology_specialist"])
def test_reference_code_is_paged_and_selected_files_survive_tool_continuations(setup, role):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    task["brief"]["role"] = role
    snapshot = workspace.discovery._context(session, task)
    reference = {"id": "implementation_reference_large", "campaign_id": campaign["id"],
        "name": "Authors' optimizer", "repository_url": "https://example.org/authors/optimizer",
        "revision": "a" * 40, "entrypoints": ["main.py"], "status": "source_available", "runnable": False,
        "verification": "Captured source still requires executable validation.",
        "files": {"main.py": "from utils import update\n", "utils/update~step.py": "# Exact captured source.\n" * 12000}}
    reference = workspace.store.put_immutable("implementation_reference", reference)
    snapshot["discovery"]["evidence"] = [deepcopy(reference)]
    task["brief"]["evidence_ids"] = [reference["id"]]
    original = deepcopy(snapshot)
    assert size(snapshot) > LIMIT

    context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
    projected = context["discovery"]["evidence"][0]
    assert size(context) < LIMIT - 10 * 1024
    assert projected["revision"] == reference["revision"]
    assert projected["status"] == "source_available" and projected["runnable"] is False
    assert projected["verification"] == reference["verification"]
    assert "files" not in projected and "content_omitted" in projected
    assert any(field["pointer"] == "/files" for field in projected["saved_record"]["fields"])

    # Read a complete entrypoint, then a page of another file. Each new tool
    # reply must stay available while the large initial source stays indexed.
    for index, pointer in enumerate(("/files/main.py", "/files/utils~1update~0step.py")):
        request_id = f"file_read_{index}"
        result = workspace.discovery.tools.execute({"id": request_id, "campaign_id": campaign["id"],
            "session_id": session["id"], "task_id": task["id"], "call": {"tool": "evidence.read", "arguments": {
                "record_id": reference["id"], "pointer": pointer, "max_bytes": 4096}}})
        receipt = {"id": "receipt_" + request_id, "campaign_id": campaign["id"], "session_id": session["id"],
            "task_id": task["id"], "request_id": request_id, "tool": "evidence.read", "result": result}
        workspace.store.put_immutable("discovery_tool_receipt", receipt)
        task.update(step=index + 1, last_tool_ids=[request_id])
        context = workspace.discovery._step_context(task, {"context_snapshot": snapshot})
        assert size(context) < LIMIT - 8192
        actual = next(row for row in context["discovery"]["tool_results"] if row["id"] == receipt["id"])
        assert actual["result"] == result
        if index == 0:
            assert result["record"] == reference["files"]["main.py"]
        else:
            assert result["record"]["text_chunk"]
            assert reference["files"]["utils/update~step.py"].startswith(result["record"]["text_chunk"])
            assert result["record"]["next_offset"] > 0
    assert workspace.store.get(reference["id"]) == reference
    assert snapshot == original
