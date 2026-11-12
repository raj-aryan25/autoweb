# Studio integration boundary and transport v1

Steps 1–2 of [the milestone plan](NEXT_MILESTONE_INTEGRATION_PLAN.md), started
2026-11-06. This delivery defines the boundary; it does not execute Studio runs.
The selected Split workspace and the existing package/CLI interfaces remain intact.

Step 3 now supplies [optional package progress observation](STUDIO_PROGRESS.md).
The existing contracts below describe the baseline and service transport boundary;
library observations are independent of the service DTOs.
Step 4 adds [supervised package execution](STUDIO_WORKER.md), including an internal
manifest of effective non-secret settings and process cleanup attestation.
Step 5 supplies [HTTP/SSE, durable history and recovery](STUDIO_API.md), with shared
HTTP record types and explicit same-origin session/CSRF setup.

## Existing package contracts

| Interface | Behavior and ownership |
| --- | --- |
| `run_login(session, configuration, verification_provider=None, *, ai_client=None)` | Validates resolved credentials, navigates, executes ordered profile actions, asserts authentication, then acknowledges verification. Returns a successful `LoginResult` with the supplied open session; raises `LoginError` carrying a failed result for handled login errors. Caller owns the supplied session. Configuration and unexpected exceptions remain distinct. |
| `authenticated_session(configuration, verification_provider=None, *, ai_client=None)` | Async context manager owns `AsyncExitStack`, provider/browser/context cleanup, and optional AI wiring. Yields only after authentication. Lazily selects Gmail when email verification needs a provider and none was supplied. Worker must explicitly inject the in-memory provider for controlled verification. |
| `LoginResult` | Frozen dataclass: site, `succeeded`/`failed`, elapsed seconds, completed `StepResult` tuple, failed stage/reason, artifact paths, optional session and AI usage. Cancellation/interruption do not extend this dataclass. Successful `authenticated_session` attaches an available `UsageSnapshot`; `run_login` alone and failed results do not necessarily have one. |
| `StepResult` / `ActionResult` | One-based profile index and action plus `succeeded`/`skipped`, elapsed duration and optional reason. Failed and unexecuted actions are absent from the completed tuple. Preserve skips. Do not manufacture successful results for absent actions. |
| Configuration | `load_configuration` explicitly resolves root/settings/profile/dotenv and process overrides into validated settings and `SecretStr` values without mutating `os.environ`. `schema_only=True` skips secret resolution and is not execution preflight. `load_site_profile` reads schema only. |
| Verification | Provider protocol: `baseline`, `receive`, `acknowledge`. Challenge baseline precedes the single request action; freshness, recipient, deadline, code/link policy and acknowledgment remain package responsibilities. `fill_otp`/`follow_magic_link` consumes the result. Gmail opens explicitly using existing OAuth files; no automatic consent. In-memory provider supplies controlled synthetic values. |
| Reports | `login_report` omits configuration and session but contains artifact filesystem paths and existing reason strings. It is not directly a service DTO. Translate diagnostic codes and register approved artifacts before transport. |

## Service and worker boundary

The loopback service owns registered IDs, validated overrides, preflight,
one-active-run coordination, idempotent start/stop, durable SQLite snapshots/events,
SSE replay and artifact authorization. The worker uses `sys.executable`, owns
browser/provider resources and runs the existing package. Worker cleanup completes
before the active slot is released. Steps 1–2 introduced contracts, Step 3 added
the package observer, Step 4 added supervised Linux execution, and Step 5 adds HTTP
and durable storage. UI runner integration remains Step 6.

`src/autoweb_api/profiles.py` is the trusted registry policy:

| ID | Source / requirements | Authorization |
| --- | --- | --- |
| `controlled-password` | Implemented trusted local fixture factory; browser | Controlled local |
| `controlled-otp` | Future local fixture factory; browser + in-memory provider | Controlled local |
| `controlled-magic-link` | Future local fixture factory; browser + in-memory provider | Controlled local |
| `practice-test-password` | Registered Practice Test Automation configuration; browser | Explicit selection of approved live site |
| `expandtesting-otp` | Registered Expand Testing configuration; browser + Gmail | Explicit selection of approved live site/mailbox |

Fixture sources are trusted resolver keys; the password factory is implemented,
while controlled OTP/magic-link factories remain unavailable until Step 7.
Registry membership is authorization policy, not proof of readiness. The current
demo UI profiles do not establish a runnable backend configuration. Exclude schema
examples, Better Auth, Stytch and arbitrary client-supplied paths/URLs. Only Python
resolves paths, credential environment references and provider configuration.
Listing descriptors must not read secrets or initialize clients. Readiness starts
`unverified`; `configured` means configuration exists, `ready` requires an explicit
successful preflight, and `unavailable` requires a known failure.

Clients submit `StartRun`: opaque request/profile IDs and bounded allowlisted
headless, slow-motion, login/verification timeout and AI-enabled overrides. Defaults
are headed, no slow motion, 120-second login, 60-second verification, AI off. No
model override, credential editing, arbitrary commands or OAuth onboarding. A request
ID is an idempotency key in the future service, not an instruction to retry email.

## States and observations

Run states: `created → preflight → running → succeeded|failed`; a stop request
enters `stopping`, then `cancelled` after cleanup. Startup/preflight may fail before
running. Service/worker loss produces `interrupted`, never an automatic rerun.
Finalization is exactly once. A completion racing with stop retains observed
authentication, independently of cancellation or cleanup failure. Authentication can
be `succeeded`, `failed`, or `not_observed`; absence of proof is not rejection.

