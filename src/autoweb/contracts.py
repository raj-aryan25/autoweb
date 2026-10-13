"""Small results shared by browser actions and future login/reporting modules."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ActionResult:
    action: str
    status: Literal["succeeded", "skipped"]
    elapsed_seconds: float
    reason: str | None = None
