"""Captured upstream code is reusable evidence, separate from executable validation."""
from hashlib import sha256
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from optimization_framework.contracts.base import Contract
from optimization_framework.storage.sqlite import now


class ReferenceInput(Contract):
    hypothesis_id: str
    name: str = Field(min_length=1, max_length=300)
    repository_url: str = Field(min_length=1, max_length=2000)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    entrypoints: list[str] = Field(min_length=1, max_length=20)
    files: dict[str, str] = Field(min_length=1, max_length=64)
    integration_notes: str = Field(min_length=1, max_length=10000)

    @field_validator("repository_url")
    @classmethod
    def repository(cls, value):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use a public HTTPS repository URL without credentials, query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def captured_files(self):
        for name in self.files:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name or name == ".":
                raise ValueError("Reference files need canonical relative paths")
        if any(name not in self.files for name in self.entrypoints):
            raise ValueError("Every reference entrypoint must have captured source")
        if sum(len(name.encode()) + len(body.encode()) for name, body in self.files.items()) > 500_000:
            raise ValueError("Capture at most 500000 bytes of reference source per method")
        return self


def summary(record):
    return {key: value for key, value in record.items() if key != "files"}


def catalog(store, campaign_id, hypothesis=None):
    records = store.list("implementation_reference", campaign_id)
    return [summary(row) for row in records if hypothesis is None or
            (row["hypothesis_id"] == hypothesis["id"] and row["algorithm"] == hypothesis["algorithm"])]


def capture(workspace, campaign_id, payload, command_id):
    values = ReferenceInput.model_validate(payload)
    hypothesis = workspace.store.get(values.hypothesis_id, "hypothesis")
    if hypothesis["campaign_id"] != campaign_id:
        raise ValueError("The reference implementation must belong to this campaign")
    record = {**values.model_dump(mode="json"), "id": "implementation_reference_" + command_id,
        "campaign_id": campaign_id, "algorithm": hypothesis["algorithm"], "created_at": now(),
        "status": "source_available", "runnable": False,
        "verification": "Source captured; campaign integration and correctness validation are required.",
        "file_hashes": {name: sha256(body.encode()).hexdigest() for name, body in values.files.items()}}
    stored = workspace.store.put_immutable("implementation_reference", record, "implementation.reference_recorded")
    return {"reference_id": stored["id"], "hypothesis_id": hypothesis["id"], "reference": summary(stored)}
