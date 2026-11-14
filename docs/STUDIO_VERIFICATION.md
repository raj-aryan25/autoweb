# Controlled verification and sanitized evidence — Step 7

Studio can execute **Controlled OTP** and **Controlled magic link** through the
same Python login package as password runs. Select a profile in Run configuration,
retain headed Chromium to watch it, and choose Start run. Use the existing
[Studio launch instructions](STUDIO_UI.md).

## Controlled runtime

The reusable email and magic-link sites now live in `autoweb.testing`; test-tree
modules only re-export compatibility names. Production imports no test module.
Profile descriptors are generated from trusted fixture metadata, independently of
the demo UI and generic example JSON files. Listing profiles constructs neither a
server nor a verification provider.

An explicitly selected run owns one ephemeral loopback server and an injected
`FixtureVerificationProvider`, built on the existing `FakeVerificationProvider`.
The server queues a synthetic value only after a real browser request with the
fixture account. The provider's two-second controlled delivery delay makes the real verification
wait inspectable; Studio does not simulate polling. Magic links retain the
requesting browser cookie and use the fixture's exact origin and one-use token.
The existing login orchestrator owns correlation, authentication and acknowledgment.
Stop cancels the observed verification wait and retains the supervised cleanup
boundary. No automatic resend occurs.

Controlled verification settings keep the generic loader's polling/timeout
invariant valid without changing Gmail policy. Controlled runs cannot enable
external AI. Configuration loading, preflight and registry listing make no Gmail,
OAuth or model request.

## Approved Gmail and optional AI

The approved Expand Testing registration is the only Gmail-backed Studio profile.
Its existing token file is required during selected-run preflight. The existing
`authenticated_session` wiring lazily opens `open_gmail_provider` during execution;
it can load/refresh existing authorization but never initiates interactive consent.
Other registrations cannot silently acquire a Gmail provider by changing their
profile's verification mode.

Gmail and Gemini Connections remain **Unverified** until there is evidence beyond
configuration. This step performs no live validation and does not finish Phase VII
Part 2. The selected framework model is unchanged. AI remains opt-in for approved
live runs. In the owned `authenticated_session` path used by Studio, a failed login now
retains its observed AI usage snapshot on `LoginError.result`, including real zero
counts. Missing usage remains null rather than
being reported as zero. Successful `LoginResult` behavior is unchanged.

## Evidence boundary

Only two producer-approved IDs can be registered:

| ID | Kind | Content |
| --- | --- | --- |
| `run-report` | `sanitized_report` | Closed execution state, authentication, diagnostic and actual AI counters |
| `failure-screenshot` | `masked_screenshot` | Existing masked package failure capture from a controlled fixture page |

The report is an execution observation, written before the guardian attests final
cleanup. Its `cleanup: await_guardian_snapshot` deliberately refers the reader to
the authoritative run record; it never claims verified cleanup prematurely.

Reports never serialize raw `LoginResult`, exceptions, configuration, credentials,
email bodies, codes, magic URLs, cookies, OAuth tokens or filesystem paths. Files
and producer directories use private permissions. Artifact registration and its
sanitized event/record inventory are committed together. The additive optional
`RunRecord.artifacts` field contains IDs, kinds and stable step IDs; it restores
Evidence after refresh or event-retention resync without exposing local paths.

The existing failure capture masks configured credential/OTP targets and every
input, textarea, editable field and embedded frame. Studio exposes those PNGs only
for its trusted controlled pages, whose visible body text does not echo secret
values. Arbitrary live pages can render secrets outside inputs, so their failure
screenshots stay private and unregistered. A live run can still receive a safe
structured report. Missing, invalid, oversized or symlinked captures remain absent;
no image or evidence record is invented. Raw traces and session-state files have
no registration and are not served.

Only the selected step's actual registered evidence appears in the inspector.
Run-level reports and controlled failure screenshots belong to Final outcome.
Authentication and cleanup status remain visible separately in Details.

## Verification

The configured Deepseek coding agent runs all checks using conda `autoweb`.
Focused coverage lives in `tests/integration/test_studio_verification.py`,
`tests/unit/test_studio_evidence.py`, and the failure-usage cases in
`tests/unit/test_ai_usage.py`. These verify actual OTP/magic workers, one request and
success-only acknowledgment, timeout, masked failure capture, closed evidence
allowlists, private permissions, registration atomicity, and null versus observed
zero/nonzero AI counters. API records, saved database rows, replay and artifacts
are checked against sensitive synthetic values.

The extended UI acceptance command starts only an owned ephemeral API and uses
controlled fixtures:

```sh
AUTOWEB_STUDIO_HEADED=1 AUTOWEB_STUDIO_VERIFICATION=1 \
  conda run -n autoweb python apps/web/scripts/verify_local_ui.py
```

It retains the password/refresh/reconnect/idempotency regression checks, then adds
headed OTP and magic-link success, real verification events and sanitized exports,
report evidence, Stop during verification, and responsive 1440/768/390/320 captures.
No live website, Gmail, OAuth consent or AI call is part of these checks.


## Acceptance record — 2026-11-12

The configured Deepseek coding agent verified the frozen implementation:

| Check | Result |
| --- | --- |
| Full deterministic regression | **521 passed, 6 skipped in 227.44 s** |
| Headed Studio acceptance | Password regression, OTP, magic link, Stop during verification passed |
| Frontend build | TypeScript + Vite passed on the final frontend source |
| Contract freshness | Generator `--check` passed |
| Privacy and evidence | Secret scans passed; real failure PNG visually confirmed both credential fields fully masked |

The 24 additional tests reconcile with the previous 497-test baseline. Skips remain
five opt-in live tests and the separately gated desktop worker test. Headed
execution was exercised by UI acceptance. No external website, Gmail, OAuth or AI
request was made, no owned API/browser processes survived, and the existing Vite
server on port 5173 was left untouched. Screenshots, including
`controlled-failure.png`, both verification profiles at four widths, and
`verification-stopped.png`, are under `.impeccable/review/studio-local/`.

Gate B (controlled verification and sanitized evidence) is delivered. The final
launcher and milestone acceptance were subsequently delivered in
[Step 8](STUDIO_ACCEPTANCE.md).
