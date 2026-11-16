"""Preview contracts and masked, memory-only capture ownership without a browser.

The capture module is exercised with duck-typed pages: its responsibility is the
mask list it builds, bounded frame handling, one-latest-frame storage and scoped
task cleanup, not Playwright behaviour (covered by integration checks).
"""

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoweb.config import load_settings, load_site_profile
from autoweb.config.models import BrowserSettings, TimeoutSettings
from autoweb_api import live_capture
from autoweb_api.contracts import LiveViewStatus, RunOverrides, StartRun
from autoweb_api.protocol import EffectiveSettings, Manifest, pending_live_view

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_TASK = "autoweb-live-capture"
FORM_SELECTOR = "input, textarea, select, [contenteditable], iframe, frame"
JPEG = b"\xff\xd8synthetic-jpeg-body"


def profile(filename="local-password.example.json"):
    return load_site_profile(f"configs/sites/{filename}", project_root=ROOT)


class FakePage:
    """Only the surface LiveCapture is allowed to depend on."""

    def __init__(self, *, viewport={"width": 1280, "height": 720}):
        self.viewport_size = viewport
        self.screenshot_kwargs = []
        self.locator_selectors = []
        self.listeners = []
        self.behaviours = []
        self._closed = False

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True

    def locator(self, selector):
        self.locator_selectors.append(selector)
        return SimpleNamespace(kind="locator", selector=selector)

    def on(self, *args):  # pragma: no cover - asserted against instead
        self.listeners.append(args)
        raise AssertionError("Capture must not register page listeners")

    async def screenshot(self, **kwargs):
        self.screenshot_kwargs.append(kwargs)
        behaviour = self.behaviours.pop(0) if self.behaviours else JPEG
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour


def session(page=None):
    page = page or FakePage()
    return SimpleNamespace(page=page)


@pytest.fixture(autouse=True)
def resolved_locators(monkeypatch):
    """Record mask targets instead of querying a real DOM."""
    calls = []
    counter = {"n": 0}

    def resolve(page, target, *, include_hidden=False):
        counter["n"] += 1
        locator = SimpleNamespace(kind="target", value=target.value, n=counter["n"])
        calls.append((target, include_hidden, locator))
        return locator

    monkeypatch.setattr(live_capture, "resolve_locator", resolve)
    return calls


def capture_tasks():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return []
    return [task for task in asyncio.all_tasks() if task.get_name() == CAPTURE_TASK]


# --- view contracts ---------------------------------------------------------


def test_studio_defaults_request_headless_live_view_and_stay_strict():
    defaults = RunOverrides()
    assert defaults.headless is True and defaults.live_view_enabled is True
    request = StartRun(request_id="request-1", profile_id="controlled-password")
    assert request.overrides.headless is True and request.overrides.live_view_enabled is True


def test_core_browser_default_is_unchanged_by_the_studio_override():
    """Only the Studio override changes; the package settings stay as configured."""
    assert BrowserSettings().headless is True
    assert load_settings(project_root=ROOT, environ={}).browser.headless is True


def test_importing_capture_initializes_no_clients_or_configuration():
    script = """
import asyncio
import sys
def guard(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('import started resources: ' + event)
    if event == 'open' and isinstance(args[0], str):
        name = args[0].lower()
        if name.endswith(('.env', 'settings.json', 'token.json', 'credentials.json')):
            raise AssertionError('import read runtime configuration')
sys.addaudithook(guard)
import autoweb_api.live_capture as module
assert module.LiveCapture
assert 'playwright' not in sys.modules and 'fastapi' not in sys.modules
try:
    asyncio.get_running_loop()
except RuntimeError:
    pass
else:
    raise AssertionError('import created an event loop')
"""
    subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)


@pytest.mark.parametrize("field", ["headless", "live_view_enabled"])
@pytest.mark.parametrize("value", ["true", 1, None])
def test_view_flags_reject_non_booleans(field, value):
    with pytest.raises(ValidationError):
        RunOverrides.model_validate({field: value})


@pytest.mark.asyncio
async def test_view_status_is_closed():
    assert LiveViewStatus().state == "disabled" and LiveViewStatus().diagnostic is None
    assert LiveViewStatus(state="live").state == "live"
    assert LiveViewStatus(state="unavailable", diagnostic="page_closed").diagnostic == "page_closed"
    for bad in ({"state": "recording"}, {"diagnostic": "raw_error"},
                {"state": "live", "url": "https://example.test"}):
        with pytest.raises(ValidationError):
            LiveViewStatus.model_validate(bad)


