"""Exercise the browser primitives in real Chromium against controlled pages."""

import asyncio

import pytest
from pydantic import SecretStr

from autoweb.browser import (
    BrowserActionError, BrowserActionTimeout, BrowserStartupError,
    accept_cookies, click, create_context, create_page, fill, fill_otp,
    navigate, open_session, resolve_locator, submit, wait_for_state, wait_for_url,
)
from autoweb.config.models import Locator

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("target", [
    Locator(by="label", value="Email"),
    Locator(by="placeholder", value="Email address"),
    Locator(by="role", value="textbox", name="Email"),
    Locator(by="css", value="#email"),
    Locator(by="test_id", value="email"),
])
async def test_fill_supports_configured_locator_strategies(page, target):
    result = await fill(page, target, "test@example.com")
    assert result.status == "succeeded"
    assert await page.locator("#email").input_value() == "test@example.com"


@pytest.mark.parametrize("target", [
    Locator(by="role", value="button", name="Run"),
    Locator(by="text", value="Run"),
    Locator(by="css", value="#run"),
    Locator(by="test_id", value="run"),
])
async def test_click_uses_exact_target_without_picking_another_button(page, target):
    result = await click(page, target)
    assert result.status == "succeeded"
    assert await page.get_by_test_id("clicked").is_visible()
    assert await page.locator("body").get_attribute("data-wrong-button") is None


async def test_form_scope_resolves_duplicate_labels_and_password(page):
    username = Locator(by="label", value="Username", scope="#login")
    password = Locator(by="label", value="Password", scope="#login")
    await fill(page, username, "test-user")
    result = await fill(page, password, SecretStr("private-password"))
    assert "private-password" not in repr(result)
    assert await page.locator("#username").input_value() == "test-user"
    assert await page.locator("#other-user").input_value() == ""
    assert await page.locator("#password").input_value() == "private-password"
    await submit(page, Locator(by="role", value="button", name="Sign in", scope="#login"))
    await wait_for_state(page, Locator(by="test_id", value="submitted"))


async def test_ambiguous_input_fails_without_exposing_secret(page):
    with pytest.raises(BrowserActionError) as failure:
        await fill(page, Locator(by="label", value="Username"), SecretStr("private-password"))
    assert failure.value.reason == "ambiguous_target"
    assert "private-password" not in str(failure.value)
    assert await page.locator("#username").input_value() == ""
    assert await page.locator("#other-user").input_value() == ""


async def test_open_shadow_dom_input_button_and_state(page):
    target = Locator(by="label", value="Shadow password", scope="#shadow-host")
    await fill(page, target, "shadow-secret")
    assert await resolve_locator(page, target).input_value() == "shadow-secret"
    await submit(page, Locator(by="role", value="button", name="Shadow submit", scope="#shadow-host"))
    await wait_for_state(page, Locator(by="test_id", value="shadow-submitted", scope="#shadow-host"))


async def test_nested_iframes_support_inputs_buttons_and_banners(page):
    frames = ["iframe#outer", "iframe#inner"]
    await accept_cookies(page, Locator(by="role", value="button", name="Inner accept", frame_path=frames))
    await wait_for_state(page, Locator(by="css", value="#inner-cookies", frame_path=frames), state="detached")
    password = Locator(by="label", value="Inner password", frame_path=frames, scope="#inner-form")
    await fill(page, password, "inner-secret")
    assert await resolve_locator(page, password).input_value() == "inner-secret"
    await submit(page, Locator(by="role", value="button", name="Inner submit", frame_path=frames))
    await wait_for_state(page, Locator(by="test_id", value="inner-submitted", frame_path=frames))


async def test_shadow_dom_inside_nested_iframes(page):
    frames = ["iframe#outer", "iframe#inner"]
    code = Locator(by="label", value="Shadow verification code", frame_path=frames, scope="#inner-shadow")
    await fill_otp(page, [code], SecretStr("001234"), expected_length=6)
    assert await resolve_locator(page, code).input_value() == "001234"
    await click(page, Locator(by="role", value="button", name="Shadow verify", frame_path=frames))
    await wait_for_state(page, Locator(by="test_id", value="inner-shadow-verified", frame_path=frames))


