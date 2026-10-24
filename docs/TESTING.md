# Testing with AutoWeb

Use conda `autoweb` for every command. From the repository root:

```sh
conda run -n autoweb python -m pip install -e ".[test]"
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb python -m pytest -q
```

The final command runs deterministic unit tests and controlled Chromium tests without
Gmail, Gemini, OAuth consent, or live websites. The live test is skipped. Local
servers need loopback socket access; Chromium needs permission to launch. Tests
use function-scoped async fixtures and pytest-asyncio strict mode. Each browser
fixture owns its context and closes it after setup failures, test failures, and
normal completion. Gmail-backed live attempts must run sequentially.

Useful subsets:

```sh
conda run -n autoweb python -m pytest -q -m unit
conda run -n autoweb python -m pytest -q -m integration
conda run -n autoweb python -m pytest -q -m e2e
```

The `e2e` selection includes the three controlled login modes and the skipped live
test. `tests/integration/test_pytest_fixtures.py` shows login followed by a protected
action. `test_pytest_entrypoint.py` verifies downstream pytest commands, report
files, failure exit codes, and trace retention. Configuration/browser/Gmail unit
tests do not require browser binaries.

## Use the fixtures in another test project

Install AutoWeb with the test extra and add this to the project's root `conftest.py`:

```python
pytest_plugins = ["autoweb.pytest_plugin"]
```

Alternatively load it using `-p autoweb.pytest_plugin`. Avoid registering it twice.
Use `-p autoweb.pytest_plugin` when passing `--autoweb-root` outside the test
project: pytest may otherwise infer its discovery root from the configuration
directory before loading the project's `conftest.py` and its custom CLI options.
No global pytest entry point is installed. Production login functions remain usable
without pytest; installing only the runtime dependencies does not load pytest.

| Fixture | Value / ownership |
| --- | --- |
| `autoweb_configuration` | Resolved configuration; override or indirectly parameterize with profile paths |
| `autoweb_settings` | Runtime settings from that configuration |
| `autoweb_site_profile` | Validated site profile |
| `autoweb_fake_provider` | Fresh in-memory provider; `enqueue("001234")` adds a synthetic code |
| `autoweb_verification_provider` | Fake by default; `None` for password login; explicitly selected Gmail otherwise |
| `autoweb_session` | Isolated browser/context/page; owns cleanup, requires provider startup first |
| `autoweb_login` | Successful `LoginResult`; its `.session` stays open through the test |

```python
import pytest

@pytest.mark.asyncio
async def test_account(autoweb_login):
    page = autoweb_login.session.page
    await page.get_by_role("button", name="Account settings").click()
    assert await page.get_by_role("heading", name="Account settings").is_visible()
```

Select a profile with `--autoweb-site`; repeat the option to run once per profile.
Omitting a profile skips tests that need the default configuration fixture.
Each selected profile gets its own session. For per-test selection:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("autoweb_configuration", [
    "configs/sites/first.local.json",
    "configs/sites/second.local.json",
], indirect=True)
async def test_login(autoweb_login):
    assert autoweb_login.status == "succeeded"
