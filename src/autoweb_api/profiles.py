"""Trusted registry policy and pure profile metadata, without loading credentials."""

from dataclasses import dataclass
from typing import Literal

from autoweb.config.models import SiteProfile
from autoweb.login.progress import action_step_ids

from .contracts import DisplayStep


@dataclass(frozen=True)
class Registration:
    id: str
    source: str
    classification: Literal["controlled", "live"]
    provider: Literal["none", "in_memory", "gmail"]


# Sources are private resolver keys, never client paths. Controlled password is
# implemented with application-owned loopback fixtures and injected verification.
REGISTRY = (
    Registration("controlled-password", "fixture:password", "controlled", "none"),
    Registration("controlled-otp", "fixture:otp", "controlled", "in_memory"),
    Registration("controlled-magic-link", "fixture:magic_link", "controlled", "in_memory"),
    Registration("practice-test-password", "configs/sites/practice-test-login.json", "live", "none"),
    Registration("expandtesting-otp", "configs/sites/expandtesting-otp.example.json", "live", "gmail"),
)


def resolve_registration(profile_id: str) -> Registration:
    for registration in REGISTRY:
        if registration.id == profile_id:
            return registration
    raise ValueError("Unknown registered profile ID")


def display_steps(profile: SiteProfile) -> list[DisplayStep]:
    """Stable content IDs survive unrelated insertions; identical actions use occurrences.

    Resolve package StepResult.index against this mapping for the immutable run
    profile. Never infer the consuming action from a UI array position.
    """
    phases = [DisplayStep(id=name, kind="phase") for name in
              ("preflight", "browser_startup", "navigation")]
    for step, step_id in zip(profile.steps, action_step_ids(profile)):
        phases.append(DisplayStep(
            id=step_id, kind="action",
            action=step.action, optional=step.optional,
            phases=["verification_wait"] if step.action in {"fill_otp", "follow_magic_link"} else [],
        ))
    phases.append(DisplayStep(id="authentication", kind="phase"))
    if profile.verification:
        phases.append(DisplayStep(id="acknowledgment", kind="phase"))
    phases.extend(DisplayStep(id=name, kind="phase") for name in ("cleanup", "outcome"))
    return phases
