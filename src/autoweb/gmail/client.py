"""Async Gmail API boundary with serialized workers and per-call transports."""

from __future__ import annotations

import asyncio
import json
import math
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import Protocol

from .errors import (
    GmailAPIError, GmailAuthenticationError, GmailMessageMissing, VerificationTimeout,
)


class MessageClient(Protocol):
    async def list_message_ids(self, query: str, *, deadline: float) -> tuple[str, ...]: ...
    async def get_message(self, message_id: str, *, deadline: float) -> dict: ...
    async def mark_read(self, message_id: str, *, deadline: float) -> None: ...


def _classify(error: Exception):
    status = getattr(getattr(error, "resp", None), "status", None)
    if status == 401:
        return GmailAuthenticationError("oauth_rejected")
    if status == 404:
        return GmailMessageMissing("message_missing")
    if status == 403:
        try:
            reasons = {item.get("reason") for item in
                       json.loads(error.content).get("error", {}).get("errors", [])}
        except (AttributeError, TypeError, ValueError):
            reasons = set()
        if reasons.intersection({"rateLimitExceeded", "userRateLimitExceeded", "backendError"}):
            return GmailAPIError("api_rate_limited", retryable=True)
        return GmailAuthenticationError("mailbox_access_denied")
    if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
        return GmailAPIError("api_unavailable", retryable=True)
    if isinstance(error, (OSError, TimeoutError)):
        return GmailAPIError("api_transport_failed", retryable=True)
    # RefreshError is imported only during real operations, not package imports.
    if type(error).__name__ == "RefreshError":
        return GmailAuthenticationError("token_refresh_failed")
    return GmailAPIError("api_request_failed")


class GmailClient:
    def __init__(self, credentials, *, request_timeout_seconds: float = 30,
                 max_retries: int = 2):
        if (not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0
                or type(max_retries) is not int or max_retries < 0):
            raise ValueError("Invalid Gmail API budgets")
        self._credentials = credentials
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autoweb-gmail")
        self._lock = asyncio.Lock()
        self._closed = False

    def _execute(self, operation: str, parameters: dict, timeout: float):
        try:
            import httplib2
            from google_auth_httplib2 import AuthorizedHttp
            from googleapiclient.discovery import build
        except ImportError:
            raise GmailAuthenticationError("gmail_dependencies_missing") from None
        http = httplib2.Http(timeout=timeout)
        try:
            authorized = AuthorizedHttp(self._credentials, http=http, max_refresh_attempts=0)
            service = build("gmail", "v1", http=authorized, cache_discovery=False,
                            static_discovery=True)
            request = getattr(service.users().messages(), operation)(userId="me", **parameters)
            return request.execute(num_retries=0)
        except Exception as error:
            raise _classify(error) from None
        finally:
            http.close()

    async def _call(self, operation: str, parameters: dict, deadline: float) -> dict:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise VerificationTimeout()
        try:
            await asyncio.wait_for(self._lock.acquire(), remaining)
        except asyncio.TimeoutError:
            raise VerificationTimeout() from None
        try:
            if self._closed:
                raise GmailAPIError("client_closed")
            for attempt in range(self.max_retries + 1):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise VerificationTimeout()
                future = asyncio.get_running_loop().run_in_executor(
                    self._executor, self._execute, operation, parameters,
                    min(self.request_timeout_seconds, remaining),
                )
                # Observe late exceptions if a deadline or cancellation detaches the worker.
                future.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                try:
                    result = await asyncio.wait_for(asyncio.shield(future),
                                                    min(self.request_timeout_seconds, remaining))
                    if not isinstance(result, dict):
                        raise GmailAPIError("api_response_invalid")
                    return result
                except (asyncio.TimeoutError, asyncio.CancelledError) as error:
                    # Python cannot kill a thread. Discard this client; its transport is bounded.
                    self._closed = True
                    self._executor.shutdown(wait=False, cancel_futures=True)
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    if monotonic() >= deadline:
                        raise VerificationTimeout() from None
                    raise GmailAPIError("api_request_timeout") from None
                except GmailAPIError as error:
                    if not error.retryable or attempt == self.max_retries:
                        raise
                    delay = min(0.25 * 2 ** attempt, max(0, deadline - monotonic()))
                    await asyncio.sleep(delay)
            raise GmailAPIError("api_request_failed")
        finally:
            self._lock.release()

    async def list_message_ids(self, query: str, *, deadline: float) -> tuple[str, ...]:
        ids, tokens = set(), set()
        token = None
        while True:
            parameters = {"q": query, "maxResults": 500}
            if token:
                parameters["pageToken"] = token
            response = await self._call("list", parameters, deadline)
            messages = response.get("messages", [])
            if not isinstance(messages, list):
                raise GmailAPIError("api_response_invalid")
            for entry in messages:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise GmailAPIError("api_response_invalid")
                ids.add(entry["id"])
            token = response.get("nextPageToken")
            if token is None or token == "":
                return tuple(sorted(ids))
            if not isinstance(token, str) or token in tokens:
                raise GmailAPIError("api_pagination_invalid")
            tokens.add(token)

    async def get_message(self, message_id: str, *, deadline: float) -> dict:
        return await self._call("get", {"id": message_id, "format": "full"}, deadline)

    async def mark_read(self, message_id: str, *, deadline: float) -> None:
        await self._call("modify", {"id": message_id, "body": {"removeLabelIds": ["UNREAD"]}}, deadline)

    async def aclose(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
