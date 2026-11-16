# Studio live browser view — proposed implementation

Status: approved by the user on 2026-11-15. Steps 1–3 are implemented and verified;
steps 4–7 remain pending. The browser view will reach Studio when the HTTP
endpoints and frontend panel are connected in subsequent steps.

## Outcome

Starting a run from localhost Studio shows the actual automated page inside Studio,
without opening a desktop Chromium window. Python continues to own Playwright and
execution; React displays a temporary view of that same page.

The target is the existing `apps/web` application, inheriting root `PRODUCT.md`
and `DESIGN.md`. Preserve its graphite/teal Split workspace and existing controls.
Use the current Linux checkout at `/home/raj/Codec/autoweb`; the Windows path in
AGENTS.md is historical. This work does not add Windows worker containment.

## Findings

- `RunOverrides.headless` and React's `defaultOverrides` default to false.
  `prepare_run` passes that setting to the existing browser launcher.
- `launch_browser` starts a browser for each isolated run. The guardian owns its
  process tree and verifies cleanup before another run can start.
- The UI subscribes to sanitized SSE progress. Evidence is registered separately;
  there is no ongoing page capture or live-view endpoint.
- `authenticated_session` exposes the session to its caller only after login.
  Capturing from that yield would miss the login itself: a lifecycle hook must
  attach the viewer before navigation and authentication actions begin.
- Progress uses a bounded JSON worker protocol with 16 KiB messages. Images do not
  belong in this channel, SSE history, SQLite, reports or exports.
- Current evidence rules deliberately keep live-site screenshots private.
  Temporary preview must be a distinct capability, not a relaxation of artifact
  serving rules.
- Playwright is pinned to 1.40.0. Use APIs available at that version; do not bundle
  a browser/library upgrade into this change.

## Architecture decision

Use **headless Chromium plus a bounded live screenshot preview**. Start with a
target of 3 frames/second at a fixed viewport (1280 × 720), using JPEG bytes and
capture-time masking. Measure latency and overhead before tuning the rate.
This is a live observation feed, not a 30 fps video or a remote desktop.

```text
Playwright page, inside the existing supervised executor
  -> optional session lifecycle hook
  -> bounded, masked frame capture
  -> private per-run binary IPC channel
  -> API in-memory latest-frame store
  -> authenticated HTTP frame requests
  -> Studio Live browser panel

Existing progress -> guardian/service -> SQLite + SSE -> steps/inspector
```

Use serial HTTP polling for the latest frame so the existing Host/Origin/session
guard and Vite `/api` proxy can be retained. No WebSocket authentication path,
public CDP port, VNC service, desktop capture or target-site iframe is needed.
The screenshot API can return bytes without writing image files. Target sites
continue executing in Playwright rather than being loaded inside Studio's origin.

A new isolated browser process/context per run remains intentional. The visible
desktop window goes away; browser pooling is outside this change because it would
alter session isolation and the existing containment/recovery guarantees.

## Implementation sequence

1. **Define view settings and contracts.** Make Studio default to headless with
   live view enabled. Keep separate-window headed execution as an explicit debug
   choice. Keep core CLI defaults unchanged. Model preview availability separately
   from execution and authentication state; an omitted override must use the new
   Studio default. Regenerate TypeScript contracts from Python definitions.

2. **Attach capture before login.** Add an optional, caller-supplied async session
   lifecycle context to `authenticated_session`, entered after session creation
   and before navigation. The core package knows no HTTP/UI details. A Studio
   capture module owns the capture task and removes all listeners/tasks on exit.
   Observe the workflow's active page; handle navigation, popup creation and page
   closure deterministically, without stealing focus or changing workflow page
   selection. Capture an initial and terminal view where safe and available.

3. **Transport frames independently.** Establish a private per-run Unix socket
   with a service-generated identity and private directory. Pass its trusted
   configuration through worker launch plumbing, never client-supplied paths.
   Validate producer identity/run association, frame type, dimensions, sequence
   and byte limits. Keep one latest frame, use bounded writes/timeouts, and drop
   obsolete frames under load. No raw pixels travel through the guardian's JSON
   progress pipe. Close/unlink the socket on all exit/recovery paths; the existing
   guardian continues to own executor/browser termination.

