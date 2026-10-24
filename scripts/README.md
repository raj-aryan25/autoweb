# Entry points

Configuration validation is available through `python -m autoweb.config` and `autoweb-config`.

Password and email OTP login run through `python -m autoweb.login`, `autoweb-login`, or this folder's thin `run_login.py` wrapper. See [the login documentation](../src/autoweb/login/README.md) for configuration, session reuse, and a controlled local demo, and [Phase V setup](../docs/EMAIL_LOGIN_SETUP.md) for the practice-site command and environment files.

Add `--report artifacts/login.json` to write a concise login result and completed step
outcomes on success or login failure. The runner exits nonzero on failure. For reusable
pytest sessions, profile parameterization, live-test selection, and optional Playwright
traces, see [testing and diagnostics](../docs/TESTING.md).

For a visible local demo including a protected action and an observation window, use `check_local_login.py` after starting the fixture server. See the [manual verification walkthrough](../docs/MANUAL_VERIFICATION.md) for exact setup, success/failure checks, and files to customize for Gmail.

Independent Gmail diagnostics run through `python -m autoweb.gmail`, `autoweb-gmail`, or `scripts/read_verification.py`. Use `--authorize` for explicit OAuth setup, or `--site` for fresh-mail polling. Codes stay hidden unless `--show-code` is selected; diagnostics never perform website actions or mark messages read. See [Gmail setup and usage](../src/autoweb/gmail/README.md). Keep these scripts thin; reusable behavior belongs in `src/autoweb/`.
