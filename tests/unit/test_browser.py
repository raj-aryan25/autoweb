"""Lifecycle failure paths and invalid inputs that real pages cannot induce reliably."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error, TimeoutError

from autoweb.browser import (
    BrowserActionError, BrowserCleanupError, BrowserStartupError,
    create_context, fill, launch_browser, open_session,
)
from autoweb.config.models import BrowserSettings, Locator, RuntimeSettings, TimeoutSettings

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_driver(monkeypatch):
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=object()), close=AsyncMock(),
        set_default_timeout=Mock(), set_default_navigation_timeout=Mock(),
    )
    browser = SimpleNamespace(
        close=AsyncMock(), new_context=AsyncMock(return_value=context), is_connected=Mock(return_value=True),
    )
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock(),
    )
    manager = SimpleNamespace(start=AsyncMock(return_value=playwright))
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: manager)
    return playwright, browser, context


async def test_launch_failure_stops_driver_and_omits_original_details(fake_driver):
    playwright, _, _ = fake_driver
    playwright.chromium.launch.side_effect = Error("private underlying details")
    with pytest.raises(BrowserStartupError) as failure:
        async with launch_browser():
            pytest.fail("Launch failed before yielding")
    playwright.stop.assert_awaited_once()
    assert "private underlying details" not in str(failure.value)


async def test_cleanup_failure_still_stops_driver(fake_driver):
    playwright, browser, _ = fake_driver
    browser.close.side_effect = Error("private cleanup details")
    with pytest.raises(BrowserCleanupError):
        async with launch_browser():
            pass
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


async def test_cleanup_failure_does_not_replace_original_failure(fake_driver):
    playwright, browser, _ = fake_driver
    browser.close.side_effect = Error("cleanup error")
    with pytest.raises(ValueError, match="original failure"):
        async with launch_browser():
            raise ValueError("original failure")
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


async def test_context_setup_failure_closes_acquired_context(fake_driver):
    _, browser, context = fake_driver
    context.set_default_timeout.side_effect = Error("setup error")
    with pytest.raises(BrowserStartupError):
        async with create_context(browser):
            pytest.fail("Settings failed before yielding")
    context.close.assert_awaited_once()


async def test_page_creation_failure_closes_context_browser_and_driver(fake_driver):
    playwright, browser, context = fake_driver
    context.new_page.side_effect = Error("new page error")
    with pytest.raises(BrowserStartupError):
        async with open_session():
            pytest.fail("No page was created")
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), "100"])
async def test_actions_reject_unbounded_or_invalid_timeouts(timeout):
    with pytest.raises(BrowserActionError, match="invalid_timeout"):
        await fill(None, Locator(by="css", value="#input"), "value", timeout_ms=timeout)


async def test_browser_launch_budget_is_independent_of_navigation(fake_driver):
    playwright, _, context = fake_driver
    settings = RuntimeSettings(
        browser=BrowserSettings(launch_timeout_ms=20000),
        timeouts=TimeoutSettings(navigation_ms=1000),
    )
    async with open_session(settings):
        pass
    assert playwright.chromium.launch.call_args.kwargs["timeout"] == 20000
    context.set_default_navigation_timeout.assert_called_once_with(1000)


async def test_browser_startup_timeout_is_reported_safely(fake_driver):
    playwright, _, _ = fake_driver
    playwright.chromium.launch.side_effect = TimeoutError("private launch log")
    with pytest.raises(BrowserStartupError) as failure:
        async with launch_browser():
            pytest.fail("Launch timed out before yielding")
    assert failure.value.reason == "timeout"
    assert "private launch log" not in str(failure.value)
    playwright.stop.assert_awaited_once()


async def test_cancellation_during_cleanup_still_closes_browser_and_driver(fake_driver):
    playwright, browser, _ = fake_driver
    closing = asyncio.Event()
    release = asyncio.Event()

    async def close_browser():
        closing.set()
        await release.wait()

    browser.close.side_effect = close_browser

    async def work():
        async with launch_browser():
            pass

    task = asyncio.create_task(work())
    try:
        await asyncio.wait_for(closing.wait(), timeout=5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
