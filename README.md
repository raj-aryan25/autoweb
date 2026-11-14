# AutoWeb

AutoWeb is a Python framework and local web workspace for reusable website login
automation. **AutoWeb Studio** connects the React frontend to the Python backend
and the existing Playwright login package.

## Current features

The eight-step Studio integration milestone is complete:

- Real controlled password, email OTP and magic-link login workflows.
- Headed Chromium execution, configurable slow motion, preflight and safe Stop.
- Live execution events, stable step inspection and separate connection/run states.
- SQLite history, refresh/reconnect recovery and idempotent Start requests.
- Sanitized JSON reports and registered masked failure screenshots from controlled
  pages; authentication and browser cleanup remain separate outcomes.
- Optional Gmail verification for approved profiles and opt-in Gemini assistance.
  Configuration alone does not prove either external service is working.
- A visibly labeled, explicitly selected demo mode, separate from real execution.

The latest milestone verification recorded **535 passed / 6 expected skips** in
one full Python run, followed by **21 passing focused checks** after the final
launcher fix. Build/schema checks, the controlled UI acceptance matrix and an
actual visible headed desktop window were also verified. These counts are separate,
not additive. See [final acceptance](docs/STUDIO_ACCEPTANCE.md).

## Local requirements

- **Linux** for the Studio backend's current process-containment implementation.
  Native Windows/macOS execution is not supported by this launcher yet.
- Conda with an environment named **autoweb**. Python 3.12 and Node.js 24 were used
  for the verified local setup; Python dependencies are defined in `pyproject.toml`.
- npm for the React/TypeScript/Vite frontend, locked by `package-lock.json`.
- Playwright's matching Chromium binary. A graphical desktop is needed for headed
  execution; choose headless Chromium in Run configuration when no display exists.

Run the following commands from the **repository root**, containing both
`pyproject.toml` and `package.json`. Use conda `autoweb` for project commands.

## First-time setup

If the environment does not exist, create it first. Skip this command when using
the existing `autoweb` environment:

```sh
conda create -n autoweb -c conda-forge python=3.12 nodejs=24
```

Install the Python backend/test dependencies, the browser, and frontend packages:

```sh
conda run -n autoweb python -m pip install -e ".[server,test]"
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb npm ci
```

If Chromium reports missing Linux system libraries, install its system dependencies
with `conda run -n autoweb python -m playwright install-deps chromium`, following
any administrator prompt on your machine.

**Controlled profiles need no `.env`, live mailbox, OAuth consent or AI key.**
For approved live profiles, copy `.env.example` to `.env` and provide only the
credentials required by the selected profile. Gmail dependencies are optional:

```sh
conda run -n autoweb python -m pip install -e ".[server,gmail,ai,test]"
```

Gemini uses the shared REST client; the `ai` extra is retained for compatibility.
Use the existing explicitly authorized Gmail setup described in
[Gmail setup](src/autoweb/gmail/README.md). Studio does not initiate OAuth consent.
Live profiles contact real services only when explicitly started; new websites
require separate authorization. Phase VII Part 2 live validation remains separate
from this completed controlled integration milestone.

## Run the complete application

This mode serves the built frontend and backend together on one local origin.
Build the UI, then start the installed launcher:

```sh
conda run -n autoweb npm run build
conda run --no-capture-output -n autoweb autoweb-studio --root .
```

Open **http://127.0.0.1:8766**. Keep the launcher terminal running. No separate
frontend or backend process is needed in this mode. Rebuild the UI after changing
frontend source to update the files served by the launcher.

To verify your local setup:

1. Open **Run configuration** and select **Controlled password**, **Controlled OTP**
   or **Controlled magic link**.
2. Keep **Chromium · headed** to watch the browser, or select headless if needed.
   Leave AI assistance off for controlled profiles; those runs use no external AI.
3. Apply configuration, then choose **Start run**. Controlled fixture servers start
   automatically for the run; no separate login-site server is needed.
4. Inspect Details, Evidence and Events, refresh the page, or reopen Run history.
   **Stop run** requests cancellation and waits for verified browser cleanup.

Press **Ctrl+C** in the launcher terminal to stop the application gracefully.
Closing the browser tab alone does not stop a running login.

An occupied port produces an error; the launcher never silently chooses another
port. To deliberately use another port:

```sh
conda run --no-capture-output -n autoweb autoweb-studio --root . --port 8767
```

Then open `http://127.0.0.1:8767`. Optional `--data-directory PATH` isolates private
history, receipts and evidence; `--ui-directory PATH` selects another built UI.
Default history is under `artifacts/studio/service/`, with execution evidence under
`artifacts/studio/runs/`. Existing history survives normal shutdown/restart.

## Run frontend and backend separately for development

Use two terminals, both at the repository root. Stop the combined launcher before
using its backend port for the separate API server.

**Terminal 1 — Python backend (port 8766):**

```sh
conda run --no-capture-output -n autoweb autoweb-api --root .
```

Backend health URL: **http://127.0.0.1:8766/api/health**. The API-only command does
not serve the frontend by default.

**Terminal 2 — React/Vite frontend (port 5173):**

