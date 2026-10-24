"""Public report and fake-provider contracts."""

import json
from time import monotonic
from datetime import datetime, timezone

import pytest

from autoweb.contracts import ActionResult, VerificationError, VerificationRequest
from autoweb.login import LoginResult, StepResult
from autoweb.reporting import write_login_report
from autoweb.testing import FakeVerificationProvider


def test_report_serializes_only_concise_outcomes(tmp_path):
    result = LoginResult("example", "failed", 1.23456,
                         (StepResult(1, "fill", ActionResult("fill", "succeeded", 0.12345)),),
                         failed_step="authentication", reason="credentials_rejected",
                         session=object())
    path = write_login_report(result, tmp_path / "nested" / "login.json")
    data = json.loads(path.read_text())
    assert data["elapsed_seconds"] == 1.235
    assert data["reason"] == "credentials_rejected"
    assert data["steps"] == [{"index": 1, "action": "fill", "status": "succeeded",
                             "reason": None, "elapsed_seconds": 0.123}]
    assert "session" not in data and "secrets" not in data


@pytest.mark.asyncio
async def test_fake_provider_requires_explicit_values_and_preserves_leading_zeros():
    provider = FakeVerificationProvider()
    request = VerificationRequest("example", "account@example.test", "sender@example.test",
                                  "account@example.test", datetime.now(timezone.utc), monotonic() + 5)
    assert await provider.baseline(request.sender, request.recipient, deadline=request.deadline) == frozenset()
    with pytest.raises(VerificationError, match="fake_verification_empty"):
        await provider.receive(request)
    provider.enqueue("001234", "005678")
    result = await provider.receive(request)
    assert result.value == "001234" and "001234" not in repr(provider) + repr(result)
    await provider.acknowledge(result, success=False, policy="on_success", deadline=request.deadline)
    await provider.acknowledge(result, success=True, policy="never", deadline=request.deadline)
    assert not provider.acknowledged
    await provider.acknowledge(result, success=True, policy="on_success", deadline=request.deadline)
    assert provider.acknowledged == [result.message_id]
    assert (await provider.receive(request)).value == "005678"
