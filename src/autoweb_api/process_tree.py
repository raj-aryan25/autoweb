"""Linux per-run ownership primitives; no process-wide changes on import."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path


def supported() -> bool:
    if sys.platform != "linux" or not Path("/proc/self/stat").exists():
        return False
    try:
        descriptor = open_pidfd(os.getpid())
        try:
            send_pidfd(descriptor, 0)
        finally:
            os.close(descriptor)
        return True
    except (OSError, AttributeError):
        return False


def open_pidfd(pid: int) -> int:
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    # Some conda Python builds omit the wrappers even on a capable Linux kernel.
    libc = ctypes.CDLL(None, use_errno=True)
    descriptor = libc.pidfd_open(ctypes.c_int(pid), ctypes.c_uint(0))
    if descriptor < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return descriptor


def send_pidfd(descriptor: int, sig: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(descriptor, sig)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.pidfd_send_signal(ctypes.c_int(descriptor), ctypes.c_int(sig), None, ctypes.c_uint(0)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def become_subreaper() -> None:
    """Only the dedicated guardian calls this, before creating any children."""
    if not supported():
        raise RuntimeError("containment_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    value = ctypes.c_int()
    if (libc.prctl(36, 1, 0, 0, 0) != 0
            or libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0 or value.value != 1):
        raise RuntimeError("containment_unavailable")


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent: int
    started: int
    state: str

    def alive(self) -> bool:
        current = read_identity(self.pid)
        return current is not None and current.started == self.started and current.state != "Z"

    def send(self, sig: int) -> None:
        """Pin the process with a pidfd, then verify start time before signaling."""
        try:
            descriptor = open_pidfd(self.pid)
        except ProcessLookupError:
            return
        try:
            current = read_identity(self.pid)
            if current is not None and current.started == self.started:
                send_pidfd(descriptor, sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(descriptor)


def read_identity(pid: int) -> ProcessIdentity | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
        fields = value[value.rindex(")") + 2:].split()
        return ProcessIdentity(pid, int(fields[1]), int(fields[19]), fields[0])
    except (OSError, ValueError, IndexError):
        return None


def descendants(root: int) -> tuple[ProcessIdentity, ...]:
    root_identity = read_identity(root)
    if root_identity is None:
        return ()
    children: dict[int, list[ProcessIdentity]] = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            identity = read_identity(int(entry.name))
            if identity is not None:
                children.setdefault(identity.parent, []).append(identity)
    result: list[ProcessIdentity] = []
    pending = [root_identity]
    while pending:
        parent = pending.pop()
        current_parent = read_identity(parent.pid)
        if current_parent is None or current_parent.started != parent.started:
            continue
        for child in children.get(parent.pid, []):
            current = read_identity(child.pid)
            if current is None or current.started != child.started or current.parent != parent.pid:
                continue
            result.append(child)
            pending.append(child)
    return tuple(result)


def owned_snapshot(root: ProcessIdentity) -> tuple[ProcessIdentity, ...]:
    """Reject a reused root PID before and after scanning its descendants."""
    if not root.alive():
        return ()
    result = descendants(root.pid)
    return result if root.alive() else ()
