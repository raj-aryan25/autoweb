"""Token accounting for optional AI calls, including malformed API metadata."""

import json
from urllib.error import HTTPError

import pytest
from pydantic import SecretStr

from autoweb.ai.client import AIError, GeminiClient
from autoweb.ai.usage import UsageMeter, UsageSnapshot, parse_usage
from autoweb.login import LoginResult
from autoweb.reporting import login_report

VALID = {"candidates": [{"finishReason": "STOP",
                         "content": {"parts": [{"text": '{"candidate_id":"0"}'}]}}]}


class Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode()

    def read(self, limit):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def client_with(monkeypatch, payload: dict) -> GeminiClient:
    import autoweb.ai.client as module

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: Response(payload))
    return GeminiClient("configured-model", SecretStr("private-api-key"))


@pytest.mark.asyncio
async def test_client_records_calls_and_prompt_and_candidate_tokens(monkeypatch):
    payload = dict(VALID, usageMetadata={"promptTokenCount": 120,
                                         "candidatesTokenCount": 8,
                                         "totalTokenCount": 128})
    client = client_with(monkeypatch, payload)
    assert await client.generate("task", "data", {}, timeout_seconds=2) == {"candidate_id": "0"}
    usage = client.usage
    assert (usage.calls, usage.prompt_tokens, usage.candidate_tokens, usage.total_tokens) == (
        1, 120, 8, 128)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {}, {"promptTokenCount": "120",
                                                "candidatesTokenCount": 8,
                                                "totalTokenCount": 128},
                                      {"promptTokenCount": -1, "candidatesTokenCount": 8,
                                       "totalTokenCount": 7}])
async def test_missing_or_malformed_usage_never_breaks_a_call(monkeypatch, metadata):
    payload = dict(VALID)
    if metadata is not None:
        payload["usageMetadata"] = metadata
    client = client_with(monkeypatch, payload)
    assert await client.generate("task", "data", {}, timeout_seconds=2) == {"candidate_id": "0"}
    usage = client.usage
    assert usage.calls == 1 and usage.prompt_tokens == 0 and usage.total_tokens == 0


@pytest.mark.asyncio
async def test_failed_request_still_counts_as_one_attempt(monkeypatch):
    import autoweb.ai.client as module

    def unavailable(*args, **kwargs):
        raise HTTPError("https://example.test/private", 503, "private", {}, None)
    monkeypatch.setattr(module, "urlopen", unavailable)
    client = GeminiClient("configured-model", SecretStr("private-api-key"))
    with pytest.raises(AIError):
        await client.generate("task", "data", {}, timeout_seconds=2)
    assert client.usage.calls == 1 and client.usage.total_tokens == 0


def test_parse_usage_rejects_non_integer_and_negative_counts():
    assert parse_usage({"usageMetadata": {"promptTokenCount": True,
                                          "candidatesTokenCount": 1,
                                          "totalTokenCount": 2}}) is None
    assert parse_usage({}) is None
    assert parse_usage([]) is None


def test_usage_meter_accumulates_and_ignores_invalid_tokens():
    meter = UsageMeter()
    meter.record_call()
    meter.record_tokens(10, 2, 12)
    meter.record_tokens(-1, 2, 3)
    assert meter.snapshot() == UsageSnapshot(1, 10, 2, 12)


def test_cost_uses_caller_supplied_prices_only():
    usage = UsageSnapshot(calls=2, prompt_tokens=1_000_000, candidate_tokens=500_000,
                          total_tokens=1_500_000)
    assert usage.estimated_cost(input_usd_per_million=0.1,
                                output_usd_per_million=0.4) == pytest.approx(0.3)
    for price in (-0.1, float("inf"), "0.1"):
        with pytest.raises(ValueError):
            usage.estimated_cost(input_usd_per_million=price, output_usd_per_million=0.4)


def test_report_includes_ai_usage_only_when_present():
    usage = UsageSnapshot(calls=2, prompt_tokens=10, candidate_tokens=4, total_tokens=14)
    assert "ai" not in login_report(LoginResult("site", "succeeded", 1.0))
    report = login_report(LoginResult("site", "succeeded", 1.0, ai_usage=usage))
    assert report["ai"] == {"calls": 2, "prompt_tokens": 10, "candidate_tokens": 4,
                            "total_tokens": 14}
