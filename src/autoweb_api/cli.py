"""Development/local API launcher. The full Studio launcher belongs to Step 8."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-directory", type=Path)
    parser.add_argument("--ui-directory", type=Path)
    args = parser.parse_args()
    from .settings import ServiceSettings

    settings = ServiceSettings(args.root, data_directory=args.data_directory, ui_directory=args.ui_directory, port=args.port)
    from .api import create_app
    import uvicorn

    uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1, proxy_headers=False,
                access_log=False, timeout_graceful_shutdown=25, ws="none")


if __name__ == "__main__":
    main()
