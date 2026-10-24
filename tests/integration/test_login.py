"""Complete password login against a real controlled cookie-authenticated site."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest

from autoweb.browser import click, wait_for_state
from autoweb.config import load_configuration
from autoweb.config.models import Locator
from autoweb.login import LoginError, authenticated_session, run_login
from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME

pytestmark = pytest.mark.asyncio


@pytest.fixture
def opened_sessions(monkeypatch):
    import autoweb.login.flow as flow

    original = flow.open_session
    sessions = []

    @asynccontextmanager
    async def capture(settings):
        async with original(settings) as session:
            sessions.append(session)
            yield session

    monkeypatch.setattr(flow, "open_session", capture)
    return sessions


@pytest.mark.parametrize("filename", ["local-password.example.json", "local-password-two-step.example.json"])
async def test_login_and_protected_action_keep_same_authenticated_session(configuration_factory, filename):
    configuration = configuration_factory(filename)
    async with authenticated_session(configuration) as result:
        assert result.status == "succeeded"
        active = result.session
        assert active.browser.is_connected()
        response = await active.context.request.get(configuration.site.entry_url.replace(
            "/login-two-step", "/api/session").replace("/login", "/api/session"))
        assert await response.json() == {"authenticated": True}
        await response.dispose()
        await click(active.page, Locator(by="role", value="button", name="Perform protected action"))
        await wait_for_state(active.page, Locator(by="test_id", value="protected-result"))
        assert await active.page.get_by_test_id("protected-result").inner_text() == "Protected action completed"
        assert DEMO_PASSWORD not in repr(result)
    assert active.page.is_closed()
    assert not active.browser.is_connected()


async def test_run_login_reuses_caller_owned_session(configuration_factory, session):
    configuration = configuration_factory()
    result = await run_login(session, configuration)
    assert result.session is session
    assert session.browser.is_connected()
    assert await session.page.get_by_test_id("account-menu").is_visible()


@pytest.mark.parametrize("filename", ["local-password.example.json", "local-password-two-step.example.json"])
async def test_wrong_password_fails_once_and_closes_session(
    configuration_factory, login_site, opened_sessions, filename,
):
    configuration = configuration_factory(filename, password="incorrect-private-password")
    before = login_site.attempt_count
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("Rejected credentials must not yield a session")
    result = failure.value.result
    assert result.status == "failed" and result.reason == "credentials_rejected"
    assert result.session is None
    assert login_site.attempt_count == before + 1
    assert result.artifacts and all(path.is_file() for path in result.artifacts)
    assert "incorrect-private-password" not in str(failure.value) + repr(result)
    assert opened_sessions[0].page.is_closed()
    assert not opened_sessions[0].browser.is_connected()


async def test_rejected_username_is_detected_before_password_entry(configuration_factory):
    configuration = configuration_factory("local-password-two-step.example.json", username="unknown-user")
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("Unknown username must fail")
    assert failure.value.result.reason == "credentials_rejected"
    assert not any(step.index == 5 for step in failure.value.result.steps)


async def test_apparent_ui_and_url_success_do_not_override_failed_session_check(configuration_factory):
    def fake(profile):
        profile["entry_url"] = profile["entry_url"].replace("/login", "/fake-success")
    configuration = configuration_factory(change=fake, login_seconds=1.5)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("An apparent dashboard is not an authenticated session")
    assert failure.value.result.reason == "login_timeout"
    assert failure.value.result.failed_step == "authentication"


async def test_rejection_wins_over_visible_success_indicator(configuration_factory):
    def both(profile):
        profile["entry_url"] = profile["entry_url"].replace("/login", "/both-outcomes")
        profile["success"] = profile["success"][:2]
    configuration = configuration_factory(change=both)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("A rejection must win over apparent success")
    assert failure.value.result.reason == "credentials_rejected"


async def test_missing_required_input_halts_flow_without_submitting(configuration_factory, login_site):
    def missing(profile):
        profile["steps"][1]["target"] = {"by": "css", "value": "#missing"}
        profile["steps"][1]["timeout_ms"] = 150
    configuration = configuration_factory(change=missing)
    before = login_site.attempt_count
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("A missing required input must fail")
    assert failure.value.result.reason == "step_timeout"
    assert failure.value.result.failed_step == "step:2:fill"
    assert login_site.attempt_count == before


async def test_overall_deadline_caps_long_step_timeouts(configuration_factory):
    def blocked(profile):
        profile["steps"].insert(2, {"action": "wait_for", "target": {"by": "css", "value": "#never"}, "timeout_ms": 10000})
    configuration = configuration_factory(change=blocked, login_seconds=0.5)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("A blocked step must respect the login deadline")
    assert failure.value.result.reason == "login_timeout"
    assert failure.value.result.elapsed_seconds < 2


async def test_absent_optional_step_is_skipped(configuration_factory):
    def optional(profile):
        profile["steps"].insert(1, {"action": "click", "target": {"by": "css", "value": "#optional"}, "optional": True, "timeout_ms": 100})
    configuration = configuration_factory(change=optional)
    async with authenticated_session(configuration) as result:
        assert result.steps[1].result.status == "skipped"
        assert result.steps[1].result.reason == "absent"


async def test_ambiguous_failure_condition_reports_configuration_problem(configuration_factory):
    def ambiguous(profile):
        profile["failure"] = [{"type": "element", "target": {"by": "css", "value": "input"}}]
    configuration = configuration_factory(change=ambiguous)
    with pytest.raises(LoginError) as failure:
        async with authenticated_session(configuration):
            pytest.fail("Ambiguous assertions must fail explicitly")
    assert failure.value.result.reason == "ambiguous_target"


async def test_cancelling_login_closes_session(configuration_factory, opened_sessions):
    def blocked(profile):
        profile["steps"].insert(1, {"action": "wait_for", "target": {"by": "css", "value": "#never"}, "timeout_ms": 10000})
    configuration = configuration_factory(change=blocked, login_seconds=10)

    async def work():
        async with authenticated_session(configuration):
            pytest.fail("Blocked login must not succeed")

    task = asyncio.create_task(work())
    try:
        async def wait_until_open():
            while not opened_sessions:
                if task.done():
                    await task
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_until_open(), timeout=35)
        await opened_sessions[0].page.wait_for_url("**/login", timeout=3000)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert opened_sessions[0].page.is_closed()
        assert not opened_sessions[0].browser.is_connected()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("password,exit_code", [(DEMO_PASSWORD, 0), ("incorrect-private-password", 1)])
async def test_cli_reports_success_or_failure_without_secrets(configuration_factory, password, exit_code):
    configuration = configuration_factory(password=password)
    environment = dict(os.environ, LOCAL_LOGIN_USERNAME=DEMO_USERNAME,
                       LOCAL_LOGIN_PASSWORD=password, AUTOWEB_HEADLESS="true", AUTOWEB_AI_ENABLED="false")
    report = configuration.project_root / "login-result.json"
    result = await asyncio.to_thread(subprocess.run, [
        sys.executable, "-m", "autoweb.login", "--site", "site.json",
        "--project-root", str(configuration.project_root),
        "--report", str(report),
    ], capture_output=True, text=True, timeout=60, env=environment,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == exit_code, result.stderr
    assert password not in result.stdout + result.stderr
    assert password not in report.read_text()
    assert json.loads(report.read_text())["status"] == ("succeeded" if exit_code == 0 else "failed")
    if exit_code == 0:
        assert "Login succeeded" in result.stdout
    else:
        assert "credentials_rejected" in result.stderr
