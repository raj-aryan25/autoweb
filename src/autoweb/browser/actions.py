"""Focused async interactions with bounded waits and explicit outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Sequence

from pydantic import SecretStr

from autoweb.config.models import Locator as LocatorSpec, TimeoutSettings
from autoweb.contracts import ActionResult

from .errors import BrowserActionError, BrowserActionTimeout
from .locators import resolve_locator

if TYPE_CHECKING:
    from playwright.async_api import Page


@dataclass(frozen=True)
class _Budget:
    action: str
    deadline: float

    def remaining_ms(self) -> float:
        remaining = (self.deadline - monotonic()) * 1000
        if remaining <= 0:
            raise BrowserActionTimeout(self.action)
        return remaining


def _timeout(value: float | None, default: float, action: str) -> float:
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrowserActionError(action, "invalid_timeout")
    if not math.isfinite(value) or value <= 0:
        raise BrowserActionError(action, "invalid_timeout")
    return float(value)


def _text(value: str | SecretStr, action: str) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if not isinstance(value, str):
        raise BrowserActionError(action, "invalid_input")
    return value


async def _perform(
    action: str, operation: Callable[[_Budget], Awaitable[str | None]], timeout_ms: float,
) -> ActionResult:
    from playwright.async_api import Error, TimeoutError

    started = monotonic()
    budget = _Budget(action, started + timeout_ms / 1000)
    try:
        reason = await operation(budget)
    except TimeoutError:
        raise BrowserActionTimeout(action) from None
    except Error as error:
        # Playwright's original errors can contain DOM snapshots or filled secrets.
        message = str(error)
        if "strict mode violation" in message:
            reason = "ambiguous_target"
        elif "has been closed" in message or "Target closed" in message:
            reason = "session_closed"
        else:
            reason = "interaction_failed"
        raise BrowserActionError(action, reason) from None
    return ActionResult(
        action, "skipped" if reason else "succeeded", monotonic() - started, reason,
    )


async def navigate(
    page: Page, url: str, *, timeout_ms: float | None = None,
    wait_until: Literal["commit", "domcontentloaded", "load"] = "domcontentloaded",
) -> ActionResult:
    timeout = _timeout(timeout_ms, TimeoutSettings().navigation_ms, "navigate")

    async def operation(budget: _Budget) -> None:
        response = await page.goto(url, timeout=budget.remaining_ms(), wait_until=wait_until)
        if response is not None and response.status >= 400:
            raise BrowserActionError("navigate", "http_error")

    return await _perform("navigate", operation, timeout)


async def click(
    page: Page, target: LocatorSpec, *, timeout_ms: float | None = None,
) -> ActionResult:
    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "click")

    async def operation(budget: _Budget) -> None:
        await resolve_locator(page, target).click(timeout=budget.remaining_ms())

    return await _perform("click", operation, timeout)


async def fill(
    page: Page, target: LocatorSpec, value: str | SecretStr, *, timeout_ms: float | None = None,
) -> ActionResult:
    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "fill")
    text = _text(value, "fill")

    async def operation(budget: _Budget) -> None:
        await resolve_locator(page, target).fill(text, timeout=budget.remaining_ms())

    return await _perform("fill", operation, timeout)


async def submit(
    page: Page, target: LocatorSpec, *, timeout_ms: float | None = None,
) -> ActionResult:
    """Click the configured submit control; callers verify the resulting state."""
    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "submit")

    async def operation(budget: _Budget) -> None:
        await resolve_locator(page, target).click(timeout=budget.remaining_ms())

    return await _perform("submit", operation, timeout)


async def accept_cookies(
    page: Page, target: LocatorSpec, *, optional: bool = True,
    timeout_ms: float | None = None,
) -> ActionResult:
    """Skip only absent optional controls; ambiguous/disabled controls still fail."""
    from playwright.async_api import TimeoutError

    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "accept_cookies")

    async def operation(budget: _Budget) -> str | None:
        # Include hidden role matches when checking physical presence.
        locator = resolve_locator(page, target, include_hidden=True)
        try:
            await locator.wait_for(state="attached", timeout=budget.remaining_ms())
        except TimeoutError:
            if optional:
                return "absent"
            raise
        await locator.click(timeout=budget.remaining_ms())
        return None

    return await _perform("accept_cookies", operation, timeout)


async def fill_otp(
    page: Page, targets: Sequence[LocatorSpec], value: str | SecretStr, *,
    expected_length: int | None = None, timeout_ms: float | None = None,
) -> ActionResult:
    """Fill a single code field or split fields within one shared timeout budget."""
    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "fill_otp")
    text = _text(value, "fill_otp")
    if not text or any(character.isspace() for character in text) or not targets:
        raise BrowserActionError("fill_otp", "invalid_input")
    if expected_length is not None:
        if (isinstance(expected_length, bool) or not isinstance(expected_length, int)
                or expected_length <= 0 or len(text) != expected_length):
            raise BrowserActionError("fill_otp", "invalid_input")
    if len(targets) not in {1, len(text)}:
        raise BrowserActionError("fill_otp", "invalid_input")
    if len({target.model_dump_json() for target in targets}) != len(targets):
        raise BrowserActionError("fill_otp", "duplicate_targets")
    values = [text] if len(targets) == 1 else list(text)

    async def operation(budget: _Budget) -> None:
        for target, character in zip(targets, values):
            await resolve_locator(page, target).fill(character, timeout=budget.remaining_ms())

    return await _perform("fill_otp", operation, timeout)


async def wait_for_state(
    page: Page, target: LocatorSpec, *,
    state: Literal["visible", "hidden", "attached", "detached"] = "visible",
    timeout_ms: float | None = None,
) -> ActionResult:
    timeout = _timeout(timeout_ms, TimeoutSettings().action_ms, "wait_for_state")

    async def operation(budget: _Budget) -> None:
        await resolve_locator(
            page, target, include_hidden=state in {"attached", "detached"},
        ).wait_for(state=state, timeout=budget.remaining_ms())

    return await _perform("wait_for_state", operation, timeout)


async def wait_for_url(
    page: Page, pattern: str, *, timeout_ms: float | None = None,
    wait_until: Literal["commit", "domcontentloaded", "load"] = "domcontentloaded",
) -> ActionResult:
    timeout = _timeout(timeout_ms, TimeoutSettings().navigation_ms, "wait_for_url")

    async def operation(budget: _Budget) -> None:
        await page.wait_for_url(pattern, timeout=budget.remaining_ms(), wait_until=wait_until)

    return await _perform("wait_for_url", operation, timeout)
