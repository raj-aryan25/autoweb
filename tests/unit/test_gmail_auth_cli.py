"""OAuth setup and diagnostic behavior without real accounts or browser actions."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoweb.config import load_settings, load_site_profile
from autoweb.config.models import GmailSettings
from autoweb.gmail import auth, cli
from autoweb.gmail.errors import GmailAuthenticationError, GmailError, VerificationTimeout


REPO = Path(__file__).parents[2]


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    settings = GmailSettings(credentials_file=tmp_path / "client.json", token_file=tmp_path / "token.json")
    settings.credentials_file.write_text("{}")
    settings.token_file.write_text("original-token")
    credentials = SimpleNamespace(valid=True, refresh_token="private-token", granted_scopes=None,
                                  has_scopes=Mock(return_value=True), to_json=Mock(return_value="new-token"))
    credentials.refresh = Mock()
    transport = SimpleNamespace(close=Mock())
    session = SimpleNamespace(request=Mock())
    flow = SimpleNamespace(oauth2session=session, run_local_server=Mock(return_value=credentials))
    imports = (SimpleNamespace(Http=Mock(return_value=transport)),
               SimpleNamespace(from_authorized_user_file=Mock(return_value=credentials)),
               Mock(), SimpleNamespace(from_client_secrets_file=Mock(return_value=flow)))
    monkeypatch.setattr(auth, "_imports", lambda: imports)
    return settings, credentials, transport, flow, imports


def test_valid_token_does_not_open_oauth_or_rewrite_token(oauth):
    settings, credentials, _, _, imports = oauth
    assert auth.load_credentials(settings) is credentials
    assert settings.token_file.read_text() == "original-token"
    imports[3].from_client_secrets_file.assert_not_called()


def test_refresh_is_explicit_bounded_and_saved_atomically(oauth):
    settings, credentials, transport, _, imports = oauth
    credentials.valid = False
    credentials.refresh.side_effect = lambda req: setattr(credentials, "valid", True)
    assert auth.load_credentials(settings) is credentials
    assert settings.token_file.read_text() == "new-token"
    imports[0].Http.assert_called_once_with(timeout=settings.request_timeout_seconds)
    transport.close.assert_called_once()


@pytest.mark.parametrize("kind,reason", [
    ("missing", "oauth_setup_required"), ("invalid", "token_invalid"),
    ("scope", "oauth_scopes_insufficient"), ("refresh", "token_refresh_failed"),
    ("no_refresh", "oauth_setup_required"),
])
def test_bad_credentials_fail_without_implicit_authorization_or_token_overwrite(oauth, kind, reason):
    settings, credentials, _, _, imports = oauth
    if kind == "missing":
        settings.token_file.unlink()
    elif kind == "invalid":
        imports[1].from_authorized_user_file.side_effect = ValueError("private-token")
    elif kind == "scope":
        credentials.has_scopes.return_value = False
    else:
        credentials.valid = False
        if kind == "refresh":
            credentials.refresh.side_effect = RuntimeError("private-token")
        else:
            credentials.refresh_token = None
    with pytest.raises(GmailAuthenticationError, match=reason) as error:
        auth.load_credentials(settings)
    assert "private-token" not in str(error.value)
    imports[3].from_client_secrets_file.assert_not_called()
    if kind != "missing":
        assert settings.token_file.read_text() == "original-token"


def test_authorization_only_writes_explicitly_configured_path(oauth):
    settings, credentials, _, flow, _ = oauth
    assert auth.authorize(settings) is credentials
    assert settings.token_file.read_text() == "new-token"
    assert flow.run_local_server.call_args.kwargs["timeout_seconds"] == 180
    assert flow.run_local_server.call_args.kwargs["host"] == "127.0.0.1"
    flow.oauth2session.request("POST", "https://example.com")
    assert flow.oauth2session.request.func.call_args.kwargs["timeout"] == settings.request_timeout_seconds


def test_refresh_scope_loss_does_not_overwrite_existing_token(oauth):
    settings, credentials, _, _, _ = oauth
    credentials.valid = False
    credentials.granted_scopes = []
    credentials.refresh.side_effect = lambda req: setattr(credentials, "valid", True)
    with pytest.raises(GmailAuthenticationError, match="scopes_insufficient"):
        auth.load_credentials(settings)
    assert settings.token_file.read_text() == "original-token"


def test_token_write_failure_is_secret_safe_and_preserves_existing_token(oauth, monkeypatch):
    settings, credentials, _, _, _ = oauth
    monkeypatch.setattr(auth.os, "replace", Mock(side_effect=OSError("private-token")))
    with pytest.raises(GmailAuthenticationError, match="token_write_failed") as error:
        auth._save_token(credentials, settings.token_file)
    assert "private-token" not in str(error.value)
    assert settings.token_file.read_text() == "original-token"
    assert sorted(path.name for path in settings.token_file.parent.iterdir()) == ["client.json", "token.json"]


@pytest.mark.parametrize("reason,exit_code", [("api", 1), ("timeout", 3)])
def test_diagnostic_cli_distinguishes_failure_and_empty_mailbox(monkeypatch, capsys, reason, exit_code):
    monkeypatch.setenv("EXAMPLE_EMAIL", "test@example.com")

    async def diagnose(*args, **kwargs):
        raise GmailError("api_unavailable") if reason == "api" else VerificationTimeout()

    monkeypatch.setattr(cli, "diagnose", diagnose)
    assert cli.main(["--site", "configs/sites/email-otp.example.json", "--project-root", str(REPO)]) == exit_code
    assert "001234" not in capsys.readouterr().out


def test_cli_authorize_is_separate_from_site_and_password_credentials(monkeypatch, capsys):
    authorize = Mock()
    monkeypatch.setattr(cli, "authorize", authorize)
    assert cli.main(["--authorize", "--project-root", str(REPO)]) == 0
    authorize.assert_called_once()
    assert "saved" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("show", [False, True])
async def test_real_diagnostic_pipeline_hides_code_and_never_acknowledges(monkeypatch, capsys, show):
    import base64

    class Client:
        def __init__(self, *args, **kwargs): self.read = []
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def list_message_ids(self, query, *, deadline): return ("old",) if "after:" not in query else ("old", "new")
        async def get_message(self, message_id, *, deadline):
            from datetime import datetime, timezone
            await asyncio.sleep(0.002)
            return {"id": message_id, "internalDate": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                    "payload": {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"001234").decode()},
                                "headers": [{"name": "From", "value": "noreply@example.com"},
                                            {"name": "To", "value": "test@example.com"},
                                            {"name": "Subject", "value": "Your verification code"}]}}
        async def mark_read(self, message_id, **kwargs): pytest.fail("Diagnostics must never acknowledge")

    monkeypatch.setattr(cli, "GmailClient", Client)
    monkeypatch.setattr(cli, "load_credentials", lambda settings: object())
    settings = load_settings(project_root=REPO, environ={})
    settings.timeouts.verification_seconds = 0.1
    settings.gmail.poll_interval_seconds = 0.005
    site = load_site_profile("configs/sites/email-otp.example.json", project_root=REPO)
    await cli.diagnose(settings, site, "test@example.com", None, show_code=show)
    output = capsys.readouterr().out
    assert ("001234" in output) is show
    assert "Verification found" in output
