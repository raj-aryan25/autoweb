"""Explicit Gemini smoke checks using synthetic data and an isolated local page."""

import pytest

from pydantic import SecretStr

from autoweb.ai.client import GeminiClient
from autoweb.ai.extraction import GeminiCodeExtractor
from autoweb.ai.locators import LocatorResolver
from autoweb.browser import open_session, resolve_locator
from autoweb.config import load_settings
from autoweb.config.loader import get_project_root, load_environment
from autoweb.config.models import Locator

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture
def live_ai_settings(request):
    root = get_project_root(request.config.getoption("autoweb_root"))
    env_file = request.config.getoption("autoweb_env_file")
    settings = load_settings(request.config.getoption("autoweb_settings"),
                             project_root=root, env_file=env_file)
    if not settings.ai.enabled or not settings.ai.model:
        pytest.fail("Live AI checks require AI enablement and an explicit model", pytrace=False)
    return settings


@pytest.fixture
def live_ai_client(request, live_ai_settings):
    environment = load_environment(get_project_root(request.config.getoption("autoweb_root")),
                                   request.config.getoption("autoweb_env_file"))
    key = environment.get(live_ai_settings.ai.api_key.env, "").strip()
    if not key:
        pytest.fail("Configured AI API key is missing", pytrace=False)
    return GeminiClient(live_ai_settings.ai.model, SecretStr(key))


async def test_live_gemini_extracts_synthetic_code(live_ai_client, live_ai_settings):
    extractor = GeminiCodeExtractor(client=live_ai_client,
                                    request_timeout_seconds=live_ai_settings.ai.request_timeout_seconds)
    value = await extractor.extract(
        "Synthetic AutoWeb test email. Your login verification code is 007319. "
        "No real user account or email is involved.", length=6, characters="[0-9]",
        timeout_seconds=live_ai_settings.ai.request_timeout_seconds,
    )
    assert value == "007319", "Gemini did not return the validated synthetic code"


@pytest.mark.parametrize("image", [False, True], ids=["metadata", "masked-image"])
async def test_live_gemini_resolves_synthetic_password_field(live_ai_client, live_ai_settings, image):
    async with open_session(live_ai_settings) as session:
        await session.page.set_content(
            '<label>Email<input type="email" value="synthetic@example.test"></label>'
            '<label>Account password<input type="password" value="synthetic-password"></label>'
            '<label>Verification code<input value="007319"></label>'
        )
        resolver = LocatorResolver(live_ai_client, screenshots=image,
                                   request_timeout_seconds=live_ai_settings.ai.request_timeout_seconds)
        chosen = await resolver.resolve(
            session.page, Locator(by="css", value="#deliberately-missing"),
            purpose="fill_password", hint="Account password input",
            secrets=["synthetic@example.test", "synthetic-password", "007319"],
            timeout_ms=(live_ai_settings.ai.request_timeout_seconds + 3) * 1000,
        )
        locator = resolve_locator(session.page, chosen)
        assert await locator.get_attribute("type") == "password"
        await locator.fill("replacement-synthetic-password")
        assert await locator.input_value() == "replacement-synthetic-password"
