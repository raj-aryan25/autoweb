"""Lazy, bounded Gemini REST transport shared by purpose-specific adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from time import monotonic
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr


class AIError(Exception):
    def __init__(self, reason="ai_unavailable", *, http_status=None):
        reasons = {"ai_unavailable", "ai_timeout", "ai_authentication_failed", "ai_model_unavailable",
                   "ai_rate_limited", "ai_request_invalid", "ai_service_unavailable",
                   "ai_transport_failed", "ai_response_too_large"}
        self.reason = reason if reason in reasons else "ai_unavailable"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        status = f"; HTTP {self.http_status}" if self.http_status is not None else ""
        super().__init__(f"Optional AI assistance unavailable ({self.reason}{status})")


class StructuredClient(Protocol):
    async def generate(self, instructions: str, text: str, schema: dict, *,
                       timeout_seconds: float, image: bytes | None = None) -> dict | None: ...


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON value")


def parse_object(response: object) -> dict | None:
    """Reject malformed, blocked, ambiguous, non-JSON, or truncated output."""
    try:
        candidates = response["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 1:
            return None
        candidate = candidates[0]
        if candidate.get("finishReason") != "STOP" or response.get("promptFeedback", {}).get("blockReason"):
            return None
        parts = candidate["content"]["parts"]
        if not isinstance(parts, list) or not parts:
            return None
        if any(not isinstance(part, dict) or not isinstance(part.get("text"), str) for part in parts):
            return None
        text = "".join(part["text"] for part in parts if not part.get("thought"))
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        return value if isinstance(value, dict) else None
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


class GeminiClient:
    """Construction has no I/O; API keys and model output never enter error text."""

    def __init__(self, model: str, api_key: SecretStr):
        if not isinstance(model, str) or not isinstance(api_key, SecretStr):
            raise ValueError("Gemini requires an explicit model and API key")
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model) or not api_key.get_secret_value().strip():
            raise ValueError("Gemini requires an explicit model and API key")
        self._model, self._key = model, api_key
        self._lock = asyncio.Lock()
        self._unavailable = False

    def _request(self, instructions, text, schema, image, timeout):
        parts = [{"text": text[:20_000]}]
        if image is not None:
            parts.append({"inlineData": {"mimeType": "image/png",
                                         "data": base64.b64encode(image).decode("ascii")}})
        payload = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": schema, "temperature": 0},
        }
        request = Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key.get_secret_value()},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read(100_001)
                if len(data) > 100_000:
                    raise AIError("ai_response_too_large")
                return parse_object(json.loads(data))
        except AIError:
            raise
        except HTTPError as error:
            reason = {400: "ai_request_invalid", 401: "ai_authentication_failed",
                      403: "ai_authentication_failed", 404: "ai_model_unavailable",
                      429: "ai_rate_limited"}.get(error.code, "ai_request_invalid" if 400 <= error.code < 500 else "ai_service_unavailable")
            status = error.code
            error.close()
            raise AIError(reason, http_status=status) from None
        except TimeoutError:
            raise AIError("ai_timeout") from None
        except (URLError, OSError):
            raise AIError("ai_transport_failed") from None
        except Exception:
            raise AIError() from None

    async def generate(self, instructions, text, schema, *, timeout_seconds, image=None):
        if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or self._unavailable
                or (image is not None and len(image) > 5_000_000)):
            raise AIError()
        deadline = monotonic() + timeout_seconds
        try:
            async def operation():
                async with self._lock:
                    if self._unavailable:
                        raise AIError()
                    return await asyncio.to_thread(self._request, instructions, text, schema,
                                                   image, max(0.001, deadline - monotonic()))
            return await asyncio.wait_for(operation(), timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            # A blocking transport cannot be killed. Discard the client on timeout
            # rather than starting additional workers on subsequent fallback calls.
            self._unavailable = True
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AIError("ai_timeout") from None


def client_from_configuration(configuration) -> GeminiClient:
    return GeminiClient(configuration.settings.ai.model, configuration.secrets["ai:api_key"])
