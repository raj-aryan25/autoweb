"""Sanitized HTTP records; credentials and filesystem locations have no fields."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .contracts import ArtifactRegistered, Count, DTO, RunOverrides, RunSnapshot, SafeID
from .protocol import Manifest


class PreflightRequest(DTO):
    profile_id: SafeID
    overrides: RunOverrides = Field(default_factory=RunOverrides)


class PreflightResult(DTO):
    status: Literal["ready", "unavailable", "invalid_configuration"]
    manifest: Manifest | None = None
    external_services: Literal["unverified"] = "unverified"


class ArtifactDescriptor(ArtifactRegistered):
    step_id: SafeID


class RunRecord(DTO):
    snapshot: RunSnapshot
    request_id: SafeID
    overrides: RunOverrides
    manifest: Manifest | None = None
    created_at: datetime
    updated_at: datetime
    active: bool
    cleanup_verified: bool
    cleanup_required: bool
    authentication: Literal["succeeded", "failed", "not_observed"]
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list, max_length=16)


class RunPage(DTO):
    runs: list[RunRecord]
    total: Count
    offset: Count
    limit: int = Field(ge=1, le=100)
    active_run_id: SafeID | None = None
