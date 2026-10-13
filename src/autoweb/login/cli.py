"""Thin password-login runner for one selected site profile."""

import argparse
import asyncio

from autoweb.browser import BrowserError
from autoweb.config import ConfigurationError, load_configuration

from .errors import LoginError
from .flow import authenticated_session


async def _run(configuration) -> None:
    async with authenticated_session(configuration) as result:
        print(f"Login succeeded: {result.site}; completed {len(result.steps)} steps")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, help="One site JSON path relative to the project root")
    parser.add_argument("--settings", default="configs/settings.json")
    parser.add_argument("--project-root")
    parser.add_argument("--env-file")
    parser.add_argument("--headed", action="store_true", help="Show the browser during login")
    args = parser.parse_args(argv)
    try:
        configuration = load_configuration(
            args.site, args.settings, project_root=args.project_root, env_file=args.env_file,
        )
        if args.headed:
            configuration.settings.browser.headless = False
        asyncio.run(_run(configuration))
    except ConfigurationError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    except (LoginError, BrowserError) as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Login interrupted\n")
    return 0