4. **Expose the temporary view.** Add run-scoped view status and latest-frame
   endpoints under `/api/runs/{id}/view`. Apply existing session and loopback
   checks, `no-store`, strict run identity and response limits. Return sequence,
   capture time and dimensions; support an unchanged-frame response. Reject
   historical/unknown run subscriptions cleanly. Requesting frames never starts,
   retries or keeps a run alive. Slow viewers cannot block execution or Stop.

5. **Build the viewing interface.** Put a prominent Live browser panel above the
   existing inspector in the flexible workspace area; retain the step list.
   Show Start/Stop controls, current step and page view together. Add fit-to-panel
   and full-screen viewing. On narrow screens, stack steps, viewer and inspector.
   Preserve keyboard access, readable status and the current visual system.
   The page is view-only; clicks/typing are not forwarded into automation.
   Keep slow motion available for short runs. Update local and demo copy that
   currently promises a separate browser window.

6. **Handle lifecycle explicitly.** Display idle, starting, live, reconnecting,
   unavailable and ended states, with a distinct stale-frame indicator. Poll only
   while viewing the active run, one request at a time; abort on run changes and
   revoke replaced image URLs. Refresh reconnects to the same active run. Losing
   the viewer does not stop or restart execution. Clear the backend frame store
   at teardown; an already displayed final image may remain labeled Ended until
   navigation/refresh. Saved history contains no live replay. Preview failure
   reports an unavailable view while normal run cleanup still occurs.

7. **Verify and document.** Delegate checks and rendered frontend verification
   to the configured `Deepseek coding` Paseo profile, rediscovered at execution
   time. Use conda `autoweb`, controlled local fixtures and isolated service data.
   Update Studio acceptance, API/UI docs, PRODUCT.md and DESIGN.md to reflect
   embedded viewing and its limits. Update the integration milestone through the
   installed progress plugin when its interface is available.

## Capture and privacy rules

- Mask form values, password/OTP/email fields, contenteditable inputs and trusted
  profile-specific sensitive regions before pixels leave the executor. Cover
  frames and shadow DOM supported by the pinned browser API. Suppress capture
  rather than sending an unmasked fallback when masking fails.
- Do not claim arbitrary website content can be automatically sanitized. Authenticated
  pages can contain personal text outside known fields. Treat a live view as private
  local runtime content; do not label it a sanitized evidence artifact.
- Transport only an allowlisted page label or sanitized origin where needed, not
  full URLs, query strings or magic-link tokens.
- Never persist preview frames, include them in exports, log payloads, or expose
  raw session state/CDP. Existing artifact policy remains independent.
- Capture cannot retry login actions, email requests or navigation. Screenshot
  timeouts are bounded; investigate interference with Playwright actions during
  controlled verification rather than assuming capture has zero overhead.

## Acceptance criteria

- Controlled password, OTP and magic-link runs visibly progress in localhost
  Studio; no desktop Chromium window appears with default settings.
- The viewer shows the same run/page executing the workflow, including navigation
  and verification waits. Initial/final states are handled even for short runs.
- On the controlled local baseline, target a first usable page frame within two
  seconds of page readiness and steady-state frame age below one second. Report
  measured capture overhead and observed frame rate; tune before acceptance if
  capture meaningfully delays actions or timeout budgets.
- Refresh/reconnect never starts a second run. Switching runs never shows pixels
  from the previous run. History explicitly has no live feed.
- Stop, timeout, browser/page failure, executor crash and service shutdown clean up
  capture resources as well as existing owned processes. Cleanup uncertainty still
  blocks replacement runs under existing rules.
- Slow clients, no viewers and a failed preview do not create unbounded queues or
  interfere with progress events and cancellation.
- Known sensitive regions are masked in controlled frame checks. Preview bytes
  are absent from SQLite, logs, artifacts, SSE and JSON exports. Unauthorized and
  cross-origin requests fail under the existing HTTP security rules.
- Frontend build, generated-contract freshness, relevant containment/security tests,
  full deterministic regression and desktop/mobile/keyboard UI checks pass.
- No new websites, real Gmail requests, OAuth consent or AI calls are required for
  implementation acceptance. Any additional live validation keeps the existing
  explicit authorization requirements.

## Approval boundary

Approve this plan to implement embedded, view-only live viewing with default
headless execution. Manual browser interaction, continuous video recording,
browser pooling, VNC and additional platform containment are outside this scope.
No application code or tests were changed or executed while preparing the original
plan. Current delivery status is recorded in STUDIO_LIVE_VIEW_FOUNDATION.md.
