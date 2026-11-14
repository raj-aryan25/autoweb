"""Test-only magic rejection harness, using the real API/package/guardian.

The public launcher and StartRun accept no scenario or executor-command override.
Only this trusted test process selects an expired controlled magic challenge.
"""

import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def rejected_fixture():
    from autoweb.testing import magic_site
    original = magic_site.MagicSite

    class RejectedHandler(magic_site.MagicHandler):
        def do_POST(self):
            if self.path == '/magic-challenge':
                self.path += '?scenario=expired'
            return super().do_POST()

    class RejectedSite(original):
        def __init__(self):
            super().__init__()
            self.RequestHandlerClass = RejectedHandler

    magic_site.MagicSite = RejectedSite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['server', 'guardian', 'executor'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--cooperative-seconds', type=float, default=5)
    parser.add_argument('--force-seconds', type=float, default=5)
    parser.add_argument('--max-seconds', type=float, default=240)
    parser.add_argument('--epoch', type=float)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.mode == 'executor':
        rejected_fixture()
        from autoweb_api.executor import main as executor
        sys.argv = [__file__, '--root', str(args.root), '--artifacts', str(args.artifacts), '--epoch', str(args.epoch)]
        return executor()
    if args.mode == 'guardian':
        from autoweb_api.guardian import LifecycleLimits, supervise
        from autoweb_api.protocol import Launch, MAX_LINE
        line = bytearray()
        while not line.endswith(b'\n') and len(line) <= MAX_LINE:
            value = os.read(sys.stdin.fileno(), 1)
            if not value:
                return 1
            line.extend(value)
        launch = Launch.model_validate_json(bytes(line))
        epoch = time.monotonic()
        command = [sys.executable, __file__, 'executor', '--root', str(args.root),
                   '--artifacts', str(args.artifacts), '--epoch', str(epoch)]
        return supervise(command, launch, LifecycleLimits(args.cooperative_seconds, args.force_seconds,
                         args.max_seconds), epoch, receipt=args.receipt)
    import uvicorn
    from autoweb_api.api import create_app
    from autoweb_api.service import RunService
    from autoweb_api.settings import ServiceSettings
    from autoweb_api.worker import WorkerCoordinator

    class RejectionCoordinator(WorkerCoordinator):
        def _command(self, limits):
            return [sys.executable, __file__, 'guardian', '--root', str(self.root), '--artifacts', str(self.artifacts),
                    '--cooperative-seconds', str(limits.cooperative_seconds), '--force-seconds', str(limits.force_seconds),
                    '--max-seconds', str(limits.max_seconds)]

    settings = ServiceSettings(args.root, data_directory=args.state, artifacts=args.state / 'runs', port=args.port,
                               ui_directory=ROOT / 'apps/web/dist')
    app = create_app(settings, service_factory=lambda settings: RunService(settings, coordinator_factory=RejectionCoordinator))
    uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False, access_log=False, ws='none')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
