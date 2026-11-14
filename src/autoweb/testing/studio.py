"""Trusted Studio verification fixtures: synthetic accounts and no external mail.

Profile metadata can be read without binding a socket or creating a provider.
"""

import asyncio
from time import monotonic

from autoweb.config.models import SiteProfile
from .verification import FakeVerificationProvider

DEMO_EMAIL = "test@example.test"


def verification_profile(profile_id: str, origin="http://127.0.0.1:1") -> SiteProfile:
    if profile_id not in {"controlled-otp", "controlled-magic-link"}:
        raise ValueError("Unknown controlled verification profile")
    magic = profile_id == "controlled-magic-link"
    data = {
        "schema_version": 1, "name": profile_id,
        "entry_url": origin + ("/magic-login" if magic else "/email-login"),
        "login_mode": "email_magic_link" if magic else "email_otp",
        "credentials": {"email": {"env": "LOCAL_VERIFICATION_EMAIL"}},
        "steps": [
            {"action": "fill", "target": {"by": "label", "value": "Email"}, "credential": "email"},
            {"action": "request_magic_link" if magic else "request_otp", "target": {
                "by": "role", "value": "button", "name": "Send sign-in link" if magic else "Send code"}},
        ],
        "verification": {
            "sender": "noreply@example.test", "subject": "Your sign-in link" if magic else "Your verification code",
            "recipient": {"env": "LOCAL_VERIFICATION_EMAIL"}, "acknowledge": "on_success",
        },
        "success": [
            {"type": "element", "target": {"by": "test_id", "value": "account-menu"}},
            {"type": "api", "url": "/api/session", "expected_json": {"authenticated": True}},
        ],
    }
    if magic:
        data["steps"].append({"action": "follow_magic_link"})
        data["verification"].update(kind="magic_link", allowed_origins=[origin], link_path_prefix="/magic",
            rejected=[{"type": "element", "target": {"by": "test_id", "value": "link-error"}}],
            expired=[{"type": "element", "target": {"by": "test_id", "value": "link-expired"}}])
    else:
        data["steps"].extend([
            {"action": "fill_otp", "targets": [{"by": "label", "value": "Verification code"}]},
            {"action": "submit", "target": {"by": "role", "value": "button", "name": "Verify"}},
        ])
        data["verification"].update(code_regex=r"\b\d{6}\b", code_length=6,
            rejected=[{"type": "element", "target": {"by": "test_id", "value": "otp-error"}}],
            expired=[{"type": "element", "target": {"by": "test_id", "value": "otp-expired"}}])
    return SiteProfile.model_validate(data)


class FixtureVerificationProvider(FakeVerificationProvider):
    """Receive values queued by the owned server after a real browser request.

    The short delivery delay makes a controlled verification wait inspectable.
    It does not invent UI events or use Gmail. Tests can select a longer delay.
    """

    def __init__(self, *, delivery_seconds=2.0):
        super().__init__()
        self.delivery_seconds = delivery_seconds

    async def receive(self, request):
        while not self._values:
            if monotonic() >= request.deadline:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0.02)
        await asyncio.sleep(self.delivery_seconds)
        return await super().receive(request)
