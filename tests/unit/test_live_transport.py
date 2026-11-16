"""Private per-run preview transport: bounds, identity, latest-frame semantics.

No HTTP, no persistence, no browser. A local duplex socketpair is enough to
exercise producer authentication and malformed wire data; peer-identity checks
that need a different process use a short-lived child.
"""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from autoweb_api import live_transport
from autoweb_api.contracts import LiveViewStatus, RunOverrides, StartRun
from autoweb_api.live_capture import MAX_FRAME_BYTES, PreviewFrame
from autoweb_api.protocol import Launch, PreviewConnection
from tests.fixtures.frames import (
    REAL_JPEG, REAL_HEIGHT, REAL_WIDTH, frame_header, heartbeat_header,
    jpeg, packet, raw_packet, status_header,
)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.asyncio


def namespace():
    return "unit-" + uuid4().hex


async def make_receiver(run_id="run-1", *, expect=True, pid=None):
    receiver = live_transport.FrameReceiver(run_id, namespace=namespace())
    await receiver.open()
    if expect:
        receiver.expect_executor(os.getpid() if pid is None else pid)
    return receiver


async def connect(receiver):
    return await asyncio.open_unix_connection(receiver.connection.socket_path)


async def close_writer(writer):
    if writer is None:
        return
    try:
        writer.transport.abort()
    except Exception:
        pass
    await asyncio.sleep(0)


async def send(writer, header, data=b""):
    writer.write(packet(header, data))
    await writer.drain()


async def send_raw(writer, payload):
    writer.write(payload)
    await writer.drain()


async def wait_value(getter, predicate, timeout=3):
    async def poll():
        while True:
            value = getter()
            if predicate(value):
                return value
            await asyncio.sleep(0.01)
    return await asyncio.wait_for(poll(), timeout)


async def expect_closed(reader, timeout=3):
    try:
        assert await asyncio.wait_for(reader.read(1), timeout) == b""
    except (ConnectionResetError, BrokenPipeError):
        pass


async def assert_rejected(receiver, reader, writer, *, status=None):
    await expect_closed(reader)
    await close_writer(writer)
    assert receiver.latest is None
    if status is not None:
        assert receiver.status == status


def frame(sequence=1, data=None):
    return PreviewFrame(sequence, datetime.now(timezone.utc), REAL_WIDTH, REAL_HEIGHT,
                        REAL_JPEG if data is None else data)


# --- wire contracts ---------------------------------------------------------


async def test_preview_connection_is_private_and_launch_repr_hides_it():
    connection = PreviewConnection(socket_path="/tmp/aw-view-x/frames.sock", token="a" * 64)
    launch = Launch(type="start", run_id="run-1",
                    request=StartRun(request_id="request-1", profile_id="controlled-password"),
                    preview=connection)
    assert connection.token not in repr(launch) and connection.socket_path not in repr(launch)
    assert connection.token not in repr(connection) and connection.socket_path not in repr(connection)
    for bad in ({"socket_path": "", "token": "a" * 64},
                {"socket_path": "/tmp/x", "token": "A" * 64},
                {"socket_path": "/" + "x" * 101, "token": "a" * 64}):
        with pytest.raises(ValidationError):
            PreviewConnection.model_validate(bad)
    with pytest.raises(ValidationError):
        StartRun.model_validate({"request_id": "r", "profile_id": "controlled-password",
                                 "preview": {"socket_path": "/tmp/x", "token": "a" * 64}})


async def test_importing_transport_starts_no_socket_or_process():
    script = """
import sys
def audit(event, args):
    if event in {'socket.bind', 'socket.connect', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('resource startup: ' + event)
sys.addaudithook(audit)
import autoweb_api.live_transport as module
assert module.FrameReceiver and module.FrameSender and module.capture_lifecycle
"""
    subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)


