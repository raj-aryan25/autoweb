"""Independent Gmail verification using synthetic saved messages and fake transports."""

import asyncio
import copy
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoweb.ai.extraction import AIExtractionError, GeminiCodeExtractor, parse_response
from autoweb.contracts import VerificationRequest
from autoweb.gmail import GmailAPIError, GmailAuthenticationError, GmailClient, GmailVerificationProvider
from autoweb.gmail.client import _classify
from autoweb.gmail.errors import GmailError, GmailMessageMissing, VerificationTimeout
from autoweb.gmail.parser import MessageBodies, decode_bodies, extract_code
from autoweb.gmail.poller import build_query, matching_timestamp


FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/gmail/messages.json").read_text())
START = datetime.fromtimestamp(1700000000, timezone.utc)


def request(**changes):
    values = dict(site="example", account="test@example.com", sender="noreply@example.com",
                  recipient="test@example.com", subject="Your verification code",
                  challenge_started_at=START, deadline=monotonic() + 1)
    values.update(changes)
    return VerificationRequest(**values)


class FakeClient:
    def __init__(self, batches, messages=None):
        self.batches = list(batches)
        self.messages = copy.deepcopy(messages or FIXTURES)
        self.queries, self.fetched, self.read = [], [], []

    async def list_message_ids(self, query, *, deadline):
        self.queries.append(query)
        batch = self.batches.pop(0) if len(self.batches) > 1 else self.batches[0]
        if isinstance(batch, Exception):
            raise batch
        return tuple(batch)

    async def get_message(self, message_id, *, deadline):
        self.fetched.append(message_id)
        message = self.messages[message_id]
        if isinstance(message, Exception):
            raise message
        return message

    async def mark_read(self, message_id, *, deadline):
        self.read.append(message_id)


def provider(client, **options):
    return GmailVerificationProvider(client, poll_interval_seconds=0.005, **options)


@pytest.mark.parametrize("name", ["plain", "html", "nested", "malformed_nested"])
def test_saved_mime_fixtures_extract_leading_zero_code(name):
    bodies = decode_bodies(FIXTURES[name]["payload"])
    result = extract_code(bodies, r"\b\d{6}\b", 6)
    assert result.value == "001234"
    assert "001234" not in repr(result) + repr(bodies)


@pytest.mark.parametrize("regex", [r"\b\d{6}\b", r"Code: (\d{6})", r"Code: (?P<code>\d{6})"])
def test_regex_supports_full_match_capture_and_named_capture(regex):
    assert extract_code(MessageBodies(("Code: 001234",)), regex, 6).value == "001234"


def test_character_rules_and_ambiguity_are_explicit():
    bodies = decode_bodies(FIXTURES["alpha"]["payload"])
    assert extract_code(bodies, r"Code: ([A-Z0-9]+)", 6, r"[A-Z0-9]").value == "AB01Z9"
    assert extract_code(bodies, r"Code: ([A-Z0-9]+)", 6).value is None
    ambiguous = extract_code(decode_bodies(FIXTURES["ambiguous"]["payload"]), r"\b\d{6}\b", 6)
    assert ambiguous.value is None and ambiguous.ambiguous
    assert extract_code(MessageBodies(("001234 001234",)), r"\b\d{6}\b", 6).value == "001234"
    assert extract_code(MessageBodies(("12345 1234567 １２３４５６",)), r"\d+", 6).value is None


@pytest.mark.parametrize("payload", [
    {"mimeType": None, "headers": None, "body": None},
    {"mimeType": "text/plain", "body": {"data": "%%%"}},
    {"mimeType": "text/plain", "body": {"attachmentId": "not-inline"}},
    {"mimeType": "multipart/mixed", "parts": [None, {}, "bad"]},
    {"mimeType": "text/plain", "filename": "attachment.txt", "body": {"data": "MDAxMjM0"}},
])
def test_malformed_or_attached_payloads_do_not_extract(payload):
    assert extract_code(decode_bodies(payload), r"\b\d{6}\b", 6).value is None


def test_html_links_scripts_and_entities_are_handled_deliberately():
    bodies = decode_bodies(FIXTURES["html"]["payload"])
    assert "654321" not in str(bodies.html)
    assert "999999" not in str(bodies.html)
    assert "888888" not in str(bodies.html)
    assert "\xa0" in bodies.html[0]


@pytest.mark.parametrize("change", [
    {"challenge_started_at": datetime(2020, 1, 1)}, {"deadline": float("nan")},
    {"deadline": 0}, {"code_length": 0}, {"code_regex": "["}, {"code_characters": "["},
    {"account": ""}, {"code_length": True},
])
def test_verification_request_preflight(change):
    with pytest.raises(ValueError):
        request(**change)


