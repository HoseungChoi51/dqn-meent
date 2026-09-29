"""Public Pi projections and authenticated tool/library callbacks."""
import hmac
import time
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from optimization_framework.contracts.base import content_hash
from .client import service_token
from .tools import PiTools, READ_KINDS


class Context(BaseModel):
    agent_id: str
    run_id: str


class ToolCall(Context):
    call_id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class ImplementationAssignment(BaseModel):
    role: str
    grant_id: str
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    instructions: str = Field(max_length=100000)
    context: dict
    output_schema: dict
    deadline_at: float


def install(app, workspace):
    gateway = PiTools(workspace.pi)

    def authenticate(authorization: str = Header(default="")):
        try:
            expected = "Bearer " + service_token()
        except (ValueError, OSError):
            raise HTTPException(503, "Pi connection is not configured") from None
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Pi service authentication required")

    @app.get("/api/campaigns/{campaign_id}/agents")
    def agents(campaign_id: str):
        workspace.store.get(campaign_id, "campaign")
        return workspace.pi.view(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/agents/records/{record_id}")
    def agent_record(campaign_id: str, record_id: str):
        entry = workspace.store.get_entry(record_id)
        if entry["kind"] not in READ_KINDS or entry["data"].get("campaign_id") != campaign_id:
            raise HTTPException(404, "Agent evidence is not in this campaign")
        return entry

    @app.post("/api/internal/pi/manifest", dependencies=[Depends(authenticate)])
    def manifest(body: Context):
        return gateway.manifest(body.agent_id, body.run_id)

    @app.post("/api/campaigns/{campaign_id}/agents/login")
    def login(campaign_id: str):
        workspace.store.get(campaign_id, "campaign")
        return workspace.pi.client.request("POST", "/v1/auth/start", {})

    @app.post("/api/internal/pi/tool", dependencies=[Depends(authenticate)])
    def tool(body: ToolCall):
        return gateway.call(**body.model_dump())

    @app.post("/api/internal/pi/implementation", dependencies=[Depends(authenticate)])
    def implementation(request: ImplementationAssignment):
        body = request.model_dump()
        role = body["role"]
        if role not in {"implementation_builder", "implementation_test_designer", "implementation_validator"}:
            raise ValueError("Unsupported implementation role")
        grant = workspace.store.get(body["grant_id"], "implementation_grant")
        campaign_id = grant["campaign_id"]
        config = workspace.pi.configuration(campaign_id)
        if not config or not config["enabled"] or config["status"] != "running":
            raise ValueError("The campaign PI is not active")
        if body["deadline_at"] <= time.time():
            raise ValueError("Implementation allocation has expired")
        if grant["request"].get("agent_parent_id") != config["pi_id"] or grant["status"] in {"completed", "failed", "cancelled", "closed_uncertain"}:
            raise ValueError("Implementation assignment is outside the active PI grant")
        frozen_spec = grant["request"]["spec"]
        if body["context"].get("spec", {}).get("mechanism") != frozen_spec.get("mechanism"):
            raise ValueError("Implementation mechanism differs from the frozen grant")
        identity = "agent_impl_" + content_hash([grant["id"], role,
            None if role == "implementation_builder" else body["request_id"]])[:28]
        with workspace.lock, workspace.store.transaction():
            agent = workspace.pi.create_agent(campaign_id, role, body["instructions"], identity,
                parent_id=config["pi_id"], grant_id=grant["id"], output_schema=body["output_schema"])
            agent["deadline_at"] = min(body["deadline_at"], time.time() + grant["request"]["compute_seconds"])
            workspace.store.put("agent_session", agent)
            import json
            run = workspace.pi.enqueue(agent, "pi_impl_run_" + body["request_id"], json.dumps(body["context"]), mode="follow_up")
        return {"agent_id": identity, "run_id": run["id"]}

    @app.get("/api/internal/pi/implementation/{run_id}", dependencies=[Depends(authenticate)])
    def implementation_result(run_id: str):
        run = workspace.store.get(run_id, "agent_run")
        agent = workspace.store.get(run["agent_id"], "agent_session")
        if not agent.get("grant_id"):
            raise ValueError("Not an implementation assignment")
        return {"agent_id": agent["id"], "status": run["status"], "output": run.get("output"), "error": run.get("error"), "usage": agent["usage"]}
