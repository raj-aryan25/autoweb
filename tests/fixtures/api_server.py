"""Real loopback API process for crash/disconnect tests; no production imports of tests."""

import argparse

from autoweb_api.api import create_app
from autoweb_api.settings import ServiceSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, required=True)
    parser.add_argument("--state", type=str, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    from pathlib import Path
    import uvicorn

    state = Path(args.state)
    settings = ServiceSettings(Path(args.root), data_directory=state, artifacts=state / "artifacts", port=args.port)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False,
                proxy_headers=False, log_level="warning", ws="none")


if __name__ == "__main__":
    main()
