"""Thin password/email-OTP login runner for one selected site profile."""

import argparse
import asyncio
from pathlib import Path

from autoweb.browser import BrowserError
from autoweb.config import ConfigurationError, load_configuration
from autoweb.contracts import VerificationError
from autoweb.reporting.json_report import write_login_report

from .errors import LoginError
from .flow import authenticated_session


async def _run(configuration, report: Path | None = None) -> None:
    try:
        async with authenticated_session(configuration) as result:
            if report is not None:
                write_login_report(result, report)
            print(f"Login succeeded: {result.site}; completed {len(result.steps)} steps")
    except LoginError as error:
        if report is not None:
            write_login_report(error.result, report)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, help="One site JSON path relative to the project root")
    parser.add_argument("--settings", default="configs/settings.json")
    parser.add_argument("--project-root")
    parser.add_argument("--env-file")
    parser.add_argument("--report", type=Path, help="Write a secret-free JSON login result")
    parser.add_argument("--headed", action="store_true", help="Show the browser during login")
    parser.add_argument(
        "--slowmo", nargs="?", const=700, type=int, metavar="MS",
        help="Slow browser actions by MS milliseconds (default when supplied: 700; 0 disables)",
    )
    args = parser.parse_args(argv)
    if args.slowmo is not None and args.slowmo < 0:
        parser.error("--slowmo must be a nonnegative number of milliseconds")
    try:
        configuration = load_configuration(
            args.site, args.settings, project_root=args.project_root, env_file=args.env_file,
        )
        if args.headed:
            configuration.settings.browser.headless = False
        if args.slowmo is not None:
            configuration.settings.browser.slow_mo_ms = args.slowmo
        asyncio.run(_run(configuration, args.report))
    except ConfigurationError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    except (LoginError, BrowserError, VerificationError) as error:
        parser.exit(1, f"{error}\n")
    except OSError:
        parser.exit(1, "Cannot write login report\n")
    except KeyboardInterrupt:
        parser.exit(130, "Login interrupted\n")
    return 0
