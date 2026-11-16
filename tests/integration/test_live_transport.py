"""End-to-end preview transport through the real worker, guardian and capture.

Pixels must stay on the private per-run Unix socket: no frame bytes in progress
events, SQLite, receipts, artifacts or guardian stdout. Real controlled password,
OTP and magic-link runs exercise masking/capture; fault runs exercise Stop, crash,
guardian death and fail-open preview creation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from autoweb.config.models import SiteProfile
from autoweb_api import live_transport
from autoweb_api.contracts import RunOverrides, StartRun
from autoweb_api.guardian import LifecycleLimits
from autoweb_api.live_capture import LiveCapture
from autoweb_api.live_transport import FrameReceiver, capture_lifecycle
from autoweb_api.process_tree import read_identity, supported
from autoweb_api.protocol import Hello, Launch, PreviewConnection, RunEvent, decode, encode
from autoweb_api.worker import CleanupUncertain, WorkerCoordinator
from tests.fixtures.api_support import configure, finished, local_client
from tests.fixtures.frames import REAL_HEIGHT, REAL_JPEG, REAL_WIDTH, jpeg
from tests.fixtures.view_faults import MARKER

FAKE = Path(__file__).resolve().parents[1] / "fixtures/view_faults.py"
MARKER_FRAME = jpeg(64, 48, comment=MARKER)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not supported(), reason="Linux pidfd containment required")]


def request(**changes):
    values = dict(headless=True, login_timeout_seconds=20, verification_timeout_seconds=5)
    values.update(changes)
    return StartRun(request_id="preview-test", profile_id="controlled-password",
                    overrides=RunOverrides(**values))


def block_profile(root):
    path = root / "configs/sites/local-password.example.json"
    profile = json.loads(path.read_text())
    profile["steps"].insert(1, {"action": "wait_for", "target": {"by": "css", "value": "#never-ready"},
                                "timeout_ms": 10000})
    path.write_text(json.dumps(profile))


def assert_clean(owner, handle, result):
    assert handle.cleanup_verified and not handle.cleanup_uncertain
    assert owner.active is None and handle.process.returncode == 0
    assert not any(identity.alive() for identity in handle.tracked.values())
    finals = [event for event in handle.events if event.payload.type == "final_outcome"]
    assert len(finals) == 1 and finals[0].payload == result.outcome


def assert_frame(frame):
    assert frame.data.startswith(b"\xff\xd8") and frame.data.endswith(b"\xff\xd9")
    assert live_transport.jpeg_dimensions(frame.data) == (frame.width, frame.height)


def assert_no_views(namespace):
    assert not list(Path("/tmp").glob(live_transport._prefix(namespace) + "*"))


async def wait_latest(preview, timeout=20):
    async def poll():
        while True:
            if preview is not None and preview.latest is not None:
                return preview.latest
            await asyncio.sleep(0.01)
    return await asyncio.wait_for(poll(), timeout)


async def wait_preview(handle, timeout=20):
    async def poll():
        while handle.preview is None:
            await asyncio.sleep(0.01)
        return await wait_latest(handle.preview, timeout)
    return await asyncio.wait_for(poll(), timeout)


def assert_no_pixels(frames, *texts):
    """No frame bytes or base64 form may appear in retained text or files."""
    blob = "\n".join(texts)
    for frame in frames:
        assert base64.b64encode(frame.data).decode() not in blob
        assert base64.b64encode(frame.data[:64]).decode() not in blob


# --- real controlled runs through the persisted service ---------------------


async def collect(service, run_id, done):
    frames = []

    async def watch():
        while not done.done():
            handle = service.handles.get(run_id)
            preview = handle.preview if handle is not None else None
            current = preview.latest if preview is not None else None
            if current is not None and (not frames or frames[-1].sequence != current.sequence):
                frames.append(current)
            await asyncio.sleep(0.005)

    watcher = asyncio.create_task(watch())
    try:
        record = await done
    finally:
        await asyncio.gather(watcher, return_exceptions=True)
    return record, frames


@pytest.mark.parametrize("profile_id", ["controlled-password", "controlled-otp", "controlled-magic-link"])
async def test_real_controlled_runs_stream_frames_without_persisting_pixels(tmp_path, profile_id):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        body = {"request_id": f"preview-{profile_id}", "profile_id": profile_id,
                "overrides": {"headless": True, "login_timeout_seconds": 20,
                              "verification_timeout_seconds": 5}}
        response = await client.post("/api/runs", json=body)
        assert response.status_code == 202
        run_id = response.json()["snapshot"]["run_id"]
        done = asyncio.create_task(finished(client, run_id))
        record, frames = await collect(service, run_id, done)

        assert record["snapshot"]["state"] == record["authentication"] == "succeeded"
        assert record["cleanup_verified"]
        assert frames, "real capture never reached the preview receiver"
        sequences = [frame.sequence for frame in frames]
        assert sequences == sorted(set(sequences))
        assert all(frame.captured_at.tzinfo is not None for frame in frames)
        for frame in frames[:3]:
            assert_frame(frame)

        texts = [json.dumps(record), json.dumps([event.model_dump(mode="json")
                                                 for event in service.store.replay(run_id, 0).events])]
        for table in ("runs", "events", "artifacts"):
            texts.extend(repr(tuple(row)) for row in
                         service.store.connection.execute(f"SELECT * FROM {table}"))
        assert_no_pixels(frames, *texts)

        for root in (service.settings.artifacts, service.settings.data_directory):
            for path in root.rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    assert not any(frame.data[:64] in data for frame in frames[:2])
        assert not list((service.settings.artifacts / run_id).rglob("*.jpg"))
        assert_no_views(str(service.settings.data_directory))


# --- no view / fail-open preview creation -----------------------------------


async def test_no_view_option_creates_no_socket_and_runs_normally(tmp_path):
    configure(tmp_path)
    owner = WorkerCoordinator(tmp_path)
    handle = await owner.start(request(live_view_enabled=False))
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == "succeeded"
    assert handle.preview is None and handle.launch.preview is None
    assert_clean(owner, handle, result)
    assert_no_views(owner.preview_namespace)


async def test_preview_creation_failure_is_fail_open(tmp_path, monkeypatch):
    configure(tmp_path)
    owner = WorkerCoordinator(tmp_path)

    async def unavailable(self):
        raise OSError("private socket unavailable")
    monkeypatch.setattr(FrameReceiver, "open", unavailable)

    handle = await owner.start(request())
    result = await asyncio.wait_for(handle.wait(), 30)
    assert result.outcome.state == "succeeded"
    assert handle.preview is None and handle.launch.preview is None
    assert handle.manifest.live_view.state == "unavailable"
    assert_clean(owner, handle, result)
    assert_no_views(owner.preview_namespace)


# --- lifecycle faults -------------------------------------------------------


async def test_stop_closes_capture_and_leaves_no_frame(tmp_path):
    configure(tmp_path)
    block_profile(tmp_path)
    owner = WorkerCoordinator(tmp_path)
    handle = await owner.start(request())
    try:
        await wait_preview(handle)
        path = Path(handle.preview.connection.socket_path)
        result = await asyncio.wait_for(handle.stop(), 30)
        assert result.outcome.state == "cancelled"
        assert handle.preview.latest is None and handle.preview.status.state == "ended"
        assert not path.exists()
        assert_clean(owner, handle, result)
    finally:
        await owner.close()


async def test_executor_crash_clears_the_frame_and_cleans_up(tmp_path):
    configure(tmp_path)
    block_profile(tmp_path)
    owner = WorkerCoordinator(tmp_path, limits=LifecycleLimits(0.4, 2, 30))
    handle = await owner.start(request())
    await wait_preview(handle)
    path = Path(handle.preview.connection.socket_path)
    read_identity(handle.executor_pid).send(signal.SIGKILL)
    result = await asyncio.wait_for(handle.wait(), 15)
    assert result.outcome.state == "interrupted"
    assert handle.preview.latest is None and handle.preview.status.state == "ended"
    assert not path.exists()
    assert_clean(owner, handle, result)


async def test_guardian_death_closes_preview_and_blocks_replacement(tmp_path):
    configure(tmp_path)
    block_profile(tmp_path)
    owner = WorkerCoordinator(tmp_path, limits=LifecycleLimits(1, 2, 30))
    handle = await owner.start(request())
    await wait_preview(handle)
    path = Path(handle.preview.connection.socket_path)
    handle.guardian_identity.send(signal.SIGKILL)
    with pytest.raises(CleanupUncertain):
        await asyncio.wait_for(handle.wait(), 10)
    assert handle.preview.latest is None and handle.preview.status.state == "ended"
    assert not path.exists()
    assert handle.cleanup_uncertain and not handle.cleanup_verified


# --- private producer isolation --------------------------------------------


async def test_fake_producer_frame_never_enters_guardian_stdout(tmp_path):
    configure(tmp_path)
    run_id = "fake-" + uuid4().hex
    namespace = "fake-" + uuid4().hex
    receiver = FrameReceiver(run_id, namespace=namespace)
    connection = await receiver.open()
    launch = Launch(type="start", run_id=run_id, request=request(), preview=connection)
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(FAKE), "guardian", "marker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw = b""
    final = None
    try:
        process.stdin.write(encode(launch))
        await process.stdin.drain()
        while line := await process.stdout.readline():
            raw += line
            frame = decode(line)
            if isinstance(frame, Hello):
                receiver.expect_executor(frame.executor_pid)
            elif isinstance(frame, RunEvent) and frame.payload.type == "final_outcome":
                final = frame.payload
        assert await process.wait() == 0
        assert final is not None and final.state == "succeeded"
        latest = await wait_latest(receiver)

        assert latest.data == MARKER_FRAME
        assert (latest.width, latest.height) == (64, 48)
        assert MARKER not in raw and b"\xff\xd8\xff" not in raw
        assert connection.token.encode() not in raw and connection.socket_path.encode() not in raw
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await receiver.close()
    assert_no_views(namespace)


async def test_worker_accepts_fake_preview_without_pixels_in_events(tmp_path, monkeypatch):
    configure(tmp_path)
    owner = WorkerCoordinator(tmp_path)
    monkeypatch.setattr(
        owner, "_command",
        lambda limits: [sys.executable, str(FAKE), "guardian", "marker"])
    handle = await owner.start(request())
    frame = await wait_preview(handle)
    assert frame.data == MARKER_FRAME and frame.width == 64 and frame.height == 48
    result = await asyncio.wait_for(handle.wait(), 20)
    assert result.outcome.state == "succeeded"
    assert handle.manifest.effective.live_view_enabled is True
    assert_clean(owner, handle, result)
    text = "".join(event.model_dump_json() for event in handle.events)
    assert MARKER.decode() not in text
    assert base64.b64encode(frame.data).decode() not in text
    assert_no_views(owner.preview_namespace)


# --- capture lifecycle isolation -------------------------------------------


class FakeLocator:
    def locator(self, *args, **kwargs):
        return self

    def frame_locator(self, *args, **kwargs):
        return self

    def get_by_role(self, *args, **kwargs):
        return self

    def get_by_label(self, *args, **kwargs):
        return self

    def get_by_placeholder(self, *args, **kwargs):
        return self

    def get_by_test_id(self, *args, **kwargs):
        return self


class FakePage(FakeLocator):
    def __init__(self):
        self.viewport_size = {"width": REAL_WIDTH, "height": REAL_HEIGHT}

    def is_closed(self):
        return False

    async def screenshot(self, **kwargs):
        return REAL_JPEG


class FakeSession:
    def __init__(self):
        self.page = FakePage()


def capture_profile():
    return SiteProfile.model_validate({
        "name": "preview-fixture", "entry_url": "http://127.0.0.1:9/fake",
        "login_mode": "password",
        "credentials": {"username": {"env": "VIEW_USER"}, "password": {"env": "VIEW_PASSWORD"}},
        "steps": [
            {"action": "fill", "target": {"by": "css", "value": "#username"}, "credential": "username"},
            {"action": "fill", "target": {"by": "css", "value": "#password"}, "credential": "password"},
            {"action": "submit", "target": {"by": "css", "value": "#submit"}},
        ],
        "success": [{"type": "element", "target": {"by": "css", "value": "#done"}}],
    })


async def test_capture_lifecycle_delivers_terminal_frame_and_ended_status():
    receiver = FrameReceiver("capture-run", namespace="capture-" + uuid4().hex)
    await receiver.open()
    receiver.expect_executor(os.getpid())
    try:
        async with capture_lifecycle(receiver.connection, "capture-run", capture_profile())(FakeSession()):
            live = await wait_latest(receiver)
            assert live.data == REAL_JPEG and live.sequence == 1
            assert receiver.status.state == "live"
        async def ended():
            while receiver.status.state != "ended":
                await asyncio.sleep(0.01)
            return receiver.status
        await asyncio.wait_for(ended(), 5)
        assert receiver.latest is not None
        assert receiver.latest.data == REAL_JPEG
    finally:
        await receiver.close()
    assert receiver.latest is None


async def test_capture_cancellation_does_not_flush_a_terminal_frame():
    receiver = FrameReceiver("cancel-run", namespace="cancel-" + uuid4().hex)
    await receiver.open()
    receiver.expect_executor(os.getpid())

    async def run():
        async with capture_lifecycle(receiver.connection, "cancel-run", capture_profile())(FakeSession()):
            await wait_latest(receiver)
            raise asyncio.CancelledError()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run(), 10)

        async def unavailable():
            while receiver.status.diagnostic != "transport_unavailable":
                await asyncio.sleep(0.01)
        await asyncio.wait_for(unavailable(), 5)
        assert receiver.latest is None and receiver.status.state == "unavailable"
    finally:
        await receiver.close()


async def test_capture_lifecycle_is_fail_open_when_sender_cannot_connect():
    connection = PreviewConnection(socket_path=f"/tmp/aw-missing-{uuid4().hex}/frames.sock",
                                   token="e" * 64)
    entered = False
    async with capture_lifecycle(connection, "missing-run", capture_profile())(FakeSession()):
        entered = True
    assert entered


async def test_on_status_callback_sees_starting_live_and_ended_without_pixels():
    statuses = []
    capture = LiveCapture(capture_profile(), on_frame=lambda frame: None,
                          on_status=statuses.append)
    async with capture.session_scope(FakeSession()):
        assert capture.status.state == "live"
    assert [status.state for status in statuses][:2] == ["starting", "live"]
    assert statuses[-1].state == "ended"
    assert "base64" not in repr(statuses)


async def test_on_status_errors_do_not_escape_or_retain_pixels():
    def broken(status):
        raise RuntimeError("viewer callback failed")

    capture = LiveCapture(capture_profile(), on_status=broken)
    async with capture.session_scope(FakeSession()):
        assert capture.status.state == "unavailable" and capture.latest is None
        assert capture.status.diagnostic == "capture_failed"
