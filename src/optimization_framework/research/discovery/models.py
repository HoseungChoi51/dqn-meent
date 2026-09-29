"""Versioned discovery policy and bounded model/tool envelopes."""
from typing import Any, Literal

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract


class RoleModel(Contract):
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)


class DiscoveryStart(Contract):
    task_id: str
    objective: str = Field(default="Find an effective optimizer for this problem using literature, experiments and rough tuning.", min_length=1, max_length=20000)
    model_call_limit: int = Field(default=96, ge=3, le=10000)
    api_budget_usd: float | None = Field(default=None, ge=0, le=10000)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=8)
    max_calls_per_task: int = Field(default=8, ge=1, le=20)
    max_output_tokens: int = Field(default=4096, ge=512, le=8192)
    max_tasks: int = Field(default=200, ge=3, le=5000)
    max_rounds: int = Field(default=8, ge=1, le=100)
    source_request_limit: int = Field(default=64, ge=0, le=1000)
    max_tools_per_task: int = Field(default=32, ge=0, le=120)
    experiment_compute_seconds: float = Field(default=0, ge=0, le=604800)
    implementation_compute_seconds: float = Field(default=0, ge=0, le=604800)
    synthesis_call_reserve: int = Field(default=3, ge=1, le=20)
    role_models: dict[str, RoleModel] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def reserve_fits(self):
        if self.synthesis_call_reserve >= self.model_call_limit:
            raise ValueError("Reserve fewer synthesis calls than the total session allowance")
        return self


class DiscoveryControl(Contract):
    session_id: str
    action: Literal["pause", "resume", "stop"]
    expected_control_revision: int = Field(ge=0)


class DiscoveryAmend(Contract):
    session_id: str
    expected_control_revision: int = Field(ge=0)
    policy: DiscoveryStart
    reason: str = Field(min_length=1, max_length=5000)


class DiscoveryRetry(Contract):
    session_id: str
    expected_control_revision: int = Field(ge=0)
    task_ids: list[str] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("Choose each failed task only once")
        return self


class DiscoveryTaskBrief(Contract):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    role: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=12000)
    persona: str = Field(default="", max_length=12000)
    stage: Literal["analyze", "study", "generate", "critique", "assess", "evaluate", "review", "synthesize", "manage"] = "analyze"
    dependencies: list[str] = Field(default_factory=list, max_length=30,
        description="Task IDs or task keys to wait for. Put already saved artifact IDs in evidence_ids.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class DiscoveryToolCall(Contract):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    tool: Literal["source.search", "source.ingest", "source.read", "evidence.read", "context.read", "implementation.inspect", "experiment.inspect", "assessment.inspect", "assessment.prepare", "assessment.launch", "assessment.wait", "task.supersede"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class DiscoveryArtifact(Contract):
    kind: Literal["problem_dossier", "literature_map", "candidate_batch", "proposal_review", "assessment_plan", "evaluation", "review", "synthesis"]
    title: str = Field(min_length=1, max_length=500)
    content: dict[str, Any]
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class DiscoveryResult(Contract):
    summary: str = Field(min_length=1, max_length=20000)
    rationale: str = Field(default="", max_length=12000)
    disposition: Literal["continue", "complete", "wait", "blocked", "handoff"] = Field(default="complete",
        description="Status of THIS assigned task, not the whole campaign. Use complete when its work product or manager assignments are delivered. Use continue only when further work within this task is needed, normally tool results. Use handoff to save partial findings, unresolved gaps and a narrow continuation when an allowance is reached. The manager coordinates subsequent stages.")
    tools: list[DiscoveryToolCall] = Field(default_factory=list, max_length=6)
    artifacts: list[DiscoveryArtifact] = Field(default_factory=list, max_length=8)
    proposed_tasks: list[DiscoveryTaskBrief] = Field(default_factory=list, max_length=12)
    dissent: list[str] = Field(default_factory=list, max_length=30)
    questions_for_manager: list[str] = Field(default_factory=list, max_length=10)
    session_action: Literal["continue", "complete", "request_researcher_input"] = Field(default="continue",
        description="Separate campaign-session decision, used only by the manager. A specialist normally returns disposition=complete and session_action=continue.")

    @model_validator(mode="after")
    def tools_need_continuation(self):
        if self.tools and self.disposition != "continue":
            raise ValueError("Tool requests require continuation so returned evidence can be interpreted")
        if len({tool.key for tool in self.tools}) != len(self.tools):
            raise ValueError("Tool keys must be unique within a result")
        return self
