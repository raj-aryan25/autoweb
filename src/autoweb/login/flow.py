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
from autoweb.config.models import (
    ClickStep, FillStep, FillOTPStep, FollowMagicLinkStep, RequestMagicLinkStep, RequestOTPStep,
)
from autoweb.contracts import VerificationError, VerificationProvider

from .assertions import check_rejection, wait_for_authentication, watch_for_rejection
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
                    verification_provider: VerificationProvider | None = None, *, ai_client=None) -> LoginResult:
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
    navigation_stack = AsyncExitStack()
    link_navigation = None
    resolver = None
    if configuration.settings.ai.enabled and configuration.settings.ai.locator_fallback:
        from autoweb.ai.client import client_from_configuration
        from autoweb.ai.locators import LocatorResolver

        resolver = LocatorResolver(ai_client or client_from_configuration(configuration),
                                   screenshots=configuration.settings.ai.screenshots,
                                   request_timeout_seconds=configuration.settings.ai.request_timeout_seconds)

    def active_deadline() -> float:
        return challenge.deadline if challenge else deadline

    def budget_ms(cap: float) -> float:
        remaining = (active_deadline() - monotonic()) * 1000
        if remaining <= 0:
            raise BrowserActionTimeout("login")
        return min(cap, remaining)

    async def steps() -> None:
        nonlocal stage, link_navigation
        for index, step in enumerate(configuration.site.steps, start=1):
            stage = f"step:{index}:{step.action}"
            step_deadline = monotonic() + (step.timeout_ms or configuration.settings.timeouts.action_ms) / 1000

            def step_budget():
                remaining = (step_deadline - monotonic()) * 1000
                if remaining <= 0:
                    raise BrowserActionTimeout(step.action)
                return budget_ms(remaining)

            if resolver and step.ai_fallback and not step.optional and not isinstance(step, FillOTPStep):
                target = getattr(step, "target", None)
                purpose = f"fill_{step.credential}" if isinstance(step, FillStep) else "click"
                if target is not None and isinstance(step, (
                    FillStep, ClickStep, RequestOTPStep, RequestMagicLinkStep,
                )):
                    secrets = [value.get_secret_value() for value in configuration.secrets.values()]
                    if challenge and challenge.result:
                        secrets.append(challenge.result.value)
                    chosen = await resolver.resolve(
                        session.page, target, purpose=purpose,
                        hint=step.ai_hint or target.name or target.value,
                        secrets=secrets, timeout_ms=step_budget(),
                    )
                    step = step.model_copy(update={"target": chosen})
            if isinstance(step, (RequestOTPStep, RequestMagicLinkStep)):
                # Baseline must finish before the one non-idempotent request click.
                action_budget = step_budget()
                await asyncio.wait_for(challenge.prepare(), active_deadline() - monotonic())
                step_deadline = monotonic() + action_budget / 1000
                result = await click(session.page, step.target, timeout_ms=step_budget())
            elif isinstance(step, FollowMagicLinkStep):
                from autoweb.browser.magic_link import MagicLinkNavigation

                selected = await asyncio.wait_for(challenge.receive(), active_deadline() - monotonic())
                link_navigation = await navigation_stack.enter_async_context(
                    MagicLinkNavigation(session.page, configuration.site.verification, active_deadline()))
                try:
                    result = await link_navigation.follow(
                        selected.value, timeout_ms=budget_ms(step.timeout_ms or configuration.settings.timeouts.navigation_ms))
                except BrowserActionError:
                    await check_rejection(session.page, configuration.site, active_deadline())
                    raise
            elif isinstance(step, FillOTPStep):
                action_budget = step_budget()
                selected = await asyncio.wait_for(challenge.receive(), active_deadline() - monotonic())
                step_deadline = monotonic() + action_budget / 1000
                if resolver and step.ai_fallback and len(step.targets) == 1:
                    target = step.targets[0]
                    chosen = await resolver.resolve(
                        session.page, target, purpose="fill_otp", hint=step.ai_hint or target.name or target.value,
                        secrets=[value.get_secret_value() for value in configuration.secrets.values()] + [selected.value],
                        timeout_ms=step_budget(),
                    )
                    step = step.model_copy(update={"targets": [chosen]})
                result = await fill_otp(
                    session.page, step.targets, SecretStr(selected.value),
                    expected_length=configuration.site.verification.code_length,
                    timeout_ms=step_budget(),
                )
            else:
                result = await execute_step(
                    session, step, configuration.secrets,
                    step_budget(),
                )
            completed.append(StepResult(index, step.action, result))

    async def workflow() -> None:
        nonlocal stage
        async with navigation_stack:
            await navigate(
                session.page, configuration.site.entry_url,
                timeout_ms=budget_ms(configuration.settings.timeouts.navigation_ms),
            )
            await _guard_steps(steps(), session, configuration, deadline)
            stage = "authentication"
            authentication = wait_for_authentication(session.page, configuration.site, active_deadline())
            if link_navigation:
                await link_navigation.wait(authentication)
            else:
                await authentication
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
    *, ai_client=None,
) -> AsyncIterator[LoginResult]:
    """Own a session, yield it only after proven authentication, and always clean up."""
    validate_login(configuration)
    if configuration.settings.ai.enabled and ai_client is None:
        from autoweb.ai.client import client_from_configuration

        ai_client = client_from_configuration(configuration)
    async with AsyncExitStack() as stack:
        if configuration.site.verification and verification_provider is None:
            # Lazy application wiring. The core runner only knows the protocol.
            from autoweb.gmail.provider import open_gmail_provider

            options = {"ai_client": ai_client} if ai_client is not None else {}
            verification_provider = await stack.enter_async_context(open_gmail_provider(configuration, **options))
        session = await stack.enter_async_context(open_session(configuration.settings))
        result = await run_login(session, configuration, verification_provider, ai_client=ai_client)
        yield result
