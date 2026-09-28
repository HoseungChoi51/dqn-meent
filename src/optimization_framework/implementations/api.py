"""Local authenticated HTTP boundary for the shared implementation service."""
from contextlib import asynccontextmanager
import argparse
import hmac
import os
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from optimization_framework.implementations.models import Contract, JobControl, JobRequest, RevalidationRequest
from optimization_framework.implementations.service import ImplementationService
from optimization_framework.implementations.runtime_resolution import ResolutionRequest
from optimization_framework.implementations.exchange import ExecutableImport


class RevokeInput(Contract):
    reason: str = Field(min_length=1, max_length=2000)


def service_token(directory):
    path = Path(directory).expanduser() / "service.token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as stream:
        token = secrets.token_urlsafe(48)
        stream.write(token + "\n")
    return token


def create_app(directory, *, token=None, start_workers=True, adapter_factory=None, allow_download=False):
    service = ImplementationService(directory, adapter_factory=adapter_factory, allow_download=allow_download)
    token = token or service_token(directory)

    @asynccontextmanager
    async def lifespan(app):
        if start_workers:
            service.start()
        yield
        if start_workers:
            service.close()

    def authenticate(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Implementation service authentication required")

    app = FastAPI(title="Grating implementation service", version="1.0", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Implementation record not found"}, status_code=404)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "grating-implementations", "protocol": 1}

    secured = [Depends(authenticate)]

    def trace_scope(job_id, workspace_id, grant_id):
        job = service.store.get(job_id, "implementation_job")
        if job.get("request", {}).get("workspace_id") != workspace_id or job.get("request", {}).get("grant_id") != grant_id:
            raise HTTPException(403, "This job is outside the requested workspace grant")
        return job

    @app.get("/v1/jobs/{job_id}/agent-log", dependencies=secured)
    def agent_log(job_id: str, workspace_id: str, grant_id: str, after: int = 0, limit: int = 200):
        job = trace_scope(job_id, workspace_id, grant_id)
        service.agent_log.project(job_id)
        page = service.agent_log.page(job_id, after=after, limit=limit)
        return {**page, "origin_service_id": service.store.identity(), "job_revision": job.get("revision", 0),
                "terminal": job["status"] in {"completed", "failed", "cancelled", "closed_uncertain"}}

    @app.get("/v1/jobs/{job_id}/agent-log/payloads/{artifact_id}", dependencies=secured)
    def agent_payload(job_id: str, artifact_id: str, workspace_id: str, grant_id: str):
        trace_scope(job_id, workspace_id, grant_id)
        import json
        return json.loads(service.agent_log.payload(job_id, artifact_id))

    @app.get("/v1/versions", dependencies=secured)
    def versions():
        return service.store.list("implementation_version")

    @app.get("/v1/versions/{version_id}", dependencies=secured)
    def version(version_id: str):
        return service.version(version_id)

    @app.get("/v1/versions/{version_id}/artifact", dependencies=secured)
    def artifact(version_id: str):
        return service.artifact(version_id)

    @app.post("/v1/versions/{version_id}/revoke", dependencies=secured)
    def revoke(version_id: str, body: RevokeInput):
        return service.revoke(version_id, body.reason)

    @app.get("/v1/versions/{version_id}/runtime", dependencies=secured)
    def runtime(version_id: str):
        from optimization_framework.implementations.runtime_resolution import availability
        return availability(service, version_id)

    @app.post("/v1/versions/{version_id}/runtime/resolve", dependencies=secured)
    def resolve_runtime(version_id: str, body: ResolutionRequest):
        from optimization_framework.implementations.runtime_resolution import resolve
        return resolve(service, version_id, body)

    @app.get("/v1/runtime-resolutions", dependencies=secured)
    def runtime_resolution(workspace_id: str, idempotency_key: str):
        from optimization_framework.implementations.runtime_resolution import receipt
        return receipt(service, workspace_id, idempotency_key)

    @app.get("/v1/versions/{version_id}/export", dependencies=secured)
    def export_version(version_id: str):
        from optimization_framework.implementations.exchange import export
        return export(service, version_id)

    @app.post("/v1/imports/inspect", dependencies=secured)
    def inspect_import(body: ExecutableImport):
        from optimization_framework.implementations.exchange import inspect
        return inspect(service, body)

    @app.post("/v1/imports/{import_id}/publish", dependencies=secured)
    def publish_import(import_id: str):
        from optimization_framework.implementations.exchange import publish
        return publish(service, import_id)

    @app.post("/v1/jobs", status_code=202, dependencies=secured)
    def submit(body: RevalidationRequest | JobRequest):
        return service.submit(body)

    @app.get("/v1/jobs", dependencies=secured)
    def jobs(workspace_id: str, campaign_id: str | None = None):
        return [job for job in service.store.list("implementation_job", campaign_id)
                if job["request"]["workspace_id"] == workspace_id]

    @app.get("/v1/jobs/{job_id}", dependencies=secured)
    def job(job_id: str):
        return service.store.get(job_id, "implementation_job")

    @app.post("/v1/jobs/{job_id}/control", dependencies=secured)
    def control(job_id: str, body: JobControl):
        return service.control_once(job_id, body.action, body.idempotency_key)

    @app.get("/v1/jobs/{job_id}/control-receipt", dependencies=secured)
    def control_receipt(job_id: str, idempotency_key: str):
        return service.control_receipt(job_id, idempotency_key)

    @app.post("/v1/versions/{version_id}/validations", status_code=202, dependencies=secured)
    def revalidate(version_id: str, body: RevalidationRequest | JobRequest):
        if isinstance(body, RevalidationRequest):
            if body.version_id != version_id:
                raise ValueError("Revalidation target differs from the URL's executable version")
            return service.submit(body)
        version = service.version(version_id)
        if version["status"] == "revoked":
            raise ValueError("Revoked artifacts require a corrected version")
        root = service.directory / "artifacts" / version["artifact_digest"] / "artifact.json"
        import json
        artifact = json.loads(root.read_text())
        from optimization_framework.implementations.models import digest
        if digest(artifact) != version["artifact_digest"]:
            raise ValueError("Artifact identity changed")
        if digest(body.spec.model_dump()) != digest(artifact["spec"]) or body.package is not None:
            raise ValueError("Revalidation must use the original specification and package")
        # Translate the legacy envelope into the same mechanical check lifecycle.
        checks = {"kind": version.get("kind", "optimizer"), "rationale": "Repeat the original frozen checks"}
        if checks["kind"] == "evaluator":
            checks["validation_mode"] = artifact["spec"].get("validation_mode", "numerical")
        return service.submit(RevalidationRequest(workspace_id=body.workspace_id, campaign_id=body.campaign_id,
            hypothesis_id=body.hypothesis_id, idempotency_key=body.idempotency_key, grant_id=body.grant_id,
            version_id=version_id, checks=checks, compute_seconds=body.compute_seconds))

    @app.get("/v1/versions/{version_id}/validations", dependencies=secured)
    def validation_history(version_id: str):
        service.version(version_id)
        return [record for record in service.store.list("implementation_validation") if record["version_id"] == version_id]

    @app.get("/v1/events", dependencies=secured)
    def events(after: int = 0, limit: int = 200):
        return service.events(after=max(0, after), limit=min(1000, max(1, limit)))

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=str(Path.home() / ".local/share/grating-lab/implementations"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--allow-wheel-downloads", action="store_true")
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.directory, allow_download=args.allow_wheel_downloads), host="127.0.0.1", port=args.port,
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