async def test_jpeg_dimensions_reads_real_and_structural_samples_without_decoding():
    assert live_transport.jpeg_dimensions(REAL_JPEG) == (REAL_WIDTH, REAL_HEIGHT)
    assert live_transport.jpeg_dimensions(jpeg(1280, 720)) == (1280, 720)
    assert live_transport.jpeg_dimensions(jpeg(32, 16, sof=0xC2)) == (32, 16)
    for invalid in (b"", b"not-a-jpeg", REAL_JPEG[:-2], b"\xff\xd8\xff\xd9",
                    jpeg(0, 10), jpeg(10, 0), jpeg(1921, 10), jpeg(10, 1081)):
        with pytest.raises(ValueError):
            live_transport.jpeg_dimensions(invalid)


async def test_packet_helper_enforces_header_and_frame_limits():
    assert live_transport._packet({"type": "heartbeat"})
    with pytest.raises(ValueError):
        live_transport._packet({"type": "x", "pad": "y" * 1100})
    with pytest.raises(ValueError):
        live_transport._packet({"type": "frame"}, b"x" * (MAX_FRAME_BYTES + 1))


# --- receiver authentication and identity -----------------------------------


async def test_receiver_accepts_authenticated_real_frame_and_replaces_latest():
    receiver = await make_receiver()
    writer = None
    try:
        reader, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token, sequence=1), REAL_JPEG)
        first = await wait_value(lambda: receiver.latest, lambda value: value is not None)
        assert first.sequence == 1 and first.data == REAL_JPEG
        assert (first.width, first.height) == (REAL_WIDTH, REAL_HEIGHT)
        assert receiver.status == LiveViewStatus(state="live")

        await send(writer, frame_header("run-1", receiver.connection.token, sequence=2), REAL_JPEG)
        second = await wait_value(lambda: receiver.latest, lambda value: value and value.sequence == 2)
        assert second.data == REAL_JPEG

        # Unexpected producer EOF clears the frame without a terminal handoff.
        await close_writer(writer)
        writer = None
        await wait_value(lambda: receiver.status, lambda value: value.diagnostic == "transport_unavailable")
        assert receiver.latest is None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_peer_must_match_the_expected_executor_pid_not_only_the_token():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    receiver = None
    writer = None
    try:
        receiver = await make_receiver(pid=child.pid)
        reader, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        await assert_rejected(receiver, reader, writer)
        writer = None
    finally:
        if writer is not None:
            await close_writer(writer)
        if receiver is not None:
            await receiver.close()
        child.terminate()
        child.wait(timeout=5)


async def test_unknown_or_dead_executor_identity_rejects_every_peer():
    receiver = await make_receiver(pid=999999)
    writer = None
    try:
        reader, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        await assert_rejected(receiver, reader, writer)
        writer = None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_same_pid_and_start_identity_is_accepted_across_state_changes(monkeypatch):
    receiver = await make_receiver()
    expected = receiver._expected
    from autoweb_api.process_tree import ProcessIdentity
    monkeypatch.setattr(live_transport, "read_identity",
                        lambda pid: ProcessIdentity(pid, 0, expected.started, "S"))
    writer = None
    try:
        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        value = await wait_value(lambda: receiver.latest, lambda item: item is not None)
        assert value.data == REAL_JPEG
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_wrong_token_or_run_never_claims_the_single_producer_slot():
    receiver = await make_receiver()
    writer = None
    try:
        reader, writer = await connect(receiver)
        await send(writer, frame_header("run-1", "0" * 64), REAL_JPEG)
        await assert_rejected(receiver, reader, writer, status=LiveViewStatus(state="starting"))
        writer = None

        reader, writer = await connect(receiver)
        await send(writer, frame_header("other-run", receiver.connection.token), REAL_JPEG)
        await assert_rejected(receiver, reader, writer, status=LiveViewStatus(state="starting"))
        writer = None

        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        assert (await wait_value(lambda: receiver.latest, lambda item: item is not None)).data == REAL_JPEG
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_a_second_producer_is_refused_after_the_executor_claims():
    receiver = await make_receiver()
    first = second = None
    try:
        _, first = await connect(receiver)
        await send(first, frame_header("run-1", receiver.connection.token, sequence=1), REAL_JPEG)
        await wait_value(lambda: receiver.latest, lambda item: item is not None)

        reader, second = await connect(receiver)
        await expect_closed(reader)
        await close_writer(second)
        second = None

        await send(first, frame_header("run-1", receiver.connection.token, sequence=2), REAL_JPEG)
        assert (await wait_value(lambda: receiver.latest,
                                 lambda item: item is not None and item.sequence == 2)).sequence == 2
    finally:
        for writer in (first, second):
            if writer is not None:
                await close_writer(writer)
        await receiver.close()


