# Supervised package execution — Step 4

Step 4 adds real package execution beneath the future local API. The implementation
supports Linux with `/proc`, subreaping and pidfds. Windows and macOS fail before
execution; equivalent containment is needed there. Existing package/CLI execution
remains unchanged. React still runs in demo mode; HTTP, SQLite history, SSE,
idempotent start requests and application launch are later steps.

## Ownership

```text
WorkerCoordinator (one active supervised unit)
  └─ guardian (sys.executable; dedicated Linux subreaper)
       └─ executor (sys.executable)
            └─ existing authenticated_session → Playwright → Chromium
```

Playwright launches browsers in detached groups on POSIX, so killing only an
executor process group is insufficient. The guardian establishes and verifies
subreaper status before creating the executor. It owns no unrelated children,
repeatedly discovers descendants, adopts orphans and reaps exits. Subreaping
provides adoption, not termination; escalation is separate. See the
[Linux subreaper documentation](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html).

Signals pin processes with pidfds and verify their `/proc` start identities.
Ancestry/root checks reject reused PIDs during scans; names never determine which
processes to kill. Python wrappers are preferred; this conda build lacks them, so
the implementation uses the installed libc exports. Unsupported kernels/libc fail
closed. See [Linux pidfd signaling](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html).

## Starting and stopping

The service will own one coordinator for its application lifetime:

```python
from pathlib import Path
from autoweb_api.contracts import StartRun
from autoweb_api.worker import WorkerCoordinator

async def controlled_login():
    coordinator = WorkerCoordinator(Path.cwd())
    try:
        handle = await coordinator.start(StartRun(
            request_id="explicit-local-run", profile_id="controlled-password",
        ))
        return (await handle.wait()).outcome
    finally:
        await coordinator.close()
```

Studio defaults to headed Chromium, no slow motion, 120-second login and 60-second
verification, with AI off. The existing overrides allow headless, slow motion,
login/verification timeout and AI enabled. The configured AI model is retained;
there is no model substitution or client-supplied executable, URL or path.

`start()` reserves the slot before process launch; another start raises
`WorkerBusy`. There is no queue or automatic rerun. `handle.stop()` is idempotent,
requests cooperative task cancellation and waits for cleanup. Cancelling a waiter
does not cancel the owner or implicitly stop a run. `next_event(cursor)` provides
ordered, bounded in-memory events, not persistence or SSE replay. Always close the
coordinator at shutdown and consume output independently of UI connections.

The executor reuses `authenticated_session` and its `AsyncExitStack` for browser,
context and provider ownership. The controlled server shares that lifetime. The
guardian waits for executor exit and an empty, reaped descendant tree before
attesting cleanup. The parent publishes the final event only after attestation and
clean guardian exit, then releases the slot. Public login dataclasses keep their
existing semantics.

Default stopping allows 5 seconds for cooperative cleanup, then up to 5 seconds
for repeated termination/kill/reap passes. Descendants receive SIGTERM, then
SIGKILL; late-created/adopted descendants are included. An independent wall deadline
is 120 seconds plus the login budget. Guardian deadline cancellation is reported as
`failed/login_timeout`, rather than a user stop. Trusted lifecycle limits cannot
exceed 60 seconds cooperative, 30 seconds force or 900 seconds total; they are not
browser overrides. Tests shorten these limits through the same production loop.

## Failures and races

The package's early login outcome is not the final service outcome. Authentication
is retained independently as `succeeded`, `failed` or `not_observed`, even across
stop/completion races or later cleanup failure. Completed login can produce a
failed service result for cleanup while retaining successful authentication.
Repeated stop after finalization returns the same result without a second final.

Startup, preflight, login, timeout, worker and cleanup failures use allowlisted
diagnostics. Unknown exception/provider text is never transported. An executor
crash becomes interrupted after surviving descendants have been reaped. A driver
may close itself and Chromium after executor pipe loss; forced cleanup is recorded
only when the guardian actually signals survivors.

