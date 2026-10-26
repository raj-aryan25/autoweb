"""Magic-link mail correlation, one-use consumption, redirect policy and session reuse."""

import json
from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio

from autoweb.config import load_configuration
from autoweb.gmail import GmailVerificationProvider
from autoweb.login import LoginError, authenticated_session
from tests.fixtures.email_site import DEMO_EMAIL
from tests.fixtures.magic_site import MagicSite

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.asyncio


@pytest.fixture
def magic_setup(tmp_path):
    server = MagicSite()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    profile = json.loads((REPO / "configs/sites/email-magic-link.example.json").read_text())
    profile["entry_url"] = origin + "/magic-login"
    profile["verification"].update(sender="noreply@example.test", allowed_origins=[origin],
        link_path_prefix="/magic", timeout_seconds=2,
        expired=[{"type": "element", "target": {"by": "test_id", "value": "link-expired"}}])
    profile["success"][1] = {"type": "api", "url": "/api/session", "expected_json": {"authenticated": True}}
    settings = json.loads((REPO / "configs/settings.json").read_text())
    settings["timeouts"].update(action_ms=1000, navigation_ms=3000, login_seconds=5, verification_seconds=2)
    settings["gmail"]["poll_interval_seconds"] = 0.01
    (tmp_path / "settings.json").write_text(json.dumps(settings))

    def configuration(scenario="normal", timeout=2):
        profile["entry_url"] = origin + "/magic-login?scenario=" + scenario
        profile["verification"]["timeout_seconds"] = timeout
        (tmp_path / "site.json").write_text(json.dumps(profile))
        return load_configuration("site.json", "settings.json", project_root=tmp_path,
                                  environ={"EXAMPLE_EMAIL": DEMO_EMAIL})
    try:
        yield server, configuration
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class Mailbox:
    def __init__(self, site):
        self.site, self.read = site, []

    async def list_message_ids(self, query, *, deadline):
        return tuple(self.site.messages)

    async def get_message(self, identity, *, deadline):
        return self.site.messages[identity]

    async def mark_read(self, identity, *, deadline):
        assert self.site.sessions
        self.read.append(identity)


@pytest.fixture
def autoweb_configuration(magic_setup):
    return magic_setup[1]()


@pytest_asyncio.fixture
async def autoweb_verification_provider(magic_setup):
    yield GmailVerificationProvider(Mailbox(magic_setup[0]), poll_interval_seconds=0.01)


async def test_public_login_fixture_supports_magic_link(autoweb_login):
    assert autoweb_login.status == "succeeded"
    assert await autoweb_login.session.page.get_by_test_id("account-menu").is_visible()


async def test_magic_link_preserves_request_cookie_and_authenticated_session(magic_setup):
    site, configuration = magic_setup
    mailbox = Mailbox(site)
    provider = GmailVerificationProvider(mailbox, poll_interval_seconds=0.01)
    async with authenticated_session(configuration(), provider) as result:
        assert result.status == "succeeded"
        active = result.session
        await active.page.get_by_role("button", name="Perform protected action").click()
        await active.page.get_by_test_id("protected-result").wait_for()
        assert await active.page.get_by_test_id("protected-result").inner_text() == "Protected action completed"
        assert len(mailbox.read) == 1
        assert "token=" not in repr(result)
    assert active.page.is_closed()
    assert site.request_count == site.verify_count == 1


@pytest.mark.parametrize("scenario,reason", [
    ("external-redirect", "verification_link_rejected"),
    ("js-external", "verification_link_rejected"),
    ("redirect-loop", "verification_link_rejected"),
    ("expired", "verification_expired"),
    ("no-mail", "verification_timeout"),
])
async def test_magic_link_failure_never_acknowledges_or_navigates_outside(magic_setup, scenario, reason):
    site, configuration = magic_setup
    mailbox = Mailbox(site)
    provider = GmailVerificationProvider(mailbox, poll_interval_seconds=0.01)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration(scenario, timeout=1), provider):
            pytest.fail("Invalid magic-link flow cannot succeed")
    assert failure.value.result.reason == reason
    assert site.outside_count == 0 and not mailbox.read
    assert site.request_count == 1 and site.verify_count <= 1
    assert "token=" not in str(failure.value) + repr(failure.value.result)


async def test_injected_provider_cannot_bypass_magic_link_destination_policy(magic_setup):
    site, configuration = magic_setup
    mailbox = Mailbox(site)
    provider = GmailVerificationProvider(mailbox, poll_interval_seconds=0.01)
    original = provider.receive
    async def malicious(request):
        result = await original(request)
        return replace(result, value="https://evil.example/magic/consume?token=private")
    provider.receive = malicious
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration(), provider):
            pytest.fail("A provider cannot bypass the link policy")
    assert failure.value.result.reason == "verification_result_invalid"
    assert site.verify_count == 0 and not mailbox.read
