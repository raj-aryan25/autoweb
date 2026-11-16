"""Ordering, failure and cancellation guarantees of the optional session hook.

No browser is launched: the hook's contract is that it wraps navigation and the
caller's post-login work while the browser is still owned by the login flow.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from autoweb.config import load_settings, load_site_profile
from autoweb.config.loader import configuration_from_models
from autoweb.login import LoginError, LoginResult, authenticated_session
from autoweb.login import flow as login_flow
from autoweb.login.results import StepResult
from autoweb_api import live_capture

ROOT = Path(__file__).resolve().parents[2]
SENTINEL = object()


@pytest.fixture
def configuration():
    site = load_site_profile("configs/sites/local-password.example.json", project_root=ROOT)
    settings = load_settings(project_root=ROOT, environ={})
    return configuration_from_models(site, settings, {
        "LOCAL_LOGIN_USERNAME": "synthetic-user",
        "LOCAL_LOGIN_PASSWORD": "synthetic-private",
    }, project_root=ROOT)


def succeeded():
    return LoginResult("local-password", "succeeded", 0.25, (StepResult(0, "fill", None),))


class Harness:
    """Fake browser ownership plus configurable lifecycle behaviour and outcomes."""

    def __init__(self, monkeypatch):
        self.events = []
        self.sessions = []
        self.behaviour = "success"
        self.observe_error = None
        self.entered = asyncio.Event()
        self.never = asyncio.Event()

    def install(self, monkeypatch):
        harness = self
        real_scope = None

        @asynccontextmanager
        async def fake_open_session(settings):
            harness.events.append("open")
            try:
                yield SENTINEL
            finally:
                harness.events.append("close")

        @asynccontextmanager
        async def lifecycle(active):
            harness.sessions.append(active)
            if harness.behaviour == "enter_failure":
                harness.events.append("enter_failed")
                raise RuntimeError("bounded setup failure")
            harness.events.append("enter")
            try:
                yield
            except BaseException as error:
                harness.events.append(("exit", type(error).__name__))
                raise
            else:
                harness.events.append(("exit", None))
                if harness.behaviour == "exit_failure":
                    raise RuntimeError("bounded cleanup failure")

        async def fake_observe(session, configuration, provider, **kwargs):
            harness.events.append("execute")
            if harness.behaviour == "login_failure":
                raise LoginError(LoginResult(
                    "local-password", "failed", 0.25, failed_step="navigation",
                    reason="step_timeout",
                ))
            if harness.behaviour == "hang":
                harness.entered.set()
                await harness.never.wait()
            return succeeded()

        monkeypatch.setattr(login_flow, "open_session", fake_open_session)
        monkeypatch.setattr(login_flow, "_observe_login", fake_observe)
        return lifecycle


@pytest.mark.asyncio
async def test_hook_wraps_navigation_and_caller_work_inside_browser_ownership(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    lifecycle = harness.install(monkeypatch)
    async with authenticated_session(configuration, session_lifecycle=lifecycle) as result:
        assert result.session is None
        harness.events.append("caller")
        assert harness.events == ["open", "enter", "execute", "caller"]
    assert harness.events == ["open", "enter", "execute", "caller", ("exit", None), "close"]
    assert harness.sessions == [SENTINEL]


@pytest.mark.asyncio
async def test_missing_hook_preserves_previous_ordering(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.install(monkeypatch)
    async with authenticated_session(configuration) as result:
        harness.events.append("caller")
    assert harness.events == ["open", "execute", "caller", "close"]
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_failed_login_still_exits_hook_before_closing_browser(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.behaviour = "login_failure"
    lifecycle = harness.install(monkeypatch)
    with pytest.raises(LoginError):
        async with authenticated_session(configuration, session_lifecycle=lifecycle):
            pytest.fail("Failed login must not yield")
    assert harness.events == ["open", "enter", "execute", ("exit", "LoginError"), "close"]


@pytest.mark.asyncio
async def test_setup_failure_prevents_execution_and_still_closes_browser(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.behaviour = "enter_failure"
    lifecycle = harness.install(monkeypatch)
    with pytest.raises(RuntimeError, match="bounded setup failure"):
        async with authenticated_session(configuration, session_lifecycle=lifecycle):
            pytest.fail("A failed hook must not yield")
    assert harness.events == ["open", "enter_failed", "close"]


@pytest.mark.asyncio
async def test_cancellation_reaches_the_hook_and_is_never_swallowed(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.behaviour = "hang"
    lifecycle = harness.install(monkeypatch)

    async def caller():
        async with authenticated_session(configuration, session_lifecycle=lifecycle):
            await asyncio.Event()

    task = asyncio.create_task(caller())
    await asyncio.wait_for(harness.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert harness.events == ["open", "enter", "execute", ("exit", "CancelledError"), "close"]


@pytest.mark.asyncio
async def test_cleanup_error_still_closes_browser(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.behaviour = "exit_failure"
    lifecycle = harness.install(monkeypatch)
    with pytest.raises(RuntimeError, match="bounded cleanup failure"):
        async with authenticated_session(configuration, session_lifecycle=lifecycle):
            pass
    assert harness.events == ["open", "enter", "execute", ("exit", None), "close"]


@pytest.mark.asyncio
async def test_cleanup_error_does_not_leave_the_browser_open_on_login_failure(monkeypatch, configuration):
    harness = Harness(monkeypatch)
    harness.behaviour = "login_failure"
    harness.install(monkeypatch)

    @asynccontextmanager
    async def raising_lifecycle(active):
        try:
            yield
        finally:
            raise RuntimeError("bounded cleanup failure")

    with pytest.raises(RuntimeError, match="bounded cleanup failure"):
        async with authenticated_session(configuration, session_lifecycle=raising_lifecycle):
            pytest.fail("Failed login must not yield")
    assert "close" in harness.events and harness.events[0] == "open"


@pytest.mark.asyncio
async def test_capture_attaches_before_execution_and_stops_before_browser_close(
    monkeypatch, configuration,
):
    """Preview capture is live before navigation and gone before teardown."""
    harness = Harness(monkeypatch)
    harness.install(monkeypatch)
    page = live_capture_page()
    capture = live_capture.LiveCapture(load_site_profile(
        "configs/sites/local-password.example.json", project_root=ROOT))

    async def observe(session, configuration, provider, **kwargs):
        harness.events.append(("execute", capture.status.state, len(page.screenshot_kwargs)))
        return succeeded()

    monkeypatch.setattr(login_flow, "_observe_login", observe)
    # The fake browser session carries the duck-typed page the capture module uses.
    fake_session = SimpleSession(page)
    monkeypatch.setattr(login_flow, "open_session", lambda settings: _opened(harness, fake_session))

    async with authenticated_session(configuration, session_lifecycle=capture.session_scope):
        pass
    assert harness.events == [("open",), ("execute", "live", 1), "close"]
    assert capture.status.state == "ended"


def live_capture_page():
    class Page:
        viewport_size = {"width": 1280, "height": 720}
        screenshot_kwargs = []

        def is_closed(self):
            return False

        def locator(self, selector):
            from types import SimpleNamespace
            return SimpleNamespace(selector=selector, kind="locator")

        async def screenshot(self, **kwargs):
            self.screenshot_kwargs.append(kwargs)
            return b"\xff\xd8frame"

    return Page()


class SimpleSession:
    def __init__(self, page):
        self.page = page


@asynccontextmanager
async def _opened(harness, active):
    harness.events.append(("open",))
    try:
        yield active
    finally:
        harness.events.append("close")


@pytest.fixture(autouse=True)
def no_real_locator(monkeypatch):
    """Capture masking must not query a real DOM in these unit checks."""
    def resolve(page, target, *, include_hidden=False):
        from types import SimpleNamespace
        return SimpleNamespace(kind="target", value=target.value)

    monkeypatch.setattr(live_capture, "resolve_locator", resolve)