def test_query_uses_timestamp_not_unread_and_escapes_subject():
    query = build_query("Verification <noreply@example.com>", "test@example.com", 'Your "code"', since=START)
    assert 'from:"noreply@example.com"' in query and 'to:"test@example.com"' in query
    assert 'subject:"Your \\"code\\""' in query
    assert "after:1699999999" in query and "unread" not in query
    with pytest.raises(ValueError):
        build_query("noreply@example.com OR bad@example.com", "test@example.com")


@pytest.mark.parametrize("kind", ["sender", "recipient", "subject", "stale", "missing_timestamp", "future"])
def test_metadata_filters_exclude_unrelated_and_stale_mail(kind):
    message = copy.deepcopy(FIXTURES["plain"])
    if kind in {"sender", "recipient", "subject"}:
        index = {"sender": 0, "recipient": 1, "subject": 2}[kind]
        message["payload"]["headers"][index]["value"] = "unrelated@example.net"
    elif kind == "stale":
        message["internalDate"] = "1699999999000"
    elif kind == "future":
        message["internalDate"] = str(int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000))
    else:
        message.pop("internalDate")
    assert matching_timestamp(message, request()) is None


def test_recipient_delivery_header_and_encoded_subject_are_supported():
    message = copy.deepcopy(FIXTURES["plain"])
    message["payload"]["headers"][1] = {"name": "Delivered-To", "value": "test@example.com"}
    message["payload"]["headers"][2]["value"] = "=?UTF-8?B?WW91ciB2ZXJpZmljYXRpb24gY29kZQ==?="
    assert matching_timestamp(message, request()) is not None


@pytest.mark.asyncio
async def test_baseline_then_poll_skips_old_malformed_and_continues_to_next_batch():
    client = FakeClient([["plain"], ["plain", "stale", "malformed"], ["plain", "stale", "malformed", "html"]])
    gmail = provider(client)
    baseline = await gmail.baseline("noreply@example.com", "test@example.com", deadline=monotonic() + 1)
    result = await gmail.receive(request(baseline_ids=baseline))
    assert result.value == "001234" and result.message_id == "html"
    assert "plain" not in client.fetched
    assert client.fetched.count("malformed") == 1
    assert not client.read
    assert "001234" not in repr(result)
    await gmail.acknowledge(result, success=False, policy="on_success", deadline=monotonic() + 1)
    await gmail.acknowledge(result, success=True, policy="never", deadline=monotonic() + 1)
    assert not client.read
    await gmail.acknowledge(result, success=True, policy="on_success", deadline=monotonic() + 1)
    await gmail.acknowledge(result, success=True, policy="on_success", deadline=monotonic() + 1)
    assert client.read == ["html"]
    with pytest.raises(GmailError, match="not_owned"):
        await gmail.acknowledge(replace(result, account="another-account"), success=True,
                                policy="on_success", deadline=monotonic() + 1)
    with pytest.raises(VerificationTimeout):
        await gmail.receive(request(deadline=monotonic() + 0.02, baseline_ids=baseline))


@pytest.mark.asyncio
async def test_candidate_order_is_actual_received_time_not_list_order():
    messages = copy.deepcopy(FIXTURES)
    messages["html"]["internalDate"] = "1700000002000"
    client = FakeClient([["plain", "html"]], messages)
    result = await provider(client).receive(request())
    assert result.message_id == "html"


@pytest.mark.asyncio
async def test_deleted_and_ambiguous_candidates_do_not_block_valid_mail():
    client = FakeClient([["gone", "ambiguous", "plain"]])
    client.messages["gone"] = GmailMessageMissing("message_missing")
    result = await provider(client).receive(request())
    assert result.message_id == "plain"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GmailAPIError("api_unavailable"), GmailAuthenticationError("oauth_rejected")])
async def test_mailbox_errors_are_not_empty_mailbox(error):
    with pytest.raises(type(error), match=error.reason):
        await provider(FakeClient([error])).receive(request())


@pytest.mark.asyncio
async def test_empty_mailbox_waits_asynchronously_until_deadline():
    client = FakeClient([[]])
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(10):
            ticks += 1
            await asyncio.sleep(0.001)

    ticker = asyncio.create_task(heartbeat())
    with pytest.raises(VerificationTimeout):
        await provider(client).receive(request(deadline=monotonic() + 0.03))
    await ticker
    assert ticks == 10 and len(client.queries) > 1
    assert not client.read


