"""Public fixtures prove all login modes and preserve the session for later work."""

import pytest

from tests.fixtures.email_site import DEMO_CODE

pytestmark = pytest.mark.e2e


@pytest.fixture
def autoweb_configuration(request, configuration_factory, email_configuration, autoweb_fake_provider):
    mode = request.param
    if mode == "password":
        return configuration_factory()
    autoweb_fake_provider.enqueue(DEMO_CODE)
    return email_configuration(mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("autoweb_configuration", ["password", "email_otp", "password_email_otp"], indirect=True)
async def test_public_login_fixture_supports_protected_work(
    autoweb_login, autoweb_session, autoweb_settings, autoweb_site_profile,
):
    assert autoweb_login.session is autoweb_session
    assert autoweb_login.site == autoweb_site_profile.name
    assert autoweb_session.timeouts == autoweb_settings.timeouts
    await autoweb_session.page.get_by_role("button", name="Perform protected action").click()
    await autoweb_session.page.get_by_test_id("protected-result").wait_for()
    assert await autoweb_session.page.get_by_test_id("protected-result").inner_text() == "Protected action completed"
