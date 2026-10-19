"""One email challenge, independent of Gmail and browser implementations."""

import asyncio
from datetime import datetime, timezone
import re
from time import monotonic

from autoweb.config import Configuration, ConfigurationError
from autoweb.contracts import (
    VerificationError, VerificationProvider, VerificationRequest, VerificationResult,
)


class EmailChallenge:
    def __init__(self, configuration: Configuration, provider: VerificationProvider,
                 login_deadline: float):
        self.configuration = configuration
        self.provider = provider
        self.login_deadline = login_deadline
        self.deadline = login_deadline
        self.request: VerificationRequest | None = None
        self.result: VerificationResult | None = None

    async def prepare(self) -> None:
        verification = self.configuration.site.verification
        timeout = verification.timeout_seconds or self.configuration.settings.timeouts.verification_seconds
        self.deadline = min(self.login_deadline, monotonic() + timeout)
        recipient = self.configuration.secrets["verification:recipient"].get_secret_value()
        baseline = await asyncio.wait_for(
            self.provider.baseline(verification.sender, recipient, verification.subject,
                                   deadline=self.deadline),
            max(0, self.deadline - monotonic()),
        )
        # Capture immediately before the browser action, after baseline completion.
        self.request = VerificationRequest(
            site=self.configuration.site.name, account=recipient, recipient=recipient,
            sender=verification.sender, subject=verification.subject,
            challenge_started_at=datetime.now(timezone.utc), deadline=self.deadline,
            baseline_ids=baseline, code_regex=verification.code_regex,
            code_length=verification.code_length, code_characters=verification.code_characters,
        )

    async def receive(self) -> VerificationResult:
        if self.request is None:
            raise ConfigurationError("OTP entry requires a prepared email challenge")
        result = await self.provider.receive(self.request)
        request = self.request
        # Enforce the contract even for injected providers, before entering any value.
        if (not isinstance(result, VerificationResult)
                or result.site != request.site or result.account != request.account
                or not result.message_id or result.message_id in request.baseline_ids
                or not isinstance(result.received_at, datetime)
                or result.received_at.utcoffset() is None
                or not request.challenge_started_at <= result.received_at <= datetime.now(timezone.utc)
                or not isinstance(result.value, str) or len(result.value) != request.code_length
                or not all(re.fullmatch(request.code_characters, char) for char in result.value)):
            raise VerificationError("verification_result_invalid")
        self.result = result
        return result

    async def acknowledge(self) -> None:
        policy = self.configuration.site.verification.acknowledge
        if self.result is not None and policy == "on_success":
            await asyncio.wait_for(
                self.provider.acknowledge(self.result, success=True, policy=policy,
                                          deadline=self.deadline),
                max(0, self.deadline - monotonic()),
            )
