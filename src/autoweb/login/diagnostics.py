"""Best-effort local failure screenshots without replacing the login error."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from autoweb.browser import resolve_locator
from autoweb.config.models import FillStep, SiteProfile

if TYPE_CHECKING:
    from playwright.async_api import Page


async def capture_failure(
    page: Page, profile: SiteProfile, artifacts_dir: Path, *, timeout_ms: float,
) -> tuple[Path, ...]:
    from playwright.async_api import Error

    if page.is_closed():
        return ()
    destination = artifacts_dir / "login-failures" / f"{profile.name}-{uuid4().hex}.png"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        masks = [resolve_locator(page, step.target, include_hidden=True)
                 for step in profile.steps if isinstance(step, FillStep)]
        masks.append(page.locator('input[type="password"]'))
        await page.screenshot(path=str(destination), mask=masks, timeout=timeout_ms)
    except (Error, OSError):
        return ()
    return (destination,)
