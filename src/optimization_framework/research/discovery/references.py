"""Validate artifact links and record explicit, provenance-checked corrections."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now

from .record_view import _select


REFERENCE_KEYS = {"source_id", "capture_id", "passage_id", "passage_ids", "retrieval_ids", "dossier_id", "dossier_ids",
    "literature_map_ids", "parent_candidate_ids", "parent_hypothesis_ids", "candidate_id", "artifact_id", "evidence_ids"}


def references(value):
    """Use the same reference fields at publication and dependency expansion."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REFERENCE_KEYS:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, list):
                    yield from (identity for identity in item if isinstance(identity, str))
            elif isinstance(item, (dict, list)):
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def citations(value, pointer=""):
    if isinstance(value, dict):
        if isinstance(value.get("source_id"), str) and isinstance(value.get("capture_id"), str):
            yield pointer, value
        for key, item in value.items():
            yield from citations(item, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from citations(item, pointer + "/" + str(index))


def citation_records(controller, session, citation, *, task=None):
    capture = controller._evidence(session, citation["capture_id"], task=task)
    if controller.store.get_entry(capture["id"])["kind"] != "source_capture":
        raise ValueError("Citation capture_id must identify a saved source capture")
    source = controller._evidence(session, capture["source_id"], task=task)
    if controller.store.get_entry(source["id"])["kind"] != "source":
        raise ValueError("A captured document must link to a saved source")
    identities = list(citation.get("passage_ids") or [])
    if citation.get("passage_id"):
        identities.append(citation["passage_id"])
    passages = []
    for identity in identities:
        passage = controller._evidence(session, identity, task=task)
        if (controller.store.get_entry(identity)["kind"] != "source_passage" or
                passage.get("capture_id") != capture["id"] or passage.get("source_id") != source["id"] or
                identity not in capture.get("passage_ids", [])):
            raise ValueError("Citation passage must belong to its exact capture and source")
        passages.append(passage)
    return capture, source, passages


def validate(controller, session, task, artifact):
    """Free-form syntheses need the same link integrity as typed work products."""
    from .knowledge import supplied_passages
    read = None
    for pointer, citation in citations(artifact.content, "/content"):
        capture, source, passages = citation_records(controller, session, citation, task=task)
        if citation["source_id"] != source["id"]:
            raise ValueError(f"Citation {pointer}/source_id does not match its saved capture. "
                f"Use the exact source_id {source['id']} from capture {capture['id']}")
        if passages:
            if read is None:
                attempt = controller.store.get(task["attempt_id"], "discovery_attempt") if task.get("attempt_id") else None
                read = set(supplied_passages(attempt["context_snapshot"])) if attempt else set()
            if any((row["id"], content_hash([row["capture_id"], row["text"]])) not in read for row in passages):
                raise ValueError("Technical claims must cite passages actually supplied to this task")
    for identity in dict.fromkeys(references(artifact.model_dump(mode="json"))):
        controller._evidence(session, identity, task=task)


def correction_id(artifact):
    return "reference_correction_" + content_hash([artifact["id"], artifact["content_hash"]])[:28]


def correct_saved_sources(controller, session, artifact_id, *, reason):
    """Maintenance repair: only missing source IDs with exact capture/passages.

    This explicit operation creates an immutable linked correction. It neither
    guesses identifiers nor rewrites the original scientific artifact/response.
    Existing-but-conflicting sources, unknown captures and cross-campaign links
    remain errors. It does not confer citation support or execution approval.
    """
    artifact = controller.store.get(artifact_id, "discovery_artifact")
    if artifact["campaign_id"] != session["campaign_id"] or artifact["session_id"] != session["id"]:
        raise ValueError("Correct only an artifact from this session and campaign")
    if not reason.strip():
        raise ValueError("A source correction requires an explanation")
    identity = correction_id(artifact)
    try:
        return controller.store.get(identity, "discovery_reference_correction")
    except KeyError:
        pass
    patches = []
    for pointer, citation in citations(artifact["content"], "/content"):
        capture, source, passages = citation_records(controller, session, citation)
        if citation["source_id"] == source["id"]:
            continue
        try:
            controller.store.get_entry(citation["source_id"])
        except KeyError:
            pass
        else:
            raise ValueError("An existing conflicting source needs scientific review, not automatic relinking")
        if not passages:
            raise ValueError("Source correction requires an exact saved passage as well as a capture")
        patches.append({"pointer": pointer + "/source_id", "before": citation["source_id"], "after": source["id"],
            "capture_id": capture["id"], "passage_ids": [row["id"] for row in passages]})
    if not patches:
        raise ValueError("This artifact has no source references eligible for correction")
    return controller.store.put_immutable("discovery_reference_correction", {"id": identity,
        "campaign_id": session["campaign_id"], "session_id": session["id"], "task_id": artifact["task_id"],
        "artifact_id": artifact["id"], "original_content_hash": artifact["content_hash"], "created_at": now(),
        "reason": reason, "patches": patches,
        "basis": "Exact saved capture and passage records agree on the source ID. Original artifact and model response remain unchanged."},
        "discovery.source_reference_corrected")


def corrected_view(store, artifact):
    if not artifact.get("content_hash"):
        return artifact
    try:
        correction = store.get(correction_id(artifact), "discovery_reference_correction")
    except KeyError:
        return artifact
    if (correction["artifact_id"] != artifact["id"] or correction["campaign_id"] != artifact["campaign_id"] or
            correction["session_id"] != artifact["session_id"] or correction["original_content_hash"] != artifact["content_hash"]):
        raise ValueError("Source correction does not match the original artifact")
    result = deepcopy(artifact)
    for patch in correction["patches"]:
        pointer = patch["pointer"]
        if not pointer.startswith("/content/") or not pointer.endswith("/source_id") or _select(result, pointer) != patch["before"]:
            raise ValueError("Source correction does not match the saved reference field")
        _select(result, pointer.rsplit("/", 1)[0])["source_id"] = patch["after"]
    result["reference_correction"] = correction
    result["original_record_hash"] = result.pop("content_hash")
    result["record_projection"] = "Source-reference correction applied; see reference_correction for the exact original value and saved provenance."
    result["content_hash"] = content_hash(result)
    return result
