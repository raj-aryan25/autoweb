# AutoWeb Studio UI

React/TypeScript interface around the Python AutoWeb package. The selected
graphite-and-teal Split workspace uses local fonts, a step list, and a keyboard
accessible Details/Evidence/Events inspector. The original decision sketch is
preserved.

## Built application

Use the `autoweb` conda environment from the repository root:

```sh
conda run -n autoweb python -m pip install -e ".[server,test]"
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb npm ci
conda run -n autoweb npm run build
conda run --no-capture-output -n autoweb autoweb-studio --root .
```

Open **http://127.0.0.1:8766**. Select Controlled password, Controlled OTP or
Controlled magic link and Start run. The default is a real headed Chromium window;
it opens separately from Studio. A Stop request waits for verified cleanup.
Ctrl+C in the launcher stops active execution, drains the stream and closes history
storage. Nothing starts a login or opens OAuth consent merely by loading Studio.

The launcher binds only to loopback. An occupied port fails with an actionable
error; use `--port 8767` deliberately rather than silently moving origins. Use
`--data-directory PATH` for an isolated private history/receipt/evidence directory,
or `--ui-directory PATH` for another built UI. Missing server dependencies or UI
builds produce setup instructions. Linux containment is the current runtime
requirement; Windows/macOS support needs equivalent ownership/cleanup primitives.

## Development

Run API and Vite separately, in two terminals:

```sh
conda run --no-capture-output -n autoweb autoweb-api --root .
conda run -n autoweb npm run dev
```

Use exactly **http://127.0.0.1:5173**. Vite's strict port and `/api` proxy preserve
the exact Host/Origin checks; a port conflict fails rather than selecting 5174.
Keep the API on 8766 for this proxy. Ctrl+C stops each development server separately.

Python dependencies remain solely in root `pyproject.toml`. Frontend dependencies
are locked by root `package-lock.json` using npm workspaces. Use a Node release
compatible with Vite 8; Node 24 was used for this workspace.

## Real execution and explicit demo

Local mode is the default. Profiles, step IDs, outcomes, elapsed observations,
evidence and saved history come from the API. Refresh restores the selected run;
SSE reconnect and resync do not restart execution. A lost Start response can be
resolved with its original request ID. Connection loss preserves results and
blocks execution until reconnection; it never switches to fake results.

`?mode=demo` explicitly selects the separate, visibly labeled UI simulation. Demo
history stays in that tab, timers can be throttled in background tabs, and its
outcome/browser controls are illustrative. Demo mode opens no API session and
contacts no website, Gmail or AI service.

Settings contain allowlisted overrides only. Credentials, OAuth authorization and
the selected AI model stay on the backend. Live profiles require explicit selection
and are limited to approved registrations. Gmail/Gemini readiness remains unverified
by configuration alone. Controlled fixtures require no live mailbox or AI.

Evidence shows only registered sanitized reports and controlled masked failure
captures, including after replay retention loss. Missing evidence is explicit.
Arbitrary live-page screenshots, raw traces and session-state files are not served.
No embedded browser stream, pause, single-step or logout control is implied.

## Verification

Primary implementation agents delegate checks to the configured Deepseek coding
Paseo profile. All commands run in conda `autoweb`:

```sh
conda run -n autoweb npm run build
conda run -n autoweb python scripts/generate_studio_contracts.py --check
conda run -n autoweb python apps/web/scripts/verify_ui.py
AUTOWEB_STUDIO_HEADED=1 AUTOWEB_STUDIO_VERIFICATION=1 \
  conda run -n autoweb python apps/web/scripts/verify_local_ui.py
AUTOWEB_STUDIO_HEADED=1 \
  conda run -n autoweb python apps/web/scripts/verify_studio_acceptance.py
```

The demo smoke expects a loopback UI server (`AUTOWEB_UI_URL`, default port 5173).
Real acceptance scripts start only owned ephemeral services with isolated state;
the remaining matrix also uses temporary controlled configuration roots. It covers
password rejection, optional skipping, OTP timeout and magic-link rejection through
the real package. A test-only guardian/executor fixture supplies magic rejection;
no public API scenario knob exists. Tests cover responsive 1440/768/390/320 states,
keyboard tabs, Follow, export privacy, history, cancellation and reconnection.
Screenshots live under `.impeccable/review/studio*`.

See [Studio UI](../../docs/STUDIO_UI.md),
[verification/evidence](../../docs/STUDIO_VERIFICATION.md), and
[final acceptance and launch](../../docs/STUDIO_ACCEPTANCE.md).