```

The explicit indirect list takes precedence over CLI profile selection. You can
instead override `autoweb_configuration` with a fixture that builds a controlled
site configuration. In that fixture, request `autoweb_fake_provider` and enqueue
the code expected by the controlled website before requesting `autoweb_login`.
The fake provider fails clearly with `fake_verification_empty` when no code is
queued. It does not synthesize email or interact with Gmail. Override
`autoweb_verification_provider` for another implementation of the provider protocol.

Without `--autoweb-live`, the public browser fixture accepts only loopback profiles,
blocks external browser requests (including redirects/subresources), and rejects AI
enablement. Custom fixtures/providers remain responsible for their own I/O.

## Explicit live selection

Live tests are marked `live` and skipped unless `--autoweb-live` is present. The
default provider remains fake even with that flag. Supply test-account environment
variables using the environment or an explicit dotenv file. Example password test:

```sh
conda run -n autoweb python -m pytest tests/e2e/test_live_login.py -q --autoweb-live --autoweb-site configs/sites/practice-test-login.json
```

Use the profile filenames present in `configs/sites/`. For Gmail OTP, first follow
[email setup](EMAIL_LOGIN_SETUP.md), including explicit OAuth setup if needed, then:

```sh
conda run -n autoweb python -m pytest tests/e2e/test_live_login.py -q --autoweb-live --autoweb-provider gmail --autoweb-site configs/sites/expandtesting-otp.example.json
```

`--autoweb-provider gmail` requires `--autoweb-live`. Provider startup uses existing
OAuth credentials; pytest never initiates consent. Use `--autoweb-root PATH`,
`--autoweb-settings PATH`, and `--autoweb-env-file PATH` to select configuration
outside this repository. Paths resolve against the selected configuration root.
Configuration and login failures give nonzero pytest outcomes.

## Diagnostics and sensitive artifacts

Each public session gets a unique `artifacts/pytest/<id>/` directory (under the
configured artifacts directory). `login.json` records login status, elapsed time,
completed step actions/statuses, failure stage/reason, and login artifact paths.
It excludes credential values, OTPs, tokens, request URLs, and session objects.
It describes login only: a subsequent protected-action failure is recorded by
pytest, while the login JSON can still say `succeeded`.

Failed login captures a screenshot before cleanup. The fixture also captures a
screenshot for setup/test-body failures, including protected actions after login.
Screenshots mask configured fill/OTP targets and password inputs. Site-rendered
secrets elsewhere on a page may still be visible. Artifact paths appear in pytest's
captured report sections; unavailable diagnostics do not replace the login failure.

Tracing is **off by default**. Explicitly opt in with
`--autoweb-trace retain-on-failure` or `--autoweb-trace on`. The former records the
whole session and writes `trace.zip` only for setup/test-body failure; successful
recordings are discarded. The latter retains successful recordings too. Trace
startup errors fail fixture setup; teardown capture errors are reported best effort.

**Raw Playwright traces can contain passwords, OTPs, cookies, authorization headers,
DOM snapshots, network bodies and unmasked screenshots. They are not redacted.**
Use synthetic accounts where possible, keep artifacts local, restrict access,
and do not attach raw traces to public issues. Ignored artifact/session directories
prevent accidental normal Git additions; they are not encryption or access control.

```sh
conda run -n autoweb python -m playwright show-trace artifacts/pytest/ID/trace.zip
```

The standalone runner remains independent of pytest:

```sh
conda run -n autoweb python -m autoweb.login --site configs/sites/password.example.json --report artifacts/login.json
conda run -n autoweb python scripts/run_login.py --help
```

`--report` is optional and relative to the working directory. It writes the same
JSON schema on login success/failure. Configuration/provider startup failures occur
before a login result exists and do not create a report. Invalid configuration exits
2; login/browser/provider/report-write failure exits 1. The example.com profile
illustrates the schema; configure a runnable site before executing it.

## Profile authoring and session reuse

Follow [the configuration schema](../configs/README.md). A profile contains a
non-secret entry URL, credential environment references, ordered parameterized
steps, stable/scoped locators, explicit authenticated UI assertions, and optional
same-origin session API checks. Email flows additionally declare verification
filters, code format and request/fill/submit steps. Add a small adapter only for
flows that cannot be expressed by existing steps. A disappearing form is not proof
of authentication.

Default fixtures always create a fresh context and perform login. Prefer keeping
related protected actions in one test using `autoweb_login.session`. Optional manual
storage-state reuse can save cookies/local storage to an ignored path:

```python
await result.session.context.storage_state(path="artifacts/account.storage-state.json")
```

To restore deliberately, use a separately owned browser/context:

```python
from time import monotonic
from autoweb.browser import launch_browser
from autoweb.login.assertions import wait_for_authentication

async with launch_browser(configuration.settings.browser) as browser:
    context = await browser.new_context(storage_state="artifacts/account.storage-state.json")
    try:
        page = await context.new_page()
        await page.goto(protected_url, wait_until="domcontentloaded",
                        timeout=configuration.settings.timeouts.navigation_ms)
        await wait_for_authentication(page, configuration.site, monotonic() + 5)
        # Protected actions only after current authentication is proved.
    finally:
        await context.close()
```

Use a known protected URL for the same site/account. A stored cookie expiry date
alone cannot establish validity: server-side revocation/logout may invalidate it.
If checks fail, discard the saved state and explicitly perform a fresh login.
Storage state may omit sessionStorage or other site-specific authentication data.
It contains authentication secrets and should never be committed or shared. There
is no automatic cache, cross-account reuse, or hidden login retry in the fixtures.
