"""Real headless capture: masked pixels, same-page observation and cleanup.

Frames stay in memory. Pixel checks decode the captured JPEG in a separate owned
Playwright page (canvas), so no undeclared image dependency or saved frame is used.
"""

import asyncio
import base64

import pytest

from autoweb.config.models import SiteProfile
from autoweb_api import live_capture
from tests.fixtures.api_support import configure, finished, local_client

pytestmark = pytest.mark.asyncio

MAGENTA = (255, 0, 255)


def capture_profile():
    """Synthetic profile whose fill targets extend masking beyond form controls."""
    return SiteProfile.model_validate({
        "name": "capture-fixture",
        "entry_url": "http://127.0.0.1:8000/masking.html",
        "login_mode": "password",
        "credentials": {"username": {"env": "CAPTURE_USERNAME"},
                        "password": {"env": "CAPTURE_PASSWORD"}},
        "steps": [
            {"action": "fill", "target": {"by": "css", "value": "#username"},
             "credential": "username"},
            {"action": "fill", "target": {"by": "css", "value": "#password"},
             "credential": "password"},
            {"action": "fill", "target": {"by": "css", "value": "[data-testid='profile-email']"},
             "credential": "username"},
            {"action": "submit", "target": {"by": "css", "value": "#done"}},
        ],
        "success": [{"type": "element", "target": {"by": "css", "value": "#done"}}],
    })


async def sample(pixels_page, frame, points):
    """Decode the captured JPEG with the browser canvas and read RGB at points."""
    return await pixels_page.evaluate(
        """async ({ data, points }) => {
            const image = new Image();
            image.src = 'data:image/jpeg;base64,' + data;
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = image.naturalWidth;
            canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            context.drawImage(image, 0, 0);
            return points.map(([x, y]) => Array.from(context.getImageData(x, y, 1, 1).data));
        }""",
        {"data": base64.b64encode(frame.data).decode(), "points": points},
    )


async def center(page, selector):
    box = await page.locator(selector).bounding_box()
    assert box, f"Missing masking fixture element: {selector}"
    return [int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)]


def assert_masked(rgb):
    r, g, b = rgb[:3]
    assert r > 200 and g < 110 and b > 200, f"Region was not masked: {rgb}"


def assert_unmasked_white(rgb):
    r, g, b = rgb[:3]
    assert min(r, g, b) > 200, f"Unmasked region changed: {rgb}"


async def test_known_regions_are_masked_in_the_captured_frame(session, fixture_url):
    page = session.page
    await page.goto(f"{fixture_url}/masking.html", wait_until="load")
    provider = live_capture.LiveCapture(capture_profile())
    pixels = await session.context.new_page()
    try:
        async with provider.session_scope(session):
            frame = provider.latest
            assert provider.status.state == "live"
            assert frame is not None and frame.data.startswith(b"\xff\xd8")
            assert (frame.width, frame.height) == (1280, 720) and frame.sequence == 1
            points = [
                await center(page, "#username"),
                await center(page, "#password"),
                await center(page, "#profile-email"),
                await center(page, "#shadow-secret"),
                await center(page, "#frame"),
                await center(page, "#caption"),
            ]
            colours = await sample(pixels, frame, points)
            for rgb in colours[:5]:
                assert_masked(rgb)
            assert_unmasked_white(colours[5])
    finally:
        await pixels.close()
    assert provider.status.state == "ended" and provider.latest is None
    assert not [task for task in asyncio.all_tasks()
                if task.get_name() == "autoweb-live-capture"]


async def test_frames_follow_navigation_and_end_when_the_page_closes(session, fixture_url, monkeypatch):
    monkeypatch.setattr(live_capture, "CAPTURE_INTERVAL_SECONDS", 0.05)
    page = session.page
    await page.goto(f"{fixture_url}/browser.html", wait_until="load")
    provider = live_capture.LiveCapture(capture_profile())
    async with provider.session_scope(session):
        first = provider.latest
        assert first is not None and provider.status.state == "live"
        await page.goto(f"{fixture_url}/destination.html", wait_until="load")

        async def advanced():
            while provider.latest is None or provider.latest.sequence <= first.sequence:
                await asyncio.sleep(0.02)
            return provider.latest

        second = await asyncio.wait_for(advanced(), 5)
        assert second.data != first.data and second.captured_at >= first.captured_at
        await page.close()

        async def closed():
            while provider.status.diagnostic != "page_closed":
                await asyncio.sleep(0.02)

        await asyncio.wait_for(closed(), 5)
        assert provider.latest is None
    assert provider.status.state == "ended"
    assert not [task for task in asyncio.all_tasks()
                if task.get_name() == "autoweb-live-capture"]


async def test_preflight_and_executed_manifest_agree_on_view_state(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        preflight = await client.post("/api/preflight",
                                      json={"profile_id": "controlled-password"})
        assert preflight.status_code == 200
        manifest = preflight.json()["manifest"]
        assert manifest["effective"]["browser"]["headless"] is True
        assert manifest["effective"]["live_view_enabled"] is True
        assert manifest["live_view"] == {"state": "unavailable",
                                         "diagnostic": "transport_unavailable"}
        response = await client.post("/api/runs", json={
            "request_id": "view-manifest", "profile_id": "controlled-password"})
        assert response.status_code == 202
        run_id = response.json()["snapshot"]["run_id"]
        record = await finished(client, run_id)
        assert record["snapshot"]["state"] == "succeeded" and record["cleanup_verified"]
        started = record["manifest"]
        assert started["effective"]["browser"]["headless"] is True
        assert started["effective"]["live_view_enabled"] is True
        assert started["live_view"] == manifest["live_view"]
        assert service.store.page().total == 1
