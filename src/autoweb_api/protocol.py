"""Bounded private worker protocol, separate from HTTP and SSE."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from time import monotonic
from typing import Literal

from pydantic import Field

from autoweb.config.models import BrowserSettings, TimeoutSettings

from .contracts import DTO, DisplayStep, FinalOutcome, RunEvent, SafeID, StartRun

MAX_LINE = 16384
MAX_EVENTS = 2048
MAX_OUTPUT = 4 * 1024 * 1024
MAX_STEPS = 256


class Launch(DTO):
    type: Literal["start"]
    run_id: SafeID
    request: StartRun


class Stop(DTO):
    type: Literal["stop"]


class EffectiveSettings(DTO):
    browser: BrowserSettings
    timeouts: TimeoutSettings
    ai_enabled: bool = Field(strict=True)
    ai_model: str | None = Field(default=None, pattern=r"^(?:models/)?[A-Za-z0-9._-]+$")


class Manifest(DTO):
    type: Literal["manifest"]
    profile_id: SafeID
    steps: list[DisplayStep] = Field(max_length=MAX_STEPS + 10)
    effective: EffectiveSettings


class ExecutorResult(DTO):
    type: Literal["result"]
    outcome: FinalOutcome


class Hello(DTO):
    type: Literal["hello"]
    run_id: SafeID
    executor_pid: int = Field(strict=True, gt=1)


class Cleaned(DTO):
    type: Literal["cleaned"]
    forced: bool = Field(strict=True)


class RecoveryReceipt(DTO):
    run_id: SafeID
    guardian_pid: int = Field(strict=True, gt=1)
    guardian_started: int = Field(strict=True, ge=0)
    outcome: FinalOutcome
    forced: bool = Field(strict=True)


def encode(value: DTO) -> bytes:
    data = value.model_dump_json().encode() + b"\n"
    if len(data) > MAX_LINE:
        raise ValueError("protocol_limit")
    return data


def decode(data: bytes):
    if len(data) > MAX_LINE or not data.endswith(b"\n"):
        raise ValueError("protocol_limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("protocol_invalid")
    kind = value.get("type")
    model = {"manifest": Manifest, "result": ExecutorResult, "hello": Hello,
             "cleaned": Cleaned}.get(kind, RunEvent)
    return model.model_validate(value)


class EventClock:
    def __init__(self, run_id: str, epoch: float):
        self.run_id = run_id
        self.epoch = epoch
        self.sequence = 0
        self.elapsed = 0.0

    def event(self, payload, *, step_id="outcome", phase_id="outcome", observation=None):
        self.sequence += 1
        self.elapsed = max(self.elapsed, monotonic() - self.epoch if observation is None else observation[1])
        return RunEvent(
            schema_version=1, run_id=self.run_id, sequence=self.sequence,
            timestamp=datetime.now(timezone.utc) if observation is None else observation[0],
            elapsed_seconds=self.elapsed, step_id=step_id, phase_id=phase_id, payload=payload,
        )
