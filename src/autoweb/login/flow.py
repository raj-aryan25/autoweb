"""Login orchestration with an injected email-verification provider."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from time import monotonic
from typing import AsyncIterator

from pydantic import SecretStr

from autoweb.browser import (
    BrowserActionError, BrowserActionTimeout, BrowserSession, click, fill_otp, navigate, open_session,
)
from autoweb.config import Configuration, ConfigurationError
from autoweb.config.loader import resolve_path
from autoweb.config.models import FillOTPStep, RequestOTPStep
from autoweb.contracts import VerificationError, VerificationProvider

from .assertions import wait_for_authentication, watch_for_rejection
from .diagnostics import capture_failure
from .errors import LoginError
from .results import LoginResult, StepResult
from .steps import execute_step
from .verification import EmailChallenge


def validate_password_login(configuration: Configuration) -> None:
    """Reject unsupported modes and unresolved credentials before opening clients."""
    if configuration.site.login_mode != "password":
        raise LoginError(LoginResult(
            configuration.site.name, "failed", 0,
            failed_step="preflight", reason="unsupported_login_mode",
        ))
    validate_login(configuration)


def validate_login(configuration: Configuration) -> None:
    """Validate resolved secrets before any provider or browser startup."""
    required = [f"credential:{name}" for name in configuration.site.credentials]
    if configuration.site.verification:
        required.append("verification:recipient")
    if any(not isinstance(configuration.secrets.get(key), SecretStr)
           or not configuration.secrets[key].get_secret_value().strip() for key in required):
        raise ConfigurationError("Login requires resolved, nonempty credential references")


async def _guard_steps(operation, session: BrowserSession, configuration: Configuration, deadline: float):
    verification = configuration.site.verification
    if not (configuration.site.failure or (verification and (verification.rejected or verification.expired))):
        return await operation
    steps = asyncio.create_task(operation)
    rejection = asyncio.create_task(watch_for_rejection(session.page, configuration.site, deadline))
    try:
        done, _ = await asyncio.wait([steps, rejection], return_when=asyncio.FIRST_COMPLETED)
        if rejection in done:
            await rejection
        return await steps
    finally:
        for task in (steps, rejection):
            if not task.done():
                task.cancel()
        await asyncio.gather(steps, rejection, return_exceptions=True)


async def run_login(session: BrowserSession, configuration: Configuration,
                    verification_provider: VerificationProvider | None = None) -> LoginResult:
    """Log in using an existing session. Its lifetime remains the caller's responsibility.

    A failure raises LoginError with a structured failed result. Success returns
    the same active session, ready for protected post-login work.
    """
    validate_login(configuration)
    if configuration.site.verification and verification_provider is None:
        raise ConfigurationError("Email login requires a verification provider")
    started = monotonic()
    deadline = started + configuration.settings.timeouts.login_seconds
    completed: list[StepResult] = []
    stage = "navigation"
    challenge = (EmailChallenge(configuration, verification_provider, deadline)
                 if configuration.site.verification else None)

    def active_deadline() -> float:
        return challenge.deadline if challenge else deadline

    def budget_ms(cap: float) -> float:
        remaining = (active_deadline() - monotonic()) * 1000
        if remaining <= 0:
            raise BrowserActionTimeout("login")
        return min(cap, remaining)

    async def steps() -> None:
        nonlocal stage
        for index, step in enumerate(configuration.site.steps, start=1):
            stage = f"step:{index}:{step.action}"
            if isinstance(step, RequestOTPStep):
                # Baseline must finish before the one non-idempotent request click.
                await asyncio.wait_for(challenge.prepare(), active_deadline() - monotonic())
                result = await click(session.page, step.target, timeout_ms=budget_ms(
                    step.timeout_ms or configuration.settings.timeouts.action_ms))
            elif isinstance(step, FillOTPStep):
                selected = await asyncio.wait_for(challenge.receive(), active_deadline() - monotonic())
                result = await fill_otp(
                    session.page, step.targets, SecretStr(selected.value),
                    expected_length=configuration.site.verification.code_length,
                    timeout_ms=budget_ms(step.timeout_ms or configuration.settings.timeouts.action_ms),
                )
            else:
                result = await execute_step(
                    session, step, configuration.secrets,
                    budget_ms(step.timeout_ms or configuration.settings.timeouts.action_ms),
                )
            completed.append(StepResult(index, step.action, result))

    async def workflow() -> None:
        nonlocal stage
        await navigate(
            session.page, configuration.site.entry_url,
            timeout_ms=budget_ms(configuration.settings.timeouts.navigation_ms),
        )
        await _guard_steps(steps(), session, configuration, deadline)
        stage = "authentication"
        await wait_for_authentication(session.page, configuration.site, active_deadline())
        if challenge:
            stage = "acknowledgment"
            await challenge.acknowledge()

    try:
        await asyncio.wait_for(workflow(), timeout=configuration.settings.timeouts.login_seconds)
    except (asyncio.TimeoutError, BrowserActionError, VerificationError) as error:
        elapsed = monotonic() - started
        if monotonic() >= deadline:
            reason = "login_timeout"
        elif challenge and monotonic() >= challenge.deadline:
            reason = "verification_timeout"
        elif isinstance(error, VerificationError):
            reason = error.reason
        elif isinstance(error, asyncio.TimeoutError):
            reason = "login_timeout"
        elif isinstance(error, BrowserActionTimeout):
            reason = "step_timeout"
        else:
            reason = error.reason
        artifacts = await capture_failure(
            session.page, configuration.site,
            resolve_path(configuration.settings.artifacts_dir, configuration.project_root),
            timeout_ms=min(configuration.settings.timeouts.action_ms, 1000),
        )
        raise LoginError(LoginResult(
            configuration.site.name, "failed", elapsed, tuple(completed),
            failed_step=stage, reason=reason, artifacts=artifacts,
        )) from None
    return LoginResult(
        configuration.site.name, "succeeded", monotonic() - started,
        tuple(completed), session=session,
    )


@asynccontextmanager
async def authenticated_session(
    configuration: Configuration, verification_provider: VerificationProvider | None = None,
) -> AsyncIterator[LoginResult]:
    """Own a session, yield it only after proven authentication, and always clean up."""
    validate_login(configuration)
    async with AsyncExitStack() as stack:
        if configuration.site.verification and verification_provider is None:
            # Lazy application wiring. The core runner only knows the protocol.
            from autoweb.gmail.provider import open_gmail_provider

            verification_provider = await stack.enter_async_context(open_gmail_provider(configuration))
        session = await stack.enter_async_context(open_session(configuration.settings))
        result = await run_login(session, configuration, verification_provider)
        yield result
