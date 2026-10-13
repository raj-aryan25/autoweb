"""Standalone Gmail authorization and fresh verification diagnostics; no website actions."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from time import monotonic

from pydantic import SecretStr

from autoweb.ai.extraction import GeminiCodeExtractor
from autoweb.config import ConfigurationError, load_settings, load_site_profile
from autoweb.config.loader import get_project_root, load_environment
from autoweb.contracts import VerificationRequest
from .auth import authorize, load_credentials
from .client import GmailClient
from .errors import GmailError, VerificationTimeout
from .poller import GmailVerificationProvider, build_query


async def diagnose(settings, site, recipient: str, api_key: str | None,
                   *, show_code: bool = False) -> None:
    verification = site.verification
    if verification is None:
        raise ConfigurationError("Gmail diagnostics require an email-verification profile")
    build_query(verification.sender, recipient, verification.subject)
    timeout = verification.timeout_seconds or settings.timeouts.verification_seconds
    if settings.gmail.poll_interval_seconds >= timeout:
        raise ConfigurationError("Gmail polling interval must be shorter than the verification timeout")
    fallback = None
    if settings.ai.enabled:
        if not api_key:
            raise ConfigurationError("Enabled Gemini extraction requires its API key")
        fallback = GeminiCodeExtractor(settings.ai.model, SecretStr(api_key))
    try:
        credentials = await asyncio.wait_for(asyncio.to_thread(load_credentials, settings.gmail),
                                             settings.gmail.request_timeout_seconds)
    except asyncio.TimeoutError:
        raise GmailError("oauth_setup_timeout") from None
    async with GmailClient(credentials, request_timeout_seconds=settings.gmail.request_timeout_seconds) as client:
        provider = GmailVerificationProvider(
            client, poll_interval_seconds=settings.gmail.poll_interval_seconds,
            request_timeout_seconds=settings.gmail.request_timeout_seconds, fallback=fallback,
        )
        # Baseline precedes the human-triggered challenge, as it will precede submit in Phase V.
        baseline = await provider.baseline(verification.sender, recipient, verification.subject,
                                          deadline=monotonic() + settings.timeouts.verification_seconds)
        started = datetime.now(timezone.utc)
        print("Baseline captured. Request a new verification email now; waiting for fresh mail.", flush=True)
        request = VerificationRequest(
            site=site.name, account=recipient, sender=verification.sender, recipient=recipient,
            challenge_started_at=started, deadline=monotonic() + timeout, baseline_ids=baseline,
            subject=verification.subject, code_regex=verification.code_regex,
            code_length=verification.code_length, code_characters=verification.code_characters,
        )
        result = await provider.receive(request)
        print(f"Verification found; method={result.extraction_method}; received={result.received_at.isoformat()}")
        if show_code:
            print(f"Code: {result.value}")
        # Retrieval alone cannot prove consumption. This diagnostic never acknowledges email.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="Email-verification profile; required unless authorizing")
    parser.add_argument("--settings", default="configs/settings.json")
    parser.add_argument("--project-root")
    parser.add_argument("--env-file")
    parser.add_argument("--authorize", action="store_true", help="Explicit OAuth consent; save the configured token")
    parser.add_argument("--show-code", action="store_true", help="Opt in to printing the sensitive code")
    args = parser.parse_args(argv)
    if not args.authorize and not args.site:
        parser.error("--site is required for verification diagnostics")
    try:
        root = get_project_root(args.project_root)
        settings = load_settings(args.settings, project_root=root, env_file=args.env_file)
        if args.authorize:
            authorize(settings.gmail)
            print("Gmail authorization saved to the configured token path.")
            return 0
        site = load_site_profile(args.site, project_root=root)
        if site.verification is None:
            raise ConfigurationError("Gmail diagnostics require an email-verification profile")
        environment = load_environment(root, args.env_file)
        recipient = environment.get(site.verification.recipient.env, "").strip()
        if not recipient:
            raise ConfigurationError(f"Missing environment variable: {site.verification.recipient.env}")
        asyncio.run(diagnose(settings, site, recipient, environment.get(settings.ai.api_key.env),
                             show_code=args.show_code))
    except VerificationTimeout as error:
        print(str(error))
        return 3
    except GmailError as error:
        print(str(error))
        return 1
    except ConfigurationError as error:
        print(f"Configuration error: {error}")
        return 2
    except ValueError:
        print("Invalid Gmail diagnostic configuration; check the profile and environment references.")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0
