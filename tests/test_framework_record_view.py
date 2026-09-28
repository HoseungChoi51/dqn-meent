"""Bounded record navigation preserves exact evidence and campaign isolation."""
from copy import deepcopy
import json

import pytest

from test_framework_discovery import setup, start
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.research.discovery.knowledge import supplied_passages
from optimization_framework.research.discovery.models import DiscoveryTaskBrief
from optimization_framework.research.discovery.record_view import RECORD_VIEW_LIMIT, view_record


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def request(session, task, name="evidence.read", **arguments):
    return {"id": "record_read_fixture", "campaign_id": session["campaign_id"],
            "session_id": session["id"], "task_id": task["id"],
            "call": {"tool": name, "arguments": arguments}}


def test_small_values_and_escaped_deep_pointers_remain_exact():
    value = {"a/b~c": {"": [{"id": "passage", "capture_id": "capture", "text": "Exact evidence"}]}}
    original = deepcopy(value)
    assert view_record(value, record_id="record") == value
    assert view_record(value, record_id="record", pointer="/a~1b~0c//0") == value["a/b~c"][""][0]
    deep = {"evidence": value}
    pointer = "/evidence"
    for _ in range(40):
        deep = {"level": deep}
        pointer = "/level" + pointer
    assert view_record(deep, record_id="deep", pointer=pointer) == value
    assert value == original


@pytest.mark.parametrize("pointer", ["without-slash", "/a~2b", "/array/-1", "/array/01", "/array/-", "/array/1", "/array/" + "9" * 5000, "/array/0/other"])
def test_invalid_pointers_cannot_use_python_negative_index_or_ambiguous_escapes(pointer):
    with pytest.raises(ValueError):
        view_record({"array": ["only"]}, record_id="record", pointer=pointer)