```sh
conda run --no-capture-output -n autoweb npm run dev
```

Open **http://127.0.0.1:5173** to use Studio. Vite proxies `/api` to the backend at
`http://127.0.0.1:8766`, so keep these development ports unchanged. Use the exact
`127.0.0.1` URL: `localhost:5173` is a different origin from the configured allowlist.
Vite uses a strict port and fails on conflicts instead of moving to 5174.

Frontend edits reload through Vite. Restart the API process after changing Python
backend code. Press **Ctrl+C in each terminal** to stop the development servers.
For this fixed proxy, changing only the API port requires a matching Vite proxy
change and review of the API's origin settings.

## Demo mode and common local issues

Append `?mode=demo` to either UI URL to open the explicit simulated preview, such
as `http://127.0.0.1:5173/?mode=demo`. Demo mode does not run Python workflows or
contact websites, Gmail or AI; its tab-local history resets on refresh. Real mode
never silently falls back to demo when the backend is unavailable.

| Symptom | What to check |
| --- | --- |
| `autoweb-studio` or `autoweb-api` not found | Run the editable Python install above in conda `autoweb`; repeat it after entry-point changes. |
| Missing server dependencies | Install `.[server,test]` in the same environment. |
| Missing UI build | Run `npm ci`, then `npm run build`, both through conda `autoweb`. |
| Port already in use | Stop the process you own, or deliberately choose another combined-launcher port. Keep development ports 5173/8766 aligned. |
| Studio cannot reach the runner | Start the API in Terminal 1; check `/api/health`, the proxy target and the exact UI origin. |
| Browser startup fails | Install Chromium and its Linux dependencies; use headless mode if no desktop display is available. |
| Cleanup is uncertain | Inspect the saved run and runner diagnostics. New starts remain blocked; do not delete history to bypass the containment safeguard. |

See the [frontend README](apps/web/README.md), [Studio UI guide](docs/STUDIO_UI.md),
[verification/evidence boundary](docs/STUDIO_VERIFICATION.md), and
[launcher/acceptance guide](docs/STUDIO_ACCEPTANCE.md) for further details.

## Structure

| Location | Responsibility |
| --- | --- |
| src/autoweb/config/ | Settings, typed site profiles, credential preflight, validation CLI |
| src/autoweb/browser/ | Async Playwright lifecycle, strict locator resolution, actions, and state waits |
| src/autoweb/gmail/ | Explicit OAuth setup, Gmail API boundary, MIME decoding, fresh verification polling |
| src/autoweb/login/ | Password-login orchestration, authentication checks, and session handoff |
| src/autoweb/ai/ | Shared optional Gemini client, validated extraction, and constrained locator assistance |
| src/autoweb/reporting/ | Concise JSON step/login reports |
| src/autoweb/metrics.py | Aggregated success rate, latency percentiles, and budget overruns |
| src/autoweb/pytest_plugin.py | Opt-in async pytest fixtures, live selection, and diagnostics |
| src/autoweb/testing/ | Reusable loopback fixture sites and in-memory verification providers |
| src/autoweb_api/ | Local HTTP/SSE API, supervised workers, SQLite history, evidence and launcher |
| apps/web/ | React/TypeScript Studio UI, explicit demo adapter and acceptance scripts |
| configs/settings.json | Non-secret browser, timeout, mailbox-path, and AI defaults |
| configs/sites/ | Non-secret example site profiles |
| tests/unit/ | Configuration and browser failure-path tests |
| tests/integration/ | Real headless Chromium checks against controlled local pages |
| tests/e2e/ | Explicitly selected live-profile login tests |
| tests/fixtures/ | Controlled DOM/frame pages and a cookie-authenticated login demo |
| scripts/ | Thin login, Gmail diagnostic, and local benchmark entry points |
| artifacts/ | Ignored screenshots, JSON reports, optional sensitive traces and session state |

The login package executes password, email OTP and magic-link flows and returns an active authenticated session; see its [usage and local demo](src/autoweb/login/README.md). The browser package implements reusable primitives; see its [API and ownership documentation](src/autoweb/browser/README.md). Gmail can retrieve fresh codes independently; see its [setup, diagnostics, and provider contract](src/autoweb/gmail/README.md). The [pytest interface](docs/TESTING.md) provides reusable authenticated-session fixtures and diagnostics. Phase VII adds optional AI locator assistance and magic links; see [setup and the validation split](docs/PHASE7.md). Part 2 live validation succeeded for Expand Testing OTP (fresh Gmail OTP login, session reuse, logout revocation) with zero Gemini calls while AI was enabled; Gemini API returned HTTP 503 for gemini-3.8-flash and magic-link demos (Better Auth, Stytch) failed before issuing usable links. See [the live validation report](docs/PHASE7_LIVE_VALIDATION.md). Importing the package does not open a browser, authenticate with Gmail, load a dotenv file, or create an AI client. Configuration loading is explicit.

## Check configuration

The original example.com profiles illustrate the schema. The local-password example profiles are runnable against the controlled demo described in [Password login](src/autoweb/login/README.md). Check a profile without supplying credentials:

