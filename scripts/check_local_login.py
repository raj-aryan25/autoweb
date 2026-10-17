"""Manually verify local password login and a protected action in the same session."""

import argparse
import asyncio
import math
from dataclasses import replace
from pathlib import Path

from pydantic import SecretStr

from autoweb.browser import BrowserError, click, wait_for_state
from autoweb.config import ConfigurationError, load_configuration
from autoweb.config.models import Locator
from autoweb.login import LoginError, authenticated_session


async def check(configuration, pause_seconds: float) -> None:
    async with authenticated_session(configuration) as result:
        session = result.session
        print(f"PASS: confirmed authentication for {result.site}", flush=True)
        await click(session.page, Locator(by="role", value="button", name="Perform protected action"),
                    timeout_ms=session.timeouts.action_ms)
        target = Locator(by="test_id", value="protected-result")
        await wait_for_state(session.page, target, timeout_ms=session.timeouts.action_ms)
        text = await session.page.get_by_test_id("protected-result").inner_text(
            timeout=session.timeouts.action_ms)
        if text != "Protected action completed":
            raise RuntimeError("Protected action did not produce the expected result")
        print("PASS: protected action completed in the authenticated session", flush=True)
        if pause_seconds:
            print(f"Browser will close in {pause_seconds:g} seconds.", flush=True)
            # Observation time only; action readiness is checked above using element state.
            await asyncio.sleep(pause_seconds)
    print("PASS: session cleaned up")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--two-step", action="store_true", help="Use username-then-password demo")
    parser.add_argument("--invalid-password", action="store_true", help="Synthetic rejection check; expected exit 1")
    parser.add_argument("--headless", action="store_true", help="Hide the browser")
    parser.add_argument("--pause-seconds", type=float, default=15, help="Observation time after success (0-300)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.pause_seconds) or not 0 <= args.pause_seconds <= 300:
        parser.error("--pause-seconds must be between 0 and 300")
    root = Path(__file__).resolve().parents[1]
    filename = "local-password-two-step.example.json" if args.two_step else "local-password.example.json"
    try:
        configuration = load_configuration(f"configs/sites/{filename}", project_root=root,
                                           env_file="configs/demo.env.example")
        configuration.settings.browser.headless = args.headless
        configuration.settings.browser.slow_mo_ms = 0 if args.headless else 150
        if args.invalid_password:
            configuration = replace(configuration, secrets={
                **configuration.secrets, "credential:password": SecretStr("incorrect-demo-password"),
            })
        asyncio.run(check(configuration, args.pause_seconds))
    except ConfigurationError as error:
        print(f"Configuration error: {error}")
        return 2
    except LoginError as error:
        print(str(error))
        for artifact in error.result.artifacts:
            print(f"Failure artifact: {artifact}")
        return 1
    except BrowserError as error:
        print(str(error))
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
