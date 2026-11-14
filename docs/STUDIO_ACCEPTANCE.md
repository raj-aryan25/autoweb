# Local launch and complete milestone acceptance — Step 8

## Launch the built application

From the repository root, using conda `autoweb`:

```sh
conda run -n autoweb python -m pip install -e ".[server,test]"
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb npm ci
conda run -n autoweb npm run build
conda run --no-capture-output -n autoweb autoweb-studio --root .
```

Open **http://127.0.0.1:8766**. Select a controlled profile, keep headed Chromium,
and Start run. Watch the separate browser, inspect observed steps/events, use Stop,
refresh and reopen history. The launcher does not start execution or initiate
OAuth consent automatically. The current containment implementation requires Linux.

`--port` chooses a deliberate alternative loopback port. The launcher reserves the
socket before constructing the service; an occupied port fails rather than silently
changing the URL. Missing dependencies and missing built UI produce actionable
setup messages. `--ui-directory` selects another build. `--data-directory` selects
an isolated private history/receipt/evidence tree. Defaults retain the existing
`artifacts/studio/service` history and `artifacts/studio/runs` evidence locations.

Ctrl+C/SIGTERM first requests cooperative worker stop and closes SSE streams,
then drains HTTP and awaits owned cleanup through the API lifespan. SQLite closes
only after its run tasks finish. Unknown cleanup remains explicit and blocks starts
across restart; neither launcher nor UI automatically reruns interrupted work.
A second forced signal or operating-system kill is not a cleanup attestation;
existing receipt/recovery rules still apply. Stop the application normally when
possible. Do not delete history to bypass cleanup uncertainty.

Development remains two separate commands: `autoweb-api --root .` on 8766 and
`npm run dev` on strict port 5173, both in conda `autoweb`. Open exactly
`http://127.0.0.1:5173`, matching the API's explicit development origin. The built
launcher instead serves UI and API from one origin. See the
[frontend README](../apps/web/README.md) for commands and demo mode.

## Acceptance coverage

| Boundary | Evidence |
| --- | --- |
| Unit/contracts | Closed events, ordering, idempotency, persistence, failed preflight, redaction, replay/resync and artifact inventory |
| Owned execution | Guardian containment, detached descendants, cancellation/crash/recovery, verified cleanup and uncertainty blocking |
| UI → API → package | Password success/rejection; OTP success/timeout; magic-link success/rejection; optional-step skipping; Stop during verification |
| Session semantics | Protected-page/session reuse, logout and rejection of the old cookie before teardown, for all controlled login modes |
| Launcher | Lazy imports/help, missing dependencies/build, fixed loopback/occupied port, installed entry point, active-run SIGINT/SIGTERM with SSE open |
| Presentation | Responsive 1440/768/390/320 states, keyboard tabs, Follow, saved history, refresh, JSON export, connection loss/resync |
| Desktop | Separate observation of an actual visible headed fixture window; headless screenshots alone do not satisfy this |

`tests/integration/test_studio_verification.py` verifies session reuse and logout
inside the still-owned authenticated session. The fixture-only POST `/logout`
revokes its server-side session and clears the browser cookie; replaying the old
cookie also fails. This creates no production profile logout action or UI session
control.

`verify_local_ui.py` covers successes and transport lifecycle.
`verify_studio_acceptance.py` completes failure and skipping paths on isolated
configuration roots. Only magic rejection needs the trusted
`tests/fixtures/studio_acceptance.py` wrapper: it runs the production API, login
package and guardian supervision with an expired local challenge. Production never
imports this test fixture, and client requests cannot select scenarios or executor
commands. All artifacts/exports/database rows/logs remain subject to secret checks.

Commands and review artifacts are documented in [Testing](TESTING.md). Gmail,
OAuth consent, new live websites and actual Gemini requests are outside controlled
milestone acceptance. Historical Phase VII Part 2 status is unchanged.


## Visible desktop observation

The Deepseek verifier captured and the primary agent visually inspected an actual
headed fixture browser on the desktop: Chromium's window decoration, the
**Local login demo** title, the loopback `/login` address and the Sign in form are
visible in `.impeccable/review/studio-local/manual-headed-login.png`. Only the
validated owned-window image was retained. This is agent-observed desktop evidence,
not a claim that the user personally watched or approved the run. Package outcomes
and verified cleanup were checked independently through the API.


## Final acceptance record — 2026-11-12

The configured Deepseek coding verifier completed the final checks:

| Check | Result |
| --- | --- |
| Full Python regression | **535 passed, 6 skipped in 256.71 s** |
| Post-fix focused checks | **21 passed in 37.58 s**, including the newly added competing-start race test |
| Frontend build | TypeScript + Vite passed; final assets byte-identical to the reviewed UI |
| Contract freshness | Generator `--check` passed, exit 0 |
| Real headed UI smoke | Password, OTP, magic link, Stop, refresh/history, retry/reconnect/resync and evidence passed |
| Remaining UI matrix | Password rejection, optional skipping, OTP timeout and magic rejection passed |
| Explicit demo smoke | Passed on an owned preview; existing Vite port 5173 untouched |
| Launcher lifecycle | 30 owned checks passed, including missing setup, conflict, security and both signals |
| Desktop | Agent visually observed the actual visible headed fixture window |

The full suite was collected before the final competing-start test was added.
The counts are intentionally separate: the subsequent focused run verifies the
final listening-socket fix, both SIGINT/SIGTERM paths and the new race regression.
The six full-suite skips remain five live tests plus the separately gated desktop
worker test; headed execution was exercised through the actual UI and desktop
observation. SIGTERM's normal exit is −15 after Uvicorn re-raises the signal, with
verified run cleanup and released port/database ownership.

Review screenshots are under `.impeccable/review/studio-acceptance/` and
`.impeccable/review/studio-local/`. All processes and state used by acceptance were
owned and isolated. No owned API/browser children survived, and no live website,
Gmail, OAuth consent or Gemini request was made. No user desktop content was
retained as evidence. Existing authorization and incomplete Phase VII Part 2 live
validation remain unchanged.
