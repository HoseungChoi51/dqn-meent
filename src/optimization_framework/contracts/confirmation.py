"""Independent confirmation claims and their frozen comparison rosters."""
from typing import Any, Literal
from pydantic import Field, model_validator

from .base import Contract
from .problems import ProblemInstance


class ConfirmationProtocol(Contract):
    id: str
    campaign_id: str
    study_id: str
    kind: Literal["seed_replication", "unseen_instance", "policy_transfer"]
    methods: dict[str, dict[str, Any]] = Field(min_length=1)
    prototypes: dict[str, str] = Field(default_factory=dict)
    instances: list[ProblemInstance] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    selection_rule: str = Field(min_length=1)
    analysis: dict[str, Any] = Field(default_factory=dict)
    nomination_id: str | None = None
    reference_evidence: list[dict[str, Any]] = Field(default_factory=list)
    policy_asset_id: str | None = None
    adaptation: Literal["forbidden", "budgeted"] = "forbidden"
    adaptation_procedure: dict[str, Any] = Field(default_factory=dict)
    authority: str
    created_at: str

    @model_validator(mode="after")
    def explicit_protocol(self):
        if len(set(self.seeds)) != len(self.seeds) or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in self.seeds):
            raise ValueError("Confirmation seeds must be distinct unsigned 32-bit integers")
        if len({instance.digest() for instance in self.instances}) != len(self.instances):
            raise ValueError("Declare each confirmation instance once")
        if self.prototypes and set(self.prototypes) != set(self.methods):
            raise ValueError("Each frozen confirmation method needs one source prototype")
        if self.kind == "policy_transfer" and not self.policy_asset_id:
            raise ValueError("Policy transfer must bind an immutable learned artifact")
        if self.kind != "policy_transfer" and (self.policy_asset_id is not None or self.adaptation != "forbidden"):
            raise ValueError("Policy settings belong only to the policy-transfer protocol")
        if self.adaptation == "budgeted" and not self.adaptation_procedure:
            raise ValueError("Permitted adaptation needs a frozen procedure and allocation")
        return self
