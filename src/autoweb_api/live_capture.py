"""Optional masked, memory-only capture owned by a login session lifecycle.

No HTTP, IPC, filesystem output or client initialization lives here. A future
transport can consume ``latest``; slow consumers never queue browser work.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from autoweb.browser import BrowserSession, resolve_locator
from autoweb.config.models import FillOTPStep, FillStep, SiteProfile

from .contracts import LiveViewStatus

CAPTURE_INTERVAL_SECONDS = 1 / 3
CAPTURE_TIMEOUT_SECONDS = 0.25
MAX_FRAME_BYTES = 1024 * 1024
MAX_VIEWPORT_WIDTH = 1920
MAX_VIEWPORT_HEIGHT = 1080


@dataclass(frozen=True)
class PreviewFrame:
    sequence: int
    captured_at: datetime
    width: int
    height: int
    data: bytes = field(repr=False)


class LiveCapture:
    """One capture owner and at most one latest frame for one session.

    Use ``session_lifecycle=capture.session_scope``. Merely constructing this
    object starts nothing. Do not share it across runs. Known input/credential
    regions and whole embedded frames are masked; arbitrary page text is still
    private website content, not a sanitized evidence artifact.
    """

    def __init__(self, profile: SiteProfile, *, on_frame: Callable[[PreviewFrame], None] | None = None,
                 on_status: Callable[[LiveViewStatus], None] | None = None):
        self._profile = profile
        # Trusted handoff only: replace/enqueue in a bounded consumer buffer,
        # never perform I/O or blocking work. No transport is constructed here.
        self._on_frame = on_frame
        self._on_status = on_status
        self._active = False
        self._sequence = 0
        self.latest: PreviewFrame | None = None
        self.status = LiveViewStatus()

    def _set_status(self, value: LiveViewStatus) -> None:
        self.status = value
        if self._on_status is not None:
            try:
                self._on_status(value)
            except Exception:
                self.latest = None
                self.status = LiveViewStatus(state="unavailable", diagnostic="capture_failed")

    async def _capture(self, session: BrowserSession) -> None:
        page = session.page
        try:
            if page.is_closed():
                self.latest = None
                self._set_status(LiveViewStatus(state="unavailable", diagnostic="page_closed"))
                return
            viewport = page.viewport_size
            if (not viewport or not 0 < viewport["width"] <= MAX_VIEWPORT_WIDTH
                    or not 0 < viewport["height"] <= MAX_VIEWPORT_HEIGHT):
                raise ValueError("unsupported_viewport")
            masks = [page.locator('input, textarea, select, [contenteditable], iframe, frame')]
            for step in self._profile.steps:
                if isinstance(step, FillStep):
                    masks.append(resolve_locator(page, step.target, include_hidden=True))
                elif isinstance(step, FillOTPStep):
                    masks.extend(resolve_locator(page, target, include_hidden=True) for target in step.targets)
            # An outer deadline also bounds broken/unresponsive screenshot calls.
            # No path means Playwright returns bytes without writing an image.
            data = await asyncio.wait_for(page.screenshot(
                type="jpeg", quality=60, full_page=False, mask=masks,
                caret="initial", timeout=CAPTURE_TIMEOUT_SECONDS * 1000,
            ), timeout=CAPTURE_TIMEOUT_SECONDS)
            if not data.startswith(b"\xff\xd8") or len(data) > MAX_FRAME_BYTES:
                raise ValueError("invalid_frame")
            self._sequence += 1
            self.latest = PreviewFrame(self._sequence, datetime.now(timezone.utc),
                                       viewport["width"], viewport["height"], data)
            if self._on_frame is not None:
                self._on_frame(self.latest)
            self._set_status(LiveViewStatus(state="live"))
        except Exception:
            # Never fall back to an unmasked frame or expose raw provider errors.
            self.latest = None
            self._set_status(LiveViewStatus(state="unavailable", diagnostic="capture_failed"))

    async def _pump(self, session: BrowserSession) -> None:
        while not session.page.is_closed():
            await asyncio.sleep(CAPTURE_INTERVAL_SECONDS)
            await self._capture(session)
        self.latest = None
        self._set_status(LiveViewStatus(state="unavailable", diagnostic="page_closed"))

    @asynccontextmanager
    async def session_scope(self, session: BrowserSession) -> AsyncIterator[None]:
        """Capture the workflow page; popups never replace it by recency/focus.

        Current orchestration always acts on ``session.page``. Observe that same
        page across navigations; unrelated popup pages are intentionally ignored.
        There are no event listeners to leak or focus changes to affect login.
        """
        if self._active:
            raise RuntimeError("Capture already owns a session")
        self._active = True
        self.latest = None
        self._sequence = 0
        self._set_status(LiveViewStatus(state="starting"))
        task = None
        cancelled = False
        try:
            await self._capture(session)
            task = asyncio.create_task(self._pump(session), name="autoweb-live-capture")
            yield
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            try:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                # Do not spend the final-frame budget when Stop cancels a run.
                if not cancelled and not session.page.is_closed():
                    await self._capture(session)
            finally:
                self.latest = None
                self._set_status(LiveViewStatus(state="ended"))
                self._active = False
