# Live browser view foundation — steps 1 and 2

Studio now defaults to headless Chromium and requests live viewing. A new run
without overrides opens no desktop browser window. Select **Chromium · separate
window (debug)** explicitly for desktop observation. CLI/package browser defaults
are unchanged.

This increment does not deliver frames to the frontend. Transport, frame endpoints
and the viewer panel are subsequent steps in [the approved plan](STUDIO_LIVE_VIEW_PLAN.md).
Studio labels requested viewing as unavailable, independently of run outcome.

## Contracts and compatibility

`RunOverrides.headless` and `live_view_enabled` default to true. Both are strict
booleans. `EffectiveSettings.live_view_enabled` records the resolved request;
`Manifest.live_view` records availability independently of authentication and run
state. New manifests report unavailable/transport_unavailable when requested, or
disabled otherwise. Old manifests without these fields decode as disabled with
live viewing off. Existing stored overrides retain their explicit browser choice;
pending starts keep their original request identity and payload.

No live-view pixels, raw URLs or sensitive data are added to JSON contracts.
Python remains the contract source; generated TypeScript/schema files are updated.

## Optional lifecycle hook

`authenticated_session(..., session_lifecycle=factory)` accepts a callable returning
an async context manager for the newly created `BrowserSession`. It enters before
login navigation and exits before the browser closes. It remains active through
post-login work inside the authenticated-session block. The default is absent.
Core login code has no Studio, HTTP, IPC or capture dependency.

Context exit receives the original failure/cancellation. Hook setup errors prevent
login and still close browser resources. Hook owners must bound setup/cleanup and
isolate best-effort observation errors; the core does not hide arbitrary hook errors.

## Capture module

`autoweb_api.live_capture.LiveCapture(profile)` supplies a `session_scope` lifecycle
factory and a memory-only `latest` frame. Constructing it does not start tasks.
Step 3 now wires it to the optional private transport consumer inside the executor;
see [the frame transport](STUDIO_FRAME_TRANSPORT.md). HTTP viewing remains pending.
An optional trusted `on_frame` callback receives masked frames, including the
terminal capture, before local memory is cleared. It must only replace/enqueue
in a bounded consumer buffer, never block or perform I/O. Callback errors make
the preview unavailable and do not replace login outcomes.

While attached, it attempts an initial frame, serial capture at no more than three
frames per second, and a final frame before normal/error exit. Cancellation skips
the final-frame delay. Each screenshot has a 250 ms outer deadline. JPEG frames
are limited to 1 MiB and viewport dimensions to 1920 × 1080; a normal Playwright
context uses 1280 × 720. The module does not change viewport, focus or navigation.
Only one latest frame is retained, and it is cleared on failure or teardown.

Inputs, textareas, selects, editable regions and credential/OTP targets are masked
at capture time. Entire iframe/frame regions are masked rather than assuming their
contents can be inspected safely. Locators cover open Shadow DOM. There is no
unmasked fallback and no image file output. Arbitrary personal page text is not
automatically sanitized; these are private runtime pixels, not exportable evidence.

The captured page is the exact `session.page` used by current login orchestration,
including magic-link navigation on that page. Unrelated popup pages never replace
it based on focus or creation time. Closing the workflow page makes capture
unavailable. No page listeners are registered. Future orchestration that changes
the workflow page must explicitly extend this association.

Capture failures affect preview availability only. Authentication outcomes, SSE,
history and evidence handling remain independent. The lifecycle owns and awaits
its capture task before the enclosing browser context exits.

## Verification

The configured Deepseek coding Paseo profile completed verification using conda
`autoweb`:

| Check | Result |
| --- | --- |
| Focused checks after terminal handoff change | 60 passed |
| Full deterministic suite | 580 passed, 6 skipped in 207 seconds |
| Generated contract freshness | Passed |
| Frontend build (TypeScript + Vite) | Passed against final source |
| Real Chromium capture | Masked JPEG pixels verified for credentials, non-input fill target, open Shadow DOM and iframe; navigation and cleanup passed |
| Rendered Studio | Headless/requested-view defaults, unavailable label, debug/off next-run settings and controlled password success passed |
| Responsive/demo checks | No horizontal overflow at 1440/390; demo copy passed; zero page errors |

The six skips remain five opt-in live tests and the explicit desktop worker test.
The controlled headless UI run succeeded with verified cleanup. No owned API or
Chromium processes remained. Review images are in
`.impeccable/review/studio-live-view/`; existing user logs were untouched.
No live websites, Gmail, OAuth consent, framework AI calls or external requests
were used. This gate preceded step 3's private frame transport; HTTP frame
endpoints and the frontend viewer panel remain pending.
