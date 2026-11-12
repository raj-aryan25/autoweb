"""Version-one, allowlisted Studio transport; independent of execution and HTTP."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RunState = Literal["created", "preflight", "running", "stopping", "succeeded", "failed", "cancelled", "interrupted"]
TerminalState = Literal["succeeded", "failed", "cancelled", "interrupted"]
StepState = Literal["pending", "running", "waiting", "succeeded", "skipped", "failed", "cancelled", "not_executed"]
ConnectionState = Literal["connecting", "connected", "reconnecting", "disconnected"]
Action = Literal["click", "submit", "accept_cookies", "open_login", "fill", "wait_for", "request_otp", "fill_otp", "request_magic_link", "follow_magic_link"]
SafeID = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")]
Count = Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Diagnostic = Literal["not_configured", "unavailable", "invalid_configuration", "verification_timeout", "login_timeout", "step_timeout", "authentication_rejected", "optional_target_absent", "browser_start_failed", "worker_failed", "cleanup_failed", "user_stop", "service_restart", "login_failed"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AIUsage(DTO):
    calls: Count
    prompt_tokens: Count
    candidate_tokens: Count
    total_tokens: Count


class DisplayStep(DTO):
    id: SafeID
    kind: Literal["phase", "action"]
    action: Action | None = None
    optional: bool = False
    # Waiting belongs to the consuming action, never a second email request.
    phases: list[Literal["verification_wait"]] = Field(default_factory=list)


class StepSnapshot(DTO):
    step_id: SafeID
    state: StepState
    elapsed_seconds: Seconds | None = None
    diagnostic: Diagnostic | None = None


class PreflightPayload(DTO):
    type: Literal["preflight"]
    status: Literal["started", "ready", "failed"]
    diagnostic: Diagnostic | None = None


class PhasePayload(DTO):
    type: Literal["browser_startup", "navigation", "authentication", "cleanup", "verification_baseline", "acknowledgment"]
    status: Literal["started", "succeeded", "failed", "cancelled"]
    diagnostic: Diagnostic | None = None


class StepStarted(DTO):
    type: Literal["step_started"]
    action: Action


class StepCompleted(DTO):
    type: Literal["step_completed"]
    action: Action
    status: Literal["succeeded", "skipped", "failed", "cancelled"]
    diagnostic: Diagnostic | None = None


class VerificationWait(DTO):
    type: Literal["verification_wait"]
    status: Literal["started", "received", "failed", "cancelled"]
    kind: Literal["otp", "magic_link"]
    diagnostic: Diagnostic | None = None


class ArtifactRegistered(DTO):
    type: Literal["artifact_registered"]
    artifact_id: SafeID
    kind: Literal["masked_screenshot", "sanitized_report"]
    # No filesystem paths, raw traces, or arbitrary URLs.


class RunStateChanged(DTO):
    type: Literal["run_state_changed"]
    state: RunState


class FinalOutcome(DTO):
    type: Literal["final_outcome"]
    state: TerminalState
    authentication: Literal["succeeded", "failed", "not_observed"]
    diagnostic: Diagnostic | None = None
    ai_usage: AIUsage | None = None

    @model_validator(mode="after")
    def proven_success(self):
        if self.state == "succeeded" and self.authentication != "succeeded":
            raise ValueError("Successful runs require observed authentication")
        return self


Payload = Annotated[PreflightPayload | PhasePayload | StepStarted | StepCompleted | VerificationWait | ArtifactRegistered | RunStateChanged | FinalOutcome, Field(discriminator="type")]


class RunEvent(DTO):
    schema_version: Literal[1]
    run_id: SafeID
    sequence: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
    timestamp: datetime
    elapsed_seconds: Seconds
    step_id: SafeID
    phase_id: SafeID
    payload: Payload

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone")
        return value


class RunSnapshot(DTO):
    schema_version: Literal[1]
    run_id: SafeID
    profile_id: SafeID
    state: RunState
    cursor: Count
    steps: list[StepSnapshot]
    outcome: FinalOutcome | None = None

    @model_validator(mode="after")
    def terminal_outcome(self):
        terminal = self.state in {"succeeded", "failed", "cancelled", "interrupted"}
        if terminal != (self.outcome is not None):
            raise ValueError("Only terminal snapshots require an outcome")
        if self.outcome is not None and self.outcome.state != self.state:
            raise ValueError("Snapshot and outcome must agree")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("Step IDs must be unique")
        return self


class Replay(DTO):
    status: Literal["replay", "resync_required"]
    after_sequence: Count
    snapshot: RunSnapshot
    events: list[RunEvent]

    @model_validator(mode="after")
    def ordered_replay(self):
        if self.status == "resync_required":
            if self.events:
                raise ValueError("Resync uses the snapshot, not partial replay")
            return self
        expected = self.after_sequence + 1
        elapsed = -1.0
        for event in self.events:
            if event.run_id != self.snapshot.run_id or event.sequence != expected:
                raise ValueError("Replay must be contiguous and belong to one run")
            if event.elapsed_seconds < elapsed:
                raise ValueError("Elapsed duration cannot decrease")
            expected += 1
            elapsed = event.elapsed_seconds
        if expected - 1 != self.snapshot.cursor:
            raise ValueError("Replay must reach the snapshot cursor")
        return self


class RunOverrides(DTO):
    headless: bool = Field(default=False, strict=True)
    slow_mo_ms: Annotated[int, Field(strict=True, ge=0, le=2000)] = 0
    login_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=600)] = 120
    verification_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=600)] = 60
    ai_enabled: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def timeout_budget(self):
        if self.verification_timeout_seconds > self.login_timeout_seconds:
            raise ValueError("Verification timeout exceeds login budget")
        return self


class StartRun(DTO):
    request_id: SafeID
    profile_id: SafeID
    overrides: RunOverrides = Field(default_factory=RunOverrides)


class ProfileDescriptor(DTO):
    id: SafeID
    display_name: Literal["Controlled password", "Controlled OTP", "Controlled magic link", "Practice Test Automation", "Expand Testing"]
    login_mode: Literal["password", "email_otp", "password_email_otp", "email_magic_link"]
    classification: Literal["controlled", "live"]
    authorization: Literal["controlled_local", "explicit_selection_required"]
    readiness: Literal["unverified", "configured", "ready", "unavailable"] = "unverified"
    required_capabilities: list[Literal["browser", "fixture_server", "in_memory_verification", "gmail"]]
    steps: list[DisplayStep]


class ContractScenario(DTO):
    name: Literal["success", "skip", "timeout", "cancellation", "reconnect", "resync", "interruption"]
    connection: ConnectionState
    transport: Replay
