# Next milestone: real AutoWeb runs in the local UI

Status: **Steps 1–5 implemented on 2026-11-06; real UI runner integration starts at Step 6.**

Prepared: 2026-11-05.

The user initially approved documentation changes only, then explicitly instructed
implementation of Steps 1–2 on 2026-11-06. See
[the integration contracts and baseline](STUDIO_INTEGRATION_CONTRACTS.md).
The subsequent instruction authorized Step 3, now covered by
[optional package progress observation](STUDIO_PROGRESS.md).
Step 4 was subsequently authorized; see [supervised package execution](STUDIO_WORKER.md)
for the worker lifecycle, Linux containment boundary and controlled verification.
Step 5 was subsequently authorized; see [the local API and durable run journal](STUDIO_API.md).

## Implementation starting points — TL;DR

Read Steps 1–2 first: establish the baseline and agree the run/event contracts.
Then add package observation before building the worker/API and connecting React.

| Component | Where to start | Planned responsibility |
| --- | --- | --- |
| Login orchestration | `src/autoweb/login/flow.py` | Add optional real progress events around navigation, actions, and authentication |
| Verification | `src/autoweb/login/verification.py`, `src/autoweb/gmail/provider.py` | Expose real verification phases while preserving existing freshness and provider rules |
| Results and reports | `src/autoweb/login/results.py`, `src/autoweb/reporting/json_report.py` | Reuse existing outcomes; define sanitized transport contracts without breaking callers |
| Browser ownership | `src/autoweb/browser/session.py` | Reuse session creation and cleanup in the worker |
| Local backend | `src/autoweb_api/` | FastAPI endpoints, worker coordination, SSE, SQLite history, and artifact access |
| UI runner boundary | `apps/web/src/demo/useDemoRunner.ts`, `apps/web/src/demo/model.ts` | Introduce a typed runner interface and real API adapter; retain explicit demo mode |
| UI integration | `apps/web/src/App.tsx`, `apps/web/src/components/` | Connect Start/Stop, profiles, inspection tabs, connection state, and history |
| Dependencies and launch | `pyproject.toml`, `apps/web/vite.config.ts`, root `package.json` | Add the Python server extra, development proxy, and documented local launcher |
| Validation | `tests/unit/`, `tests/integration/`, `tests/fixtures/`, `apps/web/scripts/verify_ui.py` | Extend controlled tests through UI → API → actual package execution |

First delivery target: one controlled headed password login with real events,
safe cancellation, and saved results. Add controlled OTP/magic-link coverage next.
The backend contains contracts, registry policy, supervised worker execution,
loopback HTTP/SSE, durable history and recovery. The login package supports progress
observation. The React runner is still simulated; Step 6 connects real execution.

## Outcome

From the selected graphite-and-teal Split workspace, choose a registered site
profile, start the Python package in headed mode, follow actual execution, stop
the run safely, and reopen its real results and sanitized evidence after refresh.
The existing Python package remains the owner of login behavior.

This milestone integrates the UI. It does not claim to complete the outstanding
Phase VII live magic-link and Gemini validation.

## Approved architecture

```text
React Split workspace
  | HTTP: profiles, configuration preflight, start, stop, history, evidence
  | SSE: ordered execution events and reconnection
Local FastAPI service (loopback only)
  | one active run worker, started with the same Python interpreter
Python worker -> existing autoweb package -> headed Playwright / verification
  |
Sanitized events/results -> local SQLite history + approved artifact directory
```

- Add `src/autoweb_api/` beside `src/autoweb/`; keep `apps/web/` in place.
- Use FastAPI and Uvicorn through a `server` extra in the root `pyproject.toml`.
  Keep all Python dependencies in that file; pin compatible versions during build.
- Use HTTP commands and server-sent events (SSE) for progress. This revises the
  earlier WebSocket suggestion: this milestone needs server-to-UI events, while
  start/stop fit HTTP. SSE has event IDs and reconnect support; replay and
  duplicate handling still have to be implemented.
