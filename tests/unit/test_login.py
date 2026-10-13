"""Password-login preflight and session-assertion schema contracts."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from autoweb.config import ConfigurationError, load_configuration, load_site_profile
from autoweb.config.models import APIAssertion
from autoweb.login import LoginError, authenticated_session

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
async def test_otp_mode_is_rejected_before_browser_startup(monkeypatch, filename):
    configuration = load_configuration(f"configs/sites/{filename}",
                                       project_root=REPO, environ={}, schema_only=True)
    opener = Mock(side_effect=AssertionError("Browser must not open"))
    monkeypatch.setattr("autoweb.login.flow.open_session", opener)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("Email verification belongs to a later phase")
    assert failure.value.result.reason == "unsupported_login_mode"
    assert failure.value.result.failed_step == "preflight"
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
