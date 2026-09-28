"""Workspace-side client. No service credentials are sent to the browser."""
import os
from pathlib import Path

import httpx

from optimization_framework.implementations.models import LibraryUnavailable


class ImplementationClient:
    def __init__(self, url=None, token=None, *, transport=None):
        self.url = (url or os.environ.get("GRATING_IMPLEMENTATIONS_URL", "http://127.0.0.1:8766")).rstrip("/")
        self.token = token
        self.transport = transport

    def request(self, method, path, body=None, **params):
        token = self.token or os.environ.get("GRATING_IMPLEMENTATIONS_TOKEN")
        if not token:
            location = Path(os.environ.get("GRATING_IMPLEMENTATIONS_TOKEN_FILE",
                str(Path.home() / ".local/share/grating-lab/implementations/service.token")))
            try:
                token = location.read_text().strip()
            except OSError:
                raise LibraryUnavailable("Implementation service is not connected. Start grating-implementations and configure its token file.") from None
        try:
            with httpx.Client(base_url=self.url, timeout=httpx.Timeout(5, connect=1), transport=self.transport,
                              follow_redirects=False, trust_env=False) as client:
                response = client.request(method, path, json=body, params=params, headers={"Authorization": "Bearer " + token})
        except httpx.HTTPError as exc:
            raise LibraryUnavailable("Implementation service is unavailable; existing prepared experiments remain independent.") from exc
        if response.status_code in {401, 403}:
            raise LibraryUnavailable("Implementation service authentication failed. Check the configured token file.")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Implementation service request failed")
            except ValueError:
                detail = "Implementation service request failed"
            raise ValueError(str(detail))
        return response.json()

    def versions(self):
        return self.request("GET", "/v1/versions")

    def version(self, identity):
        return self.request("GET", f"/v1/versions/{identity}")

    def artifact(self, identity):
        return self.request("GET", f"/v1/versions/{identity}/artifact")

    def runtime(self, identity):
        return self.request("GET", f"/v1/versions/{identity}/runtime")

    def resolve_runtime(self, identity, request):
        return self.request("POST", f"/v1/versions/{identity}/runtime/resolve", request)

    def runtime_resolution(self, workspace_id, idempotency_key):
        return self.request("GET", "/v1/runtime-resolutions", workspace_id=workspace_id, idempotency_key=idempotency_key)

    def export_version(self, identity):
        return self.request("GET", f"/v1/versions/{identity}/export")

    def inspect_import(self, request):
        return self.request("POST", "/v1/imports/inspect", request)

    def publish_import(self, identity):
        return self.request("POST", f"/v1/imports/{identity}/publish", {})

    def submit(self, request):
        return self.request("POST", "/v1/jobs", request)

    def revalidate(self, request):
        return self.request("POST", f"/v1/versions/{request['version_id']}/validations", request)

    def validations(self, identity):
        return self.request("GET", f"/v1/versions/{identity}/validations")

    def job(self, identity):
        return self.request("GET", f"/v1/jobs/{identity}")

    def events(self, after=0, limit=200):
        return self.request("GET", "/v1/events", after=after, limit=limit)

    def agent_log(self, identity, workspace_id, grant_id, after=0):
        return self.request("GET", f"/v1/jobs/{identity}/agent-log", workspace_id=workspace_id, grant_id=grant_id, after=after)

    def agent_payload(self, identity, reference, workspace_id, grant_id):
        return self.request("GET", f"/v1/jobs/{identity}/agent-log/payloads/{reference}", workspace_id=workspace_id, grant_id=grant_id)

    def control(self, identity, action, *, idempotency_key=None):
        return self.request("POST", f"/v1/jobs/{identity}/control", {"action": action,
            **({"idempotency_key": idempotency_key} if idempotency_key else {})})

    def control_receipt(self, identity, idempotency_key):
        return self.request("GET", f"/v1/jobs/{identity}/control-receipt", idempotency_key=idempotency_key)
