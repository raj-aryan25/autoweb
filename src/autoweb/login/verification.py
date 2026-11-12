"""One email challenge, independent of Gmail and browser implementations."""

import asyncio
from datetime import datetime, timezone
import re
from time import monotonic

from autoweb.config import Configuration, ConfigurationError
from autoweb.contracts import (
    VerificationError, VerificationProvider, VerificationRequest, VerificationResult,
)
from autoweb.links import valid_magic_link
from .progress import Progress


class EmailChallenge:
    def __init__(self, configuration: Configuration, provider: VerificationProvider,
                 login_deadline: float, *, progress: Progress | None = None):
        self.configuration = configuration
        self.provider = provider
        self.login_deadline = login_deadline
        self.deadline = login_deadline
        self.request: VerificationRequest | None = None
        self.result: VerificationResult | None = None
        self.progress = progress or Progress(None, configuration.site)

    async def prepare(self, *, step_id="verification_baseline") -> None:
        verification = self.configuration.site.verification
        timeout = verification.timeout_seconds or self.configuration.settings.timeouts.verification_seconds
        self.deadline = min(self.login_deadline, monotonic() + timeout)
        recipient = self.configuration.secrets["verification:recipient"].get_secret_value()
        self.progress.start("verification_baseline", step_id=step_id)
        baseline = await asyncio.wait_for(
            self.provider.baseline(verification.sender, recipient, verification.subject,
                                   deadline=self.deadline),
            max(0, self.deadline - monotonic()),
        )
        self.progress.end("verification_baseline", step_id=step_id)
        # Capture immediately before the browser action, after baseline completion.
        self.request = VerificationRequest(
            site=self.configuration.site.name, account=recipient, recipient=recipient,
            sender=verification.sender, subject=verification.subject,
            challenge_started_at=datetime.now(timezone.utc), deadline=self.deadline,
            baseline_ids=baseline, code_regex=verification.code_regex,
            code_length=verification.code_length, code_characters=verification.code_characters,
            kind=verification.kind, allowed_origins=tuple(verification.allowed_origins),
            link_path_prefix=verification.link_path_prefix,
        )

    async def receive(self, *, step_id="verification_wait") -> VerificationResult:
        if self.request is None:
            raise ConfigurationError("OTP entry requires a prepared email challenge")
        self.progress.start("verification_wait", step_id=step_id)
        result = await self.provider.receive(self.request)
        request = self.request
        # Enforce the contract even for injected providers, before entering any value.
        if (not isinstance(result, VerificationResult)
                or result.site != request.site or result.account != request.account
                or not result.message_id or result.message_id in request.baseline_ids
                or not request.matches_received_at(result.received_at)
                or not isinstance(result.value, str)):
            raise VerificationError("verification_result_invalid")
        valid = (valid_magic_link(result.value, request.allowed_origins, request.link_path_prefix)
                 if request.kind == "magic_link" else
                 len(result.value) == request.code_length and all(
                     re.fullmatch(request.code_characters, char) for char in result.value))
        if not valid:
            raise VerificationError("verification_result_invalid")
        self.result = result
        self.progress.end("verification_wait", step_id=step_id)
        return result

    async def acknowledge(self) -> None:
        policy = self.configuration.site.verification.acknowledge
        if self.result is not None and policy == "on_success":
            self.progress.start("acknowledgment")
            await asyncio.wait_for(
                self.provider.acknowledge(self.result, success=True, policy=policy,
                                          deadline=self.deadline),
                max(0, self.deadline - monotonic()),
            )
            self.progress.end("acknowledgment")
