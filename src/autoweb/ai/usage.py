"""Thread-safe token accounting for optional AI calls; prices stay caller-supplied."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable totals for one client lifetime."""

    calls: int = 0
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    total_tokens: int = 0

    def estimated_cost(self, *, input_usd_per_million: float,
                       output_usd_per_million: float) -> float:
        """Return the list-price cost in USD; no pricing table is assumed or cached."""
        prices = (input_usd_per_million, output_usd_per_million)
        if any(not isinstance(price, (int, float)) or isinstance(price, bool)
               or not math.isfinite(price) or price < 0 for price in prices):
            raise ValueError("Token prices must be finite and non-negative")
        cost = (self.prompt_tokens * input_usd_per_million
                + self.candidate_tokens * output_usd_per_million) / 1_000_000
        return round(cost, 8)


class UsageMeter:
    """Accumulate call and token counts from concurrent worker threads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls = self._prompt = self._candidate = self._total = 0

    def record_call(self) -> None:
        with self._lock:
            self._calls += 1

    def record_tokens(self, prompt: int, candidate: int, total: int) -> None:
        """Ignore malformed API metadata rather than corrupting accounting."""
        values = (prompt, candidate, total)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
               for value in values):
            return
        with self._lock:
            self._prompt += prompt
            self._candidate += candidate
            self._total += total

    def snapshot(self) -> UsageSnapshot:
        with self._lock:
            return UsageSnapshot(self._calls, self._prompt, self._candidate, self._total)


def parse_usage(response: object) -> tuple[int, int, int] | None:
    """Read Gemini usageMetadata; return None when absent or malformed."""
    try:
        metadata = response["usageMetadata"]
        values = (metadata["promptTokenCount"], metadata["candidatesTokenCount"],
                  metadata["totalTokenCount"])
    except (KeyError, TypeError):
        return None
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    return values