class Fallback:
    def __init__(self, value):
        self.value, self.calls = value, 0

    async def extract(self, text, **kwargs):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.mark.asyncio
async def test_deterministic_extraction_precedes_ai_and_ambiguity_never_goes_to_ai():
    fallback = Fallback("654321")
    assert (await provider(FakeClient([["plain"]]), fallback=fallback).receive(request())).value == "001234"
    assert fallback.calls == 0
    with pytest.raises(VerificationTimeout):
        await provider(FakeClient([["ambiguous"]]), fallback=fallback).receive(request(deadline=monotonic() + 0.02))
    assert fallback.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["001234", "12", "ABC123", 123456, AIExtractionError()])
async def test_ai_fallback_has_same_length_and_character_validation(value):
    fallback = Fallback(value)
    gmail = provider(FakeClient([["alpha"]]), fallback=fallback)
    if value == "001234":
        result = await gmail.receive(request(deadline=monotonic() + 0.03))
        assert result.value == value and result.extraction_method == "gemini"
    else:
        with pytest.raises(VerificationTimeout):
            await gmail.receive(request(deadline=monotonic() + 0.02))
    assert fallback.calls == 1


@pytest.mark.parametrize("text,expected", [
    ('{"code":"001234"}', "001234"), ('{"code":123456}', None),
    ('{"code":null}', None), ('{"code":"001234","extra":true}', None),
    ('```json\n{"code":"001234"}\n```', None), ('__import__("os")', None),
])
def test_gemini_response_is_strict_structured_json(text, expected):
    response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}
    assert parse_response(response) == expected