~~~powershell
conda run -n autoweb python -m autoweb.config --site configs/sites/password.example.json --schema-only
~~~

Copy .env.example to .env at the repository root and supply test-account values to check credential references as well:

~~~powershell
conda run -n autoweb python -m autoweb.config --site configs/sites/password.example.json
~~~

The equivalent installed command is autoweb-config. Invalid configuration exits with status 2. Schema-only mode skips secret resolution and is not a readiness check for login. Neither mode makes Gmail, Gemini, or browser calls; OAuth-file validity is checked only by explicit Gmail operations; browser availability is exercised by the browser integration tests.

The configuration API is load_configuration, load_settings, and load_site_profile from autoweb.config. load_configuration performs credential preflight by default and returns settings, the typed site profile, and masked SecretStr values under keys such as credential:password and verification:recipient. Secrets are omitted from the configuration representation. Execution code must explicitly access a secret when filling a field and must not log it.

## Paths and environment precedence

- Relative site, settings, dotenv, OAuth, and artifact paths resolve against the repository root, independently of the working directory.
- The editable installation discovers this root from its package location. Set AUTOWEB_ROOT or pass --project-root for another configuration root, including a non-editable installation.
- Defaults come from configs/settings.json. The root .env supplies overrides and credentials; process environment variables take precedence.
- Use --env-file to select another dotenv file explicitly.
- Loading dotenv values does not mutate the process environment or interpolate other variables.
- Gmail OAuth paths default to ignored secrets/gmail/credentials.json and secrets/gmail/token.json. Existing OAuth files remain in the original repository; absolute path overrides can reuse them without relocation.

See [configs/README.md](configs/README.md) for the schema and environment overrides. Credentials, OAuth files, generated artifacts, and session state are ignored by Git.

## Tests

For a step-by-step visible password demo and live Gmail checks, use the [manual verification walkthrough](docs/MANUAL_VERIFICATION.md). It lists the scripts, local configuration files, expected outcomes, and failure checks.

~~~powershell
conda run -n autoweb python -m pytest -q
~~~

Tests cover profile validation, credential resolution, secret-safe output, paths, import isolation, browser lifecycle, scoped locators, nested iframes, open Shadow DOM, password/OTP filling, complete password login, authenticated-session reuse, explicit failures, and state waits. Gmail tests cover saved synthetic MIME messages, fresh-code correlation, pagination, deadlines, API/auth errors, explicit acknowledgment, and optional AI validation. Browser integration tests use a loopback HTTP server and headless Chromium. The default command skips live tests and does not use live websites or Gmail/Gemini access. See [testing and diagnostics](docs/TESTING.md) for fixture usage, repeated profile selection, explicit live commands, sensitive trace handling, and optional session-state reuse.

For frontend compilation and shared-contract freshness, run from the repository root:

```sh
conda run -n autoweb npm run typecheck
conda run -n autoweb npm run build
conda run -n autoweb python scripts/generate_studio_contracts.py --check
```

Controlled end-to-end UI commands and desktop requirements are documented in
[Studio acceptance](docs/STUDIO_ACCEPTANCE.md) and the
[frontend README](apps/web/README.md).

## Measured metrics

`src/autoweb/metrics.py` aggregates login outcomes into success rate, p50/p95 latency,
per-step timing, and budget overruns. Repeat every local flow against its in-process
fixture site (no real website or mailbox):

~~~powershell
conda run -n autoweb python -m scripts.benchmark_login --runs 10
~~~

The command prints a Markdown summary and writes JSON plus Markdown to
`artifacts/benchmarks/`. Every run records whether the login stayed inside its
configured deadline, and the report includes AI call and token totals. Optional
`--input-price` and `--output-price` values add a list-price cost estimate; no
pricing table is hardcoded. JSON login reports include an `ai` section whenever the
caller owns a client, so a run that never needed locator fallback shows zero calls.


## Project history

AutoWeb is a standalone repository containing the modular framework implemented through Phase VI and Phase VII Part 1. Legacy sources remain in D:/RAJ ARYAN/Codec/scaile-tech/scaile-backlink: gmail-auto/ provided reference code for Phase IV, and shadowdom-auto/ contains the earlier browser automation. They are not runtime dependencies of AutoWeb. Historical trial-codes/ experiments are excluded.

The [Studio integration plan](docs/NEXT_MILESTONE_INTEGRATION_PLAN.md) records the completed eight-step local UI/backend milestone. Deterministic email login tests require no live mailbox. An opt-in live Gmail OTP smoke test passed against Expand Testing after explicit OAuth authorization, including protected-session reuse and logout. See [setup and the runnable OTP profile](docs/EMAIL_LOGIN_SETUP.md). Phase VII uses controlled local sites and injected AI responses; Part 2 live validation confirmed Expand Testing OTP success (fresh Gmail login, session reuse, logout) with zero Gemini calls while AI was enabled, though gemini-3.8-flash returned HTTP 503 and magic-link demos (Better Auth, Stytch) failed before issuing usable links (see [live validation report](docs/PHASE7_LIVE_VALIDATION.md)).
