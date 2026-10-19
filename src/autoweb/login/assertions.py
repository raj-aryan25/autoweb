"""Bounded checks for rejected credentials and authenticated UI/session state."""

from __future__ import annotations

import asyncio
from fnmatch import fnmatchcase
from time import monotonic
from typing import TYPE_CHECKING, Sequence
from urllib.parse import urljoin

from autoweb.browser import BrowserActionError, BrowserActionTimeout, resolve_locator
from autoweb.config.models import APIAssertion, Assertion, ElementAssertion, SiteProfile, URLAssertion

if TYPE_CHECKING:
    from playwright.async_api import Page

POLL_SECONDS = 0.1


def _json_subset(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _json_subset(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _json_subset(left, right) for left, right in zip(actual, expected)
        )
    # In particular, authenticated: 1 must not pass authenticated: true.
    return type(actual) is type(expected) and actual == expected


async def _matches(page: Page, condition: Assertion, deadline: float, base_url: str) -> bool:
    from playwright.async_api import Error, TimeoutError

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise BrowserActionTimeout("authentication")

    async def probe() -> bool:
        if isinstance(condition, URLAssertion):
            return fnmatchcase(page.url, condition.pattern)
        if isinstance(condition, ElementAssertion):
            locator = resolve_locator(page, condition.target, include_hidden=True)
            count = await locator.count()
            if count > 1:
                raise BrowserActionError("authentication", "ambiguous_target")
            visible = await locator.is_visible() if count else False
            return visible if condition.state == "visible" else not visible
        if isinstance(condition, APIAssertion):
            response = await page.context.request.get(
                urljoin(base_url, condition.url), timeout=min(remaining, 1) * 1000,
                max_redirects=0,
            )
            try:
                if response.status != condition.expected_status:
                    return False
                try:
                    body = await response.json()
                except (ValueError, UnicodeError):
                    return False
                return _json_subset(body, condition.expected_json)
            finally:
                await response.dispose()
        raise ValueError("Unsupported authentication assertion")

    # Playwright 1.40 protocol waits are not cancellation-safe: cancelling them
    # during navigation can leave an unobserved protocol exception. Let this
    # read-only probe settle (or fail when the context closes), observing its
    # result even when the surrounding watcher/deadline has already stopped.
    probe_task = asyncio.create_task(probe())
    probe_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    try:
        # Missing frames/endpoints must not block other rejection checks indefinitely.
        return await asyncio.wait_for(asyncio.shield(probe_task), timeout=min(remaining, 1))
    except (asyncio.TimeoutError, TimeoutError):
        return False
    except Error as error:
        message = str(error)
        if "Execution context was destroyed" in message or "Frame was detached" in message:
            return False
        reason = "ambiguous_target" if "strict mode violation" in message else "assertion_failed"
        raise BrowserActionError("authentication", reason) from None


async def _rejected(
    page: Page, conditions: Sequence[Assertion], deadline: float, base_url: str,
) -> bool:
    for condition in conditions:
        if await _matches(page, condition, deadline, base_url):
            return True
    return False


async def _pause(deadline: float) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise BrowserActionTimeout("authentication")
    # This polls current conditions; it is not an unconditional UI readiness delay.
    await asyncio.sleep(min(POLL_SECONDS, remaining))


async def watch_for_rejection(page: Page, profile: SiteProfile, deadline: float) -> None:
    while True:
        await check_rejection(page, profile, deadline)
        await _pause(deadline)


async def check_rejection(page: Page, profile: SiteProfile, deadline: float) -> None:
    if profile.verification:
        for conditions, reason in ((profile.verification.expired, "verification_expired"),
                                   (profile.verification.rejected, "verification_rejected")):
            if await _rejected(page, conditions, deadline, profile.entry_url):
                raise BrowserActionError("authentication", reason)
    if await _rejected(page, profile.failure, deadline, profile.entry_url):
        raise BrowserActionError("authentication", "credentials_rejected")


async def wait_for_authentication(page: Page, profile: SiteProfile, deadline: float) -> None:
    while True:
        await check_rejection(page, profile, deadline)
        confirmed = True
        for condition in profile.success:
            if not await _matches(page, condition, deadline, profile.entry_url):
                confirmed = False
                break
        if confirmed:
            # Failure wins even when a page also exposes an apparent success control.
            await check_rejection(page, profile, deadline)
            return
        await _pause(deadline)