- Start one worker subprocess per run using `sys.executable`, preserving the
  `autoweb` conda environment. The worker isolates browser ownership and failures
  from the API and streams a bounded, validated event protocol to it.
- Keep one active run, with no queue or automatic rescheduling. Reject another
  start with an actionable conflict response.
- Use the standard-library SQLite driver for durable run/event metadata. Bind
  the API to loopback; use a Vite `/api` proxy during development and serve the
  built UI from the API for the single-command application.

## Step 1 — establish the integration boundary and baseline

1. Record the current deterministic Python test result, frontend build result,
   and UI smoke result. Keep live tests opt-in.
2. Document the existing contracts: `run_login`, `authenticated_session`,
   `LoginResult`, `StepResult`, configuration loading, and verification providers.
3. Define the new service/worker responsibilities and the approved profile
   registry. Preserve the selected layout and current package/CLI interfaces.

**Done when:** the baseline is recorded and the package can still be imported
without initializing browser, email, AI, or API clients.

## Step 2 — define run, step, and event contracts

1. Define service-owned run states: `created`, `preflight`, `running`, `stopping`,
   `succeeded`, `failed`, `cancelled`, and `interrupted`.
2. Keep connection state separate: a disconnected UI does not mean a failed run.
   Steps can be pending, running, waiting, succeeded, skipped, failed, cancelled,
   or not executed. Preserve optional-step skips rather than calling them passes.
3. Give each event a schema version, run ID, increasing sequence number, timestamp,
   elapsed duration, event type, stable step/phase ID, and allowlisted payload.
4. Define events for preflight, browser startup, navigation, step start/completion,
   verification wait, authentication assertion, artifact registration, cleanup,
   and final outcome. Include existing AI usage counters when available; do not
   invent intermediate AI counts or inferred poll results.
5. Derive display steps from the actual profile plus explicit orchestration phases.
   Email waiting is a phase of `fill_otp`/`follow_magic_link`, not an extra browser
   action or second email request. Use stable IDs instead of array-position guesses.
6. Define matching TypeScript transport types and contract fixtures. Keep the
   library's current `LoginResult` success/failure semantics backward compatible;
   service cancellation/interruption is represented outside that dataclass.

**Done when:** Python and TypeScript agree on representative success, skip,
timeout, cancellation, and reconnect payloads, with secret values excluded.

## Step 3 — add optional progress observation to the package

1. Add an optional observer/event sink to login orchestration, threaded through
   `authenticated_session` to `run_login` without breaking existing callers.
2. Emit real navigation, step, authentication, and verification-phase boundaries.
   Add provider-specific progress only where the provider actually observes it.
3. Keep observation independent of HTTP, React, and the API package. Do not parse
   console output or fabricate running steps from the final JSON report.
4. Prevent a UI disconnect or observer failure from changing login decisions.
   Use bounded delivery, retain critical transitions, and coalesce repetitive
   informational progress rather than blocking browser execution.

**Done when:** controlled password, OTP, and magic-link tests show correct event
ordering and unchanged authentication behavior, including skipped and failed steps.

## Step 4 — implement the worker and browser lifecycle

1. Execute the existing package in a worker that owns its `AsyncExitStack`, browser,
   context, providers, and cleanup. Configuration overrides must be validated copies
   for this run; never mutate the global environment or saved profiles implicitly.
2. Default to headed Chromium with configurable slow motion and bounded timeouts.
   Run preflight before opening a browser or requesting an email.
3. Resolve credentials and OAuth references inside Python. Exchange registered
   profile IDs and allowed overrides with the UI, not credential values or arbitrary
   executable commands. Capture unexpected failures as safe diagnostic codes.
4. Implement cooperative stop first; wait for cleanup. Define and test a bounded
   fallback that terminates the worker and its owned browser process tree if it
   hangs. Do not leave detached browsers or start a replacement run during cleanup.
