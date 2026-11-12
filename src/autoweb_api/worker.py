"""Single-active-run coordination; no HTTP, persistence or automatic retries."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .contracts import FinalOutcome, RunEvent, StartRun
from .guardian import LifecycleLimits
from .process_tree import ProcessIdentity, owned_snapshot, read_identity, supported
from .protocol import MAX_EVENTS, MAX_LINE, Cleaned, Hello, Launch, Manifest, Stop, decode, encode


class WorkerBusy(RuntimeError):
    def __init__(self):
        super().__init__("An active run still owns execution or cleanup")


class CleanupUncertain(RuntimeError):
    diagnostic = "cleanup_failed"

    def __init__(self):
        super().__init__("Worker cleanup could not be verified; further starts are blocked")


@dataclass(frozen=True)
class WorkerResult:
    run_id: str
    outcome: FinalOutcome
    forced_cleanup: bool


class WorkerHandle:
    def __init__(self, launch: Launch):
        self.launch = launch
        self.run_id = launch.run_id
        self.events: list[RunEvent] = []
        self.manifest: Manifest | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.guardian_identity: ProcessIdentity | None = None
        self.executor_pid: int | None = None
        self.tracked: dict[tuple[int, int], ProcessIdentity] = {}
        self.cleanup_verified = False
        self.cleanup_uncertain = False
        self.authentication = "not_observed"
        self._stop = asyncio.Event()
        self._changed = asyncio.Condition()
        self._task: asyncio.Task | None = None
        self._finished = False

    async def wait(self) -> WorkerResult:
        # UI/API request cancellation never cancels the owning supervisor task.
        return await asyncio.shield(self._task)

    async def stop(self) -> WorkerResult:
        self.request_stop()
        return await self.wait()

    def request_stop(self) -> None:
        """Signal once without coupling cleanup ownership to an HTTP response."""
        self._stop.set()

    async def next_event(self, after_sequence=0) -> RunEvent | None:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("invalid_event_cursor")
        async with self._changed:
            while len(self.events) <= after_sequence:
                if self._finished:
                    return None
                await self._changed.wait()
            return self.events[after_sequence]

    async def _append(self, event):
        async with self._changed:
            if event.payload.type == "authentication":
                if event.payload.status == "succeeded":
                    self.authentication = "succeeded"
                elif event.payload.status == "failed" and self.authentication != "succeeded":
                    self.authentication = "failed"
            elif event.payload.type == "final_outcome":
                self.authentication = event.payload.authentication
            self.events.append(event)
            self._changed.notify_all()


class WorkerCoordinator:
    """Own one supervised unit until its guardian has attested and exited cleanly.

    root, artifact location, environment and lifecycle limits are trusted service
    construction settings, never accepted from StartRun or the future browser UI.
    """

    def __init__(self, root: Path, *, artifacts: Path | None = None,
                 limits: LifecycleLimits | None = None, environment=None,
                 receipts: Path | None = None, on_spawn=None):
        self.root = Path(root).resolve()
        self.artifacts = Path(artifacts or self.root / "artifacts/studio").resolve()
        self.limits = limits
        self.environment = dict(os.environ if environment is None else environment)
        self.receipts = None if receipts is None else Path(receipts).resolve()
        self.on_spawn = on_spawn
        self.active: WorkerHandle | None = None
        self._lock = asyncio.Lock()

    async def start(self, request: StartRun, *, run_id: str | None = None) -> WorkerHandle:
        # Revalidate copies as Pydantic model_copy can bypass validation.
        request = StartRun.model_validate_json(request.model_dump_json())
        if not supported():
            raise RuntimeError("Linux process containment is required for Studio execution")
        async with self._lock:
            if self.active is not None:
                raise WorkerBusy()
            handle = WorkerHandle(Launch(type="start", run_id=run_id or uuid4().hex, request=request))
            self.active = handle
            handle._task = asyncio.create_task(self._run(handle))
            return handle

    def _command(self, limits):
        return [sys.executable, "-m", "autoweb_api.guardian", "--root", str(self.root),
                "--artifacts", str(self.artifacts), "--cooperative-seconds", str(limits.cooperative_seconds),
                "--force-seconds", str(limits.force_seconds), "--max-seconds", str(limits.max_seconds)]

    async def close(self):
        if self.active is not None:
            await self.active.stop()

    async def _run(self, handle):
        limits = self.limits or LifecycleLimits(max_seconds=120 + handle.launch.request.overrides.login_timeout_seconds)
        reader = None
        try:
            command = self._command(limits)
            if self.receipts is not None:
                command += ["--receipt", str(self.receipts / f"{handle.run_id}.json")]
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=self.environment, start_new_session=True, limit=MAX_LINE + 1,
            )
            handle.process = process
            handle.guardian_identity = read_identity(process.pid)
            if handle.guardian_identity is None:
                raise RuntimeError("worker_identity_unavailable")
            if self.on_spawn is not None:
                # A service commits ownership BEFORE authorizing child execution.
                self.on_spawn(handle)
            process.stdin.write(encode(handle.launch))
            await process.stdin.drain()
            reader = asyncio.create_task(process.stdout.readline())
            deadline = monotonic() + limits.max_seconds + limits.cooperative_seconds + limits.force_seconds + 5
            sent_stop = False
            cleaned = None
            final = None
            sequence = 0
            next_scan = 0.0
            while True:
                if handle.guardian_identity is not None and monotonic() >= next_scan:
                    for identity in owned_snapshot(handle.guardian_identity):
                        handle.tracked[(identity.pid, identity.started)] = identity
                    next_scan = monotonic() + 0.05
                if handle._stop.is_set() and not sent_stop:
                    try:
                        process.stdin.write(encode(Stop(type="stop")))
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    sent_stop = True
                    deadline = min(deadline, monotonic() + limits.cooperative_seconds + limits.force_seconds + 5)
                if monotonic() > deadline:
                    raise CleanupUncertain()
                done, _ = await asyncio.wait({reader}, timeout=0.05)
                if not done:
                    continue
                line = reader.result()
                if not line:
                    break
                frame = decode(line)
                if isinstance(frame, Hello):
                    if frame.run_id != handle.run_id or handle.executor_pid is not None:
                        raise ValueError("protocol_invalid")
                    handle.executor_pid = frame.executor_pid
                elif isinstance(frame, Manifest):
                    if frame.profile_id != handle.launch.request.profile_id or handle.manifest is not None:
                        raise ValueError("protocol_invalid")
                    handle.manifest = frame
                elif isinstance(frame, RunEvent):
                    if (frame.run_id != handle.run_id or frame.sequence != sequence + 1
                            or sequence >= MAX_EVENTS or final is not None
                            or (handle.events and frame.elapsed_seconds < handle.events[-1].elapsed_seconds)):
                        raise ValueError("protocol_invalid")
                    sequence = frame.sequence
                    if frame.payload.type == "final_outcome":
                        final = frame
                    else:
                        await handle._append(frame)
                elif isinstance(frame, Cleaned):
                    if cleaned is not None or final is None:
                        raise ValueError("protocol_invalid")
                    cleaned = frame
                else:
                    raise ValueError("protocol_invalid")
                reader = asyncio.create_task(process.stdout.readline())
            await asyncio.wait_for(process.wait(), max(0.1, deadline - monotonic()))
            if process.returncode != 0 or cleaned is None or final is None:
                raise CleanupUncertain()
            if any(identity.alive() for identity in handle.tracked.values()):
                raise CleanupUncertain()
            handle.cleanup_verified = True
            await handle._append(final)
            self.active = None
            return WorkerResult(handle.run_id, final.payload, cleaned.forced)
        except BaseException:
            if handle.process is None:
                self.active = None  # No child was created, so there is nothing to contain.
                raise RuntimeError("worker_start_failed") from None
            # Guardian failure cannot attest orphan cleanup. Kill only identities
            # actually observed under this guardian, and never release the slot.
            handle.cleanup_uncertain = True
            await self._recover_known(handle, limits)
            raise CleanupUncertain() from None
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if handle.process is not None and handle.process.stdin is not None:
                handle.process.stdin.close()
            async with handle._changed:
                handle._finished = True
                handle._changed.notify_all()

    async def _recover_known(self, handle, limits):
        process = handle.process
        root = handle.guardian_identity
        deadline = monotonic() + limits.force_seconds
        while monotonic() < deadline:
            if root is not None and root.alive():
                for identity in owned_snapshot(root):
                    handle.tracked[(identity.pid, identity.started)] = identity
            for identity in reversed(tuple(handle.tracked.values())):
                try:
                    identity.send(signal.SIGKILL)
                except OSError:
                    pass
            if root is not None:
                try:
                    root.send(signal.SIGKILL)
                except OSError:
                    pass
            if not any(identity.alive() for identity in handle.tracked.values()) and process.returncode is not None:
                break
            await asyncio.sleep(0.02)
        try:
            await asyncio.wait_for(process.wait(), 0.5)
        except asyncio.TimeoutError:
            pass
