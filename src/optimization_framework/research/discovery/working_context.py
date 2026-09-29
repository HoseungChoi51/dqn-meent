"""Separate assigned scientific work from its retrievable provenance closure.

The full frozen snapshot remains the authority. These projections identify every
omitted body and require a recorded read before an agent makes detailed claims.
"""
from __future__ import annotations

from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.research.context import size


def page_snapshot_sections(context, run, *, measure, target_bytes, include_evidence=False):
    """Move growing archive collections behind reads of this task's snapshot.

    Paging individual bodies is insufficient when hundreds of small indexes
    themselves fill the prompt. Page whole collections, preserving their exact
    frozen positions and hashes. Authority, the brief, dependencies, guidance,
    current tool replies and scientific settings/measurements are never moved.
    The caller recompiles deduplication after each change so references cannot
    point at a passage that has just been removed from the working context.
    """
    if not run.get("id"):
        return
    snapshot = run["context_snapshot"]
    snapshot_hash = None
    paths = [(key,) for key in ("hypotheses", "history", "research_inventory", "evidence_library",
        "applicable_assets", "available_implementations", "reference_evidence", "reuse_decisions")]
    paths += [("discovery", key) for key in ("retrieval_receipts", "artifact_index", "source_captures",
        "candidates", "assessments", "tasks")]
    if include_evidence:
        paths = [("discovery", "evidence")]

    def parent(value, path):
        for key in path[:-1]:
            value = value.get(key, {})
        return value

    paths.sort(key=lambda path: size(parent(context, path).get(path[-1], [])), reverse=True)
    for path in paths:
        if measure() <= target_bytes:
            break
        owner = parent(context, path)
        original = parent(snapshot, path).get(path[-1])
        if not isinstance(original, (list, dict)) or size(owner.get(path[-1], [])) <= 2048:
            continue
        pointer = "/" + "/".join(path)
        pages = context["discovery"].setdefault("context_pages", {})
        if pointer in pages:
            continue
        if snapshot_hash is None:
            snapshot_hash = content_hash(snapshot)
        descriptor = {"read_tool": "context.read", "read_arguments": {"record_id": run["id"],
            "pointer": pointer, "offset": 0, "limit": 20, "max_bytes": 8192},
            "snapshot_hash": snapshot_hash, "total": len(original),
            "content_omitted": "This collection is paged, not empty. It remains in this task's frozen snapshot. "
                "Read selected fields or pages before relying on their content; follow next_offset until the required coverage is complete."}
        retained = [] if isinstance(original, list) else {}
        if path == ("discovery", "evidence"):
            retained = [row for row in owner[path[-1]] if str(row.get("id", "")).startswith("trial_")
                        and "algorithm" in row and "task_id" in row]
        if size(descriptor) + size(retained) >= size(owner[path[-1]]):
            continue
        pages[pointer] = descriptor
        owner[path[-1]] = retained
        if path == ("applicable_assets",):
            context.pop("applicable_asset_groups", None)
        if path == ("discovery", "evidence"):
            # References created by earlier deduplication must not claim that
            # candidate/review bodies are still supplied after collection paging.
            for hypothesis in context.get("hypotheses", []):
                if hypothesis.get("candidate_content_reference"):
                    hypothesis["candidate_content_reference"]["location"] = "Candidate body is paged; use evidence.read before relying on these fields."
                for review in hypothesis.get("reviews", []):
                    if review.pop("full_review_supplied_in_evidence", False):
                        review["full_review_read_required"] = True


def primary_ids(context, task):
    discovery = context["discovery"]
    roots = set(task["brief"].get("evidence_ids", []))
    for dependency in discovery.get("dependencies", []):
        roots.update(dependency.get("artifact_ids") or [])
    records = {row.get("id"): row for row in discovery.get("evidence", [])}
    # Legacy/manual snapshots can directly supply evidence without a brief index.
    if not roots:
        roots.update(records)
    pinned = set(roots)
    for identity in roots:
        row = records.get(identity, {})
        if row.get("candidate_id"):
            pinned.add(row["candidate_id"])
        if row.get("kind") == "proposal_review" and row.get("content", {}).get("candidate_id"):
            pinned.add(row["content"]["candidate_id"])
        if row.get("kind") == "candidate_batch":
            pinned.update(item["id"] for item in records.values() if item.get("artifact_id") == identity)
    return pinned


