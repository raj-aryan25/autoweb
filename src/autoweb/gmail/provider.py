"""Explicit, owned Gmail provider setup for application entry points."""

import asyncio
from contextlib import asynccontextmanager

from autoweb.config import Configuration, ConfigurationError
from .auth import load_credentials
from .client import GmailClient
from .errors import GmailError
from .poller import GmailVerificationProvider, build_query


@asynccontextmanager
async def open_gmail_provider(configuration: Configuration, *, ai_client=None):
    """Initialize OAuth before website actions; never initiate interactive consent."""
    settings = configuration.settings
    verification = configuration.site.verification
    recipient = configuration.secrets["verification:recipient"].get_secret_value()
    try:
        build_query(verification.sender, recipient, verification.subject)
    except ValueError:
        raise ConfigurationError("Invalid Gmail sender, recipient, or subject filter") from None
    fallback = None
    if settings.ai.enabled and verification.kind == "otp":
        from autoweb.ai.extraction import GeminiCodeExtractor

        api_key = configuration.secrets.get("ai:api_key")
        if api_key is None:
            raise ConfigurationError("Enabled Gemini extraction requires its API key")
        from autoweb.ai.client import client_from_configuration

        fallback = GeminiCodeExtractor(client=ai_client or client_from_configuration(configuration),
                                       request_timeout_seconds=settings.ai.request_timeout_seconds)
    try:
        credentials = await asyncio.wait_for(
            asyncio.to_thread(load_credentials, settings.gmail), settings.gmail.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise GmailError("oauth_setup_timeout") from None
    async with GmailClient(credentials, request_timeout_seconds=settings.gmail.request_timeout_seconds) as client:
        yield GmailVerificationProvider(
            client, poll_interval_seconds=settings.gmail.poll_interval_seconds,
            request_timeout_seconds=settings.gmail.request_timeout_seconds, fallback=fallback,
        )
