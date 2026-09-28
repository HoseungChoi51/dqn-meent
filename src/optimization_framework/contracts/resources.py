"""An execution grant is a resource envelope, not a scientific stop condition."""
from pydantic import Field, model_validator

from .base import Contract


class ExecutionGrant(Contract):
    id: str
    campaign_id: str
    owner_id: str
    worker_seconds: float = Field(gt=0)
    starts_at: float = Field(gt=0)
    deadline_at: float = Field(gt=0)
    max_workers: int = Field(ge=1, le=16)
    stop_grace_seconds: float = Field(default=5, ge=0, le=60)
    authority: str

    @model_validator(mode="after")
    def deadline_follows_activation(self):
        if self.deadline_at <= self.starts_at:
            raise ValueError("An execution deadline must follow its fixed activation time")
        return self