async def test_single_otp_preserves_leading_zeros(page):
    target = Locator(by="label", value="Verification code")
    result = await fill_otp(page, [target], "001234", expected_length=6)
    assert result.status == "succeeded"
    assert "001234" not in repr(result)
    assert await resolve_locator(page, target).input_value() == "001234"


async def test_split_otp_preserves_character_order(page):
    targets = [Locator(by="label", value=f"Code digit {index}") for index in range(1, 7)]
    await fill_otp(page, targets, "001234", expected_length=6)
    assert [await resolve_locator(page, target).input_value() for target in targets] == list("001234")


@pytest.mark.parametrize("value,count,expected_length", [
    ("", 1, 6), ("12 456", 1, 6), (123456, 1, 6), ("123456", 0, 6),
    ("12345", 1, 6), ("123456", 2, 6), ("123456", 1, True),
])
async def test_invalid_otp_is_rejected_before_filling(page, value, count, expected_length):
    targets = [Locator(by="css", value="#otp") for _ in range(count)]
    with pytest.raises(BrowserActionError) as failure:
        await fill_otp(page, targets, value, expected_length=expected_length)
    assert failure.value.reason == "invalid_input"
    assert await page.locator("#otp").input_value() == ""


async def test_duplicate_split_targets_fail_before_filling(page):
    targets = [Locator(by="css", value="#otp") for _ in range(6)]
    with pytest.raises(BrowserActionError, match="duplicate_targets"):
        await fill_otp(page, targets, "123456")
    assert await page.locator("#otp").input_value() == ""


async def test_split_otp_has_one_budget_for_all_fields(page):
    await page.evaluate('''() => {
        [100, 220, 340, 460].forEach((delay, index) => setTimeout(() => {
            const input = document.createElement('input'); input.id = 'delayed-' + index;
            document.body.appendChild(input);
        }, delay));
    }''')
    targets = [Locator(by="css", value=f"#delayed-{index}") for index in range(4)]
    with pytest.raises(BrowserActionTimeout):
        await fill_otp(page, targets, "1234", timeout_ms=250)


async def test_present_cookie_banner_is_clicked_and_removed(page):
    result = await accept_cookies(page, Locator(by="role", value="button", name="Accept all"))
    assert result.status == "succeeded"
    await wait_for_state(page, Locator(by="css", value="#cookies"), state="detached")


async def test_absent_optional_banner_is_skipped(page):
    result = await accept_cookies(page, Locator(by="text", value="Absent accept"), timeout_ms=100)
    assert result.status == "skipped"
    assert result.reason == "absent"


@pytest.mark.parametrize("name", ["Disabled accept", "Hidden accept"])
async def test_optional_banner_does_not_hide_noninteractive_control(page, name):
    with pytest.raises(BrowserActionTimeout):
        await accept_cookies(page, Locator(by="role", value="button", name=name), timeout_ms=150)


async def test_optional_banner_does_not_hide_ambiguity(page):
    with pytest.raises(BrowserActionError) as failure:
        await accept_cookies(page, Locator(by="text", value="Duplicate"), timeout_ms=200)
    assert failure.value.reason == "ambiguous_target"


async def test_optional_banner_does_not_hide_invalid_selector(page):
    with pytest.raises(BrowserActionError) as failure:
        await accept_cookies(page, Locator(by="css", value="[broken"), timeout_ms=100)
    assert failure.value.reason == "interaction_failed"


@pytest.mark.parametrize("action", ["click", "fill", "submit", "wait_for_state", "required_banner"])
async def test_required_actions_fail_explicitly_when_control_is_missing(page, action):
    target = Locator(by="css", value="#missing")
    with pytest.raises(BrowserActionTimeout):
        if action == "fill":
            await fill(page, target, "secret", timeout_ms=100)
        elif action == "required_banner":
            await accept_cookies(page, target, optional=False, timeout_ms=100)
        else:
            functions = {"click": click, "submit": submit, "wait_for_state": wait_for_state}
            await functions[action](page, target, timeout_ms=100)


