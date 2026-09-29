"""Implementation-library adapter to PI-owned persistent workers, not LLMAdapter."""
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from optimization_framework.contracts.base import content_hash
from .client import service_token


class PiImplementationAdapter:
    def __init__(self, request, *, deadline_monotonic, progress, usage=None):
        self.request = request
        self.deadline = deadline_monotonic
        self.progress = progress
        self.usage = usage or {"calls": 0, "subscription_calls": 0, "billing_mode": "subscription", "api_cost_usd": 0}

    def transport(self, route, body=None):
        base = os.environ.get("GRATING_PI_WORKSPACE_URL", "http://127.0.0.1:8765")
        request = Request(base + "/api/internal/pi/implementation" + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + service_token(), "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("detail", "Pi assignment failed")
            except ValueError:
                detail = "Pi assignment failed"
            raise ValueError(detail) from None
        except (URLError, TimeoutError, OSError):
            raise ConnectionError("Pi workspace connection interrupted") from None

    def call(self, role, context, *, result_type, instructions):
        request_id = content_hash([self.request.grant_id, role, context])[:32]
        payload = {"grant_id": self.request.grant_id, "request_id": request_id, "role": role,
            "instructions": instructions + "\nUse isolated workspace tools for development. Submit the final structured result through submit_output.",
            "context": context, "output_schema": result_type.model_json_schema(),
            "deadline_at": time.time() + max(0, self.deadline - time.monotonic())}
        submitted = None
        while time.monotonic() < self.deadline:
            self.progress()
            try:
                if submitted is None:
                    submitted = self.transport("", payload)
                current = self.transport("/" + submitted["run_id"])
                accounting = self.usage.setdefault("agent_usage", {})
                accounting[current["agent_id"]] = current.get("usage", {})
                for key in ("calls", "input", "output", "cacheRead", "cacheWrite"):
                    self.usage[key] = sum(value.get(key, 0) for value in accounting.values())
                self.usage["subscription_calls"] = self.usage["calls"]
                if current["status"] == "completed":
                    if current.get("output") is None:
                        raise ValueError("Pi worker completed without the required structured output; saved work is retained")
                    self.usage["agent_runs"] = list(dict.fromkeys([*self.usage.get("agent_runs", []), submitted["run_id"]]))
                    return result_type.model_validate(current["output"])
                if current["status"] in {"failed", "stopped", "interrupted", "paused"}:
                    raise ValueError(current.get("error") or "Pi worker interrupted; inspect its saved artifacts")
            except ConnectionError:
                pass  # Stable request identity reconciles a lost POST reply.
            time.sleep(.5)
        raise ValueError("Implementation time allocation exhausted; Pi work and tool receipts are preserved")