**Guardian failure remains explicitly uncertain.** Parent snapshots cannot prove
every possible orphan was seen. The parent kills only tracked identities, raises
`CleanupUncertain` (`cleanup_failed`), retains observed authentication/events and
keeps the active slot occupied. It emits no final event and refuses replacement
starts. Missing attestation, invalid guardian protocol, hard watchdog expiry or
unkillable processes use the same conservative result. Do not discard/recreate the
coordinator to bypass it. Durable recovery and the UI error state belong to Step 5.

## Configuration, fixtures and privacy

`prepare_run` resolves trusted registry sources inside Python, before starting a
fixture, browser or email request. Environment values are copied into a local
mapping; run values and model copies are validated and credential references
resolved without modifying `os.environ` or saved profiles. The reusable
`configuration_from_models` helper also backs the existing configuration loader.
Launch/navigation timeouts are capped at 30 seconds, default actions at 10 seconds,
Gmail requests at 30 seconds and AI requests at 10 seconds. Login/verification
retain the transport's 600-second maximum.

- `controlled-password` starts a loopback website on an ephemeral port with
  synthetic credentials. Its reusable implementation now lives in
  `autoweb.testing.password_site`; the original test fixture re-exports it.
  Production never imports the tests tree.
- `controlled-otp` and `controlled-magic-link` remain registered but unavailable
  pending Step 7 fixture wiring. They cannot silently select Gmail.
- The two approved live registrations resolve only trusted files and credential
  references. Gmail requires existing authorization and never initiates consent.
  This delivery did not run either live profile.

The manifest carries actual step IDs and effective browser/timeouts/AI metadata,
excluding credential values/references, locators, paths and email filters. Frames
are validated and limited to 16 KiB, with 2,048 events and 4 MiB queued output per
boundary. Profiles allow at most 256 actions and must also fit the manifest byte
limit. Invalid/oversized configuration fails before browser startup. Package stdout
and raw stderr are never forwarded as progress.

Masked failure screenshots stay private under a generated run ID in the trusted
artifact root (default `artifacts/studio/`), with a mode-0700 run directory. Paths
are not transported; no raw traces/session state are recorded or served. Artifact
registration, further sanitization and serving remain Step 7; SQLite is Step 5.

## Verification

```sh
conda run -n autoweb python -m pytest tests/unit/test_worker.py tests/integration/test_worker.py tests/integration/test_worker_containment.py -q
conda run -n autoweb env AUTOWEB_TEST_HEADED=1 python -m pytest tests/integration/test_worker.py::test_worker_headed_password_on_desktop -q
conda run -n autoweb python -m pytest -q
conda run -n autoweb npm run build
```

The headed check uses the current desktop display, explicitly disables headless
mode, checks Chromium's command and effective settings, completes a real controlled
password login, and verifies cleanup. It does not replace manual UI-to-browser
acceptance in Step 8. Normal regression skips this display-dependent test and the
five opt-in live tests.

Tests cover copied configuration, import safety, wire privacy, failed preflight/
startup, rejection, timeouts, browser closure, cooperative/idempotent stop,
concurrent starts, cancelled waiters, completion races and executor crash. Fault
executors exercise ignored termination, detached descendants, late forks, corrupt/
oversized protocol and authentication retained across forced stop. Guardian-death
tests verify known-process recovery, survival of unrelated processes and blocked
replacement. Test-only commands cannot be selected by `StartRun`.

Verified on 2026-11-06 using conda `autoweb`:

| Check | Result |
| --- | --- |
| Final deterministic Python suite | 454 passed, 6 skipped in 158.30 s |
| Explicit headed desktop password check | 1 passed in 3.95 s |
| Frontend build (`tsc` + Vite) | Passed |
| Generated transport/schema freshness | Passed |

The six normal skips are five live tests and the explicit desktop check. Controlled
checks used loopback/browser process permissions; no approved/new live websites,
real Gmail access, OAuth consent or AI calls were used. The UI remains in demo mode.
