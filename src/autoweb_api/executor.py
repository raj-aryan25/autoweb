"""Private package executor. Only the per-run guardian launches this module."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import asdict
from pathlib import Path

from autoweb.browser.errors import BrowserCleanupError, BrowserStartupError
from autoweb.config import ConfigurationError
from autoweb.contracts import VerificationError
from autoweb.login import LoginError, ProgressObserver, authenticated_session

from .configuration import ProfileUnavailable, controlled_site, prepare_run
from .contracts import AIUsage, FinalOutcome
from .profiles import display_steps
from .protocol import (
    MAX_LINE, MAX_OUTPUT, EffectiveSettings, EventClock, ExecutorResult, Launch, Manifest, Stop, encode,
)


class WireOutput:
    """A bounded writer task, never awaited by a browser action or observer."""

    def __init__(self, fd: int):
        self.fd = fd
        os.set_blocking(fd, False)
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.pending = 0

    def send(self, frame):
        data = encode(frame)
        if self.pending + len(data) > MAX_OUTPUT:
            raise ValueError("protocol_limit")
        self.pending += len(data)
        self.queue.put_nowait(data)

    async def pump(self):
        loop = asyncio.get_running_loop()
        while (data := await self.queue.get()) is not None:
            remaining = memoryview(data)
            while remaining:
                try:
                    count = os.write(self.fd, remaining)
                    remaining = remaining[count:]
                except BlockingIOError:
                    ready = loop.create_future()
                    loop.add_writer(self.fd, lambda: ready.done() or ready.set_result(None))
                    try:
                        await ready
                    finally:
                        loop.remove_writer(self.fd)
            self.pending -= len(data)


class ExecutionEvents:
    def __init__(self, clock, output):
        self.clock, self.output = clock, output
        self.authentication = "not_observed"
        self.browser_started = False
        self.preflight_ready = False

    def emit(self, payload, **fields):
        self.output.send(self.clock.event(payload, **fields))

    def observe(self, observer):
        for event in observer.drain():
            if event.informational or event.kind in {"preflight", "login_outcome"}:
                continue
            status = "failed" if event.status == "error" else event.status
            payload = {"type": event.kind, "status": status}
            if event.kind == "step":
                payload = {"type": "step_started", "action": event.action} if status == "started" else {
                    "type": "step_completed", "action": event.action, "status": status,
                    "diagnostic": "optional_target_absent" if status == "skipped" else None,
                }
            elif event.kind == "verification_wait":
                # Verification profiles are not enabled in the application harness yet.
                # This path is shared with the approved Gmail registration.
                payload["kind"] = self.verification_kind
                payload["status"] = "received" if status == "succeeded" else status
            if event.kind == "authentication":
                if status == "succeeded":
                    self.authentication = "succeeded"
                elif status == "failed" and self.authentication != "succeeded":
                    self.authentication = "failed"
            offset = (observer.started_monotonic or self.clock.epoch) - self.clock.epoch
            self.emit(payload, step_id=event.step_id, phase_id=event.phase_id,
                      observation=(event.timestamp, offset + event.elapsed_seconds))
            if event.kind == "browser_startup" and status == "succeeded":
                self.browser_started = True
                self.emit({"type": "run_state_changed", "state": "running"},
                          observation=(event.timestamp, offset + event.elapsed_seconds))
        if observer.overflowed:
            raise ValueError("observation_incomplete")


def diagnostic(error):
    if isinstance(error, LoginError):
        return {
            "login_timeout": "login_timeout", "verification_timeout": "verification_timeout",
            "step_timeout": "step_timeout", "credentials_rejected": "authentication_rejected",
            "verification_rejected": "authentication_rejected",
        }.get(error.result.reason, "login_failed")
    if isinstance(error, BrowserStartupError):
        return "browser_start_failed"
    if isinstance(error, BrowserCleanupError):
        return "cleanup_failed"
    if isinstance(error, ProfileUnavailable):
        return "unavailable"
    if isinstance(error, ConfigurationError):
        return "invalid_configuration"
    if isinstance(error, VerificationError):
        return "unavailable"
    return "worker_failed"


async def execute(launch, root, artifacts, events, observer):
    usage = None
    try:
        try:
            prepared = prepare_run(launch.request, root, artifacts / launch.run_id)
        except ValueError:
            raise ConfigurationError("Invalid registered run configuration") from None
        events.verification_kind = (prepared.configuration.site.verification.kind
                                    if prepared.configuration.site.verification else None)
        settings = prepared.configuration.settings
        try:
            manifest = Manifest(type="manifest", profile_id=launch.request.profile_id,
                                steps=display_steps(prepared.configuration.site), effective=EffectiveSettings(
                                    browser=settings.browser, timeouts=settings.timeouts,
                                    ai_enabled=settings.ai.enabled, ai_model=settings.ai.model,
                                ))
            events.output.send(manifest)  # Validate size before any fixture/browser startup.
        except ValueError:
            raise ConfigurationError("Profile metadata exceeds worker limits") from None
        directory = prepared.configuration.settings.artifacts_dir
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        events.emit({"type": "preflight", "status": "ready"}, step_id="preflight", phase_id="preflight")
        events.preflight_ready = True
        with controlled_site(prepared) as configuration:
            async with authenticated_session(configuration, observer=observer) as result:
                if result.ai_usage is not None:
                    usage = AIUsage(**asdict(result.ai_usage))
        return "succeeded", None, usage
    except asyncio.CancelledError:
        return "cancelled", "user_stop", usage
    except Exception as error:
        code = diagnostic(error)
        if code == "authentication_rejected" and events.authentication != "succeeded":
            events.authentication = "failed"
        return "failed", code, usage


async def run(root: Path, artifacts: Path, epoch: float, output_fd: int):
    reader = asyncio.StreamReader(limit=MAX_LINE)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer,
    )
    output = WireOutput(output_fd)
    writer = asyncio.create_task(output.pump())
    try:
        line = await reader.readline()
        if len(line) > MAX_LINE:
            raise ValueError("protocol_limit")
        launch = Launch.model_validate_json(line)
        clock = EventClock(launch.run_id, epoch)
        events = ExecutionEvents(clock, output)
        events.emit({"type": "run_state_changed", "state": "created"})
        events.emit({"type": "run_state_changed", "state": "preflight"})
        events.emit({"type": "preflight", "status": "started"}, step_id="preflight", phase_id="preflight")
        observer = ProgressObserver()
        operation = asyncio.create_task(execute(launch, root, artifacts, events, observer))
        command = asyncio.create_task(reader.readline())
        stop_sent = False
        try:
            while not operation.done():
                await asyncio.wait({operation, command, writer}, timeout=0.02,
                                   return_when=asyncio.FIRST_COMPLETED)
                events.observe(observer)
                if writer.done():
                    await writer
                    raise BrokenPipeError()
                if command.done() and not stop_sent:
                    value = command.result()
                    if value:
                        Stop.model_validate_json(value)
                    if not operation.done():
                        operation.cancel()
                    stop_sent = True
                # Do not spin on an already completed control read.
                if stop_sent and not operation.done():
                    await asyncio.sleep(0.02)
            try:
                state, code, usage = await operation
            except asyncio.CancelledError:
                state, code, usage = "cancelled", "user_stop", None
            events.observe(observer)
            if not events.preflight_ready and state == "failed":
                events.emit({"type": "preflight", "status": "failed", "diagnostic": code},
                            step_id="preflight", phase_id="preflight")
            output.send(ExecutorResult(type="result", outcome=FinalOutcome(
                type="final_outcome", state=state, authentication=events.authentication,
                diagnostic=code, ai_usage=usage,
            )))
        finally:
            command.cancel()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(command, operation, return_exceptions=True)
            observer.close()
        output.queue.put_nowait(None)
        await asyncio.wait_for(writer, 3)
    finally:
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--epoch", type=float, required=True)
    args = parser.parse_args()
    # Keep accidental package prints and unexpected tracebacks out of the wire.
    descriptor = os.dup(sys.stdout.fileno())
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")
    try:
        asyncio.run(run(args.root, args.artifacts, args.epoch, descriptor))
    except BaseException:
        return 1
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
