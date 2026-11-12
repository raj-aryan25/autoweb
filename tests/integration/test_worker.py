"""Real package execution under one run's Linux guardian; never live services."""

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path

import pytest

from autoweb_api.contracts import RunOverrides, StartRun
from autoweb_api.guardian import LifecycleLimits
from autoweb_api.process_tree import owned_snapshot, read_identity, supported
from autoweb_api.worker import CleanupUncertain, WorkerBusy, WorkerCoordinator

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not supported(), reason="Linux pidfd containment required")]


@pytest.fixture
def worker_root(tmp_path):
    (tmp_path / "configs/sites").mkdir(parents=True)
    for relative in ["configs/settings.json", "configs/sites/local-password.example.json"]:
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    return tmp_path


def request(**changes):
    values = dict(headless=True, login_timeout_seconds=10, verification_timeout_seconds=1)
    values.update(changes)
    return StartRun(request_id="worker-test", profile_id="controlled-password", overrides=RunOverrides(**values))


def change_profile(root, change):
    path = root / "configs/sites/local-password.example.json"
    profile = json.loads(path.read_text())
    change(profile)
    path.write_text(json.dumps(profile))


def block_profile(root):
    change_profile(root, lambda profile: profile["steps"].insert(1, {
        "action": "wait_for", "target": {"by": "css", "value": "#never-ready"}, "timeout_ms": 10000,
    }))


async def until(handle, predicate):
    cursor = 0
    async def consume():
        nonlocal cursor
        while (event := await handle.next_event(cursor)) is not None:
            cursor = event.sequence
            if predicate(event):
                return event
        pytest.fail("Worker completed without the expected event")
    return await asyncio.wait_for(consume(), 20)


def assert_clean(owner, handle, result):
    assert handle.cleanup_verified and not handle.cleanup_uncertain
    assert owner.active is None
    assert handle.process.returncode == 0
    assert not any(identity.alive() for identity in handle.tracked.values())
    assert [event.sequence for event in handle.events] == list(range(1, len(handle.events) + 1))
    finals = [event for event in handle.events if event.payload.type == "final_outcome"]
    assert len(finals) == 1 and finals[0].payload == result.outcome
    assert handle.events[-1] == finals[0]


async def test_worker_runs_package_and_releases_browser(worker_root):
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request())
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == result.outcome.authentication == "succeeded"
    assert handle.manifest.profile_id == "controlled-password"
    assert handle.manifest.effective.browser.engine == "chromium"
    assert handle.manifest.effective.browser.headless is True
    assert handle.manifest.effective.timeouts.login_seconds == 10
    assert_clean(owner, handle, result)
    text = "".join(event.model_dump_json() for event in handle.events)
    assert "test-password" not in text and "test-user" not in text
    assert "http://" not in text and "storage_state" not in text


@pytest.mark.skipif(os.environ.get("AUTOWEB_TEST_HEADED") != "1", reason="Explicit desktop headed check")
async def test_worker_headed_password_on_desktop(worker_root):
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request(headless=False, slow_mo_ms=250, login_timeout_seconds=15))
    await until(handle, lambda event: event.payload.type == "browser_startup" and event.payload.status == "succeeded")
    command_lines = []
    for identity in owned_snapshot(handle.guardian_identity):
        try:
            command_lines.append(Path(f"/proc/{identity.pid}/cmdline").read_bytes())
        except OSError:
            pass
    assert any(b"chrome" in line and b"--headless" not in line and b"--type=" not in line for line in command_lines)
    assert handle.manifest.effective.browser.headless is False
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == "succeeded"
    assert_clean(owner, handle, result)


async def test_stop_is_cooperative_idempotent_and_holds_active_slot(worker_root):
    block_profile(worker_root)
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request())
    await until(handle, lambda event: event.payload.type == "step_started" and event.payload.action == "wait_for")
    stopping = asyncio.create_task(handle.stop())
    await asyncio.sleep(0)
    with pytest.raises(WorkerBusy):
        await owner.start(request())
    results = await asyncio.gather(stopping, handle.stop())
    assert results[0] == results[1]
    assert results[0].outcome.state == "cancelled" and not results[0].forced_cleanup
    assert_clean(owner, handle, results[0])


async def test_concurrent_starts_create_only_one_run(worker_root):
    owner = WorkerCoordinator(worker_root)
    values = await asyncio.gather(owner.start(request()), owner.start(request()), return_exceptions=True)
    assert sum(isinstance(value, WorkerBusy) for value in values) == 1
    handle = next(value for value in values if not isinstance(value, BaseException))
    result = await asyncio.wait_for(handle.stop(), 20)
    assert result.outcome.state == "cancelled"
    assert_clean(owner, handle, result)


