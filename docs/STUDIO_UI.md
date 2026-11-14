# Studio local runner — Step 6

Studio opens in **local runner mode**. Start and Stop operate the existing Python
package through the loopback API. `?mode=demo` explicitly selects the separate,
visibly labeled timer-based preview; backend errors never select it automatically.

## Run locally

From the repository root, using the `autoweb` conda environment:

```sh
conda run -n autoweb npm run build
conda run -n autoweb autoweb-api --root . --ui-directory apps/web/dist
```

Open `http://127.0.0.1:8766`. Select **Controlled password**, retain the default
headed Chromium setting, and choose **Start run**. Its fixture uses synthetic
credentials and a private loopback server. The browser opens separately; the UI
shows actual phases, actions, timestamps and the authentication outcome. Stopping
waits for verified browser cleanup before releasing the server's active slot.
Linux containment remains the current platform limitation.

Development can use the existing Vite `/api` proxy with API port 8766 and
`conda run -n autoweb npm run dev -- --host 127.0.0.1`. Use exactly
`http://127.0.0.1:5173`; the API's development origin is exact and Vite's port is
strict. Leave an occupied port alone. Serving the built UI through the API avoids
the development origin boundary entirely.

## Execution and inspection

- Profiles, manifests and snapshots come from the server registry. Configuration
  contains only profile IDs and allowlisted overrides, never credentials, model
  names, arbitrary URLs or file paths. The real browser engine is Chromium.
- Preflight checks configuration without execution. Starting a live profile is an
  explicit selection and can contact its approved website and required services.
  Listing profiles and opening Connections do not access Gmail, initiate OAuth or
  make Gemini calls. [Step 7](STUDIO_VERIFICATION.md) enables controlled OTP and
  magic-link execution without a live mailbox.
- The UI keeps connection state separate from run state. It reconciles snapshots
  after progress events, reconnects after a broken stream, and restores the
  selected saved run on refresh. Neither reconnect nor refresh creates a run.
- Start is guarded immediately against duplicate clicks. Its opaque request ID and
  allowlisted payload are saved before submission. After a lost response,
  **Resolve start** resubmits the same ID; it cannot duplicate execution. The
  selected run ID and pending request are the only browser-persisted metadata.
  Secrets and CSRF tokens are not saved in browser storage.
- Stable backend step IDs drive selection and Follow active step. Skipped,
  cancelled, interrupted and unexecuted states remain distinct. Durations are
  observed backend values; the UI does not synthesize elapsed polling or AI calls.
- Details separates authentication outcome from cleanup. Unverified cleanup blocks
  new starts and remains visible even if authentication succeeded. Stop stays in
  progress until the server reports the result and cleanup state.
- Evidence links only registered sanitized backend artifacts. Empty evidence does
  not imply failure or an unverified synthetic screenshot. Step 7 produces safe
  reports and controlled masked failure captures, with an inventory preserved
  after event-retention resync.
- Events and exports contain sanitized backend DTOs. Replayed events are ordered,
  deduplicated and capped at 1,024 in memory. After retention loss, a fresh snapshot
  replaces the projection and the event list is explicitly marked partial.
- History uses saved server pages (25 records each); text/outcome filters apply to
  the displayed page. Settings apply to the next run, while inspected historical
  records retain their effective settings.

`runner/Runner.ts` defines the shared typed execution controls. `LocalApi` owns
HTTP/session/SSE transport; `useLocalRunner` owns selection, reconciliation and
request state. `useDemoAdapter` wraps the existing `useDemoRunner`. Real and demo
components mount separately, so opening the demo creates no local API session.

## Verification

The delegated **Deepseek coding** Paseo profile runs all tests and frontend
verification in `autoweb`. The following scripts use controlled loopback traffic:

```sh
conda run -n autoweb python apps/web/scripts/verify_ui.py
AUTOWEB_STUDIO_HEADED=1 conda run -n autoweb python apps/web/scripts/verify_local_ui.py
```

The demo script adds `mode=demo` to `AUTOWEB_UI_URL` (default Vite port 5173).
The local acceptance script starts an owned API on an ephemeral loopback port,
serves the built UI with isolated temporary history, and terminates only that
owned server. The headed option exercises headed package execution; without it,
the same checks use a headless executor for environments without a desktop.

Coverage includes actual controlled login, duplicate clicks, refresh restoration,
sanitized event export, keyboard tabs, Follow active step, durable history,
stream disconnection and service-shutdown reconnect, Stop cleanup, lost accepted
start response, resync,
unavailable/reconnect and 1440/768/390/320 layouts. Screenshots and
server diagnostics are under `.impeccable/review/studio-local/`.


## Acceptance record — 2026-11-12

Deepseek coding verified the Step 6 baseline at **497 passed, 6 skipped** (five
opt-in live cases and one opt-in desktop test). The final frontend TypeScript/Vite build and contract generator freshness check
passed; the explicit demo smoke also passed. Headed local acceptance passed against the actual
package on the desktop: authentication succeeded, cleanup was verified, observed
steps and ordered timestamped events matched the saved Python snapshot, and Stop
produced cancellation with verified cleanup. No external website, Gmail, OAuth
or AI request was made. Owned API/browser processes were cleaned up; the user's
existing Vite server on port 5173 was left untouched.

Gate A (Steps 1–6) is delivered. Controlled OTP/magic-link execution and sanitized
evidence production were subsequently implemented in [Step 7](STUDIO_VERIFICATION.md);
the launcher and final milestone acceptance are delivered in [Step 8](STUDIO_ACCEPTANCE.md).
