"""Test-only guardian/executor pair that speaks the real worker protocol.

Never imported or selectable by production code. The executor emits a valid
manifest/result and, when a preview connection is present, sends one structurally
valid JPEG carrying a marker so tests can prove pixels never enter the guardian's
JSON progress pipe. A separate mode crashes after capture to exercise preview EOF.
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MARKER = b"AUTOWEB-PREVIEW-MARKER-7f3c"
WIDTH, HEIGHT = 64, 48


def _marker_jpeg():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.fixtures.frames import jpeg

    return jpeg(WIDTH, HEIGHT, comment=MARKER)


def _emit(clock, payload, **fields):
    from autoweb_api.protocol import encode

    sys.stdout.buffer.write(encode(clock.event(payload, **fields)))
    sys.stdout.buffer.flush()


def _manifest(launch):
    from autoweb.config.models import BrowserSettings, TimeoutSettings
    from autoweb_api.protocol import EffectiveSettings, Manifest

    return Manifest(
        type="manifest", profile_id=launch.request.profile_id, steps=[],
        effective=EffectiveSettings(
            browser=BrowserSettings(), timeouts=TimeoutSettings(), ai_enabled=False,
            live_view_enabled=launch.request.overrides.live_view_enabled,
        ),
    )


async def _preview(launch, mode):
    from autoweb_api.live_capture import PreviewFrame
    from autoweb_api.live_transport import FrameSender

    if launch.preview is None:
        return
    sender = FrameSender(launch.run_id, launch.preview)
    if not await sender.start():
        return
    sender.offer(PreviewFrame(1, datetime.now(timezone.utc), WIDTH, HEIGHT, _marker_jpeg()))
    await asyncio.sleep(0.3)
    if mode == "crash":
        os._exit(9)
    await sender.close(flush=True)


def executor(mode):
    from autoweb_api.protocol import EventClock, ExecutorResult, Launch, encode
    from autoweb_api.contracts import FinalOutcome

    launch = Launch.model_validate_json(sys.stdin.buffer.readline())
    clock = EventClock(launch.run_id, time.monotonic())
    _emit(clock, {"type": "run_state_changed", "state": "preflight"})
    _emit(clock, {"type": "authentication", "status": "succeeded"},
          step_id="authentication", phase_id="authentication")
    sys.stdout.buffer.write(encode(_manifest(launch)))
    sys.stdout.buffer.flush()
    asyncio.run(_preview(launch, mode))
    sys.stdout.buffer.write(encode(ExecutorResult(
        type="result",
        outcome=FinalOutcome(type="final_outcome", state="succeeded", authentication="succeeded"),
    )))
    sys.stdout.buffer.flush()


def guardian(mode):
    from autoweb_api.guardian import LifecycleLimits, supervise
    from autoweb_api.protocol import Launch

    data = bytearray()
    while not data.endswith(b"\n"):
        data.extend(os.read(sys.stdin.fileno(), 1))
    launch = Launch.model_validate_json(bytes(data))
    epoch = time.monotonic()
    return supervise([sys.executable, __file__, "executor", mode], launch,
                     LifecycleLimits(0.4, 2, 30), epoch)


if __name__ == "__main__":
    if sys.argv[1] == "executor":
        executor(sys.argv[2])
    else:
        raise SystemExit(guardian(sys.argv[2]))
