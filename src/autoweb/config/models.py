"""Validated settings and declarative login profiles; no external clients."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlsplit

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveNumber = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
CredentialName = Literal["username", "email", "password"]


def _validate_http_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and not any(character.isspace() for character in value)
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("URL must be absolute HTTP(S) without credentials")


def _origin(value: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class EnvironmentReference(Model):
    env: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class BrowserSettings(Model):
    engine: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = Field(default=True, strict=True)
    slow_mo_ms: Annotated[int, Field(strict=True, ge=0)] = 0
    launch_timeout_ms: PositiveInt = 30000


class TimeoutSettings(Model):
    action_ms: PositiveInt = 5000
    navigation_ms: PositiveInt = 30000
    login_seconds: PositiveNumber = 120
    verification_seconds: PositiveNumber = 60

    @model_validator(mode="after")
    def check_budget(self) -> TimeoutSettings:
        if self.verification_seconds > self.login_seconds:
            raise ValueError("verification timeout cannot exceed the login timeout")
        return self


class GmailSettings(Model):
    credentials_file: Path = Path("secrets/gmail/credentials.json")
    token_file: Path = Path("secrets/gmail/token.json")
    scopes: list[NonEmpty] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/gmail.modify"],
        min_length=1,
    )
    poll_interval_seconds: PositiveNumber = 5
    request_timeout_seconds: PositiveNumber = 30


class AISettings(Model):
    enabled: bool = Field(default=False, strict=True)
    model: NonEmpty | None = None
    api_key: EnvironmentReference = Field(
        default_factory=lambda: EnvironmentReference(env="GOOGLE_API_KEY")
    )

    @model_validator(mode="after")
    def check_enabled_model(self) -> AISettings:
        if self.enabled and self.model is None:
            raise ValueError("AI requires an explicit model when enabled")
        return self


class RuntimeSettings(Model):
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    ai: AISettings = Field(default_factory=AISettings)
    artifacts_dir: Path = Path("artifacts")


class Locator(Model):
    by: Literal["role", "label", "placeholder", "text", "css", "test_id"]
    value: NonEmpty
    name: NonEmpty | None = None
    exact: bool = Field(default=True, strict=True)
    frame_path: list[NonEmpty] = Field(default_factory=list)
    scope: NonEmpty | None = None

    @model_validator(mode="after")
    def check_role_name(self) -> Locator:
        if self.name is not None and self.by != "role":
            raise ValueError("name is only supported for a role locator")
        return self


class Step(Model):
    optional: bool = Field(default=False, strict=True)
    timeout_ms: PositiveInt | None = None


class ClickStep(Step):
    action: Literal["click", "submit", "accept_cookies", "open_login"]
    target: Locator


class FillStep(Step):
    action: Literal["fill"]
    target: Locator
    credential: CredentialName


class WaitStep(Step):
    action: Literal["wait_for"]
    target: Locator
    state: Literal["visible", "hidden", "attached", "detached"] = "visible"


class RequestOTPStep(Step):
    action: Literal["request_otp"]
    target: Locator


class FillOTPStep(Step):
    action: Literal["fill_otp"]
    targets: list[Locator] = Field(min_length=1)


LoginStep = Annotated[
    Union[ClickStep, FillStep, WaitStep, RequestOTPStep, FillOTPStep],
    Field(discriminator="action"),
]


class ElementAssertion(Model):
    type: Literal["element"]
    target: Locator
    state: Literal["visible", "hidden"] = "visible"


class URLAssertion(Model):
    type: Literal["url"]
    pattern: NonEmpty


class APIAssertion(Model):
    type: Literal["api"]
    url: NonEmpty
    expected_status: Annotated[int, Field(strict=True, ge=100, le=599)] = 200
    expected_json: dict[NonEmpty, Any] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def check_api_url(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("API URLs must use forward slashes")
        if value.startswith("/") and not value.startswith("//"):
            if any(character.isspace() for character in value):
                raise ValueError("API URL cannot contain whitespace")
            return value
        _validate_http_url(value)
        return value

    @field_validator("expected_json")
    @classmethod
    def check_expected_json(cls, value: dict) -> dict:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("expected_json must contain finite JSON values") from None
        return value


Assertion = Annotated[
    Union[ElementAssertion, URLAssertion, APIAssertion], Field(discriminator="type"),
]


class VerificationSettings(Model):
    sender: NonEmpty
    subject: NonEmpty | None = None
    recipient: EnvironmentReference
    code_regex: NonEmpty = r"\b\d{6}\b"
    code_length: Annotated[int, Field(strict=True, gt=0, le=128)] = 6
    code_characters: NonEmpty = r"[0-9]"
    timeout_seconds: PositiveNumber | None = None
    acknowledge: Literal["never", "on_success"] = "on_success"

    @field_validator("code_regex", "code_characters")
    @classmethod
    def check_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error:
            raise ValueError("code_regex must be a valid regular expression") from None
        return value


class SiteProfile(Model):
    schema_version: Literal[1] = 1
    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    entry_url: NonEmpty
    login_mode: Literal["password", "email_otp", "password_email_otp"]
    credentials: dict[CredentialName, EnvironmentReference]
    steps: list[LoginStep] = Field(min_length=1)
    success: list[Assertion] = Field(min_length=1)
    failure: list[Assertion] = Field(default_factory=list)
    verification: VerificationSettings | None = None

    @field_validator("entry_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        _validate_http_url(value)
        return value

    @model_validator(mode="after")
    def check_flow(self) -> SiteProfile:
        for condition in self.success + self.failure:
            if (isinstance(condition, APIAssertion) and not condition.url.startswith("/")
                    and _origin(condition.url) != _origin(self.entry_url)):
                raise ValueError("API assertions must use the login site origin")
        fills = [(index, step) for index, step in enumerate(self.steps)
                 if isinstance(step, FillStep)]
        for _, step in fills:
            if step.credential not in self.credentials:
                raise ValueError("fill step references an undeclared credential")
        required_fills = {step.credential for _, step in fills if not step.optional}
        if not required_fills.intersection({"username", "email"}):
            raise ValueError("login requires a non-optional username or email fill step")
        if not any(isinstance(check, ElementAssertion) and check.state == "visible"
                   for check in self.success):
            raise ValueError("success requires a visible authenticated element assertion")
        if self.login_mode in {"password", "password_email_otp"}:
            if "password" not in required_fills:
                raise ValueError("password login requires a non-optional password fill step")
        requests = [index for index, step in enumerate(self.steps)
                    if isinstance(step, RequestOTPStep)]
        codes = [index for index, step in enumerate(self.steps)
                 if isinstance(step, FillOTPStep)]
        if self.login_mode == "password":
            if self.verification is not None or requests or codes:
                raise ValueError("password-only login cannot include email verification")
            last_fill = max(index for index, step in fills if not step.optional)
            if not any(isinstance(step, ClickStep) and step.action == "submit"
                       and not step.optional for step in self.steps[last_fill + 1:]):
                raise ValueError("password login requires a submit after credential entry")
        else:
            if self.verification is None or len(requests) != 1 or len(codes) != 1:
                raise ValueError("email login requires verification, one request_otp, and one fill_otp")
            request, code = requests[0], codes[0]
            if request >= code or self.steps[request].optional or self.steps[code].optional:
                raise ValueError("required request_otp must precede required fill_otp")
            if any(index >= request for index, step in fills if not step.optional):
                raise ValueError("required credential entry must precede the OTP request")
            if not any(isinstance(step, ClickStep) and step.action == "submit"
                       and not step.optional for step in self.steps[code + 1:]):
                raise ValueError("email login requires a submit after OTP entry")
            targets = self.steps[code].targets
            if len(targets) not in {1, self.verification.code_length}:
                raise ValueError("OTP targets must contain one field or one field per code character")
        return self
