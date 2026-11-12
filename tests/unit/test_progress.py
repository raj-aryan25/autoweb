"""Observation is bounded and never invokes consumer code in execution paths."""

import asyncio
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from autoweb.config import ConfigurationError, load_configuration, load_site_profile
from autoweb.login import LoginResult, authenticated_session
from autoweb.login.progress import Progress, ProgressObserver


def progress_pair():
    observer = ProgressObserver()
    profile = load_site_profile("configs/sites/local-password.example.json")
    return observer, Progress(observer, profile)


def test_unconsumed_transitions_survive_information_flood():
    observer, progress = progress_pair()
    progress.start("navigation")
    for _ in range(10000):
        observer._publish(kind="verification_wait", status="started",
                          step_id="action:1", phase_id="verification_wait", informational=True)
    progress.end("navigation")
    events = observer.drain()
    assert [(item.kind, item.status) for item in events] == [
        ("navigation", "started"), ("verification_wait", "started"), ("navigation", "succeeded"),
    ]
    assert not observer.overflowed
    assert len({item.ordinal for item in events}) == 3


def test_distinct_informational_phases_are_bounded_separately():
    observer, progress = progress_pair()
    progress.start("navigation")
    for index in range(1000):
        observer._publish(kind="verification_wait", status="started",
                          step_id=f"action:{index}", phase_id="verification_wait", informational=True)
    assert len(observer.drain()) == 17


def test_transition_overflow_is_explicit_and_nonblocking():
    observer, progress = progress_pair()
    for _ in range(1000):
        progress.emit("navigation", "started")
    assert observer.overflowed
    assert len(observer.drain()) == observer._capacity


def test_sink_failure_does_not_escape(monkeypatch):
    observer, progress = progress_pair()

    def fail(**kwargs):
        raise RuntimeError("private synthetic consumer failure")

    monkeypatch.setattr(observer, "_publish", fail)
    progress.start("navigation")
    progress.end("navigation")
    assert not observer.drain()


def test_prepare_failure_disables_observation(monkeypatch):
    observer = ProgressObserver()
    monkeypatch.setattr(observer, "_prepare", lambda count: (_ for _ in ()).throw(RuntimeError("broken")))
    profile = load_site_profile("configs/sites/local-password.example.json")
    progress = Progress(observer, profile)
    progress.emit("navigation", "started")
    assert not observer.drain()


@pytest.mark.asyncio
async def test_next_event_and_disconnect_wake_consumer():
    observer, progress = progress_pair()
    waiter = asyncio.create_task(observer.next_event())
    await asyncio.sleep(0)
    progress.emit("navigation", "started")
    assert (await asyncio.wait_for(waiter, 1)).kind == "navigation"
    waiter = asyncio.create_task(observer.next_event())
    await asyncio.sleep(0)
    observer.close()
    assert await asyncio.wait_for(waiter, 1) is None
    progress.emit("navigation", "succeeded")
    assert not observer.drain()


@pytest.mark.asyncio
async def test_cancelling_a_consumer_does_not_close_producer():
    observer, progress = progress_pair()
    waiter = asyncio.create_task(observer.next_event())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    progress.emit("navigation", "succeeded")
    event = await asyncio.wait_for(observer.next_event(), 1)
    assert event.status == "succeeded"


def test_open_boundaries_close_in_reverse_order_once():
    observer, progress = progress_pair()
    progress.start("step", step_id="action:1", action="fill_otp", index=1)
    progress.start("verification_wait", step_id="action:1")
    progress.finish_open("failed")
    progress.finish_open("failed")
    assert [(event.kind, event.status) for event in observer.drain()] == [
        ("step", "started"), ("verification_wait", "started"),
        ("verification_wait", "failed"), ("step", "failed"),
    ]


@pytest.mark.asyncio
async def test_observed_preflight_failure_does_not_open_browser(monkeypatch):
    configuration = load_configuration("configs/sites/local-password.example.json", environ={}, schema_only=True)
    opener = Mock(side_effect=AssertionError("Browser must not open"))
    monkeypatch.setattr("autoweb.login.flow.open_session", opener)
    observer = ProgressObserver()
    with pytest.raises(ConfigurationError):
        async with authenticated_session(configuration, observer=observer):
            pytest.fail("Unresolved configuration cannot yield")
    opener.assert_not_called()
    assert [(event.kind, event.status) for event in observer.drain()] == [
        ("preflight", "started"), ("preflight", "failed"), ("login_outcome", "failed"),
    ]


@pytest.mark.asyncio
async def test_observed_startup_error_preserves_exception_and_cleanup(monkeypatch):
    configuration = load_configuration("configs/sites/local-password.example.json", environ={
        "LOCAL_LOGIN_USERNAME": "synthetic", "LOCAL_LOGIN_PASSWORD": "synthetic-secret", "AUTOWEB_AI_ENABLED": "false",
    })
    original = RuntimeError("private startup error")

    @asynccontextmanager
    async def broken(settings):
        raise original
        yield  # pragma: no cover

    monkeypatch.setattr("autoweb.login.flow.open_session", broken)
    observer = ProgressObserver()
    with pytest.raises(RuntimeError) as failure:
        async with authenticated_session(configuration, observer=observer):
            pytest.fail("Startup failure must propagate")
    assert failure.value is original
    events = observer.drain()
    assert [(event.kind, event.status) for event in events][-5:] == [
        ("browser_startup", "started"), ("browser_startup", "error"),
        ("login_outcome", "error"), ("cleanup", "started"), ("cleanup", "succeeded"),
    ]
    assert "private startup error" not in repr(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["body", "cleanup"])
async def test_observed_resource_exit_preserves_exception_context(monkeypatch, failure_stage):
    configuration = load_configuration("configs/sites/local-password.example.json", environ={
        "LOCAL_LOGIN_USERNAME": "synthetic", "LOCAL_LOGIN_PASSWORD": "synthetic-secret", "AUTOWEB_AI_ENABLED": "false",
    })
    original = RuntimeError("private failure")
    observed_exceptions = []
    closed = []

    @asynccontextmanager
    async def session_scope(settings):
        try:
            yield Mock()
        finally:
            observed_exceptions.append(sys.exc_info()[1])
            closed.append(True)
            if failure_stage == "cleanup":
                raise original

    monkeypatch.setattr("autoweb.login.flow.open_session", session_scope)
    monkeypatch.setattr("autoweb.login.flow._execute_login", AsyncMock(return_value=LoginResult(
        configuration.site.name, "succeeded", 0,
    )))
    observer = ProgressObserver()
    with pytest.raises(RuntimeError) as failure:
        async with authenticated_session(configuration, observer=observer) as result:
            assert result.status == "succeeded"
            if failure_stage == "body":
                raise original
    assert failure.value is original and closed == [True]
    assert observed_exceptions == [original if failure_stage == "body" else None]
    events = observer.drain()
    assert [event.status for event in events if event.kind == "login_outcome"] == ["succeeded"]
    assert [event.status for event in events if event.kind == "cleanup"] == [
        "started", "succeeded" if failure_stage == "body" else "error",
    ]