@pytest.mark.parametrize("case,expected", [("rejection", "authentication_rejected"), ("timeout", "login_timeout"),
                                         ("preflight", "invalid_configuration"), ("startup", "browser_start_failed")])
async def test_worker_failures_are_safe_and_clean(worker_root, case, expected):
    values = {}
    environment = dict(os.environ)
    if case == "rejection":
        change_profile(worker_root, lambda profile: profile["steps"][2].update(target={"by": "label", "value": "Username"}))
    elif case == "timeout":
        block_profile(worker_root)
        values["login_timeout_seconds"] = 1
    elif case == "preflight":
        (worker_root / "configs/sites/local-password.example.json").write_text('{"invalid":"private-secret"}')
    else:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(worker_root / "missing-browser-binaries")
    owner = WorkerCoordinator(worker_root, environment=environment)
    handle = await owner.start(request(**values))
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == "failed" and result.outcome.diagnostic == expected
    assert_clean(owner, handle, result)
    assert "private-secret" not in repr(handle.events)
    if case == "preflight":
        assert not any(event.payload.type == "browser_startup" for event in handle.events)


async def test_executor_crash_reaps_detached_browser(worker_root):
    block_profile(worker_root)
    owner = WorkerCoordinator(worker_root, limits=LifecycleLimits(0.4, 2, 30))
    handle = await owner.start(request())
    await until(handle, lambda event: event.payload.type == "step_started" and event.payload.action == "wait_for")
    read_identity(handle.executor_pid).send(signal.SIGKILL)
    result = await asyncio.wait_for(handle.wait(), 15)
    assert result.outcome.state == "interrupted" and result.outcome.diagnostic == "worker_failed"
    # The driver may close Chromium itself on pipe EOF. Forced cleanup is needed
    # only for survivors; the invariant is that all owned processes are gone.
    assert_clean(owner, handle, result)


async def test_closing_browser_during_action_does_not_claim_success(worker_root):
    block_profile(worker_root)
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request())
    await until(handle, lambda event: event.payload.type == "step_started" and event.payload.action == "wait_for")
    roots = []
    for identity in owned_snapshot(handle.guardian_identity):
        try:
            command = Path(f"/proc/{identity.pid}/cmdline").read_bytes()
            if b"chrome" in command and b"--type=" not in command:
                roots.append(identity)
        except OSError:
            pass
    assert roots
    for identity in roots:
        identity.send(signal.SIGTERM)
    result = await asyncio.wait_for(handle.wait(), 20)
    assert result.outcome.state == "failed" and result.outcome.authentication != "succeeded"
    assert_clean(owner, handle, result)


async def test_stop_completion_race_preserves_authentication(worker_root):
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request())
    await until(handle, lambda event: event.payload.type == "authentication" and event.payload.status == "succeeded")
    result = await asyncio.wait_for(handle.stop(), 20)
    assert result.outcome.authentication == "succeeded"
    assert result.outcome.state in {"succeeded", "cancelled"}
    assert_clean(owner, handle, result)
    count = len(handle.events)
    assert await handle.stop() == result and len(handle.events) == count


async def test_cancelled_waiter_does_not_cancel_run(worker_root):
    owner = WorkerCoordinator(worker_root)
    handle = await owner.start(request())
    waiter = asyncio.create_task(handle.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == "succeeded"
    assert_clean(owner, handle, result)


async def test_guardian_wall_deadline_is_a_timeout_not_a_user_stop(worker_root):
    block_profile(worker_root)
    owner = WorkerCoordinator(worker_root, limits=LifecycleLimits(2, 2, 2))
    handle = await owner.start(request())
    result = await asyncio.wait_for(handle.wait(), 15)
    assert result.outcome.state == "failed" and result.outcome.diagnostic == "login_timeout"
    assert_clean(owner, handle, result)


async def test_guardian_death_blocks_replacement_and_kills_only_known_tree(worker_root):
    block_profile(worker_root)
    owner = WorkerCoordinator(worker_root, limits=LifecycleLimits(1, 2, 30))
    unrelated = await asyncio.create_subprocess_exec("sleep", "30")
    try:
        handle = await owner.start(request())
        await until(handle, lambda event: event.payload.type == "step_started" and event.payload.action == "wait_for")
        await asyncio.sleep(0.1)  # Let the independent parent collect a fresh ownership snapshot.
        assert handle.tracked
        handle.guardian_identity.send(signal.SIGKILL)
        with pytest.raises(CleanupUncertain):
            await asyncio.wait_for(handle.wait(), 10)
        assert handle.cleanup_uncertain and not handle.cleanup_verified
        assert not any(identity.alive() for identity in handle.tracked.values())
        assert not any(event.payload.type == "final_outcome" for event in handle.events)
        with pytest.raises(WorkerBusy):
            await owner.start(request())
        assert unrelated.returncode is None
    finally:
        unrelated.terminate()
        await unrelated.wait()