Connection states (`connecting`, `connected`, `reconnecting`, `disconnected`) belong
to the UI and never change run state. Step states are `pending`, `running`,
`waiting`, `succeeded`, `skipped`, `failed`, `cancelled`, `not_executed`. An optional
absence is `skipped`; actions after an early stop/failure are `not_executed`.
On interruption, retain the last observed step status rather than asserting that
unobserved worker cleanup or action cancellation succeeded.

`display_steps(profile)` adds preflight, browser startup, navigation, authentication,
conditional acknowledgment, cleanup and outcome phases to actual profile actions.
Action IDs hash canonical validated step content plus an occurrence number for
identical actions. Unrelated insertions do not renumber other action IDs. A changed
action gets a new ID; duplicate actions are distinguished by their occurrence.
Persist the exact run profile and index-to-ID mapping; do not recompute historical
IDs against a newly edited profile. Email waiting is a nested `verification_wait`
phase of its consuming action, never an additional request action. Package observer
events use the shared mapping; final result reconstruction cannot supply live timing.

## Event and replay protocol

`RunEvent` includes schema version 1, run ID, contiguous sequence starting at 1,
timezone-aware timestamp, finite elapsed seconds, stable step/phase IDs and a
discriminated payload. The payload `type` is the event type, so there is no second
event-name field that could disagree. Allowlisted payloads cover preflight, browser
startup, navigation, step start/completion, verification wait, authentication,
artifact registration, cleanup, run-state transitions and final outcome.

Each run's service sequence is authoritative, elapsed duration never decreases,
and wall-clock timestamps are informational. Only actual observations are emitted;
there are no invented poll counts or intermediate AI usage totals. Final AI usage
is nullable and uses the existing calls/prompt/candidate/total counters when present.
Zero is an observed zero; null means unavailable. Cleanup errors can coexist with
successful authentication in the final service outcome.

SSE will use decimal sequence as `id` on a run-specific stream and JSON `RunEvent`
as data. Reconnect sends the last applied cursor. Ignore duplicates at or below that
cursor; a sequence gap requires recovery. A replay response contains only events
strictly after `after_sequence`, contiguous through `snapshot.cursor`, for one run.
If retention cannot supply that interval, return `resync_required` with an empty
event list and an authoritative snapshot. Replace local state from that snapshot
and resume after its cursor. Subscribe without a snapshot/replay race; a refresh
reattaches to the known run rather than starting another. Stream implementation,
retention bounds and persistence remain Step 5 work.

## Sanitization and shared schema

All DTOs reject extra fields. Diagnostic values are a closed vocabulary; map unknown
package reasons to a generic code, never exception text. Payloads have no arbitrary
message, URL, locator, credentials, email, cookie, token, code or filesystem-path
fields. Artifacts use registered opaque IDs and approved kinds. Do not pass raw
reports, trace files, email bodies or session objects into transport. Schema validation
does not redact images or prove that a producer supplied a truthful opaque ID;
producer sanitization and synthetic-secret integration tests remain required.

Python models in `src/autoweb_api/contracts.py` are the source of truth.
`scripts/generate_studio_contracts.py` generates TypeScript transport types, JSON
Schema and TypeScript fixtures from the shared JSON scenarios. TypeScript checks
shape/discriminants; Python additionally enforces bounds, timestamps, unique steps,
terminal outcome agreement and replay invariants. The generated schema is a tuple
of scenario, profile descriptor and start request roots, with named `$defs` for
individual DTOs. It does not assert runtime TypeScript input validation exists.

```sh
conda run -n autoweb python scripts/generate_studio_contracts.py
conda run -n autoweb python scripts/generate_studio_contracts.py --check
conda run -n autoweb python -m pytest tests/unit/test_studio_contracts.py -q
conda run -n autoweb npm run typecheck
```

Fixtures cover success with observed zero AI usage, optional skip, verification
timeout, cancellation, reconnect replay, resync and interruption. These are
transport examples, not evidence of package execution or live-site readiness.

## Baseline and validation

Checks use conda `autoweb`, with live selection omitted, on 2026-11-06 in the
current Linux checkout (`/home/raj/Codec/autoweb`; the Windows path in `AGENTS.md`
is historical and is not mounted here).

| Check | Result |
| --- | --- |
| Initial deterministic Python suite | 355 passed, 5 live skipped, 2 failed in 208.53 s |
| Focused rerun of the two baseline failures | Both passed in 2.43 s |
| Initial frontend build | Passed (`tsc --noEmit` + Vite) |
| Existing UI smoke | Passed; responsive sizes 320–1440 px, tabs/keyboard, export, simulated success/timeout/stop, history, configuration, profiles, settings, no external requests |
| Generated transport TypeScript check | Passed |
| Frontend build after adding transport types/fixtures | Passed |
| New contract checks, including import safety and generated-file freshness | 33 passed in 1.42 s |
| Final deterministic suite | 390 passed, 5 live skipped in 157.22 s |

The two initial failures were
`test_optional_banner_does_not_hide_ambiguity` (expected ambiguity error did not
occur within its 200 ms action budget) and
`test_gmail_provider_correlates_mail_and_only_acknowledges_consumed_message[normal]`
(controlled fake-mailbox verification timeout). They passed on focused rerun;
timing sensitivity is suspected, not established as a root cause. Existing login
code and test timeouts were not changed to mask these failures.
Both tests also passed in the final full suite.

The first restricted-sandbox suite stalled and was terminated without a test
result. The recorded full baseline and focused rerun used approved loopback/browser
access. Vite likewise needed loopback permission, and Chromium smoke needed browser
launch permission. Neither result involves live sites, real mailbox access, OAuth
consent, AI calls, or manual headed-browser verification. UI screenshots remain
under the existing ignored `.impeccable/review/studio/` directory.