async def test_connection_before_executor_identity_times_out_without_claiming():
    receiver = await make_receiver(expect=False)
    writer = None
    try:
        reader, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        await expect_closed(reader, 3)
        await close_writer(writer)
        writer = None
        assert receiver.latest is None

        receiver.expect_executor(os.getpid())
        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        assert (await wait_value(lambda: receiver.latest, lambda item: item is not None)).data == REAL_JPEG
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


# --- malformed and out-of-bounds frames -------------------------------------


def _malformed_cases():
    run = "run-1"

    def header(**changes):
        value = frame_header(run, "__TOKEN__")
        value.update(changes)
        return value

    def packet_case(value):
        return ("packet", value)

    return [
        ("empty-header", ("raw", b""), b"", "starting"),
        ("oversized-header", ("raw", b"{" + b"x" * 1100), b"", "starting"),
        ("not-json", ("raw", b"not-json"), b"", "starting"),
        ("not-object", ("raw", b"[]"), b"", "starting"),
        ("wrong-run", packet_case(heartbeat_header("other-run", "__TOKEN__")), b"", "starting"),
        ("missing-token", packet_case({"type": "heartbeat", "run_id": run}), b"", "starting"),
        ("token-not-string", packet_case({"type": "heartbeat", "run_id": run, "token": 5}),
         b"", "starting"),
        ("unknown-type", packet_case({"type": "pixels", "run_id": run, "token": "__TOKEN__"}),
         b"", "unavailable"),
        ("frame-extra", packet_case(header(extra="https://private.example")), b"", "unavailable"),
        ("frame-missing-field",
         packet_case({key: value for key, value in header().items() if key != "size"}),
         b"", "unavailable"),
        ("status-extra",
         packet_case(status_header(run, "__TOKEN__", "starting", None) | {"url": "https://private"}),
         b"", "unavailable"),
        ("status-live", packet_case(status_header(run, "__TOKEN__", "live", None)), b"", "unavailable"),
        ("format", packet_case(header(format="png")), b"", "unavailable"),
        ("sequence-zero", packet_case(header(sequence=0)), b"", "unavailable"),
        ("sequence-bool", packet_case(header(sequence=True)), b"", "unavailable"),
        ("width-zero", packet_case(header(width=0)), b"", "unavailable"),
        ("width-large", packet_case(header(width=1921)), b"", "unavailable"),
        ("height-large", packet_case(header(height=1081)), b"", "unavailable"),
        ("size-zero", packet_case(header(size=0)), b"", "unavailable"),
        ("size-large", packet_case(header(size=MAX_FRAME_BYTES + 1)), b"", "unavailable"),
        ("naive-time", packet_case(header(captured_at="2026-01-01T00:00:00")), b"", "unavailable"),
        ("bad-time", packet_case(header(captured_at="not-a-time")), b"", "unavailable"),
        ("frame-not-jpeg", packet_case(header(size=10)), b"not-a-jpeg", "unavailable"),
        ("frame-no-eoi", packet_case(header(size=len(REAL_JPEG) - 2)), REAL_JPEG[:-2], "unavailable"),
        ("dimension-mismatch", packet_case(header(width=1280, height=720)), REAL_JPEG, "unavailable"),
    ]


@pytest.mark.parametrize("name,spec,data,status", _malformed_cases(),
                         ids=[case[0] for case in _malformed_cases()])