def legacy_manifest():
    return {
        "type": "manifest",
        "profile_id": "controlled-password",
        "steps": [],
        "effective": {
            "browser": BrowserSettings().model_dump(),
            "timeouts": TimeoutSettings().model_dump(),
            "ai_enabled": False,
        },
    }


def test_saved_manifest_without_view_fields_keeps_legacy_meaning():
    manifest = Manifest.model_validate(legacy_manifest())
    assert manifest.live_view.state == "disabled" and manifest.live_view.diagnostic is None
    assert manifest.effective.live_view_enabled is False


def test_requested_view_is_never_reported_as_live_before_transport_exists():
    assert pending_live_view(True) == LiveViewStatus(state="unavailable", diagnostic="transport_unavailable")
    assert pending_live_view(False) == LiveViewStatus()
    manifest = Manifest(
        type="manifest", profile_id="controlled-password", steps=[],
        effective=EffectiveSettings(browser=BrowserSettings(), timeouts=TimeoutSettings(),
                                     ai_enabled=False, live_view_enabled=True),
        live_view=pending_live_view(True),
    )
    assert Manifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert manifest.live_view.state != "live"


# --- capture module ---------------------------------------------------------


def test_construction_is_inert():
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    assert capture.latest is None and capture.status.state == "disabled"
    assert capture.status.diagnostic is None
    assert page.screenshot_kwargs == [] and capture_tasks() == []


@pytest.mark.asyncio
async def test_scope_publishes_one_masked_bounded_jpeg_frame(resolved_locators):
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        assert capture.status.state == "live"
        frame = capture.latest
        assert frame is not None and frame.sequence == 1
        assert frame.data == JPEG and (frame.width, frame.height) == (1280, 720)
        assert frame.captured_at.tzinfo is not None
        options = page.screenshot_kwargs[0]
        assert options["type"] == "jpeg" and options["quality"] == 60
        assert options["full_page"] is False and options["caret"] == "initial"
        assert options["timeout"] == live_capture.CAPTURE_TIMEOUT_SECONDS * 1000
        assert options["timeout"] <= 250 and "path" not in options
        # Every form control is masked first, then every declared fill target.
        assert page.locator_selectors == [FORM_SELECTOR]
        assert options["mask"][0].selector == FORM_SELECTOR
        assert [locator.value for locator in options["mask"][1:]] == ["Username", "Password"]
        assert all(include_hidden for _, include_hidden, _ in resolved_locators)
    assert page.listeners == []


@pytest.mark.asyncio
async def test_otp_targets_and_leaf_inputs_are_masked(resolved_locators):
    page = FakePage()
    capture = live_capture.LiveCapture(profile("email-otp.example.json"))
    async with capture.session_scope(session(page)):
        masks = page.screenshot_kwargs[0]["mask"]
        assert masks[0].selector == FORM_SELECTOR
        assert [locator.value for locator in masks[1:]] == ["Email", "Verification code"]
        assert len(resolved_locators) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", [RuntimeError("private page text"), b"not-a-jpeg",
                                       b"\xff\xd8" + b"x" * live_capture.MAX_FRAME_BYTES])
async def test_invalid_or_failed_capture_is_suppressed_without_fallback(behaviour):
    page = FakePage()
    page.behaviours = [behaviour]
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        assert capture.latest is None
        assert capture.status == LiveViewStatus(state="unavailable", diagnostic="capture_failed")
        assert len(page.screenshot_kwargs) == 1  # No unmasked retry.
    assert "private page text" not in repr(capture.status)


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [None, {"width": 0, "height": 720},
                                      {"width": 1921, "height": 720}, {"width": 1280, "height": 1081}])
async def test_unsupported_viewports_are_never_captured(viewport):
    page = FakePage(viewport=viewport)
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        assert capture.latest is None
        assert capture.status.diagnostic == "capture_failed"
        assert page.screenshot_kwargs == []


@pytest.mark.asyncio
async def test_page_closed_before_start_reports_closed_and_skips_capture():
    page = FakePage()
    page.close()
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        assert capture.latest is None
        assert capture.status == LiveViewStatus(state="unavailable", diagnostic="page_closed")
        assert page.screenshot_kwargs == []
    assert capture_tasks() == []


@pytest.mark.asyncio
async def test_frames_replace_each_other_and_sequence_never_decreases(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 0.01)
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        first = capture.latest.sequence
        await asyncio.sleep(0.06)
        assert capture.latest.sequence > first
        assert len(page.screenshot_kwargs) == 1 + (capture.latest.sequence - 1)
        assert capture.status.state == "live"


