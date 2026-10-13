"""Translate configured locators without choosing arbitrary matching elements."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoweb.config.models import Locator as LocatorSpec

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


def resolve_locator(
    page: Page, target: LocatorSpec, *, include_hidden: bool = False,
) -> Locator:
    """Build a lazy strict locator through nested frames and optional form scope.

    Playwright locators traverse open Shadow DOM. Frame paths use iframe CSS
    selectors from outermost to innermost; targets and frame owners remain strict.
    """
    root = page
    for selector in target.frame_path:
        root = root.frame_locator(selector)
    if target.scope is not None:
        root = root.locator(target.scope)
    if target.by == "role":
        return root.get_by_role(
            target.value, name=target.name, exact=target.exact, include_hidden=include_hidden,
        )
    if target.by == "label":
        return root.get_by_label(target.value, exact=target.exact)
    if target.by == "placeholder":
        return root.get_by_placeholder(target.value, exact=target.exact)
    if target.by == "text":
        return root.get_by_text(target.value, exact=target.exact)
    if target.by == "test_id":
        return root.get_by_test_id(target.value)
    return root.locator(target.value)
