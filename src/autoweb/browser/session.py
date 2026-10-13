"""Explicit async browser/context ownership, including cleanup after failures."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Iterable

from autoweb.config.models import BrowserSettings, RuntimeSettings, TimeoutSettings

from .errors import BrowserCleanupError, BrowserStartupError

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page


@dataclass(frozen=True)
class BrowserSession:
    browser: Browser = field(repr=False)
    context: BrowserContext = field(repr=False)
    page: Page = field(repr=False)
    timeouts: TimeoutSettings


async def _cleanup(
    callbacks: Iterable[Callable[[], Awaitable[None]]], *, preserve_failure: bool,
) -> None:
    async def close_resources() -> bool:
        failed = False
        for callback in callbacks:
            try:
                await callback()
            except Exception:
                # Attempt every resource's cleanup; never replace an original failure.
                failed = True
        return failed

    cleanup = asyncio.create_task(close_resources())
    cancellation = None
    while True:
        try:
            failed = await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError as error:
            if cleanup.cancelled():
                raise
            # Cancellation must not leave browser/driver cleanup running detached.
            cancellation = error
    if cancellation is not None:
        raise cancellation
    if failed and not preserve_failure:
        raise BrowserCleanupError() from None


@asynccontextmanager
async def launch_browser(
    settings: BrowserSettings | None = None,
) -> AsyncIterator[Browser]:
    """Own a Playwright driver and browser for the duration of the async context."""
    from playwright.async_api import Error, TimeoutError, async_playwright

    settings = settings or BrowserSettings()
    try:
        playwright = await async_playwright().start()
    except Error:
        raise BrowserStartupError("playwright driver") from None
    browser = None
    try:
        try:
            browser = await getattr(playwright, settings.engine).launch(
                headless=settings.headless, slow_mo=settings.slow_mo_ms,
                timeout=settings.launch_timeout_ms,
            )
        except TimeoutError:
            raise BrowserStartupError(settings.engine, "timeout") from None
        except Error as error:
            reason = (
                "missing_executable" if "Executable doesn't exist" in str(error)
                else "startup_failed"
            )
            raise BrowserStartupError(settings.engine, reason) from None
        yield browser
    finally:
        callbacks = [playwright.stop]
        if browser is not None:
            callbacks.insert(0, browser.close)
        await _cleanup(callbacks, preserve_failure=sys.exc_info()[0] is not None)


@asynccontextmanager
async def create_context(
    browser: Browser, timeouts: TimeoutSettings | None = None,
) -> AsyncIterator[BrowserContext]:
    """Own an isolated context; all pages in it close together at exit."""
    from playwright.async_api import Error

    timeouts = timeouts or TimeoutSettings()
    try:
        context = await browser.new_context()
    except Error:
        raise BrowserStartupError("browser context") from None
    async def close_context() -> None:
        # Closing the browser already closes its contexts and pages.
        if browser.is_connected():
            await context.close()

    try:
        try:
            context.set_default_timeout(timeouts.action_ms)
            context.set_default_navigation_timeout(timeouts.navigation_ms)
        except Error:
            raise BrowserStartupError("context settings") from None
        yield context
    finally:
        await _cleanup([close_context], preserve_failure=sys.exc_info()[0] is not None)


async def create_page(context: BrowserContext) -> Page:
    """Create a page whose lifetime belongs to its context."""
    from playwright.async_api import Error

    try:
        return await context.new_page()
    except Error:
        raise BrowserStartupError("page") from None


@asynccontextmanager
async def open_session(settings: RuntimeSettings | None = None) -> AsyncIterator[BrowserSession]:
    """Keep the session open for caller-owned work and close it at context exit."""
    settings = settings or RuntimeSettings()
    async with launch_browser(settings.browser) as browser:
        async with create_context(browser, settings.timeouts) as context:
            page = await create_page(context)
            yield BrowserSession(browser, context, page, settings.timeouts)