@pytest.mark.asyncio
async def test_exit_cancels_the_capture_task_and_allows_a_new_scope(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        assert len(capture_tasks()) == 1
    assert capture_tasks() == []
    assert capture.status.state == "ended"
    assert len(page.screenshot_kwargs) == 2  # Initial plus one bounded terminal capture.
    page.behaviours = []
    async with capture.session_scope(session(page)):
        assert capture.latest.sequence == 1


@pytest.mark.asyncio
async def test_owner_cancellation_stops_without_spending_terminal_capture(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    entered = asyncio.Event()

    async def owner():
        async with capture.session_scope(session(page)):
            entered.set()
            await asyncio.Event()

    task = asyncio.create_task(owner())
    await entered.wait()
    assert len(page.screenshot_kwargs) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capture.latest is None and capture.status.state == "ended"
    assert len(page.screenshot_kwargs) == 1
    assert capture_tasks() == []


@pytest.mark.asyncio
async def test_a_capture_owner_cannot_be_shared_between_sessions(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(FakePage())):
        with pytest.raises(RuntimeError):
            async with capture.session_scope(session(FakePage())):
                pytest.fail("Second scope must not attach")
        assert capture.status.state == "live"
    assert capture_tasks() == []


@pytest.mark.asyncio
async def test_closed_page_ends_the_pump_without_leaking_tasks(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 0.01)
    page = FakePage()
    capture = live_capture.LiveCapture(profile())
    async with capture.session_scope(session(page)):
        page.close()
        for _ in range(50):
            if capture.latest is None and capture.status.diagnostic == "page_closed":
                break
            await asyncio.sleep(0.01)
        assert capture.latest is None
        assert capture.status == LiveViewStatus(state="unavailable", diagnostic="page_closed")
    assert capture_tasks() == []


# --- trusted frame handoff --------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_frame_is_handed_off_before_local_memory_clears(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    delivered = []
    capture = None

    def keep_last(frame):
        # Bounded consumer: replace, never append or perform I/O.
        assert capture.latest is frame
        assert isinstance(frame, live_capture.PreviewFrame)
        delivered[:] = [frame]

    capture = live_capture.LiveCapture(profile(), on_frame=keep_last)
    page = FakePage()
    async with capture.session_scope(session(page)):
        assert [frame.sequence for frame in delivered] == [1]
    assert [frame.sequence for frame in delivered] == [2]
    assert delivered[0].data.startswith(b"\xff\xd8")
    assert (delivered[0].width, delivered[0].height) == (1280, 720)
    assert capture.latest is None and capture.status.state == "ended"
    assert len(page.screenshot_kwargs) == 2


@pytest.mark.asyncio
async def test_consumer_failure_cannot_change_body_outcome_or_send_raw_pixels(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    calls = []

    def broken(frame):
        calls.append(frame.sequence)
        raise RuntimeError("consumer bug with private detail")

    capture = live_capture.LiveCapture(profile(), on_frame=broken)
    page = FakePage()
    body_ran = False
    async with capture.session_scope(session(page)):
        body_ran = True
        assert capture.latest is None
        assert capture.status == LiveViewStatus(state="unavailable", diagnostic="capture_failed")
        assert len(page.screenshot_kwargs) == 1  # No unmasked fallback after the failure.
    assert body_ran and capture.latest is None and capture.status.state == "ended"
    assert calls == [1, 2]  # The terminal attempt still goes through the same bounded path.
    assert "consumer bug" not in repr(capture.status)


@pytest.mark.asyncio
async def test_invalid_frames_never_reach_the_consumer():
    delivered = []
    page = FakePage()
    page.behaviours = [b"not-a-jpeg", b"not-a-jpeg"]
    capture = live_capture.LiveCapture(profile(), on_frame=delivered.append)
    async with capture.session_scope(session(page)):
        assert delivered == [] and capture.status.diagnostic == "capture_failed"
    assert delivered == []


@pytest.mark.asyncio
async def test_cancelled_run_has_no_terminal_handoff(monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 3600)
    delivered = []
    capture = live_capture.LiveCapture(profile(), on_frame=delivered.append)
    page = FakePage()
    entered = asyncio.Event()

    async def owner():
        async with capture.session_scope(session(page)):
            entered.set()
            await asyncio.Event()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(entered.wait(), 1)
    assert [frame.sequence for frame in delivered] == [1]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert [frame.sequence for frame in delivered] == [1]
    assert capture.latest is None and capture.status.state == "ended"
    assert len(page.screenshot_kwargs) == 1
