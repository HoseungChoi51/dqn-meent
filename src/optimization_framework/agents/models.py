from typing import Literal
from pydantic import Field
from optimization_framework.contracts.base import Contract


ROLES = ("pi", "literature_specialist", "methodology_specialist", "cross_domain_explorer",
         "skeptical_domain_analyst", "empirical_assessor", "proposal_reviewer", "implementation_builder",
         "implementation_test_designer", "implementation_validator", "results_analyst")


class Activate(Contract):
    objective: str = Field(default="Continue the campaign from its saved evidence and unfinished requests.", min_length=1, max_length=20000)
    max_subagents: int = Field(default=4, ge=1, le=8)
    delegated: bool = True


class Message(Contract):
    agent_id: str | None = None
    message: str = Field(min_length=1, max_length=50000)
    mode: Literal["steer", "follow_up"] = "steer"
    question_id: str | None = None


class Control(Contract):
    agent_id: str | None = None
    action: Literal["pause", "resume", "stop"]
    expected_control_revision: int = Field(ge=0)


class Rollback(Contract):
    reason: str = Field(min_length=1, max_length=5000)
