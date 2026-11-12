# AutoWeb Studio UI

Local React + TypeScript app for watching and diagnosing login workflows. The
selected graphite-and-teal split workspace is implemented with locally bundled
fonts. The original decision sketch is unchanged.

## Run locally

From the repository root, using the project environment:

```sh
conda run -n autoweb npm ci
conda run -n autoweb npm run dev
```

Open the URL Vite prints (normally http://127.0.0.1:5173).
The development server stays running until stopped with Ctrl+C. It has no
heartbeat expiration and binds only to loopback.

```sh
conda run -n autoweb npm run build
conda run -n autoweb npm run typecheck
```

With the development server running, verify the UI in local headless Chromium:

```sh
conda run -n autoweb python apps/web/scripts/verify_ui.py
```

The smoke check covers responsive layout, keyboard tabs, JSON export, simulated
success and timeout, cancellation, history, profiles, and settings. It asserts
that the UI makes no requests outside localhost and writes review screenshots
to `.impeccable/review/studio/`.

Python dependencies remain solely in the root `pyproject.toml`. Frontend
packages are defined here and locked in the root `package-lock.json` through npm
workspaces. Use a current Node LTS compatible with Vite 8 (Node 24 was used).

## What's interactive

- Select steps; inspect Details, Evidence, and Events. Tabs support arrow keys.
- Continue the initial waiting snapshot, stop it, or run another demo.
- Change profiles, browser metadata, speed, timeout, and simulated outcome.
- Simulate an email timeout without contacting a mailbox or resending a request.
- Reopen completed runs and filter history; export sanitized step events as JSON.
- Explore profiles, connection boundaries, and session settings.
- Follow the active step, or turn following off by inspecting another step.

**All run data is simulated.** No Python process, browser, Gmail authorization,
or AI client is initialized. No secrets or production profiles are read. History
and settings are held in memory and reset on refresh. Background tabs may throttle
simulation timers. Reported elapsed time is illustrative, not a benchmark.

The UI-only simulation lives in `src/demo/`; it is separate from the rendering
components. Replace that boundary with a local API/event transport in the next
milestone, retaining login behavior in `src/autoweb/`. Headed execution will open
its own browser window; this UI does not imply an embedded stream.

Steps 1–2 of the integration plan now define generated transport types and shared
fixtures in `src/transport/`. They are not connected to the demo runner yet. See
[the contracts and baseline](../../docs/STUDIO_INTEGRATION_CONTRACTS.md) for the
registry policy, event/replay semantics and generation commands.

Step 4 now supplies [supervised package execution](../../docs/STUDIO_WORKER.md)
on Linux, including a controlled headed password check. It is not wired to this
UI yet; Start/Stop here still control the visibly labeled demo.

Step 5 adds the [local API, history and SSE](../../docs/STUDIO_API.md). Start it in
another terminal with `conda run -n autoweb autoweb-api --root .` after installing
`.[server,test]`. Vite now stays on port 5173 and proxies `/api` to loopback port
8766, preserving Host/Origin for the local request checks. The React runner itself
remains simulated until Step 6.
