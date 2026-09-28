"""Partition a frozen reconsideration snapshot without changing its authority."""
from collections import OrderedDict
from copy import deepcopy

from optimization_framework.contracts.base import content_hash


def _expand_text(value, root):
    if isinstance(value, list):
        return [_expand_text(item, root) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    reference = value.get("$ref") if len(value) == 1 else None
    if isinstance(reference, str) and reference.startswith((
            "#/shared_decision_text/", "#/decision_refresh/shared_review_text/")):
        target = root
        for key in reference[2:].split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
        return deepcopy(target)
    return {key: _expand_text(item, root) for key, item in value.items()}


def _materialize(context):
    """Expand decision-only references before changing selected array indices."""
    result = _expand_text(context, context)
    for index, current in enumerate(result.get("decisions", [])):
        reference = current.get("supplied_record_reference")
        if not reference:
            continue
        original = context
        for key in reference["json_pointer"][2:].split("/"):
            original = original[int(key)] if isinstance(original, list) else original[key]
        original = _expand_text(original, context)
        for field in reference.get("removed_fields", []):
            original.pop(field, None)
        original.update(reference.get("overrides", {}))
        result["decisions"][index] = original
    result.pop("shared_decision_text", None)
    result.pop("decision_reference_basis", None)
    result.get("decision_refresh", {}).pop("shared_review_text", None)
    return result


def _group(item):
    action = item.get("action") or {}
    payload = action.get("command_payload") or {}
    operation = action.get("command_operation") or ""
    kind = action.get("kind") or "direction"
    if operation == "campaign.update":
        return "allocation", "Campaign scope and resource allocation"
    if kind == "implement" or operation.startswith(("implementation.", "evaluator.")):
        return "implementation", "Implementation and validation prerequisites"
    if kind in {"review", "compare", "search"} or operation.startswith(("literature.", "source.", "comparison.", "finding.")):
        return "evidence", "Evidence and completed-work assessment"
    if kind in {"probe", "extend", "validate"} or operation.startswith(("draft.", "trial.", "study.", "validation.")):
        # Keep methods, controls, seed replications and study-wide limits together.
        task = action.get("task_id") or payload.get("task_id") or "campaign"
        return "experiments:" + task, "Experiment designs and comparisons"
    return "direction", "Scientific direction and unresolved questions"


def _decision_index(records):
    fields = ("id", "title", "status", "action_id", "trial_id", "charter_version", "guidance_revision",
              "resolution_revision", "choice", "comment", "audience")
    return [{**{key: deepcopy(row[key]) for key in fields if key in row},
             **({"options": deepcopy(row["options"])} if row.get("choice") and "options" in row else {}),
             "scope_note": "Current status and exact researcher answer. Selected originals are supplied below; other request details remain in their saved records."}
            for row in records]


def _compact_text(context):
    """Share exact repeated prose, including repeated measurement descriptions."""
    owners = {}

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"application_commands", "application_schema_definitions", "schema", "$defs", "definitions",
                        "command_payload", "implementation_spec"} or key.endswith("_schema"):
                    continue  # Keep every supplied JSON Schema valid as JSON Schema.
                if isinstance(item, str) and len(item) >= 160:
                    owners.setdefault(item, []).append((value, key))
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and len(item) >= 160:
                    owners.setdefault(item, []).append((value, index))
                elif isinstance(item, (dict, list)):
                    visit(item)

    visit(context)
    shared = {}
    for text, references in owners.items():
        if len(references) < 2:
            continue
        identity = content_hash(text)
        shared[identity] = text
        for owner, key in references:
            owner[key] = {"$ref": "#/decision_refresh/shared_review_text/" + identity}
    if shared:
        context["decision_refresh"]["shared_review_text"] = shared
        context["decision_refresh"]["text_reference_basis"] = (
            "Text $ref objects resolve against this context to exact strings in decision_refresh.shared_review_text. "
            "These references compress repeated prose without changing it. Expand them before composing any action payload.")
    return context


def partition_context(context, max_groups=8):
    """Related originals stay together; each reviewer receives only its subset."""
    if not 1 <= max_groups <= 8:
        raise ValueError("Use one through eight review groups")
    base = _materialize(context)
    selected = base.get("decision_refresh", {}).get("decisions", [])
    if not selected:
        raise ValueError("A decision reassessment needs selected original requests")
    groups = OrderedDict()
    task_names = {task["id"]: task.get("name", task["id"]) for task in base.get("tasks", [])}
    for item in selected:
        key, title = _group(item)
        if key.startswith("experiments:"):
            task_id = key.removeprefix("experiments:")
            title = "Cross-task experiment plans" if task_id == "campaign" else "Experiment designs: " + task_names.get(task_id, task_id)
        groups.setdefault(key, {"title": title, "items": []})["items"].append(item)
    rows = list(groups.values())
    # Only overflow groups are combined. Membership is stable across retries.
    if len(rows) > max_groups:
        rows = rows[:max_groups - 1] + [{"title": "Related remaining campaign decisions",
            "items": [item for group in rows[max_groups - 1:] for item in group["items"]]}]
    result = []
    all_ids = [item["decision"]["id"] for item in selected]
    for group in rows:
        scoped = deepcopy(base)
        ids = [item["decision"]["id"] for item in group["items"]]
        scoped["decision_refresh"]["decisions"] = deepcopy(group["items"])
        scoped["decision_refresh"]["review_scope"] = {
            "title": group["title"], "decision_ids": ids,
            "other_group_decision_ids": [identity for identity in all_ids if identity not in ids],
            "instructions": "Assess only the supplied original requests. Identify dependencies on other groups for the manager. "
                "Return evidence-based assessments, concrete alternatives and dissent; do not publish choices or authorize work."}
        scoped["decisions"] = _decision_index(scoped.get("decisions", []))
        # Reviewers assess evidence and feasibility; the final manager alone
        # receives the typed command catalog and composes executable proposals.
        for key in ("application_commands", "application_schema_definitions", "application_schema_reference_basis"):
            scoped.pop(key, None)
        scoped["application_command_scope"] = {"reason": "Reviewer assessment only; action composition belongs to the final campaign manager."}
        result.append({"title": group["title"], "decision_ids": ids, "context": _compact_text(scoped)})
    return result


def synthesis_context(context, reviews):
    """Give the manager every original plus all completed, attributable reviews."""
    result = _materialize(context)
    expected = {item["decision"]["id"] for item in result["decision_refresh"]["decisions"]}
    supplied = set()
    for review in reviews:
        ids = set(review["decision_ids"])
        if not ids or not ids <= expected:
            raise ValueError("A review references a decision outside this reassessment")
        supplied.update(ids)
    if supplied != expected:
        raise ValueError("Every selected decision needs a completed review before manager synthesis")
    result["decisions"] = _decision_index(result.get("decisions", []))
    result["decision_refresh"]["review_results"] = deepcopy(reviews)
    result["decision_refresh"]["synthesis_instructions"] = (
        "All parallel reviews below used the same frozen campaign snapshot. They are assessments, not execution authority. "
        "Reconcile disagreements, duplicate recommendations, cross-group dependencies and combined resource requirements. "
        "Account for each selected original decision ID. Publish only consolidated, concrete requests that still need researcher "
        "judgment. Resolve internal operational questions from the supplied evidence. All new executable actions require "
        "researcher approval. Preserve the latest scientific request, exact comments, uncertainties and minority views.")
    return _compact_text(result)
