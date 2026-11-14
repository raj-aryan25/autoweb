"""Launch the built AutoWeb Studio and its API on one loopback origin."""

import argparse
import errno
import importlib.util
from pathlib import Path
import socket
import sys


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('port must be an integer') from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return port


def run_server(settings, listener):
    # Imports stay lazy: --help and package imports never construct the service.
    import uvicorn
    from .api import create_app

    app = create_app(settings)

    class StudioServer(uvicorn.Server):
        async def shutdown(self, sockets=None):
            service = getattr(app.state, 'service', None)
            if service is not None:
                # Close SSE and request cooperative worker stop BEFORE draining
                # HTTP connections, then let lifespan await verified cleanup.
                service.request_shutdown()
            await super().shutdown(sockets=sockets)

    server = StudioServer(uvicorn.Config(app, host='127.0.0.1', port=settings.port,
        workers=1, proxy_headers=False, access_log=False, timeout_graceful_shutdown=25, ws='none'))
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        pass
    return 0 if server.started else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='repository root (default: current directory)')
    parser.add_argument('--port', type=port_number, default=8766, help='fixed loopback port (default: 8766)')
    parser.add_argument('--ui-directory', type=Path, help='built UI directory (default: ROOT/apps/web/dist)')
    parser.add_argument('--data-directory', type=Path, help='private history/evidence directory')
    args = parser.parse_args(argv)
    for module in ('fastapi', 'starlette', 'uvicorn'):
        if importlib.util.find_spec(module) is None:
            print('Studio server dependencies are missing. Install with: conda run -n autoweb python -m pip install -e ".[server]"', file=sys.stderr)
            return 2
    root = args.root.resolve()
    ui = (args.ui_directory or root / 'apps/web/dist').resolve()
    if not (ui / 'index.html').is_file():
        print('Studio UI build is missing. From the repository root run: conda run -n autoweb npm ci; then conda run -n autoweb npm run build', file=sys.stderr)
        return 2
    from .process_tree import supported
    if not supported():
        print('Studio execution currently requires Linux with verified process containment.', file=sys.stderr)
        return 2
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', args.port))
            listener.listen(128)
        except OSError as error:
            message = 'is already in use; stop its owner or choose --port' if error.errno == errno.EADDRINUSE else 'cannot be bound; check local permissions for --port'
            print(f'Loopback port {args.port} {message}. No service was started.', file=sys.stderr)
            return 2
        from .settings import ServiceSettings
        state = args.data_directory.resolve() if args.data_directory else None
        settings = ServiceSettings(root, ui_directory=ui, port=args.port, data_directory=state,
                                   artifacts=state / 'runs' if state else None)
        print(f'Starting AutoWeb Studio at http://127.0.0.1:{args.port}\nCtrl+C stops active execution and waits for browser cleanup.', flush=True)
        try:
            return run_server(settings, listener)
        except ModuleNotFoundError:
            print('Studio dependencies are incomplete. Install the server extra in conda autoweb.', file=sys.stderr)
            return 2
    finally:
        listener.close()


if __name__ == '__main__':
    raise SystemExit(main())
