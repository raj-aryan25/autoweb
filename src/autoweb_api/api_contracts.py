"""Sanitized HTTP records; credentials and filesystem locations have no fields."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import ArtifactRegistered, Count, DTO, LiveViewStatus, RunOverrides, RunSnapshot, SafeID
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


class ViewFrameMetadata(DTO):
    sequence: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
    captured_at: datetime
    width: Annotated[int, Field(strict=True, ge=1, le=1920)]
    height: Annotated[int, Field(strict=True, ge=1, le=1080)]
    byte_length: Annotated[int, Field(strict=True, ge=1, le=1048576)]
    format: Literal["jpeg"] = "jpeg"

    @field_validator("captured_at")
    @classmethod
    def timezone_required(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone")
        return value


class RunView(DTO):
    run_id: SafeID
    status: LiveViewStatus
    frame: ViewFrameMetadata | None = None

    @model_validator(mode="after")
    def frame_availability(self):
        if self.status.state == "live" and self.frame is None:
            raise ValueError("A live view requires a frame")
        if self.frame is not None and self.status.state not in {"live", "ended"}:
            raise ValueError("Only a live or ending view can have a frame")
        return self
