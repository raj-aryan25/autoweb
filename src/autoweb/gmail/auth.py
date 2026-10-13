"""Explicit OAuth operations. Ordinary message access never opens an OAuth browser."""

from __future__ import annotations

import os
import tempfile
from functools import partial
from pathlib import Path

from autoweb.config.models import GmailSettings
from .errors import GmailAuthenticationError


def _imports():
    try:
        import httplib2
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise GmailAuthenticationError("gmail_dependencies_missing") from None
    return httplib2, Credentials, Request, InstalledAppFlow


def _save_token(credentials, path: Path) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(credentials.to_json())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise GmailAuthenticationError("token_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_credentials(settings: GmailSettings):
    """Load/refresh an existing token; setup is a separate explicit operation."""
    httplib2, Credentials, Request, _ = _imports()
    if not settings.token_file.is_file():
        raise GmailAuthenticationError("oauth_setup_required")
    try:
        # Do not supply scopes here: retain the token's recorded grant for validation.
        credentials = Credentials.from_authorized_user_file(str(settings.token_file))
    except (OSError, ValueError, UnicodeError):
        raise GmailAuthenticationError("token_invalid") from None
    if not credentials.has_scopes(settings.scopes):
        raise GmailAuthenticationError("oauth_scopes_insufficient")
    if not credentials.valid:
        if not credentials.refresh_token:
            raise GmailAuthenticationError("oauth_setup_required")
        http = httplib2.Http(timeout=settings.request_timeout_seconds)
        try:
            credentials.refresh(Request(http))
        except Exception:
            raise GmailAuthenticationError("token_refresh_failed") from None
        finally:
            http.close()
        if not credentials.valid:
            raise GmailAuthenticationError("token_invalid")
        if credentials.granted_scopes is not None and not set(settings.scopes).issubset(credentials.granted_scopes):
            raise GmailAuthenticationError("oauth_scopes_insufficient")
        _save_token(credentials, settings.token_file)
    return credentials


def authorize(settings: GmailSettings, *, timeout_seconds: float = 180):
    """Opt-in interactive consent, replacing only the explicitly configured token."""
    _, _, _, InstalledAppFlow = _imports()
    if not settings.credentials_file.is_file():
        raise GmailAuthenticationError("oauth_client_file_missing")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_file), settings.scopes)
        # run_local_server forwards kwargs to authorization_url, not fetch_token.
        # Bound the isolated OAuth session's token-exchange HTTP request explicitly.
        flow.oauth2session.request = partial(flow.oauth2session.request,
                                            timeout=settings.request_timeout_seconds)
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, timeout_seconds=timeout_seconds,
            authorization_prompt_message="Authorize Gmail in the browser window.",
            success_message="Gmail authorization completed. You can close this window.",
        )
    except Exception:
        raise GmailAuthenticationError("oauth_authorization_failed") from None
    if not credentials.valid or not credentials.has_scopes(settings.scopes):
        raise GmailAuthenticationError("oauth_scopes_insufficient")
    _save_token(credentials, settings.token_file)
    return credentials