async def test_state_wait_handles_delayed_ui_and_disappearing_elements(page):
    await click(page, Locator(by="css", value="#reveal"))
    await wait_for_state(page, Locator(by="test_id", value="delayed"), state="visible")
    await page.get_by_test_id("delayed").evaluate("element => element.hidden = true")
    await wait_for_state(page, Locator(by="test_id", value="delayed"), state="hidden")
    await page.get_by_test_id("delayed").evaluate("element => element.remove()")
    await wait_for_state(page, Locator(by="test_id", value="delayed"), state="detached")


async def test_navigation_and_url_wait(session, fixture_url):
    result = await navigate(session.page, f"{fixture_url}/browser.html")
    assert result.status == "succeeded"
    await click(session.page, Locator(by="role", value="link", name="Next page"))
    await wait_for_url(session.page, "**/destination.html")
    assert await session.page.get_by_role("heading", name="Destination").is_visible()


async def test_http_error_is_not_navigation_success(session, fixture_url):
    with pytest.raises(BrowserActionError) as failure:
        await navigate(session.page, f"{fixture_url}/does-not-exist.html")
    assert failure.value.reason == "http_error"


async def test_navigation_timeout_is_explicit(session, fixture_url):
    release = asyncio.Event()
    finished = asyncio.Event()

    async def hold_request(route):
        try:
            await release.wait()
            await route.abort()
        finally:
            finished.set()

    await session.page.route("**/never", hold_request)
    try:
        with pytest.raises(BrowserActionTimeout):
            await navigate(session.page, f"{fixture_url}/never", timeout_ms=100)
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=5)
        await session.page.unroute("**/never", hold_request)


async def test_closed_page_errors_are_safe(page):
    await page.close()
    with pytest.raises(BrowserActionError) as failure:
        await fill(page, Locator(by="css", value="#email"), "private-password")
    assert failure.value.reason == "session_closed"
    assert "private-password" not in str(failure.value)


async def test_session_closes_resources_after_normal_completion():
    async with open_session() as active:
        assert active.browser.is_connected()
        assert not active.page.is_closed()
    assert active.page.is_closed()
    assert not active.browser.is_connected()


async def test_session_closes_resources_and_preserves_body_failure():
    with pytest.raises(ValueError, match="original failure"):
        async with open_session() as active:
            raise ValueError("original failure")
    assert active.page.is_closed()
    assert not active.browser.is_connected()


async def test_contexts_are_isolated_and_close_all_pages(session):
    async with create_context(session.browser, session.timeouts) as other:
        first = await create_page(other)
        second = await create_page(other)
        await session.context.add_cookies([{"name": "session", "value": "test", "url": "https://example.com"}])
        assert await other.cookies() == []
    assert first.is_closed() and second.is_closed()
    assert session.browser.is_connected()
    assert not session.page.is_closed()


async def test_context_creation_on_closed_browser_is_explicit(session):
    await session.browser.close()
    with pytest.raises(BrowserStartupError):
        async with create_context(session.browser):
            pytest.fail("A closed browser cannot create a context")


async def test_cancellation_cleans_up_session():
    ready = asyncio.Event()
    holder = {}

    async def work():
        async with open_session() as active:
            holder["session"] = active
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await asyncio.wait_for(ready.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert holder["session"].page.is_closed()
    assert not holder["session"].browser.is_connected()


async def test_duplicate_iframe_owners_fail_explicitly(page):
    await page.locator("#outer").evaluate("element => element.after(element.cloneNode(true))")
    target = Locator(by="label", value="Inner password", frame_path=["iframe#outer", "iframe#inner"])
    with pytest.raises(BrowserActionError) as failure:
        await fill(page, target, "private-password", timeout_ms=500)
    assert failure.value.reason == "ambiguous_target"


async def test_optional_banner_can_arrive_within_its_wait_budget(page):
    await page.evaluate('''() => setTimeout(() => {
        const button = document.createElement('button');
        button.id = 'late-banner'; button.textContent = 'Late accept';
        button.onclick = () => button.remove(); document.body.appendChild(button);
    }, 100)''')
    result = await accept_cookies(page, Locator(by="role", value="button", name="Late accept"), timeout_ms=1000)
    assert result.status == "succeeded"
    assert await page.locator("#late-banner").count() == 0


async def test_context_cleanup_tolerates_a_context_closed_by_caller(session):
    async with create_context(session.browser) as other:
        page = await create_page(other)
        await other.close()
    assert page.is_closed()
    assert session.browser.is_connected()
