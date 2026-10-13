"""Secret-free login outcomes and the successful caller-owned session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from autoweb.contracts import ActionResult

if TYPE_CHECKING:
    from autoweb.browser import BrowserSession


@dataclass(frozen=True)
class StepResult:
    index: int
    action: str
    result: ActionResult


@dataclass(frozen=True)
class LoginResult:
    site: str
    status: Literal["succeeded", "failed"]
    elapsed_seconds: float
    steps: tuple[StepResult, ...] = ()
    failed_step: str | None = None
    reason: str | None = None
    artifacts: tuple[Path, ...] = ()
    session: BrowserSession | None = field(default=None, repr=False)
