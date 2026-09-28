"""Local runtime availability and durable, explicit resolution receipts."""
import time
from typing import Literal

from pydantic import Field

from optimization_framework.implementations.models import Contract, CapabilityUnavailable, digest
from optimization_framework.implementations.runtime import bundle_runtime_root, resolve_runtime, verify_runtime
from optimization_framework.storage.sqlite import atomic_json, now


class ResolutionRequest(Contract):
    schema_version: Literal[1] = 1
    workspace_id: str = Field(min_length=1, max_length=160)
    campaign_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    runtime_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def receipt(service, workspace_id, idempotency_key):
    identity = "runtime_resolution_" + digest([workspace_id, idempotency_key]) + "_receipt"
    try:
        return service.store.get(identity, "runtime_resolution_receipt")
    except KeyError:
        return None


def availability(service, version_id):
    bundle = service.artifact(version_id, ready=False)
    result = {"version_id": version_id, "runtime_digest": bundle["version"]["runtime_digest"],
              "runtime_schema": bundle["artifact"]["runtime"].get("schema_version", 1)}
    try:
        verify_runtime(bundle_runtime_root(bundle), bundle["artifact"]["runtime"])
    except (OSError, ValueError) as exc:
        return {**result, "status": "unavailable", "reason": str(exc),
                "can_resolve": result["runtime_schema"] in {1, 2}}
    return {**result, "status": "available", "reason": "Exact runtime content is available locally", "can_resolve": True}


def resolve(service, version_id, request):
    """One explicit operation; retries recover its receipt without repeating work."""
    request = request if isinstance(request, ResolutionRequest) else ResolutionRequest(**request)
    identity = "runtime_resolution_" + digest([request.workspace_id, request.idempotency_key])
    request_hash = digest({"version_id": version_id, "request": request.model_dump(mode="json")})
    with service.lock:
        try:
            operation = service.store.get(identity, "runtime_resolution_operation")
        except KeyError:
            operation = None
        if operation:
            if operation["request_hash"] != request_hash:
                raise ValueError("Runtime resolution identity already belongs to a different request")
            if operation.get("receipt_id"):
                return service.store.get(operation["receipt_id"], "runtime_resolution_receipt")
        bundle = service.artifact(version_id, ready=False)
        if bundle["version"]["runtime_digest"] != request.runtime_digest:
            raise ValueError("Runtime resolution target changed")
        resumed = operation is not None
        operation = operation or {"id": identity, "schema_version": 1, "version_id": version_id, "request_hash": request_hash,
            "request": request.model_dump(mode="json"), "campaign_id": request.campaign_id, "created_at": now()}
        operation.update(status="resolving", recovered_after_interruption=resumed)
        service.store.put("runtime_resolution_operation", operation, "runtime.resolution_started")
        started = time.monotonic()
        try:
            try:
                root = bundle_runtime_root(bundle)
                verify_runtime(root, bundle["artifact"]["runtime"])
            except (OSError, ValueError):
                root = resolve_runtime(service.directory / "runtimes", bundle["artifact"]["runtime"])
            location = service.directory / "artifacts" / bundle["version"]["artifact_digest"] / "runtime-location.json"
            atomic_json(location, {"runtime_digest": request.runtime_digest, "root": str(root),
                                   "resolution_receipt_id": identity + "_receipt"})
            verified = verify_runtime(root, bundle["artifact"]["runtime"])
            conversion = verified.get("conversion")
            status, reason = "available", ("Legacy runtime measurements verified; relocation has recorded historical coverage limits"
                if conversion else "Exact runtime content resolved from installed dependencies")
        except (CapabilityUnavailable, OSError, ValueError) as exc:
            status, reason = "unavailable", str(exc)
            conversion = None
        receipt = {"id": identity + "_receipt", "schema_version": 1, "campaign_id": request.campaign_id,
            "operation_id": identity, "request_hash": request_hash, "version_id": version_id,
            "runtime_digest": request.runtime_digest, "status": status, "reason": reason,
            **({"conversion": conversion} if conversion else {}),
            "costs": {"elapsed_seconds": None if resumed else time.monotonic() - started, "model_calls": 0,
                      "downloaded_bytes": 0}, "recovered_after_interruption": resumed, "created_at": now()}
        with service.store.transaction():
            service.store.put_immutable("runtime_resolution_receipt", receipt, "runtime.resolution_recorded")
            operation.update(status="completed", receipt_id=receipt["id"], finished_at=now())
            service.store.put("runtime_resolution_operation", operation, "runtime.resolution_completed")
        return service.store.get(receipt["id"], "runtime_resolution_receipt")