@pytest.mark.asyncio
async def test_gemini_adapter_is_lazy_bounded_and_uses_api_key_header(monkeypatch):
    from pydantic import SecretStr
    import autoweb.ai.extraction as module

    captured = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            return json.dumps({"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"code":"001234"}'}]}}]}).encode()

    def open_request(req, *, timeout):
        captured.append((req, timeout))
        return Response()

    monkeypatch.setattr(module, "urlopen", open_request)
    extractor = GeminiCodeExtractor("configured-model", SecretStr("private-api-key"))
    assert not captured and "private-api-key" not in repr(extractor)
    assert await extractor.extract("email", length=6, characters=r"[0-9]", timeout_seconds=1) == "001234"
    req, timeout = captured[0]
    assert "private-api-key" not in req.full_url and timeout == 1
    assert req.get_header("X-goog-api-key") == "private-api-key"


@pytest.mark.asyncio
async def test_client_pagination_deduplicates_and_detects_repeating_tokens(monkeypatch):
    client = GmailClient(None)
    calls = []
    responses = [{"messages": [{"id": "b"}], "nextPageToken": "next"},
                 {"messages": [{"id": "b"}, {"id": "a"}]}]

    def execute(operation, parameters, timeout):
        calls.append((operation, parameters, timeout))
        return responses.pop(0)

    monkeypatch.setattr(client, "_execute", execute)
    async with client:
        assert await client.list_message_ids("query", deadline=monotonic() + 1) == ("a", "b")
        assert calls[1][1]["pageToken"] == "next"
        responses.extend([{"nextPageToken": "same"}, {"nextPageToken": "same"}])
        with pytest.raises(GmailAPIError, match="pagination_invalid"):
            await client.list_message_ids("query", deadline=monotonic() + 1)


@pytest.mark.asyncio
async def test_client_retries_are_bounded_and_errors_are_preserved(monkeypatch):
    client = GmailClient(None, max_retries=1)
    calls = []

    def execute(*args):
        calls.append(args)
        raise GmailAPIError("api_unavailable", retryable=True)

    monkeypatch.setattr(client, "_execute", execute)
    async with client:
        with pytest.raises(GmailAPIError, match="api_unavailable"):
            await client.get_message("id", deadline=monotonic() + 2)
    assert len(calls) == 2


@pytest.mark.parametrize("status,error_type,retryable", [
    (401, GmailAuthenticationError, False), (403, GmailAuthenticationError, False),
    (404, GmailMessageMissing, False), (429, GmailAPIError, True),
    (503, GmailAPIError, True), (400, GmailAPIError, False),
])
def test_api_error_classification_does_not_expose_response(status, error_type, retryable):
    error = Exception("private response with code 001234")
    error.resp = SimpleNamespace(status=status)
    error.content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'
    result = _classify(error)
    assert isinstance(result, error_type)
    assert getattr(result, "retryable", False) is retryable
    assert "001234" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_inflight_timeout_or_cancellation_discards_client_without_blocking_loop(monkeypatch, cancel):
    client = GmailClient(None, request_timeout_seconds=0.02)
    started, release = threading.Event(), threading.Event()

    def execute(*args):
        started.set()
        release.wait(1)
        return {}

    monkeypatch.setattr(client, "_execute", execute)
    task = asyncio.create_task(client.get_message("id", deadline=monotonic() + 1))
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(GmailAPIError, match="api_request_timeout"):
                await task
        with pytest.raises(GmailAPIError, match="client_closed"):
            await client.get_message("id", deadline=monotonic() + 1)
    finally:
        release.set()
        await client.aclose()


@pytest.mark.asyncio
async def test_provider_rejects_parallel_receive_and_can_resume_after_cancellation():
    gmail = provider(FakeClient([[]]))
    pending = asyncio.create_task(gmail.receive(request()))
    await asyncio.sleep(0)
    with pytest.raises(GmailError, match="concurrent"):
        await gmail.receive(request())
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with pytest.raises(VerificationTimeout):
        await gmail.receive(request(deadline=monotonic() + 0.01))


def test_charset_and_inline_html_digits_decode_without_losing_boundaries():
    import base64
    plain = {"mimeType": "text/plain", "headers": [
        {"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}],
        "body": {"data": base64.urlsafe_b64encode("Vérification: 001234".encode("latin1")).decode().rstrip("=")}}
    assert extract_code(decode_bodies(plain), r"\b\d{6}\b", 6).value == "001234"
    html = {"mimeType": "text/html", "body": {
        "data": base64.urlsafe_b64encode(b"<p>Code: <b>00</b><span>1234</span></p>").decode()}}
    assert extract_code(decode_bodies(html), r"\b\d{6}\b", 6).value == "001234"


def test_real_execute_constructs_fresh_bounded_transports_and_closes_them(monkeypatch):
    import sys

    transports, requests = [], []

    def http(*, timeout):
        transport = SimpleNamespace(timeout=timeout, close=Mock())
        transports.append(transport)
        return transport

    def authorized(credentials, *, http, max_refresh_attempts):
        assert credentials == "synthetic" and max_refresh_attempts == 0
        return http

    def get(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(execute=Mock(return_value={"id": kwargs["id"]}))

    messages = SimpleNamespace(get=get)
    service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: messages))
    build = Mock(return_value=service)
    monkeypatch.setitem(sys.modules, "httplib2", SimpleNamespace(Http=http))
    monkeypatch.setitem(sys.modules, "google_auth_httplib2", SimpleNamespace(AuthorizedHttp=authorized))
    monkeypatch.setitem(sys.modules, "googleapiclient", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", SimpleNamespace(build=build))
    client = GmailClient("synthetic")
    try:
        assert client._execute("get", {"id": "first", "format": "full"}, 0.1) == {"id": "first"}
        assert client._execute("get", {"id": "second", "format": "full"}, 0.2) == {"id": "second"}
        assert len(transports) == 2 and transports[0] is not transports[1]
        assert requests[0]["userId"] == "me"
        assert transports[0].timeout == 0.1 and transports[1].timeout == 0.2
        assert all(transport.close.call_count == 1 for transport in transports)
        assert build.call_args.kwargs["static_discovery"] is True
        assert build.call_args.kwargs["cache_discovery"] is False
    finally:
        client._executor.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {"messages": None}, {"messages": [{}]}, {"nextPageToken": 123}])
async def test_invalid_api_responses_are_explicit_errors(monkeypatch, response):
    async with GmailClient(None) as client:
        monkeypatch.setattr(client, "_execute", lambda *args: response)
        with pytest.raises(GmailAPIError):
            await client.list_message_ids("query", deadline=monotonic() + 1)


@pytest.mark.asyncio
async def test_elapsed_deadline_performs_no_api_operation(monkeypatch):
    async with GmailClient(None) as client:
        execute = Mock()
        monkeypatch.setattr(client, "_execute", execute)
        with pytest.raises(VerificationTimeout):
            await client.get_message("id", deadline=monotonic() - 1)
        execute.assert_not_called()


@pytest.mark.asyncio
async def test_slow_ai_fallback_cannot_extend_challenge_deadline():
    class SlowFallback:
        async def extract(self, *args, **kwargs):
            await asyncio.sleep(1)
            return "001234"

    with pytest.raises(VerificationTimeout):
        await provider(FakeClient([["alpha"]]), fallback=SlowFallback()).receive(
            request(deadline=monotonic() + 0.02))
