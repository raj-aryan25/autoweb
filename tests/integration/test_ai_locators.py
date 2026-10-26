"""Real DOM validation with injected AI; no Gemini access or model output execution."""

import json

import pytest
from pydantic import SecretStr

from autoweb.ai.client import AIError
from autoweb.ai.locators import LocatorResolver
from autoweb.browser import BrowserActionError, open_session
from autoweb.config.models import AISettings, Locator
from autoweb.login import LoginError, authenticated_session
from autoweb.testing import FakeVerificationProvider
from tests.fixtures.email_site import DEMO_CODE
from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME

pytestmark = pytest.mark.asyncio


class Client:
    def __init__(self, value="choose", mutate=None):
        self.value, self.mutate, self.calls = value, mutate, []

    async def generate(self, instructions, text, schema, **kwargs):
        self.calls.append((instructions, text, kwargs))
        if self.mutate:
            await self.mutate()
        if isinstance(self.value, Exception):
            raise self.value
        if self.value != "choose":
            return self.value
        candidates = json.loads(text)["candidates"]
        return {"candidate_id": candidates[0]["id"]}


def enabled(configuration, screenshots=False):
    configuration.settings.ai = AISettings(enabled=True, model="configured-model", locator_fallback=True,
                                           screenshots=screenshots, request_timeout_seconds=1)
    configuration.secrets["ai:api_key"] = SecretStr("private-api-key")
    return configuration


async def test_configured_login_survives_ai_outage_without_a_call(configuration_factory):
    client = Client(AIError())
    configuration = enabled(configuration_factory())
    for step in configuration.site.steps:
        step.ai_fallback = True
    async with authenticated_session(configuration, ai_client=client) as result:
        assert result.status == "succeeded"
    assert not client.calls


async def test_missing_password_uses_actual_password_prompt_and_preserves_success_checks(configuration_factory):
    def change(profile):
        profile["steps"][2].update(target={"by": "css", "value": "#obsolete-password"},
                                   ai_fallback=True, timeout_ms=2000)
    configuration = enabled(configuration_factory(change=change))
    client = Client()
    async with authenticated_session(configuration, ai_client=client) as result:
        await result.session.page.get_by_role("button", name="Perform protected action").click()
        await result.session.page.get_by_test_id("protected-result").wait_for()
    assert len(client.calls) == 1
    instructions, text, options = client.calls[0]
    assert json.loads(text)["task"] == "fill_password"
    assert DEMO_USERNAME not in text and DEMO_PASSWORD not in text and "private-api-key" not in text
    assert options["image"] is None


async def test_single_field_otp_fallback_uses_code_prompt(email_configuration):
    configuration = enabled(email_configuration(timeout=3))
    step = next(step for step in configuration.site.steps if step.action == "fill_otp")
    step.targets = [Locator(by="css", value="#obsolete-code")]
    step.ai_fallback, step.timeout_ms = True, 2000
    provider = FakeVerificationProvider()
    provider.enqueue(DEMO_CODE)
    client = Client()
    async with authenticated_session(configuration, provider, ai_client=client) as result:
        assert result.status == "succeeded"
    assert json.loads(client.calls[0][1])["task"] == "fill_otp"
    assert DEMO_CODE not in client.calls[0][1]


@pytest.mark.parametrize("suggestion,reason", [
    ({"candidate_id": "unknown"}, "ai_locator_ambiguous"),
    ({"candidate_id": ["0", "1"]}, "ai_locator_ambiguous"),
    ({"candidate_id": "0", "code": "arbitrary"}, "ai_locator_invalid"),
    (None, "ai_locator_invalid"), (AIError(), "ai_unavailable"),
])
async def test_invalid_suggestions_do_not_submit_credentials(configuration_factory, login_site, suggestion, reason):
    def change(profile):
        profile["steps"][2].update(target={"by": "css", "value": "#missing"}, ai_fallback=True, timeout_ms=1500)
    configuration = enabled(configuration_factory(change=change))
    before = login_site.attempt_count
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration, ai_client=Client(suggestion)):
            pytest.fail("Invalid suggestions cannot yield")
    assert failure.value.result.reason == reason
    assert login_site.attempt_count == before


async def test_changed_candidate_is_rejected_and_screenshot_masks_all_inputs(session):
    page = session.page
    await page.set_content('<label>Password<input type="password" value="private-password"></label>'
                           '<label>OTP<input value="001234"></label><p>private-password</p>')
    screenshots = []
    original = page.screenshot
    # A wrapper verifies masking occurs before any external-client call.
    async def capture(**kwargs):
        assert kwargs["mask"] and len(kwargs["mask"]) == 3
        image = await original(**kwargs)
        screenshots.append(image)
        return image
    page.screenshot = capture

    async def mutate():
        await page.locator('input[type="password"]').evaluate("el => el.type = 'text'")
    client = Client(mutate=mutate)
    resolver = LocatorResolver(client, screenshots=True)
    with pytest.raises(BrowserActionError) as failure:
        await resolver.resolve(page, Locator(by="css", value="#missing"), purpose="fill_password",
                               hint="Password", secrets=["private-password", "001234"], timeout_ms=2000)
    assert failure.value.reason == "ai_locator_changed"
    assert screenshots and client.calls[0][2]["image"] == screenshots[0]
    assert "private-password" not in client.calls[0][1] and "001234" not in client.calls[0][1]


async def test_ambiguous_configured_control_does_not_call_ai(session):
    await session.page.set_content('<input><input>')
    client = Client()
    resolver = LocatorResolver(client)
    with pytest.raises(BrowserActionError, match="ambiguous_target"):
        await resolver.resolve(session.page, Locator(by="css", value="input"), purpose="fill_email",
                               hint="Email", secrets=[], timeout_ms=1000)
    assert not client.calls
