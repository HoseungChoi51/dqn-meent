"""Researcher feedback rounds through HTTP, coordinator, and the research engine."""

import copy
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from dqn_meent.workspace import research
from dqn_meent.workspace.api import create_app


def test_two_feedback_revision_rounds_and_visible_critique(tmp_path, monkeypatch):
    """Saved notes drive each revision while originals and critique stay visible."""
    for key in list(research.os.environ):
        if key.startswith("GRATING_LLM_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "GRATING_LLM_ENABLED": "true", "GRATING_LLM_PROVIDER": "compatible",
        "GRATING_LLM_BASE_URL": "http://localhost:8123/v1", "GRATING_LLM_MODEL": "test-model",
        "GRATING_LLM_KEY_FILE": str(tmp_path / "missing.key"),
        "GRATING_LLM_INPUT_USD_PER_MILLION": "1", "GRATING_LLM_OUTPUT_USD_PER_MILLION": "2",
    }.items():
        monkeypatch.setenv(key, value)

    expected = {}
    prompts = []

    def respond(request):
        assert request.url.host == "localhost" and request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        prompt = json.loads(payload["messages"][1]["content"])
        prompts.append(prompt)
        assert prompt["selected_hypothesis"]["id"] == expected["target_id"]
        assert prompt["researcher_request"]["hypothesis_id"] == expected["target_id"]
        assert prompt["revision_context"] == expected["revision_context"]
        note = expected.get("note")
        result = {
            "analysis": f"Assessing {expected['target_id']}: test locality before assuming block moves help.",
            "hypotheses": [{
                "title": f"Revision {len(prompts)}", "algorithm": "block_tabu",
                "mechanism": "Use a bounded block size with a matched arbitrary-move diagnostic.",
                "rationale": "A bounded neighborhood may reduce unproductive proposals; it remains unmeasured.",
                "assumptions": ["Nearby boundaries may interact"],
                "predictions": ["Better acceptance per solver call if locality is useful"],
                "failure_modes": ["Nonlocal optical interactions"],
                "cheapest_check": "Compare paired local and arbitrary coordinated moves.",
                # Deliberately omit the selected parent; the engine must retain
                # trusted lineage even when a provider fails that instruction.
                "parent_ids": [],
                "change_summary": expected.get("change", "No revision requested."),
                "feedback_response": f"{note['id']}: {note['text']} Incorporated into the revised diagnostic." if note else "Critique only.",
            }],
            "actions": [{
                "kind": "probe", "title": "Suggested locality diagnostic",
                "rationale": "Distinguish local from arbitrary coordinated moves.",
                "question": "Does locality help?", "task_id": expected["task_id"],
                "hypothesis_id": expected["target_id"], "budget_calls": 8,
                "expected_information": "Paired neighborhood evidence", "stopping_condition": "Review after eight calls",
            }],
        }
        return httpx.Response(200, json={
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            "choices": [{"message": {"content": json.dumps(result)}}],
        })

    # This test isolates model feedback; catalog refresh must not reuse the
    # provider transport double or contact a separately running library.
    app = create_app(tmp_path / "workspace", start_workers=False,
        implementation_client=SimpleNamespace(versions=lambda: []))
    with TestClient(app) as client:
        real_client = httpx.Client
        monkeypatch.setattr(research.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
        created = client.post("/api/campaigns", json={
            "name": "Repeated researcher feedback", "autonomy": "delegated", "llm_budget_usd": 5,
            "compute_budget_seconds": 500, "validation_reserve_seconds": 30,
            "tasks": [{"name": "Development", "physics": {"n_cells": 6, "fourier_order": 1}}],
        })
        assert created.status_code == 201, created.text
        campaign = created.json()

        def state():
            response = client.get("/api/state", params={"campaign_id": campaign["id"]})
            assert response.status_code == 200, response.text
            return response.json()

        expected["task_id"] = state()["tasks"][0]["id"]
        created = client.post("/api/hypotheses", json={
            "campaign_id": campaign["id"], "title": "Boundary block search", "algorithm": "block_tabu",
            "mechanism": "Propose coordinated contiguous flips.",
            "rationale": "Local interactions may make coordinated moves useful.",
        })
        assert created.status_code == 201, created.text
        parent = created.json()

        def save_note(target_id, text):
            response = client.post(f"/api/hypotheses/{target_id}/review", json={"text": text})
            assert response.status_code == 200, response.text
            note = response.json()["reviews"][-1]
            assert note["text"] == text and note["author"] == "researcher"
            return note

        def run(mode, target_id, note=None):
            expected.update(target_id=target_id, note=note,
                            revision_context={"hypothesis_id": target_id, "reviews": [note]} if note else None)
            before_calls = len(prompts)
            response = client.post("/api/research", json={
                "campaign_id": campaign["id"], "mode": mode, "hypothesis_id": target_id,
                "feedback_review_ids": [note["id"]] if note else [],
                "message": "Revise this idea using my saved comment." if note else "Critique this idea without revising it.",
            })
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            thread = app.state.workspace.research_threads[run_id]
            thread.join(timeout=5)
            assert not thread.is_alive(), "Mocked research did not finish"
            current = state()
            completed = next(r for r in current["research_runs"] if r["id"] == run_id)
            assert completed["status"] == "completed", completed
            assert len(prompts) - before_calls == (4 if mode == "evolve" else 2)
            assert current["trials"] == [], "Comment/critique requests must not launch delegated numerical work"
            return completed, current

        first_note = save_note(parent["id"], "Limit blocks to three cells; show what changed and why.")
        assert state()["research_runs"] == [], "Saving a comment alone must not call a model"
        expected["change"] = "Cap block proposals at three cells."
        first_run, first_state = run("evolve", parent["id"], first_note)
        children = [h for h in first_state["hypotheses"] if h.get("research_run_id") == first_run["id"]]
        assert len(children) == 4
        for child in children:
            assert child["parent_ids"] == [parent["id"]]
            assert child["revision_context"] == {"hypothesis_id": parent["id"], "reviews": [first_note]}
            assert child["change_summary"] == expected["change"]
            assert first_note["id"] in child["feedback_response"] and first_note["text"] in child["feedback_response"]
        child = copy.deepcopy(children[0])

        second_note = save_note(child["id"], "Now preserve 50% fill factor using paired swaps.")
        expected["change"] = "Replace unconstrained flips with fill-factor-preserving swaps."
        second_run, second_state = run("evolve", child["id"], second_note)
        grandchildren = [h for h in second_state["hypotheses"] if h.get("research_run_id") == second_run["id"]]
        assert len(grandchildren) == 4
        for grandchild in grandchildren:
            assert grandchild["parent_ids"] == [child["id"]]
            assert grandchild["revision_context"] == {"hypothesis_id": child["id"], "reviews": [second_note]}
            assert grandchild["change_summary"] == expected["change"]
            assert second_note["id"] in grandchild["feedback_response"] and second_note["text"] in grandchild["feedback_response"]
        preserved_parent = next(h for h in second_state["hypotheses"] if h["id"] == parent["id"])
        preserved_child = next(h for h in second_state["hypotheses"] if h["id"] == child["id"])
        assert preserved_parent["mechanism"] == parent["mechanism"]
        assert first_note in preserved_parent["reviews"]
        assert preserved_child["mechanism"] == child["mechanism"]
        assert preserved_child["revision_context"] == child["revision_context"]
        assert second_note in preserved_child["reviews"]

        grandchild = grandchildren[0]
        critique_run, final_state = run("review", grandchild["id"])
        assert len(final_state["hypotheses"]) == len(second_state["hypotheses"])
        assert critique_run["result"]["hypotheses"] == []
        dossier = next(h for h in final_state["hypotheses"] if h["id"] == grandchild["id"])
        critique = next(r for r in dossier["reviews"] if r.get("research_run_id") == critique_run["id"])
        assert critique["author"] == "agent" and critique["origin"] == "llm"
        assert critique["text"] == f"Assessing {grandchild['id']}: test locality before assuming block moves help."
        assert final_state["trials"] == []
