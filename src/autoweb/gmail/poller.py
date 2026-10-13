"""Fresh-message correlation and asynchronous verification polling."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from email.utils import getaddresses
from time import monotonic
from typing import Literal

from autoweb.ai.extraction import AIExtractionError, ExtractionFallback
from autoweb.contracts import VerificationRequest, VerificationResult
from .client import MessageClient
from .errors import GmailError, GmailMessageMissing, VerificationTimeout
from .parser import decode_bodies, extract_code, headers, valid_code


def _address(value: str) -> str:
    addresses = getaddresses([value])
    if len(addresses) != 1 or not re.fullmatch(r'[^@\s<>"()]+@[^@\s<>"()]+', addresses[0][1]):
        raise ValueError("Verification filters require one email address")
    return addresses[0][1].casefold()


def _quoted(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Invalid verification filter")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_query(sender: str, recipient: str, subject: str | None = None,
                *, since: datetime | None = None) -> str:
    recipient_value = _quoted(_address(recipient))
    parts = [f"from:{_quoted(_address(sender))}",
             "{" + " ".join(f"{key}:{recipient_value}" for key in ("to", "cc", "deliveredto")) + "}"]
    if subject is not None:
        parts.append(f"subject:{_quoted(subject)}")
    if since is not None:
        # Gmail search seconds are coarse; metadata performs the precise comparison.
        parts.append(f"after:{int(since.timestamp()) - 1}")
    return " ".join(parts)


def matching_timestamp(message: dict, request: VerificationRequest) -> datetime | None:
    payload = message.get("payload")
    if not isinstance(payload, dict) or not isinstance(message.get("id"), str):
        return None
    try:
        received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None
    if not request.challenge_started_at <= received <= datetime.now(timezone.utc):
        return None
    meta = headers(payload)
    senders = [address.casefold() for _, address in getaddresses(meta.get("from", []))]
    recipients = [address.casefold() for _, address in getaddresses([
        value for key in ("to", "cc", "bcc", "delivered-to", "x-original-to")
        for value in meta.get(key, [])
    ])]
    if senders != [_address(request.sender)] or _address(request.recipient) not in recipients:
        return None
    if request.subject is not None and not any(
        request.subject.casefold() in value.casefold() for value in meta.get("subject", [])
    ):
        return None
    return received


class GmailVerificationProvider:
    """Sequential provider. Results are reserved on selection and acknowledged explicitly."""

    def __init__(self, client: MessageClient, *, poll_interval_seconds: float = 5,
                 request_timeout_seconds: float = 30,
                 fallback: ExtractionFallback | None = None):
        if any(not math.isfinite(value) or value <= 0
               for value in (poll_interval_seconds, request_timeout_seconds)):
            raise ValueError("Gmail polling budgets must be finite and positive")
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.fallback = fallback
        self._selected: dict[str, VerificationResult] = {}
        self._acknowledged: set[str] = set()
        self._receiving = False

    async def baseline(self, sender: str, recipient: str, subject: str | None = None,
                       *, deadline: float) -> frozenset[str]:
        return frozenset(await self.client.list_message_ids(
            build_query(sender, recipient, subject), deadline=deadline,
        ))

    async def _receive(self, request: VerificationRequest) -> VerificationResult:
        query = build_query(request.sender, request.recipient, request.subject,
                            since=request.challenge_started_at)
        inspected = set(request.baseline_ids) | set(self._selected)
        while monotonic() < request.deadline:
            ids = await self.client.list_message_ids(query, deadline=request.deadline)
            candidates = []
            for message_id in ids:
                if message_id in inspected:
                    continue
                try:
                    message = await self.client.get_message(message_id, deadline=request.deadline)
                except GmailMessageMissing:
                    inspected.add(message_id)
                    continue
                inspected.add(message_id)
                if message.get("id") != message_id:
                    continue
                timestamp = matching_timestamp(message, request)
                if timestamp is not None:
                    candidates.append((timestamp, message_id, message))
            # Actual received timestamps, not API list order, decide candidate priority.
            for timestamp, message_id, message in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True):
                if monotonic() >= request.deadline:
                    raise VerificationTimeout()
                bodies = decode_bodies(message["payload"])
                extracted = extract_code(bodies, request.code_regex, request.code_length,
                                         request.code_characters)
                value, method = extracted.value, "regex"
                if value is None and not extracted.ambiguous and self.fallback and (bodies.plain or bodies.html):
                    timeout = min(self.request_timeout_seconds, request.deadline - monotonic())
                    try:
                        suggestion = await asyncio.wait_for(self.fallback.extract(
                            "\n".join(bodies.plain or bodies.html), length=request.code_length,
                            characters=request.code_characters, timeout_seconds=timeout,
                        ), timeout)
                    except (AIExtractionError, asyncio.TimeoutError):
                        suggestion = None
                    if valid_code(suggestion, request.code_length, request.code_characters):
                        value, method = suggestion, "gemini"
                if monotonic() >= request.deadline:
                    raise VerificationTimeout()
                if value is not None:
                    result = VerificationResult(value, message_id, timestamp, method,
                                                request.site, request.account)
                    self._selected[message_id] = result
                    return result
            await asyncio.sleep(min(self.poll_interval_seconds, max(0, request.deadline - monotonic())))
        raise VerificationTimeout()

    async def receive(self, request: VerificationRequest) -> VerificationResult:
        if self._receiving:
            raise GmailError("concurrent_mailbox_attempt_unsupported")
        self._receiving = True
        try:
            return await self._receive(request)
        finally:
            self._receiving = False

    async def acknowledge(self, result: VerificationResult, *, success: bool,
                          policy: Literal["never", "on_success"], deadline: float) -> None:
        if policy not in {"never", "on_success"}:
            raise ValueError("Unsupported acknowledgment policy")
        if not success or policy == "never":
            return
        if self._selected.get(result.message_id) != result:
            raise GmailError("verification_result_not_owned")
        if result.message_id not in self._acknowledged:
            await self.client.mark_read(result.message_id, deadline=deadline)
            self._acknowledged.add(result.message_id)
