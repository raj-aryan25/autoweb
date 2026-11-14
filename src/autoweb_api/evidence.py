"""Producer-only, fixed-name sanitized evidence. Never serialize raw LoginResult.

Existing package screenshots are eligible only on the controlled fixture pages,
whose visible text never echoes secrets. Live-site captures remain private.
"""

import json
import os
from pathlib import Path

from .artifacts import read_artifact

ARTIFACTS = {
    "run-report": ("evidence/report.json", "sanitized_report"),
    "failure-screenshot": ("evidence/failure.png", "masked_screenshot"),
}


def approved_artifact(artifact_id, kind):
    relative, expected = ARTIFACTS[artifact_id]
    if kind != expected:
        raise ValueError("unapproved_artifact")
    return relative


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def publish_evidence(directory: Path, events, prepared, state, code, usage, failure=None):
    """Best effort: absence of evidence never replaces the authentication result."""
    if not directory.is_dir():
        return
    # All fields below are closed transport values, never target/URL/error text.
    report = {"schema_version": 1, "profile_id": prepared.registration.id,
              "execution_state": state, "authentication": events.authentication,
              "diagnostic": code, "ai_usage": usage.model_dump() if usage else None,
              "cleanup": "await_guardian_snapshot"}
    try:
        relative, kind = ARTIFACTS["run-report"]
        write_private(directory / relative, json.dumps(report, separators=(",", ":")).encode())
        events.emit({"type": "artifact_registered", "artifact_id": "run-report", "kind": kind})
    except (OSError, ValueError):
        pass
    if failure is None or prepared.registration.classification != "controlled":
        return
    # Do not trust arbitrary raw paths or serve a screenshot from a live website.
    for source in failure.artifacts[:1]:
        try:
            if source.parent != directory / "login-failures" or source.suffix != ".png":
                continue
            data, _ = read_artifact(directory.parent, directory.name,
                                    "login-failures/" + source.name, "masked_screenshot")
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 10 * 1024 * 1024:
                continue
            relative, kind = ARTIFACTS["failure-screenshot"]
            write_private(directory / relative, data)
            events.emit({"type": "artifact_registered", "artifact_id": "failure-screenshot", "kind": kind})
        except (OSError, ValueError, KeyError):
            continue
