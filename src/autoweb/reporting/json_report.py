"""Explicit structured reporting; omit configuration, URLs, values and sessions."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoweb.login.results import LoginResult


def login_report(result: "LoginResult") -> dict:
    return {
        "site": result.site,
        "status": result.status,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "failed_step": result.failed_step,
        "reason": result.reason,
        "steps": [
            {"index": step.index, "action": step.action,
             "status": step.result.status, "reason": step.result.reason,
             "elapsed_seconds": round(step.result.elapsed_seconds, 3)}
            for step in result.steps
        ],
        "artifacts": [str(path) for path in result.artifacts],
    }


def write_login_report(result: "LoginResult", destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(login_report(result), indent=2) + "\n", encoding="utf-8")
    return destination
