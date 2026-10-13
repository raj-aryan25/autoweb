"""Execute one configured password-login step using the browser primitives."""

from __future__ import annotations

from time import monotonic
from typing import Mapping

from pydantic import SecretStr

from autoweb.browser import (
    BrowserActionTimeout, BrowserSession, accept_cookies, click, fill,
    submit, wait_for_state,
)
from autoweb.config.models import ClickStep, FillStep, LoginStep, WaitStep
from autoweb.contracts import ActionResult


async def execute_step(
    session: BrowserSession, step: LoginStep,
    credentials: Mapping[str, SecretStr], timeout_ms: float,
) -> ActionResult:
    started = monotonic()
    deadline = started + timeout_ms / 1000

    def remaining_ms() -> float:
        remaining = (deadline - monotonic()) * 1000
        if remaining <= 0:
            raise BrowserActionTimeout(step.action)
        return remaining

    if step.optional and not (isinstance(step, ClickStep) and step.action == "accept_cookies"):
        try:
            await wait_for_state(
                session.page, step.target, state="attached", timeout_ms=remaining_ms(),
            )
        except BrowserActionTimeout:
            return ActionResult(step.action, "skipped", monotonic() - started, "absent")
    if isinstance(step, FillStep):
        return await fill(
            session.page, step.target, credentials[f"credential:{step.credential}"],
            timeout_ms=remaining_ms(),
        )
    if isinstance(step, WaitStep):
        return await wait_for_state(
            session.page, step.target, state=step.state, timeout_ms=remaining_ms(),
        )
    if isinstance(step, ClickStep):
        if step.action == "accept_cookies":
            return await accept_cookies(
                session.page, step.target, optional=step.optional, timeout_ms=remaining_ms(),
            )
        operation = submit if step.action == "submit" else click
        return await operation(session.page, step.target, timeout_ms=remaining_ms())
    raise ValueError("Unsupported step in a password login profile")
