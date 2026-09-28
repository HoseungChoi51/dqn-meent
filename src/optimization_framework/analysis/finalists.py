"""Researcher shortlists retain exact prototypes without declaring a winner."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.evaluation.confirmation import method_definition, resolved_method_definition
from optimization_framework.storage.sqlite import now


def prototype_details(trial, study, task=None, hypothesis=None):
    """Pure projection shared by comparison rows and shortlist admission."""
    reason = None
    if not study or study.get("scope") != "exploratory":
        reason = "Finalists are selected from an exploratory study."
    elif trial.get("recipe") or trial.get("diagnostic_grant_id") or trial.get("algorithm") == "validate":
        reason = "Scientific diagnostic jobs cannot serve as optimizer prototypes."
    elif any(row.get("locked") or row.get("split", row.get("task_split")) in {"test", "heldout", "confirmation"}
             or row.get("protected_cohort_id") or row.get("confirmation_protocol_id")
             for row in (trial, task or {})):
        reason = "Protected confirmation evidence cannot select a development finalist."
    elif trial.get("status") != "completed":
        reason = "Wait until this test run has completed before marking it as a finalist."
    elif trial.get("execution_contract") != 1:
        reason = "This historical run needs a versioned optimizer procedure before selection."
    elif trial.get("method_contract") == 3:
        reason = "This procedure has cell-specific bindings; use its frozen template execution to confirm it."
    procedure = resolved = None
    try:
        procedure = method_definition(trial)
        resolved = resolved_method_definition(trial)
    except (ValueError, KeyError) as exc:
        reason = reason or str(exc)
    return {"procedure": procedure, "procedure_id": content_hash(procedure) if procedure is not None else None,
        "resolved_procedure": resolved, "question": trial.get("question", ""),
        "hypothesis_id": trial.get("hypothesis_id"), "hypothesis_title": (hypothesis or {}).get("title"),
        "finalist_eligible": reason is None, "finalist_ineligible_reason": reason}


def summary(record):
    """Small, explicit campaign-memory view; snapshots remain in the record."""
    return {**{key: record[key] for key in ("id", "study_id", "label", "revision", "trial_ids", "prototype_trial_ids")},
        "meaning": "Researcher shortlist only; no frozen nomination, scientific winner claim, or execution authorization.",
        "entries": [{**{key: item[key] for key in ("method_id", "procedure_id", "trial_id", "source_trial_ids")},
            **{key: item["resolved_procedure"].get(key) for key in
               ("algorithm", "algorithm_config", "max_steps", "wall_seconds", "completion")}}
            for item in record["entries"]]}


def set_selection(workspace, campaign_id, values, *, command_id):
    """Called inside the command transaction; history and current state commit together."""
    from .general import method_identity

    store = workspace.store
    study = store.get(values.study_id, "study")
    if study["campaign_id"] != campaign_id:
        raise ValueError("The study belongs to another campaign")
    if study["scope"] != "exploratory":
        raise ValueError("Finalists are selected from an exploratory study")
    identity = "finalists_" + study["id"]
    try:
        previous = store.get(identity, "finalist_selection")
    except KeyError:
        previous = None
    revision = previous["revision"] if previous else 0
    if values.expected_revision is not None and values.expected_revision != revision:
        raise ValueError("Finalist selection changed; reload the current shortlist before saving")
    trial_ids = list(dict.fromkeys(values.trial_ids))
    if set(values.expected_procedure_ids) - set(trial_ids):
        raise ValueError("Procedure checks must refer to selected prototype trials")
    tasks = {item["id"]: item for item in store.list("task", campaign_id)}
    hypotheses = {item["id"]: item for item in store.list("hypothesis", campaign_id)}
    grouped, sources = {}, []
    for trial_id in trial_ids:
        trial = store.get(trial_id, "trial")
        if trial["campaign_id"] != campaign_id or trial.get("study_id") != study["id"]:
            raise ValueError("Every finalist must belong to this campaign and exploratory study")
        details = prototype_details(trial, study, tasks.get(trial.get("task_id")), hypotheses.get(trial.get("hypothesis_id")))
        if not details["finalist_eligible"]:
            raise ValueError(details["finalist_ineligible_reason"])
        procedure_id = details["procedure_id"]
        if trial_id in values.expected_procedure_ids and values.expected_procedure_ids[trial_id] != procedure_id:
            raise ValueError("The prototype procedure changed; refresh its parameters and allocation before selecting it")
        method_id, method = method_identity(trial)
        source = {"trial_id": trial_id, "method_id": method_id, "procedure_id": procedure_id,
            "seed": trial["seed"], "experiment_spec_hash": trial.get("experiment_spec_hash"),
            "result_digest": content_hash(trial.get("result") or {}), "status": trial["status"]}
        sources.append(source)
        entry = grouped.setdefault(procedure_id, {**source, "source_trial_ids": [], "method": method,
            **{key: details[key] for key in ("procedure", "resolved_procedure", "question", "hypothesis_id", "hypothesis_title")}})
        entry["source_trial_ids"].append(trial_id)
    entries = list(grouped.values())
    created_at = now()
    record = {"schema_version": 1, "id": identity, "campaign_id": campaign_id, "study_id": study["id"],
        "revision": revision + 1,
        "label": values.label if values.label is not None else (previous or {}).get("label", "Finalists"),
        "trial_ids": trial_ids, "prototype_trial_ids": [entry["trial_id"] for entry in entries],
        "selected_method_ids": list(dict.fromkeys(source["method_id"] for source in sources)),
        "selected_procedure_ids": list(grouped), "entries": entries, "sources": sources,
        "authority": "researcher", "claim_level": "manual_shortlist", "command_id": command_id,
        "created_at": (previous or {}).get("created_at", created_at), "updated_at": created_at}
    store.put_immutable("finalist_selection_revision", {**deepcopy(record),
        "id": identity + "_revision_" + str(revision + 1), "selection_id": identity}, "finalist.selection_revised")
    store.put("finalist_selection", record, "finalist.selection_updated")
    return record


def confirmation_selection(store, campaign_id, request):
    """Validate only imported shortlist members, allowing additional controls."""
    if request.finalist_selection_id is None:
        return None
    selection = store.get(request.finalist_selection_id, "finalist_selection")
    if selection["campaign_id"] != campaign_id:
        raise ValueError("The finalist selection belongs to another campaign")
    if selection["revision"] != request.finalist_selection_revision:
        raise ValueError("Finalist selection changed; load its current revision before freezing confirmation")
    sources = {item["trial_id"]: item for item in selection["sources"]}
    selected = [identity for identity in dict.fromkeys(request.prototype_trial_ids) if identity in sources]
    if not selected:
        raise ValueError("The confirmation design no longer includes a prototype from this finalist selection")
    for identity in selected:
        if content_hash(method_definition(store.get(identity, "trial"))) != sources[identity]["procedure_id"]:
            raise ValueError("A shortlisted prototype procedure changed; review and save its current allocation before confirmation")
    return {"selection_id": selection["id"], "selection_revision": selection["revision"],
        "selection_revision_id": selection["id"] + "_revision_" + str(selection["revision"]),
        "source_study_id": selection["study_id"], "prototype_procedure_ids": {
            identity: sources[identity]["procedure_id"] for identity in selected}}