def test_large_dictionary_index_and_array_pages_are_bounded_and_replayable():
    value = {"body": [{"id": f"row-{i}", "text": "measurement " * 150} for i in range(70)],
             "large/field": {f"key-{i}": "x" * 1000 for i in range(80)}}
    original = deepcopy(value)
    index = view_record(value, record_id="large")
    assert index["record_projection"] == "field_index"
    assert {row["pointer"] for row in index["fields"]} == {"/body", "/large~1field"}
    assert size(index) <= RECORD_VIEW_LIMIT
    offset, restored = 0, []
    while True:
        page = view_record(value, record_id="large", pointer="/body", offset=offset, limit=4)
        assert page["record_projection"] == "array_page" and size(page) <= RECORD_VIEW_LIMIT
        assert page["offset"] == offset and len(page["items"]) <= 4
        restored.extend(page["items"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert restored == value["body"]
    fields, offset = [], 0
    while True:
        page = view_record(value, record_id="large", pointer="/large~1field", offset=offset, limit=7)
        assert page["record_projection"] == "field_index" and size(page) <= RECORD_VIEW_LIMIT
        fields.extend(row["key"] for row in page["fields"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert fields == list(value["large/field"])
    assert value == original


def test_utf8_text_pages_reconstruct_exact_text_without_claiming_full_passage():
    text = ('초격자 🧪 "quoted"\n' * 3500) + "END"
    passage = {"id": "passage", "capture_id": "capture", "text": text}
    index = view_record(passage, record_id="passage")
    assert not list(supplied_passages(index))
    offset, chunks = 0, []
    while True:
        page = view_record(passage, record_id="passage", pointer="/text", offset=offset)
        assert page["record_projection"] == "text_page" and size(page) <= RECORD_VIEW_LIMIT
        assert page["text_chunk"] == text[offset:offset + len(page["text_chunk"]) ]
        assert not list(supplied_passages(page))
        chunks.append(page["text_chunk"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(chunks) == text


def test_huge_array_child_is_explicit_reference_with_replay_pointer():
    value = [{"id": "huge", "data": "x" * 100_000}]
    page = view_record(value, record_id="array")
    child = page["items"][0]
    assert child["record_projection"] == "reference"
    assert child["read_arguments"]["pointer"] == "/0"
    assert size(page) <= RECORD_VIEW_LIMIT
    fields = view_record(value, **child["read_arguments"])
    assert fields["record_projection"] == "field_index"
    assert {row["key"] for row in fields["fields"]} == {"id", "data"}


def test_explicit_entry_limit_pages_even_small_arrays_and_object_indexes():
    array = [{"value": i} for i in range(10)]
    first = view_record(array, record_id="small_array", limit=1)
    assert first["record_projection"] == "array_page"
    assert first["items"] == array[:1] and first["next_offset"] == 1
    last = view_record(array, record_id="small_array", offset=9, limit=1)
    assert last["record_projection"] == "array_page"
    assert last["items"] == array[9:] and last["next_offset"] is None
    indexed = view_record({str(i): i for i in range(10)}, record_id="small_object", limit=2)
    assert indexed["record_projection"] == "field_index"
    assert [field["key"] for field in indexed["fields"]] == ["0", "1"] and indexed["next_offset"] == 2


def test_tool_reads_oversized_saved_receipt_by_pointer_without_mutating_audit_record(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    passages = [{"id": f"passage_{i}", "capture_id": "capture", "text": "Primary method text. " * 100} for i in range(100)]
    receipt = workspace.store.put_immutable("discovery_tool_receipt", {"id": "large_source_receipt",
        "campaign_id": campaign["id"], "session_id": session["id"], "task_id": task["id"],
        "tool": "source.read", "request_id": "past_read", "status": "completed", "result": {"passages": passages}})
    result = workspace.discovery.tools.execute(request(session, task, record_id=receipt["id"]))
    assert result["record"]["record_projection"] == "field_index"
    assert size(result) <= RECORD_VIEW_LIMIT
    result = workspace.discovery.tools.execute(request(session, task, record_id=receipt["id"], pointer="/result/passages/17"))
    assert result == {"record": passages[17]}
    assert list(supplied_passages(result)) == list(supplied_passages(passages[17]))
    assert workspace.store.get(receipt["id"]) == receipt


def test_requested_text_budget_is_preserved_across_tool_page_continuations(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    text = "많은 증거 🧪 \n" * 2000
    record = workspace.store.put_immutable("discovery_tool_receipt", {"id": "large_text_receipt",
        "campaign_id": campaign["id"], "session_id": session["id"], "task_id": task["id"],
        "tool": "evidence.read", "request_id": "past_text_read", "status": "completed", "result": {"body": text}})
    arguments = {"record_id": record["id"], "pointer": "/result/body", "max_bytes": 4096}
    chunks = []
    while True:
        result = workspace.discovery.tools.execute(request(session, task, **arguments))
        assert size(result) <= 4096
        page = result["record"]
        assert page["record_projection"] == "text_page"
        assert page["read_arguments"]["max_bytes"] == 4096
        chunks.append(page["text_chunk"])
        if page["next_offset"] is None:
            break
        arguments = {**page["read_arguments"], "offset": page["next_offset"]}
    assert len(chunks) > 1 and "".join(chunks) == text
    assert workspace.store.get(record["id"]) == record


def test_pointer_reads_still_enforce_campaign_and_independent_reviewer_scope(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    other = workspace.create_campaign(CampaignInput(name="Other", tasks=[TaskInput(name="Other task", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="belong to the campaign"):
        workspace.discovery.tools.execute(request(session, task, record_id=other["id"], pointer="/name"))
    reviewers = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key=key, role=key, stage="review",
        objective="Independent review") for key in ["reviewer_one", "reviewer_two"]], batch_id="independent_record_read")
    reviewer = reviewers[0]
    reviewer["attempt_id"] = "reviewer_record_attempt"
    workspace.store.put("discovery_task", reviewer)
    workspace.store.put_immutable("discovery_attempt", {"id": reviewer["attempt_id"], "context_snapshot": {}})
    with pytest.raises(ValueError, match="only its assigned evidence"):
        workspace.discovery.tools.execute(request(session, reviewer, record_id=task["id"], pointer="/brief"))


def test_experiment_inspection_pages_the_record_and_curve_and_checks_problem_scope(setup, monkeypatch):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    record = {"id": "trial_large", "campaign_id": campaign["id"], "task_id": session["problem_task_id"],
              "status": "completed", "algorithm": "random", "result": {"best_objective": 0.7}, "execution_manifest": {"files": "x" * 70_000}}
    workspace.store.put("trial", record)
    curve = [{"step": i, "objective": i / 1000} for i in range(1000)]
    monkeypatch.setattr(workspace, "metrics", lambda identity: curve)
    index = workspace.discovery.tools.execute(request(session, task, "experiment.inspect", record_id=record["id"]))
    assert index["record_projection"] == "field_index" and index["read_tool"] == "experiment.inspect"
    assert size(index) <= RECORD_VIEW_LIMIT
    selected = workspace.discovery.tools.execute(request(session, task, "experiment.inspect", record_id=record["id"], pointer="/trial/result"))
    assert selected == record["result"]
    page = workspace.discovery.tools.execute(request(session, task, "experiment.inspect", record_id=record["id"], pointer="/curve", offset=200, limit=10))
    assert page["items"] == curve[200:210] and page["next_offset"] == 210
    record["task_id"] = "a_different_problem"
    workspace.store.put("trial", record)
    with pytest.raises(ValueError, match="different problem"):
        workspace.discovery.tools.execute(request(session, task, "experiment.inspect", record_id=record["id"], pointer="/result"))


def test_assessment_inspection_pages_measurements_but_small_reply_is_unchanged(setup, monkeypatch):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    record = {"id": "assessment_record", "campaign_id": campaign["id"], "session_id": session["id"], "task_id": task["id"]}
    workspace.store.put("discovery_assessment", record)
    readiness = {"ready": True}
    evidence = {"measurements": [{"trial_id": f"trial_{i}", "result": {"description": "x" * 1000}} for i in range(100)]}
    monkeypatch.setattr(workspace.discovery.assessments, "readiness", lambda identity: readiness)
    monkeypatch.setattr(workspace.discovery.assessments, "evidence", lambda identity: evidence)
    index = workspace.discovery.tools.execute(request(session, task, "assessment.inspect", record_id=record["id"]))
    assert index["record_projection"] == "field_index" and index["read_tool"] == "assessment.inspect"
    page = workspace.discovery.tools.execute(request(session, task, "assessment.inspect", record_id=record["id"], pointer="/evidence/measurements", offset=15, limit=5))
    assert page["items"] == evidence["measurements"][15:20] and size(page) <= RECORD_VIEW_LIMIT
    evidence["measurements"] = []
    assert workspace.discovery.tools.execute(request(session, task, "assessment.inspect", record_id=record["id"])) == {
        "assessment": record, "readiness": readiness, "evidence": evidence}
