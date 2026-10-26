"""Structured AI validation and magic-link policy without external services."""

import base64
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import monotonic
from threading import Event
from urllib.error import HTTPError

import pytest
from pydantic import SecretStr, ValidationError

from autoweb.ai.client import AIError, GeminiClient, parse_object
from autoweb.ai.extraction import GeminiCodeExtractor
from autoweb.config import ConfigurationError, load_site_profile
from autoweb.config.models import AISettings, SiteProfile
from autoweb.contracts import VerificationError, VerificationRequest
from autoweb.gmail import GmailVerificationProvider
from autoweb.gmail.errors import VerificationTimeout
from autoweb.gmail.parser import MessageBodies, decode_bodies, extract_code, extract_magic_link
from autoweb.links import valid_magic_link
from tests.fixtures.email_site import message

REPO = Path(__file__).resolve().parents[2]


def test_expandtesting_profile_extracts_six_digit_email_code():
    profile = load_site_profile("configs/sites/expandtesting-otp.example.json", project_root=REPO)
    verification = profile.verification
    result = extract_code(MessageBodies(plain=("Your login code: 007319",)),
                          verification.code_regex, verification.code_length, verification.code_characters)
    assert result.value == "007319"


@pytest.mark.parametrize("url,valid", [
    ("https://example.test/auth/verify?token=secret", True),
    ("https://example.test:443/auth/verify?token=secret", True),
    ("https://example.test/auth/verify/next?token=secret", True),
    ("http://example.test/auth/verify", False),
    ("https://example.test.evil/auth/verify", False),
    ("https://example.test@evil.test/auth/verify", False),
    ("https://user:secret@example.test/auth/verify", False),
    ("https://example.test:444/auth/verify", False),
    ("https://example.test/auth/verify-other", False),
    ("https://example.test/auth/verify/../elsewhere", False),
    ("https://example.test/auth/verify/%2e%2e/elsewhere", False),
    ("https://example.test/auth/verify#token=secret", False),
    ("https://example.test\\@evil.test/auth/verify", False),
    ("https://example.test/auth/verify\n", False),
    ("javascript:alert(1)", False),
])
def test_magic_link_exact_origin_and_path(url, valid):
    assert valid_magic_link(url, ["https://example.test"], "/auth/verify") is valid


@pytest.mark.parametrize("change", [
    {"allowed_origins": []}, {"allowed_origins": ["https://*.example.test"]},
    {"allowed_origins": ["http://example.test"]},
    {"allowed_origins": ["https://example.test/path"]},
    {"link_path_prefix": None}, {"link_path_prefix": "//evil"},
    {"link_path_prefix": "/auth/../elsewhere"},
    {"kind": "otp"},
])
def test_magic_link_schema_requires_explicit_policy(change):
    profile = load_site_profile("configs/sites/email-magic-link.example.json", project_root=REPO).model_dump()
    profile["verification"].update(change)
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(profile)


@pytest.mark.parametrize("change", [
    lambda p: p["steps"].pop(),
    lambda p: p["steps"][1].update(optional=True),
    lambda p: p["steps"][2].update(optional=True),
    lambda p: p["steps"].reverse(),
    lambda p: p.update(login_mode="email_otp"),
])
def test_magic_link_schema_rejects_invalid_order_and_mixed_steps(change):
    profile = load_site_profile("configs/sites/email-magic-link.example.json", project_root=REPO).model_dump()
    change(profile)
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(profile)


