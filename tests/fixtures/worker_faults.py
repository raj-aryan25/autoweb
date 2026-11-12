"""Private test-only executors; never imported or selectable by production code."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from autoweb_api.process_tree import read_identity


def record(path, pid=None):
    identity = read_identity(os.getpid() if pid is None else pid)
    with Path(path).open("a") as stream:
        stream.write(json.dumps({"pid": identity.pid, "started": identity.started}) + "\n")


def leaf(path):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    record(path)
    while True:
        time.sleep(1)


def executor(mode, path, epoch):
    from autoweb_api.protocol import EventClock, Launch, encode

    launch = Launch.model_validate_json(sys.stdin.buffer.readline())
    record(path)

    def spawn_leaf(*args):
        process = subprocess.Popen([sys.executable, __file__, "leaf", path], start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record(path, process.pid)
        return process

    def late_leaf(*args):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        spawn_leaf()

    signal.signal(signal.SIGTERM, late_leaf if mode == "late" else signal.SIG_IGN)
    spawn_leaf()
    clock = EventClock(launch.run_id, float(epoch))
    sys.stdout.buffer.write(encode(clock.event({"type": "run_state_changed", "state": "running"})))
    if mode == "auth":
        sys.stdout.buffer.write(encode(clock.event({"type": "authentication", "status": "succeeded"},
                                                  step_id="authentication", phase_id="authentication")))
    sys.stdout.buffer.flush()
    if mode == "crash":
        time.sleep(0.2)
        os._exit(7)
    if mode == "flood":
        sys.stdout.buffer.write(b"private-secret-001234" * 4000 + b"\n")
        sys.stdout.buffer.flush()
    while True:
        time.sleep(1)


def guardian(mode, path):
    from autoweb_api.guardian import LifecycleLimits, supervise
    from autoweb_api.protocol import Launch

    data = bytearray()
    while not data.endswith(b"\n"):
        data.extend(os.read(sys.stdin.fileno(), 1))
    launch = Launch.model_validate_json(bytes(data))
    epoch = time.monotonic()
    return supervise([sys.executable, __file__, "executor", mode, path, str(epoch)],
                     launch, LifecycleLimits(0.2, 2, 15), epoch)


if __name__ == "__main__":
    if sys.argv[1] == "leaf":
        leaf(sys.argv[2])
    elif sys.argv[1] == "executor":
        executor(*sys.argv[2:])
    else:
        raise SystemExit(guardian(*sys.argv[2:]))
