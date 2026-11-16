"""Private Linux frame IPC, independent of durable progress and login actions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from .contracts import LiveViewStatus
from .live_capture import MAX_FRAME_BYTES, MAX_VIEWPORT_HEIGHT, MAX_VIEWPORT_WIDTH, LiveCapture, PreviewFrame
from .process_tree import read_identity
from .protocol import PreviewConnection

MAX_HEADER_BYTES = 1024
IO_TIMEOUT_SECONDS = 1.0
MAX_CLIENTS = 4


def _prefix(namespace: str) -> str:
    return "aw-view-" + hashlib.sha256(namespace.encode()).hexdigest()[:16] + "-"


def cleanup_stale_views(namespace: str) -> None:
    """After service DB lock + process recovery, remove only our socket dirs.

    No recursive deletion, symlink traversal, frame files or shared directory.
    The namespace derives from trusted service data-directory configuration.
    """
    for directory in Path("/tmp").glob(_prefix(namespace) + "*"):
        try:
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                continue
            entries = list(directory.iterdir())
            if entries:
                if len(entries) != 1 or entries[0].name != "frames.sock":
                    continue
                if not stat.S_ISSOCK(entries[0].lstat().st_mode):
                    continue
                entries[0].unlink()
            directory.rmdir()
        except OSError:
            pass


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read bounded JPEG SOF dimensions; never decode untrusted compressed pixels."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("invalid_frame")
    index = 2
    while index + 4 <= len(data):
        if data[index] != 255:
            break
        while index < len(data) and data[index] == 255:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xDA, 0xD9}:
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2} and length >= 8:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            if 0 < width <= MAX_VIEWPORT_WIDTH and 0 < height <= MAX_VIEWPORT_HEIGHT:
                return width, height
            break
        index += length
    raise ValueError("invalid_frame")


def _packet(header: dict, data: bytes = b"") -> bytes:
    encoded = json.dumps(header, separators=(",", ":")).encode()
    if len(encoded) > MAX_HEADER_BYTES or len(data) > MAX_FRAME_BYTES:
        raise ValueError("preview_limit")
    return struct.pack("!I", len(encoded)) + encoded + data


def _unique_header(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_header")
        result[key] = value
    return result


async def _close_writer(writer):
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), IO_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        writer.transport.abort()
        raise
    except Exception:
        writer.transport.abort()


class FrameReceiver:
    """One run, one authenticated executor, one latest masked JPEG in memory."""

    def __init__(self, run_id: str, *, namespace: str):
        self.run_id = run_id
        self.namespace = namespace
        self.connection: PreviewConnection | None = None
        self.latest: PreviewFrame | None = None
        self.status = LiveViewStatus(state="starting")
        self._directory = None
        self._server = None
        self._tasks = set()
        self._expected = None
        self._ready = asyncio.Event()
        self._claimed = False
        self._closed = False

    async def open(self) -> PreviewConnection:
        self._directory = tempfile.TemporaryDirectory(prefix=_prefix(self.namespace), dir="/tmp")
        path = str(Path(self._directory.name) / "frames.sock")
        self.connection = PreviewConnection(socket_path=path, token=secrets.token_hex(32))
        try:
            self._server = await asyncio.start_unix_server(self._accept, path=path, limit=16384)
            os.chmod(path, 0o600)
            return self.connection
        except BaseException:
            await self.close()
            raise

    def expect_executor(self, pid: int) -> None:
        self._expected = read_identity(pid)
        self._ready.set()
        task = asyncio.create_task(self._startup_deadline(), name="autoweb-preview-startup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _startup_deadline(self):
        await asyncio.sleep(2 * IO_TIMEOUT_SECONDS)
        if not self._claimed and not self._closed:
            self.status = LiveViewStatus(state="unavailable", diagnostic="transport_unavailable")

    def _accept(self, reader, writer):
        if self._closed or self._claimed or len(self._tasks) >= MAX_CLIENTS:
            writer.close()
            return
        task = asyncio.create_task(self._receive(reader, writer), name="autoweb-preview-receiver")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _receive(self, reader, writer):
        authenticated = False
        sequence = 0
        last_time = None
        try:
            peer_pid, uid, _ = struct.unpack("3i", writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            await asyncio.wait_for(self._ready.wait(), IO_TIMEOUT_SECONDS)
            identity = read_identity(peer_pid)
            if (uid != os.getuid() or identity is None or self._expected is None
                    or (identity.pid, identity.started) != (self._expected.pid, self._expected.started)
                    or not identity.alive()):
                return
            while not self._closed:
                # A producer sends a heartbeat/status when there are no frames.
                size = struct.unpack("!I", await asyncio.wait_for(reader.readexactly(4), 3))[0]
                if not 0 < size <= MAX_HEADER_BYTES:
                    raise ValueError("preview_limit")
                header = json.loads(await asyncio.wait_for(reader.readexactly(size), IO_TIMEOUT_SECONDS),
                                    object_pairs_hook=_unique_header)
                if (not isinstance(header, dict) or header.get("run_id") != self.run_id
                        or not isinstance(header.get("token"), str)
                        or not hmac.compare_digest(header["token"], self.connection.token)):
                    raise ValueError("invalid_producer")
                if not authenticated:
                    if self._claimed:
                        return
                    self._claimed = authenticated = True
                kind = header.get("type")
                if kind == "heartbeat" and set(header) == {"type", "run_id", "token"}:
                    continue
                if kind == "status":
                    if set(header) != {"type", "run_id", "token", "state", "diagnostic"}:
                        raise ValueError("invalid_status")
                    value = LiveViewStatus(state=header["state"], diagnostic=header["diagnostic"])
                    if value.state == "live":
                        raise ValueError("frame_required")
                    self.status = value
                    if value.state != "ended":
                        self.latest = None
                    continue
                if kind != "frame" or set(header) != {"type", "run_id", "token", "sequence",
                        "captured_at", "width", "height", "format", "size"}:
                    raise ValueError("invalid_frame")
                number, width, height, count = (header[key] for key in ("sequence", "width", "height", "size"))
                if (any(type(value) is not int for value in (number, width, height, count))
                        or not sequence < number <= 9007199254740991 or header["format"] != "jpeg"
                        or not 0 < count <= MAX_FRAME_BYTES or not 0 < width <= MAX_VIEWPORT_WIDTH
                        or not 0 < height <= MAX_VIEWPORT_HEIGHT):
                    raise ValueError("invalid_frame")
                timestamp = datetime.fromisoformat(header["captured_at"])
                if timestamp.utcoffset() is None or (last_time is not None and timestamp < last_time):
                    raise ValueError("invalid_timestamp")
                data = await asyncio.wait_for(reader.readexactly(count), IO_TIMEOUT_SECONDS)
                if jpeg_dimensions(data) != (width, height):
                    raise ValueError("invalid_dimensions")
                self.latest = PreviewFrame(number, timestamp, width, height, data)
                self.status = LiveViewStatus(state="live")
                sequence, last_time = number, timestamp
        except (Exception, asyncio.CancelledError):
            pass  # No producer text or pixels may escape through progress/logs.
        finally:
            if authenticated and not self._closed:
                if self.status.state != "ended":
                    self.latest = None
                    self.status = LiveViewStatus(state="unavailable", diagnostic="transport_unavailable")
            await _close_writer(writer)

    async def close(self):
        self._closed = True
        self._ready.set()
        if self._server is not None:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self.latest = None
        self.status = LiveViewStatus(state="ended")
        if self._directory is not None:
            try:
                self._directory.cleanup()
            except OSError:
                # Recovery will retry our private socket-only directory. This
                # must never turn successful authentication into run failure.
                pass
            self._directory = None


class FrameSender:
    """Nonblocking capture handoff; one pending frame, bounded socket writes."""

    def __init__(self, run_id: str, connection: PreviewConnection):
        self.run_id, self.connection = run_id, connection
        self._pending = None
        self._status = LiveViewStatus(state="starting")
        self._wake = asyncio.Event()
        self._writer = None
        self._task = None
        self.failed = False

    async def start(self) -> bool:
        try:
            _, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.connection.socket_path), IO_TIMEOUT_SECONDS)
            self._writer.transport.set_write_buffer_limits(high=65536)
            self._task = asyncio.create_task(self._pump(), name="autoweb-preview-sender")
            return True
        except Exception:
            self.failed = True
            return False

    def offer(self, frame: PreviewFrame):
        if self.failed:
            raise RuntimeError("preview_unavailable")
        if not 0 < len(frame.data) <= MAX_FRAME_BYTES:
            raise ValueError("preview_limit")
        self._pending = frame
        self._status = LiveViewStatus(state="live")
        self._wake.set()

    def status(self, value: LiveViewStatus):
        if value.state != "live":
            self._status = value
            if value.state == "unavailable":
                self._pending = None
            self._wake.set()

    async def _write(self, header, data=b""):
        header.update(run_id=self.run_id, token=self.connection.token)
        self._writer.write(_packet(header, data))
        await asyncio.wait_for(self._writer.drain(), IO_TIMEOUT_SECONDS)

    async def _pump(self):
        try:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), 1)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                frame, self._pending = self._pending, None
                if frame is not None:
                    await self._write({"type": "frame", "sequence": frame.sequence,
                        "captured_at": frame.captured_at.isoformat(), "width": frame.width,
                        "height": frame.height, "format": "jpeg", "size": len(frame.data)}, frame.data)
                else:
                    if self._status.state == "live":
                        await self._write({"type": "heartbeat"})
                    else:
                        await self._write({"type": "status", "state": self._status.state,
                                           "diagnostic": self._status.diagnostic})
        except Exception:
            self.failed = True
            self._pending = None

    async def close(self, *, flush=True):
        # Give the already-bounded writer one opportunity to send the terminal
        # handoff. Stop/crash must not depend on a viewer draining it.
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        try:
            if flush and not self.failed and self._writer is not None:
                frame, self._pending = self._pending, None
                if frame is not None:
                    await self._write({"type": "frame", "sequence": frame.sequence,
                        "captured_at": frame.captured_at.isoformat(), "width": frame.width,
                        "height": frame.height, "format": "jpeg", "size": len(frame.data)}, frame.data)
                await self._write({"type": "status", "state": "ended", "diagnostic": None})
        except Exception:
            self.failed = True
        finally:
            self._pending = None
            if self._writer is not None:
                await _close_writer(self._writer)


def capture_lifecycle(connection: PreviewConnection, run_id: str, profile):
    """Build an optional, bounded observer; preview failures never retry login."""
    @asynccontextmanager
    async def scope(session):
        sender = FrameSender(run_id, connection)
        cancelled = False
        try:
            if await sender.start():
                capture = LiveCapture(profile, on_frame=sender.offer, on_status=sender.status)
                async with capture.session_scope(session):
                    yield
            else:
                yield
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            await sender.close(flush=not cancelled)
    return scope