async def test_malformed_frames_are_rejected_without_publishing_pixels(name, spec, data, status):
    receiver = await make_receiver()
    writer = None
    try:
        kind, header = spec
        if kind == "raw":
            payload = raw_packet(header)
        else:
            resolved = {key: (receiver.connection.token if value == "__TOKEN__" else value)
                        for key, value in header.items()}
            payload = packet(resolved)
        reader, writer = await connect(receiver)
        await send_raw(writer, payload + data)
        expected = LiveViewStatus(state=status,
                                  diagnostic="transport_unavailable" if status == "unavailable" else None)
        await assert_rejected(receiver, reader, writer, status=expected)
        writer = None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_duplicate_json_header_keys_are_rejected_by_the_wire_parser():
    receiver = await make_receiver()
    writer = None
    try:
        token = receiver.connection.token
        header = (b'{"type":"heartbeat","run_id":"run-1","run_id":"run-1","token":"'
                  + token.encode() + b'"}')
        reader, writer = await connect(receiver)
        await send_raw(writer, raw_packet(header))
        await assert_rejected(receiver, reader, writer, status=LiveViewStatus(state="starting"))
        writer = None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_duplicate_backward_and_decreasing_time_frames_are_rejected():
    now = datetime.now(timezone.utc)
    cases = [
        ("duplicate-sequence", frame_header("run-1", "__TOKEN__", sequence=2, captured_at=now),
         frame_header("run-1", "__TOKEN__", sequence=2, captured_at=now)),
        ("backward-sequence", frame_header("run-1", "__TOKEN__", sequence=2, captured_at=now),
         frame_header("run-1", "__TOKEN__", sequence=1, captured_at=now)),
        ("decreasing-time", frame_header("run-1", "__TOKEN__", sequence=2, captured_at=now),
         frame_header("run-1", "__TOKEN__", sequence=3, captured_at=now - timedelta(seconds=1))),
    ]
    for name, first_header, second_header in cases:
        receiver = await make_receiver()
        writer = None
        try:
            def resolved(header):
                return {key: (receiver.connection.token if value == "__TOKEN__" else value)
                        for key, value in header.items()}
            reader, writer = await connect(receiver)
            await send(writer, resolved(first_header), REAL_JPEG)
            await wait_value(lambda: receiver.latest, lambda item: item is not None)
            await send(writer, resolved(second_header), REAL_JPEG)
            await expect_closed(reader)
            # The authenticated failure clears the retained frame.
            await wait_value(lambda: receiver.status, lambda value: value.state == "unavailable")
            assert receiver.latest is None, name
        finally:
            if writer is not None:
                await close_writer(writer)
            await receiver.close()


# --- status and terminal retention ------------------------------------------


async def test_ended_status_retains_terminal_frame_until_receiver_close():
    receiver = await make_receiver()
    writer = None
    try:
        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        await wait_value(lambda: receiver.latest, lambda item: item is not None)
        await send(writer, status_header("run-1", receiver.connection.token, "ended"))
        await wait_value(lambda: receiver.status, lambda value: value.state == "ended")
        assert receiver.latest is not None

        # Producer EOF after the explicit terminal status keeps the last image.
        await close_writer(writer)
        writer = None
        await asyncio.sleep(0.1)
        assert receiver.status.state == "ended" and receiver.latest is not None
        terminal = receiver.latest

        await receiver.close()
        assert receiver.latest is None and receiver.status.state == "ended"
        assert not Path(receiver.connection.socket_path).exists()
        assert terminal.data == REAL_JPEG
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_heartbeat_preserves_the_live_frame_without_new_pixels():
    receiver = await make_receiver()
    writer = None
    try:
        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        first = await wait_value(lambda: receiver.latest, lambda item: item is not None)
        for _ in range(3):
            await send(writer, heartbeat_header("run-1", receiver.connection.token))
        await asyncio.sleep(0.15)
        assert receiver.latest is first and receiver.status.state == "live"
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_unavailable_status_clears_the_frame_and_live_status_requires_pixels():
    receiver = await make_receiver()
    writer = None
    try:
        _, writer = await connect(receiver)
        await send(writer, frame_header("run-1", receiver.connection.token), REAL_JPEG)
        await wait_value(lambda: receiver.latest, lambda item: item is not None)
        await send(writer, status_header("run-1", receiver.connection.token, "unavailable", "capture_failed"))
        await wait_value(lambda: receiver.status, lambda value: value.state == "unavailable")
        assert receiver.latest is None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()

    receiver = await make_receiver()
    writer = None
    try:
        _, writer = await connect(receiver)
        await send(writer, status_header("run-1", receiver.connection.token, "live"))
        # live without a frame is rejected after authentication.
        await wait_value(lambda: receiver.status, lambda value: value.state == "unavailable")
        assert receiver.latest is None
    finally:
        if writer is not None:
            await close_writer(writer)
        await receiver.close()


