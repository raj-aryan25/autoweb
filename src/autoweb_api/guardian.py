"""Dedicated Linux subreaper for one supervised AutoWeb execution unit."""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .contracts import FinalOutcome, RunEvent
from .process_tree import become_subreaper, descendants, read_identity
from .protocol import (
    MAX_EVENTS, MAX_LINE, MAX_OUTPUT, Cleaned, EventClock, ExecutorResult,
    Hello, Launch, Manifest, RecoveryReceipt, Stop, decode, encode,
)


@dataclass(frozen=True)
class LifecycleLimits:
    cooperative_seconds: float = 5
    force_seconds: float = 5
    max_seconds: float = 240

    def __post_init__(self):
        import math

        values = (self.cooperative_seconds, self.force_seconds, self.max_seconds)
        if any(type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= maximum
               for value, maximum in zip(values, (60, 30, 900))):
            raise ValueError("invalid_lifecycle_limits")


class Lines:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            index = self.buffer.index(b"\n") + 1
            if index > MAX_LINE:
                raise ValueError("protocol_limit")
            line = bytes(self.buffer[:index])
            del self.buffer[:index]
            yield line
        if len(self.buffer) >= MAX_LINE:
            raise ValueError("protocol_limit")


class Output:
    def __init__(self, fd):
        self.fd = fd
        self.buffer = bytearray()
        self.closed = False
        os.set_blocking(fd, False)

    def push(self, frame):
        if not self.closed:
            data = encode(frame)
            if len(self.buffer) + len(data) > MAX_OUTPUT:
                raise ValueError("protocol_limit")
            self.buffer.extend(data)

    def flush(self):
        if self.closed or not self.buffer:
            return
        try:
            count = os.write(self.fd, self.buffer)
            del self.buffer[:count]
        except BlockingIOError:
            pass
        except BrokenPipeError:
            self.closed = True
            self.buffer.clear()


def reap(process):
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return
        if pid == process.pid:
            process.returncode = os.waitstatus_to_exitcode(status)