5. Close sessions on success/failure/cancellation, matching existing package
   ownership. Persistent browser sessions, pause/resume, and manual takeover are
   deferred; the UI must not suggest those controls are available.
6. Coordinate stop-versus-completion races: finalize once, retain the observed
   authentication outcome, and distinguish login errors from startup/cleanup errors.

**Done when:** a controlled headed password login completes and stops cleanly;
browser close, startup failure, timeout, and worker crash leave truthful outcomes
and no owned processes behind.

## Step 5 — add the local API, storage, and event stream

1. Create an explicit application factory and lifecycle; add a local service CLI.
2. Implement the proposed endpoints below. Persist validated run configuration
   snapshots, results, artifact references, and ordered events in SQLite.
3. Make start requests idempotent using a client request ID. A repeated POST after
   an uncertain network response must not create another email request or run.
4. Implement SSE replay by event ID, de-duplication, and a snapshot-plus-cursor
   recovery path. Bound progress retention and return an explicit resync response
   if the requested history is unavailable; close terminal streams deliberately.
5. On reload, reattach to the active run. On backend restart, mark unfinished runs
   interrupted, reconcile owned workers, and never rerun them automatically.
6. Protect local execution with Host/Origin validation, exact development origins,
   and same-origin session/CSRF handling for state-changing requests. Avoid blanket
   CORS access and credentials in URLs. Serve only registered run artifacts by ID.

**Done when:** reconnect, refresh, duplicate start, concurrent start, stop races,
service restart, and artifact path validation pass controlled tests.

| Endpoint | Responsibility |
| --- | --- |
| `GET /api/health` | Service availability; does not initialize external clients |
| `GET /api/profiles` | Registered profiles, real step/phase metadata, capability flags |
| `POST /api/preflight` | Validate a selected run; return sanitized readiness results |
| `POST /api/runs` | Start one run with an idempotency/request ID |
| `GET /api/runs` | Paginated/filterable durable run history |
| `GET /api/runs/{id}` | Current snapshot, effective non-secret settings, event cursor |
| `GET /api/runs/{id}/events` | SSE stream and replay |
| `POST /api/runs/{id}/stop` | Idempotent stop request and cleanup status |
| `GET /api/runs/{id}/artifacts/{artifact_id}` | Registered, sanitized evidence |

## Step 6 — connect the Split workspace to real execution

1. Introduce a typed runner interface, a local-API implementation, and a demo
   adapter around `useDemoRunner`. Demo mode stays visibly labeled and explicitly
   selected; there is no silent fallback to simulation on backend failure.
2. Replace hardcoded real-mode profiles, step counts, durations, evidence counts,
   statuses, and history with API data. Keep the selected graphite/teal composition.
3. Wire Start and Stop to the API, show preflight/starting/stopping states, and
   prevent duplicate clicks. Remove simulated-outcome controls from real mode.
4. Subscribe to events, reconcile snapshots, and restore the selected run after
   refresh. Reconnection must neither erase completed steps nor start a new run.
5. Populate Details/Evidence/Events and export from sanitized backend records.
   Continue supporting keyboard tabs, selected-step inspection, and Follow active step.
6. Make Connections distinguish configured, checked, unavailable, and unverified.
   Reading configuration is not proof that Gmail or Gemini works. An unavailable
   runner must show an actionable error and disable execution.

**Done when:** a user sees a real browser performing a controlled login and the
UI's steps, timestamps, and final result agree with the Python outcome.

## Step 7 — integrate verification and evidence honestly

1. Validate OTP and magic-link execution first against controlled local fixtures
   using the existing in-memory verification provider. Adapt reusable fixture
   servers for the controlled application harness; avoid production imports from
   the `tests` tree.
2. Wire the existing Gmail provider only for approved profiles. Load existing OAuth
   authorization explicitly during a selected run; do not initiate consent.
3. Reuse masked failure screenshots and structured errors. Show evidence that exists;
   missing captures remain an explicit empty state. Do not add a live browser stream.