async def test_close_is_idempotent_unlinks_and_never_reuses_a_private_path():
    receiver = await make_receiver()
    connection = receiver.connection
    directory = Path(connection.socket_path).parent
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(connection.socket_path).stat().st_mode) == 0o600
    await receiver.close()
    await receiver.close()
    assert not directory.exists() and not Path(connection.socket_path).exists()
    assert receiver.latest is None and receiver.status.state == "ended"

    replacement = await make_receiver()
    try:
        assert replacement.connection.socket_path != connection.socket_path
        assert replacement.connection.token != connection.token
    finally:
        await replacement.close()


# --- stale private directories ----------------------------------------------


async def _socket_at(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
    finally:
        sock.close()


def _matching_directory(prefix, mode=0o700):
    directory = Path("/tmp") / (prefix + uuid4().hex)
    directory.mkdir(mode=mode)
    return directory


async def test_cleanup_stale_views_removes_only_private_socket_directories():
    trusted = "cleanup-" + uuid4().hex
    prefix = live_transport._prefix(trusted)
    removed = _matching_directory(prefix)
    await _socket_at(removed / "frames.sock")
    empty = _matching_directory(prefix)

    wrong_mode = _matching_directory(prefix, 0o755)
    await _socket_at(wrong_mode / "frames.sock")

    extra_file = _matching_directory(prefix)
    await _socket_at(extra_file / "frames.sock")
    (extra_file / "frame.jpg").write_bytes(b"preview")

    regular = _matching_directory(prefix)
    (regular / "frames.sock").write_bytes(b"not-a-socket")

    symlinked = _matching_directory(prefix)
    (symlinked / "frames.sock").symlink_to("/tmp")

    other = _matching_directory(live_transport._prefix("other-" + uuid4().hex))
    await _socket_at(other / "frames.sock")

    try:
        live_transport.cleanup_stale_views("unrelated-" + uuid4().hex)
        assert removed.exists() and empty.exists() and other.exists()

        live_transport.cleanup_stale_views(trusted)
        assert not removed.exists() and not empty.exists()
        assert wrong_mode.exists() and extra_file.exists() and regular.exists()
        assert symlinked.exists() and other.exists()
    finally:
        for directory in (removed, empty, wrong_mode, extra_file, regular, symlinked, other):
            _remove_tree(directory)


def _remove_tree(directory):
    if not directory.exists() and not directory.is_symlink():
        return
    for child in directory.iterdir() if directory.is_dir() and not directory.is_symlink() else ():
        try:
            child.unlink()
        except OSError:
            pass
    if directory.is_symlink():
        directory.unlink()
    else:
        try:
            directory.rmdir()
        except OSError:
            import shutil
            shutil.rmtree(directory)


# --- sender ----------------------------------------------------------------


async def test_sender_latest_only_delivers_the_final_frame_and_ended_status():
    receiver = await make_receiver()
    sender = live_transport.FrameSender("run-1", receiver.connection)
    try:
        assert await sender.start()
        offered = []
        for sequence in range(1, 6):
            value = PreviewFrame(sequence, datetime.now(timezone.utc), REAL_WIDTH, REAL_HEIGHT, REAL_JPEG)
            offered.append(value)
            sender.offer(value)
        await sender.close(flush=True)
        latest = await wait_value(lambda: receiver.latest, lambda item: item is not None)
        assert latest.sequence == offered[-1].sequence and latest.data == REAL_JPEG
        await wait_value(lambda: receiver.status, lambda value: value.state == "ended")
        assert receiver.latest is not None  # terminal frame retained until receiver close
    finally:
        await sender.close(flush=False)
        await receiver.close()


async def test_sender_offer_is_nonblocking_and_close_is_bounded_when_the_peer_stalls():
    path = str(Path("/tmp") / ("aw-stall-" + uuid4().hex + ".sock"))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)  # queued, never accepted: no reader drains the stream
    sender = live_transport.FrameSender("run-1", PreviewConnection(socket_path=path, token="b" * 64))
    try:
        assert await sender.start()
        big = jpeg(10, 10, comment=b"x" * 60000)
        started = time.monotonic()
        for sequence in range(1, 51):
            sender.offer(PreviewFrame(sequence, datetime.now(timezone.utc), 10, 10, big))
        assert time.monotonic() - started < 0.5
        assert sender._pending is not None and sender._pending.sequence == 50

        started = time.monotonic()
        await asyncio.wait_for(sender.close(flush=True), 6)
        assert time.monotonic() - started < 6
    finally:
        await sender.close(flush=False)
        listener.close()
        Path(path).unlink(missing_ok=True)