def supervise(command, launch: Launch, limits: LifecycleLimits, epoch: float, *, receipt: Path | None = None) -> int:
    """Internal supervisor; production supplies a fixed executor command.

    The subreaper is isolated from the API and has no unrelated children. Tests
    exercise this same containment loop with trusted fault executors.
    """
    become_subreaper()  # Must succeed and be verified BEFORE Popen.
    clock = EventClock(launch.run_id, epoch)
    output = Output(sys.stdout.fileno())
    selector = selectors.DefaultSelector()
    parent_lines, child_lines = Lines(), Lines()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, close_fds=True)
    assert process.stdin is not None and process.stdout is not None
    os.set_blocking(process.stdin.fileno(), False)
    os.write(process.stdin.fileno(), encode(launch))
    os.set_blocking(sys.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    selector.register(sys.stdin.fileno(), selectors.EVENT_READ, "parent")
    selector.register(process.stdout.fileno(), selectors.EVENT_READ, "child")
    output.push(Hello(type="hello", run_id=launch.run_id, executor_pid=process.pid))
    output.push(clock.event({"type": "run_state_changed", "state": "created"}))
    result = None
    authentication = "not_observed"
    child_sequence = 0
    child_elapsed = -1.0
    child_eof = False
    stop_reason = None
    stop_deadline = None
    force_started = None
    forced = False
    protocol_error = False
    cleanup_started = False
    signal_stop = False

    def on_signal(*args):
        nonlocal signal_stop
        signal_stop = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def emit(payload, **fields):
        if clock.sequence >= MAX_EVENTS:
            raise ValueError("protocol_limit")
        output.push(clock.event(payload, **fields))

    def request_stop(reason):
        nonlocal stop_reason, stop_deadline
        if stop_reason is None:
            stop_reason = reason
            stop_deadline = time.monotonic() + limits.cooperative_seconds
            emit({"type": "run_state_changed", "state": "stopping"})
            try:
                os.write(process.stdin.fileno(), encode(Stop(type="stop")))
            except (BrokenPipeError, BlockingIOError):
                pass

    def begin_cleanup():
        nonlocal cleanup_started
        if not cleanup_started:
            cleanup_started = True
            emit({"type": "cleanup", "status": "started"},
                 step_id="cleanup", phase_id="process_cleanup")

    try:
        while True:
            now = time.monotonic()
            if signal_stop or output.closed:
                request_stop("user_stop")
            if now - epoch >= limits.max_seconds:
                request_stop("login_timeout")
            for key, _ in selector.select(0.02):
                try:
                    data = os.read(key.fd, MAX_LINE)
                    if not data:
                        selector.unregister(key.fd)
                        if key.data == "parent":
                            request_stop("user_stop")
                        else:
                            child_eof = True
                            if child_lines.buffer:
                                raise ValueError("protocol_invalid")
                        continue
                    lines = parent_lines if key.data == "parent" else child_lines
                    for line in lines.feed(data):
                        if key.data == "parent":
                            Stop.model_validate_json(line)
                            request_stop("user_stop")
                            continue
                        frame = decode(line)
                        if result is not None:
                            raise ValueError("protocol_invalid")
                        if isinstance(frame, RunEvent):
                            if (frame.run_id != launch.run_id or frame.sequence != child_sequence + 1
                                    or frame.elapsed_seconds < child_elapsed
                                    or frame.payload.type == "final_outcome"):
                                raise ValueError("protocol_invalid")
                            child_sequence = frame.sequence
                            child_elapsed = frame.elapsed_seconds
                            if frame.payload.type == "authentication":
                                if frame.payload.status == "succeeded":
                                    authentication = "succeeded"
                                elif frame.payload.status == "failed" and authentication != "succeeded":
                                    authentication = "failed"
                            if frame.payload.type == "run_state_changed":
                                if frame.payload.state == "created" or stop_reason is not None:
                                    continue
                                if frame.payload.state not in {"preflight", "running"}:
                                    raise ValueError("protocol_invalid")
                            emit(frame.payload, step_id=frame.step_id, phase_id=frame.phase_id,
                                 observation=(frame.timestamp, frame.elapsed_seconds))
                        elif isinstance(frame, Manifest):
                            if frame.profile_id != launch.request.profile_id:
                                raise ValueError("protocol_invalid")
                            output.push(frame)
                        elif isinstance(frame, ExecutorResult):
                            result = frame.outcome
                            # Independently retain observed authentication across crashes/stops.
                            if result.authentication == "succeeded":
                                if authentication != "succeeded":
                                    raise ValueError("protocol_invalid")
                            elif authentication == "not_observed":
                                authentication = result.authentication
                        else:
                            raise ValueError("protocol_invalid")
                except (ValueError, TypeError, OSError):
                    protocol_error = True
                    # Stop reading this source, preventing an unbounded corrupt stream.
                    try:
                        selector.unregister(key.fd)
                    except KeyError:
                        pass
                    if key.data == "child":
                        child_eof = True
                    request_stop("worker_failed")

            reap(process)
            owned = descendants(os.getpid())
            if process.returncode is not None:
                begin_cleanup()
                if owned and stop_deadline is None:
                    stop_deadline = time.monotonic() + limits.cooperative_seconds
                if not owned and child_eof:
                    break
            if stop_deadline is not None and now >= stop_deadline:
                begin_cleanup()
                if force_started is None:
                    force_started = now
                for identity in reversed(owned):
                    identity.send(signal.SIGTERM if now - force_started < min(0.25, limits.force_seconds / 2)
                                  else signal.SIGKILL)
                forced = forced or bool(owned)
                if now - force_started >= limits.force_seconds and owned:
                    emit({"type": "cleanup", "status": "failed", "diagnostic": "cleanup_failed"},
                         step_id="cleanup", phase_id="process_cleanup")
                    # No final outcome or cleanup attestation. Parent keeps the slot blocked.
                    return 2
            output.flush()

        if protocol_error:
            final = FinalOutcome(type="final_outcome", state="interrupted",
                                 authentication=authentication, diagnostic="worker_failed")
        elif process.returncode != 0 or result is None:
            state = "cancelled" if stop_reason == "user_stop" else "failed" if stop_reason == "login_timeout" else "interrupted"
            final = FinalOutcome(type="final_outcome", state=state, authentication=authentication,
                                 diagnostic=stop_reason or "worker_failed")
        else:
            final = result.model_copy(update={"authentication": authentication})
            if stop_reason == "login_timeout" and final.state == "cancelled":
                # The package sees task cancellation, but the guardian knows
                # this was its wall deadline rather than a user stop request.
                final = final.model_copy(update={"state": "failed", "diagnostic": "login_timeout"})
            if forced and final.state == "succeeded":
                final = final.model_copy(update={"state": "failed", "diagnostic": "cleanup_failed"})
        if receipt is not None:
            identity = read_identity(os.getpid())
            write_receipt(receipt, RecoveryReceipt(run_id=launch.run_id, guardian_pid=identity.pid,
                          guardian_started=identity.started, outcome=final, forced=forced))
        emit({"type": "cleanup", "status": "succeeded"}, step_id="cleanup", phase_id="process_cleanup")
        emit(final)
        output.push(Cleaned(type="cleaned", forced=forced))
        deadline = time.monotonic() + 2
        while output.buffer and not output.closed and time.monotonic() < deadline:
            output.flush()
            time.sleep(0.005)
        return 0 if not output.buffer else 2
    finally:
        selector.close()
        process.stdin.close()
        process.stdout.close()


def write_receipt(path: Path, receipt: RecoveryReceipt):
    """Atomic cleanup evidence, written only AFTER all descendants are reaped."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(receipt.model_dump_json().encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--cooperative-seconds", type=float, default=5)
    parser.add_argument("--force-seconds", type=float, default=5)
    parser.add_argument("--max-seconds", type=float, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    epoch = time.monotonic()
    try:
        # Read exactly one frame without BufferedReader read-ahead swallowing a
        # stop command sent immediately after launch. Initial input is bounded.
        import select

        line = bytearray()
        initial_deadline = time.monotonic() + 5
        while not line.endswith(b"\n") and len(line) <= MAX_LINE:
            remaining = initial_deadline - time.monotonic()
            if remaining <= 0 or not select.select([sys.stdin.fileno()], [], [], remaining)[0]:
                return 1
            value = os.read(sys.stdin.fileno(), 1)
            if not value:
                return 1
            line.extend(value)
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            return 1
        launch = Launch.model_validate_json(bytes(line))
        limits = LifecycleLimits(args.cooperative_seconds, args.force_seconds, args.max_seconds)
        command = [sys.executable, "-m", "autoweb_api.executor", "--root", str(args.root),
                   "--artifacts", str(args.artifacts), "--epoch", str(epoch)]
        return supervise(command, launch, limits, epoch, receipt=args.receipt)
    except BaseException:
        # Never put exception strings, paths, tokens or tracebacks on the wire.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