def provenance_view(context, task):
    """Project only indirect bodies, consistently for managers and specialists.

Direct candidates, reviews, passages, numerical records and current tool results
retain their complete working content. Candidate batch backlinks carry origin,
not an instruction to study every sibling produced by the same old call.
"""
    context = deepcopy(context)
    discovery = context["discovery"]
    pinned = primary_ids(context, task)
    projected = []
    candidate_overviews = set()

    def index(row, description, fields=()):
        result = {key: row[key] for key in ("id", "kind", "title", "source_id", "capture_id", "task_id",
                  "artifact_id", "created_at", "request_id", "tool", "status", "error", *fields) if key in row}
        result["content_omitted"] = description + " Use evidence.read with this exact record ID before making detailed claims."
        return result

    evidence = []
    for row in discovery.get("evidence", []):
        identity = row.get("id")
        value = row
        if identity not in pinned:
            if row.get("kind") == "candidate_batch":
                value = index(row, "Origin batch backlink; the assigned candidates are supplied separately.")
                value["dossier_ids"] = row.get("content", {}).get("dossier_ids", [])
                value["literature_map_ids"] = row.get("content", {}).get("literature_map_ids", [])
            elif str(identity).startswith("candidate_") and row.get("mechanism"):
                candidate_overviews.add(identity)
                value = index(row, "Background candidate overview; detailed specification and evidence are not supplied by this index.",
                    ("family_id", "family", "algorithm", "mechanism", "parent_candidate_ids", "parent_hypothesis_ids"))
            elif row.get("tool", "").startswith("source.") and "result" in row:
                value = index(row, "Indirect retrieval provenance; its document bodies are not assigned source passages.")
                body = row.get("result") or {}
                value["result"] = {"retrieval_id": body.get("retrieval_id"), "capture_id": body.get("capture", {}).get("id"),
                    "passage_ids": [item["id"] for item in body.get("passages", [])],
                    "content_omitted": "Read the saved receipt or selected passages before citing their content."}
            elif ("capture_id" in row and "text" in row) or ("passage_ids" in row and "source_id" in row):
                value = index(row, "Indirect source provenance; the full passage or capture remains available.",
                    ("passage_ids", "coverage", "limitations", "url"))
        if value is not row:
            projected.append(identity)
        evidence.append(value)
    discovery["evidence"] = evidence

    # Avoid dangling references introduced by exact candidate/card deduplication
    # when that candidate has become a background overview. Card-specific review
    # and execution status remain visible; the full card is still retrievable.
    cards = []
    complete_reviews = {row["id"]: row["content"] for row in evidence
                        if row.get("kind") == "proposal_review" and row.get("content")}
    for row in context.get("hypotheses", []):
        reviews = []
        for review in row.get("reviews", []):
            original = complete_reviews.get(review.get("id"))
            if original:
                text = f"Conceptual verdict: {original.get('verdict')}. {original.get('rationale')}"
                for label, key in (("Mechanism checks", "mechanism_checks"), ("Risks", "conflicts_and_risks"),
                        ("Required changes", "required_changes"), ("Implementation follow-up", "implementation_followup"),
                        ("Suggested tests", "suggested_tests")):
                    if original.get(key):
                        text += "\n\n" + label + ":\n" + "\n".join("- " + value for value in original[key])
                if review.get("text") == text:
                    review = {key: value for key, value in review.items() if key != "text"} | {
                        "full_review_supplied_in_evidence": True}
            reviews.append(review)
        if "reviews" in row:
            row["reviews"] = reviews
        if row.get("id") not in pinned and row.get("candidate_id") in candidate_overviews:
            row = index(row, "Background hypothesis overview; reviews and readiness are retained here.",
                ("candidate_id", "family_id", "algorithm", "algorithm_config", "implementation_status", "executable",
                 "parent_ids", "reviews", "status", "requires_concept_review", "claim_level"))
        cards.append(row)
    context["hypotheses"] = cards
    # Global navigation indexes repeat metadata already present in the working
    # evidence. Remove only entries whose every indexed value is equal; unique
    # titles, statuses or other metadata continue to appear unchanged.
    supplied = {row.get("id"): row for row in evidence}
    for key in ("artifact_index", "candidates", "source_captures"):
        if key in discovery:
            discovery[key] = [row for row in discovery[key] if row.get("id") not in supplied or
                              any(supplied[row["id"]].get(field) != value for field, value in row.items())]
    discovery["working_evidence_selection"] = {"primary_ids": sorted(pinned), "indexed_record_ids": projected,
        "basis": "Explicit assignments and dependency work products take priority over transitive provenance. "
            "Every full record remains in the frozen context and evidence store. An index does not supply the omitted scientific content."}
    return context
