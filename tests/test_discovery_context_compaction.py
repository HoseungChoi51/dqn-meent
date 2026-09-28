"""Evidence-aware compaction preserves current science and historical receipts."""
from copy import deepcopy

import pytest

from test_framework_discovery import setup, start
from optimization_framework.research.context import LIMIT, size


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
