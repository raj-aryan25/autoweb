"""Complete email login using injected providers and real Chromium/cookies."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Thread
from time import monotonic

import pytest

from autoweb.config import load_configuration
from autoweb.contracts import VerificationError, VerificationResult
from autoweb.gmail import GmailVerificationProvider
from autoweb.login import LoginError, authenticated_session, run_login
from autoweb.browser import open_session
from tests.fixtures.email_site import DEMO_CODE, DEMO_EMAIL, EmailSite, message

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.asyncio


@pytest.fixture
def email_site():
    server = EmailSite()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def email_configuration(tmp_path, email_site):
    def build(mode="email_otp", *, scenario="normal", split=False, timeout=2):
        filename = "email-otp.example.json" if mode == "email_otp" else "password-email-otp.example.json"
        profile = json.loads((REPO / "configs/sites" / filename).read_text())
        route = "email-login" if mode == "email_otp" else "password-email-login"
        profile["entry_url"] = f"http://127.0.0.1:{email_site.server_port}/{route}?scenario={scenario}&split={str(split).lower()}"
        profile["success"][1] = {"type": "api", "url": "/api/session", "expected_json": {"authenticated": True}}
        profile["verification"].update(sender="noreply@example.test", timeout_seconds=timeout,
            rejected=[{"type": "element", "target": {"by": "test_id", "value": "otp-error"}}],
            expired=[{"type": "element", "target": {"by": "test_id", "value": "otp-expired"}}])
        request = next(step for step in profile["steps"] if step["action"] == "request_otp")
        request["target"] = {"by": "role", "value": "button", "name": "Send code"}
        if split:
            next(step for step in profile["steps"] if step["action"] == "fill_otp")["targets"] = [
                {"by": "label", "value": f"Digit {i + 1}"} for i in range(6)]
        settings = json.loads((REPO / "configs/settings.json").read_text())
        settings["timeouts"].update(login_seconds=5, verification_seconds=timeout, action_ms=700)
        settings["gmail"]["poll_interval_seconds"] = 0.01
        (tmp_path / "settings.json").write_text(json.dumps(settings))
        (tmp_path / "site.json").write_text(json.dumps(profile))
        return load_configuration("site.json", "settings.json", project_root=tmp_path, environ={
            "EXAMPLE_EMAIL": DEMO_EMAIL, "EXAMPLE_USERNAME": "test-user", "EXAMPLE_PASSWORD": "test-password",
        })
    return build


class FakeProvider:
    def __init__(self, site, *, value=DEMO_CODE, invalid=None, hang=False):
        self.site, self.value, self.invalid, self.hang = site, value, invalid, hang
        self.requests, self.acknowledged = [], []
        self.baseline_calls = 0

    async def baseline(self, sender, recipient, subject=None, *, deadline):
        assert self.site.request_count == 0  # Correlation precedes the browser action.
        self.baseline_calls += 1
        return frozenset({"old-message"})

    async def receive(self, request):
        self.requests.append(request)
        if self.hang:
            await asyncio.Event().wait()
        result = VerificationResult(self.value, "fresh-message", datetime.now(timezone.utc),
                                    "regex", request.site, request.account)
        changes = {
            "baseline": {"message_id": "old-message"},
            "stale": {"received_at": request.challenge_started_at - timedelta(seconds=1)},
            "account": {"account": "another@example.test"},
            "format": {"value": "bad-code"},
        }
        return replace(result, **changes.get(self.invalid, {}))

    async def acknowledge(self, result, *, success, policy, deadline):
        assert success and policy == "on_success" and monotonic() < deadline
        assert self.site.sessions  # Real server authentication precedes acknowledgment.
        self.acknowledged.append(result.message_id)


class FakeMailbox:
    def __init__(self, site):
        self.site = site
        self.read = []

    async def list_message_ids(self, query, *, deadline):
        with self.site.lock:
            return tuple(self.site.messages)

    async def get_message(self, message_id, *, deadline):
        with self.site.lock:
            return self.site.messages[message_id]

    async def mark_read(self, message_id, *, deadline):
        assert self.site.sessions
        self.read.append(message_id)


@pytest.mark.parametrize("mode,split", [("email_otp", False), ("password_email_otp", True)])
async def test_email_modes_keep_authenticated_session(email_site, email_configuration, mode, split):
    configuration = email_configuration(mode, split=split)
    provider = FakeProvider(email_site)
    async with authenticated_session(configuration, provider) as result:
        assert result.status == "succeeded"
        await result.session.page.get_by_role("button", name="Perform protected action").click()
        await result.session.page.get_by_test_id("protected-result").wait_for()
        assert await result.session.page.get_by_test_id("protected-result").inner_text() == "Protected action completed"
        assert DEMO_CODE not in repr(result)
        assert provider.acknowledged == ["fresh-message"]
        assert provider.requests[0].deadline > monotonic()
        active = result.session
    assert active.page.is_closed()
    assert email_site.request_count == email_site.verify_count == provider.baseline_calls == 1


@pytest.mark.parametrize("value,scenario,reason", [
    ("999999", "normal", "verification_rejected"), (DEMO_CODE, "expired", "verification_expired"),
])
async def test_wrong_or_expired_code_never_acknowledged_or_retried(
    email_site, email_configuration, value, scenario, reason,
):
    provider = FakeProvider(email_site, value=value)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(email_configuration(scenario=scenario), provider):
            pytest.fail("Rejected code must fail")
    assert failure.value.result.reason == reason
    assert not provider.acknowledged
    assert email_site.request_count == email_site.verify_count == 1
    assert value not in str(failure.value)


@pytest.mark.parametrize("invalid", ["baseline", "stale", "account", "format"])
async def test_invalid_provider_result_not_entered(email_site, email_configuration, invalid):
    provider = FakeProvider(email_site, invalid=invalid)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(email_configuration(), provider):
            pytest.fail("Invalid result must fail")
    assert failure.value.result.reason == "verification_result_invalid"
    assert email_site.verify_count == 0
    assert not provider.acknowledged


async def test_hanging_provider_is_bounded_by_challenge_deadline(email_site, email_configuration):
    provider = FakeProvider(email_site, hang=True)
    configuration = email_configuration(timeout=0.4)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration, provider):
            pytest.fail("No code must time out")
    assert failure.value.result.reason == "verification_timeout"
    assert email_site.verify_count == 0 and not provider.acknowledged


@pytest.mark.parametrize("scenario", ["normal", "no-mail"])
async def test_gmail_provider_correlates_mail_and_only_acknowledges_consumed_message(
    email_site, email_configuration, scenario,
):
    email_site.messages["stale"] = message("stale", received_at=datetime.now(timezone.utc) - timedelta(days=1))
    client = FakeMailbox(email_site)
    provider = GmailVerificationProvider(client, poll_interval_seconds=0.01)
    configuration = email_configuration(scenario=scenario, timeout=1)
    async with open_session(configuration.settings) as session:
        # Fresh unrelated mail arrives after baseline; metadata must still exclude it.
        original = provider.baseline

        async def baseline(*args, **kwargs):
            ids = await original(*args, **kwargs)
            email_site.messages["unrelated"] = message("unrelated", sender="other@example.test")
            return ids

        provider.baseline = baseline
        if scenario == "normal":
            result = await run_login(session, configuration, provider)
            assert result.session is session
            assert len(client.read) == 1 and client.read[0] not in {"stale", "unrelated"}
        else:
            with pytest.raises(LoginError) as failure:
                await run_login(session, configuration, provider)
            assert failure.value.result.reason == "verification_timeout"
            assert not client.read and email_site.verify_count == 0


async def test_never_acknowledgment_policy(email_site, email_configuration):
    configuration = email_configuration()
    configuration.site.verification.acknowledge = "never"
    provider = FakeProvider(email_site)
    async with authenticated_session(configuration, provider):
        assert not provider.acknowledged


async def test_baseline_failure_never_requests_email(email_site, email_configuration):
    provider = FakeProvider(email_site)

    async def unavailable(*args, **kwargs):
        raise VerificationError("mailbox_unavailable")

    provider.baseline = unavailable
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(email_configuration(), provider):
            pytest.fail("Mailbox failure must stop before request")
    assert failure.value.result.reason == "mailbox_unavailable"
    assert email_site.request_count == 0 and not provider.acknowledged


async def test_code_submission_uses_remaining_challenge_budget(email_site, email_configuration):
    provider = FakeProvider(email_site)
    configuration = email_configuration(timeout=0.6)
    async with open_session(configuration.settings) as session:
        original = provider.receive

        async def disable_submission(request):
            result = await original(request)
            # Code is available, but the site's next transition never becomes ready.
            await session.page.get_by_role("button", name="Verify").wait_for()
            await session.page.get_by_role("button", name="Verify").evaluate("el => el.disabled = true")
            return result

        provider.receive = disable_submission
        with pytest.raises(LoginError) as failure:
            await run_login(session, configuration, provider)
        assert failure.value.result.reason == "verification_timeout"
        assert email_site.verify_count == 0 and not provider.acknowledged
        assert not session.page.is_closed()


async def test_failed_authentication_assertion_never_acknowledges(email_site, email_configuration):
    provider = FakeProvider(email_site)
    configuration = email_configuration(timeout=0.8)
    configuration.site.success[1].expected_json = {"authenticated": False}
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration, provider):
            pytest.fail("All authentication assertions must pass")
    assert failure.value.result.reason == "verification_timeout"
    assert email_site.sessions and not provider.acknowledged
    assert email_site.verify_count == 1


async def test_acknowledgment_failure_reports_stage_without_resubmitting(email_site, email_configuration):
    provider = FakeProvider(email_site)

    async def unavailable(*args, **kwargs):
        assert email_site.sessions
        raise VerificationError("acknowledgment_failed")

    provider.acknowledge = unavailable
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(email_configuration(), provider):
            pytest.fail("Acknowledgment failure must be explicit")
    assert failure.value.result.failed_step == "acknowledgment"
    assert failure.value.result.reason == "acknowledgment_failed"
    assert email_site.request_count == email_site.verify_count == 1


async def test_cancellation_during_polling_keeps_caller_owned_session(email_site, email_configuration):
    configuration = email_configuration()
    provider = FakeProvider(email_site, hang=True)
    async with open_session(configuration.settings) as session:
        task = asyncio.create_task(run_login(session, configuration, provider))
        try:
            async def started():
                while not provider.requests:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(started(), 4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not session.page.is_closed()
            assert not provider.acknowledged
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
