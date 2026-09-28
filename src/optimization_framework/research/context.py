"""Bound the whole evidence view, not just the campaign-memory subsection."""
from copy import deepcopy
import json
import re

from optimization_framework.contracts.base import content_hash


LIMIT = 180 * 1024


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def trim_memory(memory, allowance):
    if size(memory) <= allowance or not memory.get("structured"):
        return
    memory["document"] = "Current guidance and constraints are retained in the structured context; complete history remains in the versioned export."
    structured = memory["structured"]
    memory.setdefault("history_counts", {key: len(structured.get(key, [])) for key in ("findings", "counterevidence", "reuse_decisions", "source_ids")})
    structured["source_ids"] = []
    for key in ("findings", "counterevidence", "reuse_decisions"):
        while structured.get(key) and size(memory) > allowance:
            structured[key].pop(0)
    while memory["retrieved_records"] and size(memory) > allowance:
        memory["retrieved_records"].pop()


def bounded(context, *, question="", target_id=None):
    if size(context) <= LIMIT:
        return context
    context = deepcopy(context)
    # Leave room for the selected scientific records as well as their memory
    # index. This only reduces retrievable history, never current constraints.
    trim_memory(context["manager_context"], 64 * 1024)
    for trial in context["trials"]:
        if trial.get("scientific_environment"):
            trial["scientific_environment_digest"] = content_hash(trial.pop("scientific_environment"))
        if trial.get("result") == trial.get("progress"):
            trial.pop("progress", None)
        curve = trial.get("curve", [])
        if len(curve) > 40:
            trial["curve"] = [curve[round(i * (len(curve) - 1) / 39)] for i in range(40)]
    retrieved = {row["id"] for row in context["manager_context"]["retrieved_records"]}
    active = {row["id"] for row in context["trials"] if row["status"] in {"queued", "running", "pausing", "stopping", "paused"}}
    pinned = {target_id, *active}
    pinned.update(row.get("hypothesis_id") for row in context["trials"] if row["id"] in active)
    pinned.update(row.get("implementation_version_id") for row in context["hypotheses"] if row["id"] in pinned)
    words = set(re.findall(r"[\w-]{4,}", question.lower()))
    tables = ("trials", "hypotheses", "evidence_library", "history", "decisions", "available_implementations",
        "applicable_assets", "reuse_decisions", "reproduction_comparisons", "historical_reproduction_sources")
    counts = {key: len(context.get(key, [])) for key in tables}
    context["evidence_selection"] = {"basis": "Pinned target, active work, relevant retrieval, question matches, then recent records.",
        "original_counts": counts, "omitted_counts": {},
        "history_location": "Versioned campaign context and records.jsonl; omission is not rejection or contrary evidence."}
    candidates = []
    for key in tables:
        for index, row in enumerate(context.get(key, [])):
            identity = row.get("id", row.get("key"))
            if identity in pinned or key == "decisions" and row.get("status") in {"pending", "executing"}:
                continue
            relevant = identity in retrieved
            label = " ".join(str(row.get(field, "")) for field in ("title", "name", "question", "mechanism", "content")).lower()
            matches = len(words & set(re.findall(r"[\w-]{4,}", label)))
            candidates.append((relevant, matches, index, key, row))
    candidates.sort(key=lambda item: item[:4])
    estimated_size = size(context)
    for _, _, _, key, row in candidates:
        if estimated_size <= LIMIT - 2048:
            break
        context[key].remove(row)
        estimated_size -= size(row) + 1
    selection = context["evidence_selection"]
    selection["omitted_counts"] = {key: count - len(context.get(key, [])) for key, count in counts.items() if count > len(context.get(key, []))}
    # Memory history is also retrievable. Current guidance, authority, resources,
    # studies, pending issues and next actions are never reduced here.
    memory = context["manager_context"]
    visible_hypotheses = {row["id"] for row in context["hypotheses"]}
    context["implementation_readiness"] = {key: value for key, value in context["implementation_readiness"].items() if key in visible_hypotheses}
    if size(context) > LIMIT:
        trim_memory(memory, LIMIT - size(context) + size(memory))
    if size(context) > LIMIT:
        raise ValueError("Current campaign constraints, active work and the selected evidence exceed the model context allowance. Scope the request; no active constraints were discarded.")
    return context