4. Keep passwords, codes, magic-link URLs, raw email bodies, cookies, and tokens out
   of events, SQLite records, exports, logs, and screenshots exposed by the service.
   Raw Playwright traces/session-state files are not automatically served.
5. Report actual optional AI usage already available through `LoginResult.ai_usage`.
   AI remains opt-in; successful Gemini calls are not a prerequisite for integration.

**Done when:** real controlled OTP and magic-link runs appear correctly in the UI,
and sensitive synthetic test values cannot be found in exposed records or artifacts.

## Step 8 — complete acceptance checks and local launch

1. Add meaningful unit/contract tests for event ordering, persistence, idempotency,
   cancellation, failed preflight, redaction, and reconnection.
2. Add end-to-end UI-to-API-to-package tests using controlled fixture websites:
   password success/rejection, OTP success/timeout, magic-link success/rejection,
   optional-step skipping, and stop during verification.
3. Verify authenticated-session reuse and logout revocation in the controlled
   integration harness before teardown. Do not invent a logout action in a profile
   or expose an unimplemented session-control button.
4. Verify headed execution manually on the user's desktop. Automated CI checks may
   use headless mode; a headless pass is not proof that a visible window was shown.
5. Repeat the existing deterministic Python suite, frontend build, UI smoke checks,
   and responsive/keyboard review of changed connection and execution states.
6. Add a documented `autoweb-studio` launcher for the built UI and API, with explicit
   dependency setup, loopback URL, port-conflict errors, and graceful shutdown.
   Keep the development UI/API commands documented separately.
7. Update `PRODUCT.md`, the frontend README, testing documentation, and the design
   record for any implemented component/state changes.

**Done when:** the user can start the local application, run and stop a real
controlled headed login, refresh without losing the run, and reopen its results.

## Delivery gates

| Gate | Reviewable result |
| --- | --- |
| A — first real integration | Steps 1–6: actual headed password login, real events, stop, durable history |
| B — verification/evidence | Step 7: controlled OTP/magic link, honest readiness, sanitized evidence |
| C — milestone acceptance | Step 8: full checks, desktop headed verification, launcher and documentation |

When the user instructs implementation to begin, implement in this order. Report
each gate; these are delivery checkpoints, not additional approval prompts for
already-authorized local work.

## Live-site validation boundary

- Controlled local tests are the required acceptance path and require no live mailbox.
- An optional live smoke check may use the already-approved Expand Testing OTP
  profile and existing Gmail test mailbox, in an explicit user-triggered run.
- New websites require separate confirmation under `AGENTS.md`.
- Do not retry the failed Better Auth or Stytch email-request attempts automatically.
- Do not change the selected AI model or silently substitute another provider.
- Historical Gemini HTTP 503 and failed magic-link demo checks remain historical
  results, not proof of current service health or completed Phase VII validation.

## Deferred work

Tauri packaging; remote/multi-user execution; parallel runs and scheduling; visual
workflow editing; credential editing in the UI; OAuth onboarding; browser embedding
or streaming; pause/single-step/manual takeover; persistent authenticated-session
management; benchmark dashboards and cost pricing.

## Technical references

- [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/custom-response/)
- [SSE event IDs and reconnect protocol](https://html.spec.whatwg.org/dev/server-sent-events.html)
- Existing source boundaries: `src/autoweb/login/flow.py`,
  `src/autoweb/login/verification.py`, `src/autoweb/gmail/provider.py`,
  `src/autoweb/reporting/json_report.py`, and `apps/web/src/demo/`.

## Approval

The user approved the plan on 2026-11-06, initially requesting documentation only.
Subsequent instructions authorized Steps 1–2, Step 3, Step 4, and Step 5. Shared
contracts, package observation, supervised Linux execution and the durable local
API are implemented; Steps 6–8 remain. Tests are delegated to the user's configured
Deepseek coding subagent.

Approval does not authorize testing new live websites or starting OAuth consent.