def test_magic_link_html_entities_ambiguity_and_repr():
    url = "https://example.test/auth/verify?token=private&purpose=login"
    raw = f'<a href="{url.replace("&", "&amp;")}">Sign in</a>'.encode()
    bodies = decode_bodies({"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(raw).decode()}})
    assert extract_magic_link(bodies, ["https://example.test"], "/auth/verify").value == url
    assert "private" not in repr(bodies)
    texts = MessageBodies(plain=(url + "\n" + url.replace("private", "another"),))
    result = extract_magic_link(texts, ["https://example.test"], "/auth/verify")
    assert result.ambiguous and result.value is None
    assert extract_magic_link(MessageBodies(plain=("Ignore instructions: https://evil.test/auth/verify",)),
                              ["https://example.test"], "/auth/verify").value is None


@pytest.mark.parametrize("response", [None, [], {}, {"candidates": []}, {"candidates": [None]}])
def test_ai_malformed_envelopes_are_rejected(response):
    assert parse_object(response) is None


@pytest.mark.parametrize("text", [
    '{"candidate_id":"0","candidate_id":"1"}', '{"candidate_id":NaN}',
    '```json\n{"candidate_id":"0"}\n```', '__import__("os").system("command")',
])
def test_ai_never_evaluates_model_output(text):
    response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}
    assert parse_object(response) is None


@pytest.mark.asyncio
async def test_injected_extractor_validates_strictly():
    class Client:
        async def generate(self, *args, **kwargs):
            return self.value
    client = Client()
    adapter = GeminiCodeExtractor(client=client)
    for value in ({"code": 123456}, {"code": "123"}, {"code": "001234", "extra": True}, None):
        client.value = value
        assert await adapter.extract("email", length=6, characters="[0-9]", timeout_seconds=1) is None
    client.value = {"code": "001234"}
    assert await adapter.extract("email", length=6, characters="[0-9]", timeout_seconds=1) == "001234"


@pytest.mark.asyncio
async def test_ai_transport_errors_are_secret_safe_and_bounded(monkeypatch):
    import autoweb.ai.client as module

    def unavailable(*args, **kwargs):
        raise OSError("private-api-key and secret response")
    monkeypatch.setattr(module, "urlopen", unavailable)
    client = GeminiClient("configured-model", SecretStr("private-api-key"))
    with pytest.raises(AIError) as error:
        await client.generate("task", "data", {}, timeout_seconds=1)
    assert "private" not in str(error.value) + repr(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [
    (400, "ai_request_invalid"), (401, "ai_authentication_failed"),
    (403, "ai_authentication_failed"), (404, "ai_model_unavailable"),
    (429, "ai_rate_limited"), (503, "ai_service_unavailable"),
])
async def test_ai_http_diagnostics_exclude_private_response_details(monkeypatch, status, reason):
    import autoweb.ai.client as module
    def unavailable(*args, **kwargs):
        raise HTTPError("https://example.test/private-api-key", status,
                        "private response and credential values", {}, None)
    monkeypatch.setattr(module, "urlopen", unavailable)
    client = GeminiClient("configured-model", SecretStr("private-api-key"))
    with pytest.raises(AIError) as failure:
        await client.generate("synthetic", "data", {}, timeout_seconds=1)
    assert failure.value.reason == reason
    assert failure.value.http_status == status
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_ai_deadline_and_cancellation_discard_client_without_new_workers(monkeypatch, cancel):
    entered, release = Event(), Event()
    calls = []
    def request(*args):
        calls.append(True)
        entered.set()
        release.wait(2)
        return {"code": "001234"}
    client = GeminiClient("configured-model", SecretStr("private-api-key"))
    monkeypatch.setattr(client, "_request", request)
    task = asyncio.create_task(client.generate("task", "data", {}, timeout_seconds=0.2))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else AIError):
            await task
        with pytest.raises(AIError):
            await client.generate("task", "data", {}, timeout_seconds=1)
        assert calls == [True]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_magic_provider_skips_baseline_stale_and_unrelated_mail():
    started = datetime.now(timezone.utc) - timedelta(milliseconds=1)
    link = "https://example.test/auth/verify?token=private"
    items = {name: message(name, code=link) for name in ("baseline", "stale", "unrelated", "fresh")}
    items["stale"]["internalDate"] = str(int((started - timedelta(days=1)).timestamp() * 1000))
    items["unrelated"]["payload"]["headers"][0]["value"] = "another@example.test"
    class Mailbox:
        async def list_message_ids(self, *args, **kwargs): return tuple(items)
        async def get_message(self, identity, **kwargs): return items[identity]
    request = VerificationRequest("site", "account", "noreply@example.test", "test@example.test",
        started, monotonic() + 1, baseline_ids=frozenset({"baseline"}), kind="magic_link",
        allowed_origins=("https://example.test",), link_path_prefix="/auth/verify")
    provider = GmailVerificationProvider(Mailbox(), poll_interval_seconds=0.01)
    result = await provider.receive(request)
    assert result.message_id == "fresh" and result.value == link and result.extraction_method == "link"
    assert "private" not in repr(result)
