"""Repeatable localhost benchmark for the supported login flows.

Runs each flow against its in-process fixture site, so results are deterministic
and never touch a real website or mailbox. Run from the repository root:

    conda run -n autoweb python -m scripts.benchmark_login --runs 10

Each iteration owns a fresh browser; reported latency is the login phase only
(LoginResult.elapsed_seconds), not browser launch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Iterator

from autoweb.ai.usage import UsageSnapshot
from autoweb.config import load_configuration
from autoweb.gmail.poller import GmailVerificationProvider
from autoweb.login import LoginError, authenticated_session
from autoweb.metrics import BenchmarkReport, build_report, write_report
from tests.fixtures.email_site import DEMO_EMAIL, EmailSite
from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME, LoginSite
from tests.fixtures.magic_site import MagicSite

REPO = Path(__file__).resolve().parents[1]
LOGIN_BUDGET_SECONDS = 15.0
PROFILES = {
    "password": "local-password.example.json",
    "email_otp": "email-otp.example.json",
    "email_magic_link": "email-magic-link.example.json",
}
ROUTES = {"password": "/login", "email_otp": "/email-login",
          "email_magic_link": "/magic-login?scenario=normal"}
ENVIRON = {
    "LOCAL_LOGIN_USERNAME": DEMO_USERNAME,
    "LOCAL_LOGIN_PASSWORD": DEMO_PASSWORD,
    "EXAMPLE_EMAIL": DEMO_EMAIL,
    "EXAMPLE_USERNAME": DEMO_USERNAME,
    "EXAMPLE_PASSWORD": DEMO_PASSWORD,
}


class FixtureMailbox:
    """Minimal Gmail-compatible mailbox backed by a fixture server's messages."""

    def __init__(self, site: EmailSite):
        self.site = site

    async def list_message_ids(self, query, *, deadline):
        with self.site.lock:
            return tuple(self.site.messages)

    async def get_message(self, identity, *, deadline):
        with self.site.lock:
            return self.site.messages[identity]

    async def mark_read(self, identity, *, deadline):
        return None


@contextmanager
def serve(server) -> Iterator:
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def adapt_profile(flow: str, origin: str, profile: dict) -> dict:
    profile["entry_url"] = origin + ROUTES[flow]
    if flow == "email_otp":
        profile["success"][1] = {"type": "api", "url": "/api/session",
                                 "expected_json": {"authenticated": True}}
        profile["verification"].update(
            sender="noreply@example.test", timeout_seconds=5,
            rejected=[{"type": "element", "target": {"by": "test_id", "value": "otp-error"}}],
            expired=[{"type": "element", "target": {"by": "test_id", "value": "otp-expired"}}],
        )
    elif flow == "email_magic_link":
        profile["success"][1] = {"type": "api", "url": "/api/session",
                                 "expected_json": {"authenticated": True}}
        profile["verification"].update(
            sender="noreply@example.test", timeout_seconds=5, allowed_origins=[origin],
            link_path_prefix="/magic",
            expired=[{"type": "element", "target": {"by": "test_id", "value": "link-expired"}}],
        )
    return profile


def build_configuration(tmp: Path, flow: str, origin: str):
    profile = json.loads((REPO / "configs/sites" / PROFILES[flow]).read_text())
    adapt_profile(flow, origin, profile)
    settings = json.loads((REPO / "configs/settings.json").read_text())
    settings["timeouts"].update(action_ms=700, navigation_ms=5000,
                                login_seconds=LOGIN_BUDGET_SECONDS, verification_seconds=5)
    settings["gmail"]["poll_interval_seconds"] = 0.01
    settings["artifacts_dir"] = str(tmp / "artifacts")
    (tmp / "settings.json").write_text(json.dumps(settings))
    (tmp / "site.json").write_text(json.dumps(profile))
    return load_configuration("site.json", "settings.json", project_root=tmp,
                              environ=ENVIRON)


async def measure(flow: str, runs: int) -> list:
    server_type = {"password": LoginSite, "email_otp": EmailSite,
                   "email_magic_link": MagicSite}[flow]
    with serve(server_type()) as server:
        with TemporaryDirectory(prefix=f"autoweb-bench-{flow}-") as directory:
            configuration = build_configuration(
                Path(directory), flow, f"http://127.0.0.1:{server.server_port}")
            provider = (GmailVerificationProvider(FixtureMailbox(server),
                                                  poll_interval_seconds=0.01)
                        if flow != "password" else None)
            results = []
            for _ in range(runs):
                try:
                    async with authenticated_session(configuration, provider) as result:
                        results.append(result)
                except LoginError as error:
                    results.append(error.result)
            return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=5,
                        help="iterations per flow (default: 5)")
    parser.add_argument("--flow", action="append", choices=sorted(PROFILES),
                        help="flow to benchmark; repeatable (default: all)")
    parser.add_argument("--output", default=str(REPO / "artifacts/benchmarks/latest"),
                        help="JSON/Markdown destination without suffix")
    parser.add_argument("--input-price", type=float, default=None,
                        help="optional USD per million prompt tokens")
    parser.add_argument("--output-price", type=float, default=None,
                        help="optional USD per million candidate tokens")
    arguments = parser.parse_args()
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    flows = arguments.flow or list(PROFILES)
    usage = UsageSnapshot()
    runs_by_flow = {flow: asyncio.run(measure(flow, arguments.runs)) for flow in flows}
    cost = None
    if arguments.input_price is not None and arguments.output_price is not None:
        cost = usage.estimated_cost(input_usd_per_million=arguments.input_price,
                                    output_usd_per_million=arguments.output_price)
    report: BenchmarkReport = build_report(
        runs_by_flow, {flow: LOGIN_BUDGET_SECONDS for flow in flows},
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ai_usage=usage, ai_cost_usd=cost,
    )
    json_path, markdown_path = write_report(report, arguments.output)
    print(report.to_markdown())
    print(f"Wrote {json_path} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
