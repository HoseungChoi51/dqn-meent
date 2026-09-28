"""Installed-host requirements pinned independently of captured worker code."""
from typing import Literal

from pydantic import Field

from .base import Contract


class IsolationPolicy(Contract):
    kind: Literal["supervised_capture"] = "supervised_capture"
    private_bytes: int = Field(default=8 * 1024**3, ge=1024**2, multiple_of=4096)
    private_entries: int = Field(default=100000, ge=100)
    published_bytes: int = Field(default=64 * 1024**3, ge=1024**2)
