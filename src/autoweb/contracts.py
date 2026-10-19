"""Shared action results and verification contracts, independent of provider implementations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
import math
import re


class VerificationError(Exception):
    """Provider-neutral failure carrying a static, secret-safe reason."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Verification failed: {reason}")


@dataclass(frozen=True)
class ActionResult:
    action: str
    status: Literal["succeeded", "skipped"]
    elapsed_seconds: float
    reason: str | None = None


@dataclass(frozen=True)
class VerificationRequest:
    """One challenge, correlated to an account and an absolute monotonic deadline."""

    site: str
    account: str = field(repr=False)
    sender: str
    recipient: str = field(repr=False)
    challenge_started_at: datetime
    deadline: float
    baseline_ids: frozenset[str] = field(default_factory=frozenset)
    subject: str | None = None
    code_regex: str = r"\b\d{6}\b"
    code_length: int = 6
    code_characters: str = r"[0-9]"

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip()
                   for value in (self.site, self.account, self.sender, self.recipient)):
            raise ValueError("Verification identity and filters must be nonempty")
        if not isinstance(self.challenge_started_at, datetime) or self.challenge_started_at.utcoffset() is None:
            raise ValueError("Challenge timestamp must be timezone-aware")
        if (type(self.deadline) not in {int, float} or not math.isfinite(self.deadline)
                or self.deadline <= 0):
            raise ValueError("Verification deadline must be finite and positive")
        if type(self.code_length) is not int or not 1 <= self.code_length <= 128:
            raise ValueError("Code length must be between 1 and 128")
        if self.subject is not None and (not isinstance(self.subject, str) or not self.subject.strip()):
            raise ValueError("Verification subject must be nonempty when supplied")
        try:
            if not self.code_regex or not self.code_characters:
                raise ValueError("Verification regular expressions must be nonempty")
            re.compile(self.code_regex)
            re.compile(self.code_characters)
        except (re.error, TypeError):
            raise ValueError("Invalid verification regular expression") from None
        object.__setattr__(self, "baseline_ids", frozenset(self.baseline_ids))


@dataclass(frozen=True)
class VerificationResult:
    """A selected code; its value never appears in the normal representation."""

    value: str = field(repr=False)
    message_id: str
    received_at: datetime
    extraction_method: Literal["regex", "gemini"]
    site: str
    account: str = field(repr=False)


class VerificationProvider(Protocol):
    async def baseline(self, sender: str, recipient: str, subject: str | None = None,
                       *, deadline: float) -> frozenset[str]: ...

    async def receive(self, request: VerificationRequest) -> VerificationResult: ...

    async def acknowledge(self, result: VerificationResult, *, success: bool,
                          policy: Literal["never", "on_success"], deadline: float) -> None: ...
