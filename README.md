# AutoWeb

A Python framework for reusable Playwright login tests, with an independent Gmail verification module. Phases I-IV establish packaging, validated configuration, async browser primitives, complete password-login flows, and independent Gmail code retrieval. Connecting Gmail to OTP browser login remains Phase V in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Environment and installation

Use the existing conda environment **autoweb** for all project commands. Run installation commands from the repository root:

~~~powershell
conda run -n autoweb python -m pip install -e ".[test]"
~~~

Gmail and Gemini dependencies are optional for the new package:

~~~powershell
conda run -n autoweb python -m pip install -e ".[gmail,ai,test]"
~~~

Dependencies are declared in pyproject.toml. Versions reflect the installed autoweb baseline verified during Phase I (Python 3.10.18), rather than an SDK upgrade. Install the matching Chromium binary for browser integration tests:

~~~powershell
conda run -n autoweb python -m playwright install chromium
~~~

## Structure

| Location | Responsibility |
| --- | --- |
| src/autoweb/config/ | Settings, typed site profiles, credential preflight, validation CLI |
| src/autoweb/browser/ | Async Playwright lifecycle, strict locator resolution, actions, and state waits |
| src/autoweb/gmail/ | Explicit OAuth setup, Gmail API boundary, MIME decoding, fresh verification polling |
| src/autoweb/login/ | Password-login orchestration, authentication checks, and session handoff |
| src/autoweb/ai/ | Optional Gemini verification extraction; broader discovery remains future work |
| src/autoweb/reporting/ | Future step results and failure diagnostics |
| configs/settings.json | Non-secret browser, timeout, mailbox-path, and AI defaults |
| configs/sites/ | Non-secret example site profiles |
| tests/unit/ | Configuration and browser failure-path tests |
| tests/integration/ | Real headless Chromium checks against controlled local pages |
| tests/fixtures/ | Controlled DOM/frame pages and a cookie-authenticated login demo |
| scripts/ | Thin password-login and independent Gmail diagnostic entry points |
| artifacts/ | Ignored runtime output; created by future execution code as needed |

The login package executes password flows and returns an active authenticated session; see its [usage and local demo](src/autoweb/login/README.md). The browser package implements reusable primitives; see its [API and ownership documentation](src/autoweb/browser/README.md). Gmail can retrieve fresh codes independently; see its [setup, diagnostics, and provider contract](src/autoweb/gmail/README.md). Broader reporting and AI discovery remain future work. Importing the package does not open a browser, authenticate with Gmail, load a dotenv file, or create an AI client. Configuration loading is explicit.

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

Tests cover profile validation, credential resolution, secret-safe output, paths, import isolation, browser lifecycle, scoped locators, nested iframes, open Shadow DOM, password/OTP filling, complete password login, authenticated-session reuse, explicit failures, and state waits. Gmail tests cover saved synthetic MIME messages, fresh-code correlation, pagination, deadlines, API/auth errors, explicit acknowledgment, and optional AI validation. Browser integration tests use a loopback HTTP server and headless Chromium. Tests do not use live websites or Gmail/Gemini access.

## Project history

AutoWeb is a standalone repository containing the modular framework implemented through Phase IV. Legacy sources remain in D:/RAJ ARYAN/Codec/scaile-tech/scaile-backlink: gmail-auto/ provided reference code for Phase IV, and shadowdom-auto/ contains the earlier browser automation. They are not runtime dependencies of AutoWeb. Historical trial-codes/ experiments are excluded.

Future work follows IMPLEMENTATION_PLAN.md. End-to-end OTP browser login is pending Phase V; live Gmail/Gemini validation is opt-in and has not been performed.
