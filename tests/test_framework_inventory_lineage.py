"""Compaction must not silently sever a newly generated proposal's lineage."""
import json

from optimization_framework.research import engine
from optimization_framework.research.context import bounded
from test_framework_working_inventory import crowded_context
from test_workspace_research import configure, isolated_provider, mock_provider


def test_llm_child_keeps_saved_inventory_parent_but_rejects_invented_parent(monkeypatch):
    configure(monkeypatch)
    context = bounded(crowded_context())
    assert context["hypotheses"] == []
    assert [row["id"] for row in context["research_inventory"]["proposals"]] == ["hill"]

    def response(payload, count):
        prompt = json.loads(payload["messages"][1]["content"])
        assert prompt["context"]["hypotheses"] == []
        assert prompt["context"]["research_inventory"]["proposals"][0]["id"] == "hill"
        return {"analysis": "Refine the supplied hill-climbing mechanism; evaluate the change before ranking it.",
            "hypotheses": [{"title": "Adaptive restart hill climbing", "algorithm": "hillclimb",
                "mechanism": "Adapt the restart threshold to recent stagnation.",
                "rationale": "Test whether adapting restart timing improves progress at matched cost.",
                "assumptions": ["Recent stagnation predicts the value of restarting."],
                "predictions": ["Fewer unproductive evaluations after long plateaus."],
                "failure_modes": ["Premature restarts interrupt useful local search."],
                "cheapest_check": "Compare fixed and adaptive thresholds at matched seeds and budgets.",
                "parent_ids": ["hill", "invented_parent"]}] if count == 1 else []}

    calls = mock_provider(monkeypatch, response)
    result = engine.run_research({"mode": "generate", "message": "Refine the saved hill-climbing proposal."}, context)
    assert calls and result["mode"] == "llm"
    assert len(result["hypotheses"]) == 1
    assert result["hypotheses"][0]["parent_ids"] == ["hill"]
