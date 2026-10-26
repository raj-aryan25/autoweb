"""Explicit Expand Testing Gmail OTP smoke, session reuse and logout."""

from time import monotonic
from urllib.parse import urlsplit

import pytest

from autoweb.ai.client import client_from_configuration
from autoweb.login.assertions import wait_for_authentication

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


class CountedClient:
    def __init__(self, client):
        self.client, self.calls = client, 0

    async def generate(self, *args, **kwargs):
        self.calls += 1
        return await self.client.generate(*args, **kwargs)


@pytest.fixture
def autoweb_ai_client(autoweb_configuration):
    if not autoweb_configuration.settings.ai.enabled:
        pytest.fail("This live check requires AI enabled to verify deterministic precedence", pytrace=False)
    return CountedClient(client_from_configuration(autoweb_configuration))


@pytest.fixture(autouse=True)
def selected_site_only(autoweb_configuration):
    if (urlsplit(autoweb_configuration.site.entry_url).hostname != "practice.expandtesting.com"
            or autoweb_configuration.site.login_mode != "email_otp"):
        pytest.fail("Select the explicit Expand Testing OTP profile for this live check", pytrace=False)


async def test_live_otp_session_reuse_and_logout(autoweb_login, autoweb_configuration, autoweb_ai_client):
    session = autoweb_login.session
    page = session.page
    await page.goto("https://practice.expandtesting.com/secure", wait_until="domcontentloaded", timeout=30000)
    await wait_for_authentication(page, autoweb_configuration.site, monotonic() + 15)
    assert autoweb_ai_client.calls == 0, "Configured OTP login should not need a Gemini request"
    await page.get_by_role("link", name="Logout", exact=True).click(timeout=15000)
    await page.wait_for_url("**/login", timeout=15000)
    await page.goto("https://practice.expandtesting.com/secure", wait_until="domcontentloaded", timeout=30000)
    assert urlsplit(page.url).path != "/secure", "Logout must revoke protected access"
    await page.get_by_role("button", name="Login", exact=True).wait_for(timeout=10000)