async def test_sender_start_failure_is_fail_open_and_close_is_safe():
    sender = live_transport.FrameSender(
        "run-1", PreviewConnection(socket_path="/tmp/aw-view-missing/frames.sock", token="c" * 64))
    assert await sender.start() is False
    assert sender.failed is True
    with pytest.raises(RuntimeError):
        sender.offer(frame())
    await asyncio.wait_for(sender.close(flush=True), 2)


async def test_sender_rejects_empty_and_oversized_frames_without_retaining_them():
    sender = live_transport.FrameSender(
        "run-1", PreviewConnection(socket_path="/tmp/aw-view-unused/frames.sock", token="d" * 64))
    for data in (b"", b"x" * (MAX_FRAME_BYTES + 1)):
        with pytest.raises(ValueError):
            sender.offer(PreviewFrame(1, datetime.now(timezone.utc), 1280, 720, data))
        assert sender._pending is None


async def test_sender_transport_failure_marks_failed_and_stays_bounded():
    receiver = await make_receiver()
    sender = live_transport.FrameSender("run-1", receiver.connection)
    try:
        assert await sender.start()
        sender.offer(frame(1))
        await wait_value(lambda: receiver.latest, lambda item: item is not None)
        await receiver.close()
        sender.offer(frame(2))

        async def failed():
            while not sender.failed:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(failed(), 4)
        with pytest.raises(RuntimeError):
            sender.offer(frame(3))
        await asyncio.wait_for(sender.close(flush=True), 3)
    finally:
        await sender.close(flush=False)
        await receiver.close()


async def test_sender_close_without_flush_does_not_send_a_terminal_status():
    receiver = await make_receiver()
    sender = live_transport.FrameSender("run-1", receiver.connection)
    try:
        assert await sender.start()
        sender.offer(frame(1))
        first = await wait_value(lambda: receiver.latest, lambda item: item is not None)
        sender.offer(frame(2))
        await sender.close(flush=False)
        await wait_value(lambda: receiver.status, lambda value: value.diagnostic == "transport_unavailable")
        assert receiver.latest is None and first.sequence == 1
    finally:
        await sender.close(flush=False)
        await receiver.close()


async def test_sender_status_packets_do_not_publish_pixels():
    receiver = await make_receiver()
    sender = live_transport.FrameSender("run-1", receiver.connection)
    try:
        assert await sender.start()
        sender.status(LiveViewStatus(state="starting"))
        await asyncio.sleep(0.05)
        sender.status(LiveViewStatus(state="ended"))
        await wait_value(lambda: receiver.status, lambda value: value.state == "ended")
        assert receiver.latest is None
    finally:
        await sender.close(flush=False)
        await receiver.close()
