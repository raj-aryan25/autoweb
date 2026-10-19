"""Password-login preflight and session-assertion schema contracts."""

import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import Mock
from time import monotonic

import pytest
from pydantic import ValidationError

from autoweb.config import ConfigurationError, load_configuration, load_site_profile
from autoweb.config.models import APIAssertion
from autoweb.contracts import VerificationError
from autoweb.login import LoginError, authenticated_session, run_login

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_schema_only_password_configuration_cannot_open_browser(monkeypatch):
    configuration = load_configuration("configs/sites/password.example.json",
                                       project_root=REPO, environ={}, schema_only=True)
    opener = Mock(side_effect=AssertionError("Browser must not open"))
    monkeypatch.setattr("autoweb.login.flow.open_session", opener)
    with pytest.raises(ConfigurationError):
        async with authenticated_session(configuration):
            pytest.fail("Unresolved credentials cannot yield a session")
    opener.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["email-otp.example.json", "password-email-otp.example.json"])
async def test_unresolved_otp_configuration_is_rejected_before_browser_startup(monkeypatch, filename):
    configuration = load_configuration(f"configs/sites/{filename}",
                                       project_root=REPO, environ={}, schema_only=True)
    opener = Mock(side_effect=AssertionError("Browser must not open"))
    monkeypatch.setattr("autoweb.login.flow.open_session", opener)
    with pytest.raises(ConfigurationError):
        async with authenticated_session(configuration):
            pytest.fail("Unresolved email credentials must fail preflight")
    opener.assert_not_called()


@pytest.mark.parametrize("change", [
    {"url": "//other.example/session"},
    {"url": "/\\other.example/session"},
    {"url": "javascript:alert(1)"},
    {"url": "https://user:password@example.com/session"},
    {"url": "/bad path"},
    {"expected_status": True},
    {"expected_status": 99},
    {"expected_json": {}},
    {"expected_json": {"authenticated": float("nan")}},
])
def test_api_assertion_rejects_invalid_configuration(change):
    values = {"type": "api", "url": "/api/session", "expected_json": {"authenticated": True}}
    values.update(change)
    with pytest.raises(ValidationError):
        APIAssertion.model_validate(values)


def test_absolute_api_assertion_must_use_site_origin():
    profile = load_site_profile("configs/sites/password.example.json", project_root=REPO)
    values = profile.model_dump()
    values["success"].append({"type": "api", "url": "https://other.example/session", "expected_json": {"authenticated": True}})
    with pytest.raises(ValidationError, match="login site origin"):
        type(profile).model_validate(values)


def email_configuration():
    return load_configuration("configs/sites/email-otp.example.json", project_root=REPO,
                              environ={"EXAMPLE_EMAIL": "test@example.test", "AUTOWEB_AI_ENABLED": "false"})


@pytest.mark.asyncio
async def test_run_email_login_requires_explicit_provider():
    with pytest.raises(ConfigurationError, match="verification provider"):
        await run_login(None, email_configuration())


@pytest.mark.asyncio
async def test_oauth_failure_precedes_browser_startup(monkeypatch):
    @asynccontextmanager
    async def unavailable(configuration):
        raise VerificationError("oauth_setup_required")
        yield  # pragma: no cover

    opener = Mock(side_effect=AssertionError("Browser must not open"))
    monkeypatch.setattr("autoweb.gmail.provider.open_gmail_provider", unavailable)
    monkeypatch.setattr("autoweb.login.flow.open_session", opener)
    with pytest.raises(VerificationError, match="oauth_setup_required"):
        async with authenticated_session(email_configuration()):
            pytest.fail("Missing OAuth cannot yield a browser")
    opener.assert_not_called()


@pytest.mark.asyncio
async def test_owned_provider_closes_when_browser_startup_fails(monkeypatch):
    events = []

    @asynccontextmanager
    async def provider(configuration):
        events.append("provider-open")
        try:
            yield Mock()
        finally:
            events.append("provider-close")

    @asynccontextmanager
    async def broken_browser(settings):
        events.append("browser-open")
        raise RuntimeError("synthetic startup failure")
        yield  # pragma: no cover

    monkeypatch.setattr("autoweb.gmail.provider.open_gmail_provider", provider)
    monkeypatch.setattr("autoweb.login.flow.open_session", broken_browser)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with authenticated_session(email_configuration()):
            pytest.fail("Broken browser cannot yield")
    assert events == ["provider-open", "browser-open", "provider-close"]


@pytest.mark.parametrize("field", ["rejected", "expired"])
def test_verification_api_assertion_requires_same_origin(field):
    values = email_configuration().site.model_dump()
    values["verification"][field] = [{"type": "api", "url": "https://other.example/status",
                                      "expected_json": {"expired": True}}]
    with pytest.raises(ValidationError, match="login site origin"):
        type(email_configuration().site).model_validate(values)


@pytest.mark.asyncio
async def test_cancelled_assertion_observes_late_navigation_error(monkeypatch):
    from playwright.async_api import Error
    from autoweb.login.assertions import _matches

    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = []

    async def count():
        started.set()
        try:
            await release.wait()
            raise Error("Execution context was destroyed")
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        finally:
            finished.set()

    locator = Mock()
    locator.count = count
    monkeypatch.setattr("autoweb.login.assertions.resolve_locator", lambda *args, **kwargs: locator)
    condition = email_configuration().site.success[0]
    probe = asyncio.create_task(_matches(Mock(), condition, monotonic() + 2, "https://example.com"))
    await started.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert not cancelled
    release.set()
    await finished.wait()
    await asyncio.sleep(0)  # Allow the exception-observer callback to run.
