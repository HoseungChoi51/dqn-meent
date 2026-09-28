"""Legacy HTTP shapes translated into the owning application command.

Idempotency-Key identifies a retry. X-Campaign-Revision and
X-Guidance-Revision supply explicit preconditions; old clients without those
headers act against the revision observed at admission. Accepted keyed retries
retain the original envelope, never the newer revision observed on retry.
"""
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.storage.sqlite import identifier


def request_identity(request, idempotency_key=None):
    header = request.headers.get("idempotency-key")
    if header and idempotency_key and header != idempotency_key:
        raise ValueError("Header and body idempotency keys must identify the same request")
    key = header or idempotency_key
    return "http_" + content_hash([request.url.path, key]) if key else identifier("http")


def prior_request(workspace, request, idempotency_key):
    try:
        return workspace.store.get(request_identity(request, idempotency_key), "work_command")["request"]
    except KeyError:
        return None


def resource(workspace, accepted, kind, target):
    """Legacy asynchronous controls expose current state and the fixed receipt."""
    record = workspace.store.get(accepted["outcome"][target], kind)
    return {**record, "command_id": accepted["id"], "command_outcome": accepted["outcome"]}


def execute(workspace, request, operation, payload, *, campaign_id=None, idempotency_key=None):
    identity = request_identity(request, idempotency_key)
    with workspace.lock:
        try:
            previous = workspace.store.get(identity, "work_command")["request"]
        except KeyError:
            previous = {}
        if operation == "campaign.create":
            campaign_id = "campaign_" + content_hash(identity)[:32]
            revision = 0
        else:
            revision = previous.get("expected_revision")
            if revision is None:
                revision = workspace.store.get(campaign_id, "campaign")["version"]
        if request.headers.get("x-campaign-revision") is not None:
            revision = int(request.headers["x-campaign-revision"])
        guidance = previous.get("expected_guidance_revision")
        if request.headers.get("x-guidance-revision") is not None:
            guidance = int(request.headers["x-guidance-revision"])
        payload = dict(payload)
        if operation == "issue.resolve" and payload.get("expected_revision") is None:
            revision_target = previous.get("payload", {}).get("expected_revision")
            if revision_target is None:
                issue = workspace.store.get(payload["issue_id"], "manager_issue")
                revision_target = issue.get("revision", issue.get("occurrences", 1))
            payload["expected_revision"] = revision_target
        if operation in {"trial.control", "research.control"}:
            revision_target = previous.get("payload", {}).get("expected_control_revision")
            if request.headers.get("x-control-revision") is not None:
                revision_target = int(request.headers["x-control-revision"])
            if revision_target is None:
                kind, key = ("trial", "trial_id") if operation == "trial.control" else ("research_run", "run_id")
                revision_target = workspace.store.get(payload[key], kind).get("control_revision", 0)
            payload["expected_control_revision"] = revision_target
        if operation == "decision.resolve":
            revision_target = previous.get("payload", {}).get("expected_resolution_revision")
            if request.headers.get("x-resolution-revision") is not None:
                revision_target = int(request.headers["x-resolution-revision"])
            if revision_target is None:
                revision_target = workspace.store.get(payload["decision_id"], "decision").get("resolution_revision", 0)
            payload["expected_resolution_revision"] = revision_target
        if operation == "hypothesis.status":
            revision_target = previous.get("payload", {}).get("expected_status_revision")
            if request.headers.get("x-status-revision") is not None:
                revision_target = int(request.headers["x-status-revision"])
            if revision_target is None:
                revision_target = workspace.store.get(payload["hypothesis_id"], "hypothesis").get("status_revision", 0)
            payload["expected_status_revision"] = revision_target
        command = Command(id=identity, campaign_id=campaign_id, operation=operation,
            expected_revision=revision, expected_guidance_revision=guidance, payload=payload)
    return workspace.commands.execute(command, actor="researcher")
