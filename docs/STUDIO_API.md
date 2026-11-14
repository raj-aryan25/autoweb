# Local API, durable runs and event replay — Step 5

Step 5 connects the supervised worker to a loopback FastAPI service and SQLite
history. The backend runs real controlled password logins and preserves records
across refresh/restart. The React workspace now connects to it through
[Step 6](STUDIO_UI.md), completing Gate A through
controlled headed UI acceptance. Phase VII live validation remains separate.

The current service/worker execution path supports Linux. Windows/macOS require
equivalent process containment. No live websites, Gmail/OAuth or framework AI calls
were used for this delivery's verification.

## Setup and development

All Python dependencies stay in `pyproject.toml`. The `server` extra pins FastAPI
0.162.0, Starlette 1.0.0 and Uvicorn 0.54.0; tests add HTTPX 0.28.1. These pins preserve
the project's Python 3.10 minimum; verification used the existing Python 3.12 conda
environment.

```sh
conda run -n autoweb python -m pip install -e ".[server,test]"
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb autoweb-api --root .
```

The API defaults to `http://127.0.0.1:8766`. Only `127.0.0.1`, `localhost` and `::1`
are accepted bind hosts; one server process owns the database and worker slot.
There is no reload/multi-worker launcher or automatic run rescheduling.

In a second terminal, start the frontend with the existing workspace command:

```sh
conda run -n autoweb npm run dev
```

Vite uses strict port 5173 and proxies `/api` to port 8766 while preserving Host and
Origin. If 5173 is occupied, it fails rather than silently moving to an origin the
API does not allow. The exact development origin is `http://127.0.0.1:5173`.
Browser clients should use relative `/api` URLs through this proxy, without CORS.

For optional same-origin built UI hosting:

```sh
conda run -n autoweb npm run build
conda run -n autoweb autoweb-api --root . --ui-directory apps/web/dist
```

Serving the built files does not enable a real UI runner yet. The final
`autoweb-studio` application launcher remains Step 8 work.

## Commands and records

| Endpoint | Behavior |
| --- | --- |
| `GET /api/health` | Local availability, active run ID and cleanup-blocked flag; no browser, Gmail or AI initialization |
| `GET /api/session` | Same-origin session cookie and a CSRF token for command headers |
| `GET /api/profiles` | Registered IDs, actual step metadata and capabilities; listing does not resolve credentials or check external services |
| `POST /api/preflight` | Selected profile ID + allowed overrides; sanitized configuration readiness, no execution |
| `POST /api/runs` | `StartRun` (request ID, profile ID, overrides); 202 for one new reservation, 200 for the same accepted request |
| `GET /api/runs` | Paginated history, exact state/profile filters and the active run ID |
| `GET /api/runs/{id}` | Durable `RunRecord`, including snapshot/cursor, effective manifest, non-secret overrides and cleanup state |
| `GET /api/runs/{id}/events` | Ordered SSE with cursor replay and explicit resync/terminal controls |
| `POST /api/runs/{id}/stop` | Idempotent stop; 202 during cleanup, 200 after completion |
| `GET /api/runs/{id}/artifacts/{artifact_id}` | Only internally registered, producer-approved evidence |

History accepts `offset`, `limit` (1–100), `state`, and `profile_id`. It uses database
pagination rather than loading every run into the application. Records contain
`snapshot`, `request_id`, `overrides`, optional `manifest`, creation/update times,
`active`, `cleanup_verified`, `cleanup_required`, and observed `authentication`.
They contain no credential values/references, email bodies, cookies, tokens,
locators, raw exceptions, trace paths or filesystem locations.

Preflight `ready` means local configuration passes validation and execution is
supported; it is not proof that Gmail/Gemini works. `external_services` explicitly
remains `unverified`. The two controlled verification fixtures are still unavailable
pending Step 7. Registered live sites still require explicit selection and existing
authorization. No request/consent flow runs when listing profiles or preflighting.

## Local request protection

All requests require an exact allowed loopback Host. Supplied Origin values and
browser fetch-site metadata are checked; arbitrary domains, `null` origins and
cross-site requests are rejected. There is no wildcard CORS policy or trust in
forwarded headers.

Clients first fetch `/api/session`. It sets an HttpOnly, SameSite=Strict cookie
scoped to `/api`, and returns `csrf_token`. The cookie is intentionally not Secure
because the local loopback service uses HTTP. Commands require that cookie,
`X-AutoWeb-CSRF`, an exact allowed Origin and `Content-Type: application/json`.
Cookies/tokens are memory-only, expire after eight hours and are invalidated on
service restart. Clients re-handshake after a 401, then fetch/reattach to the known
run; they must not start another run as a reconnect strategy. Tokens never go in
URLs or SQLite. Protected reads require the session too.

Bodies are limited to 16 KiB. Invalid inputs return static error codes without
echoing the request or exception. Access logging is disabled by the local launcher.
Duplicate request IDs with different configuration return `409 request_id_conflict`;
another active run returns `409 run_active`; unverified cleanup returns
`409 cleanup_required`. There is no queue.

## Durability and idempotency

The default service state is `artifacts/studio/service/history.sqlite3`, with a
lifetime exclusive writer lock, WAL and `synchronous=FULL`. The database is private
mode 0600; new state directories are mode 0700. Run artifacts use a separate trusted
root, default `artifacts/studio/runs/`. Paths are operator configuration, never HTTP
request inputs. One coordinator belongs to the application lifespan.

The service commits the validated request, fingerprint, reservation and initial
event before scheduling execution. The same request ID with the same normalized
configuration returns its original run after lost responses, completion or restart.
It cannot create a second login/email request. Changing the body under an existing
key is a conflict, rather than a configuration update. A failed/conflicting start
does not silently retry.

Worker events update the snapshot and cursor in the same transaction before event
retention is pruned. Service sequences are contiguous and separate from the worker
cursor used for ingestion de-duplication. Snapshot state preserves optional skips,
actual action/phase IDs, authentication observations and available AI counters.
Execution tasks are application-owned; HTTP/SSE disconnect and cancelled waiters
do not cancel them. Graceful application shutdown requests stop and waits for
worker cleanup and journal consumption before closing SQLite.

## SSE reconnect and bounded retention

Each `progress` event carries a decimal `id` and one JSON `RunEvent`. Resume with
`Last-Event-ID`, or `?after=<snapshot.cursor>` for initial attachment. On reconnect,
the header takes precedence over the original query cursor. Server replay excludes
events at/below the cursor; clients also ignore duplicate IDs. Snapshot-plus-cursor
recovery and streaming both read the durable journal, not simulated timers or logs.

The default retention is the last 1,024 events per run, configurable from 1 to 2,048.
The complete snapshot remains after pruning. If a cursor is unavailable or ahead
of the stored cursor, the stream sends `resync_required` with the authoritative
snapshot and an empty event list, then closes. The client must close its EventSource,
fetch the complete run record, replace local state and attach after that cursor.
A gap must not be treated as a successful partial replay.

Terminal streams deliberately send `stream_end`; clients close EventSource on it.
Reconnect at the final cursor returns HTTP 204, which stops EventSource retrying.
Cleanup-uncertain streams end with `cleanup_required`; shutdown has a separate
control. Ten-second comments keep an idle stream alive. Slow consumers use their
own stream task and cannot backpressure package execution. If retention overtakes
them, they receive resync rather than an unbounded per-client event queue.

This follows the [EventSource cursor/reconnect rules](https://html.spec.whatwg.org/multipage/server-sent-events.html)
and uses [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/custom-response/).

## Restart recovery and cleanup uncertainty

The parent durably records guardian PID/start identity before writing Launch to
its control pipe. An unfinished reservation without ownership was never authorized
to create an executor. The guardian atomically writes a private cleanup receipt
only after reaping every descendant, with run ID, guardian identity, observed
outcome, available counters and whether cleanup was forced. It then sends the
ordinary cleanup attestation. This closes the crash window between child creation
and a durable process identity.

On restart, the writer lock prevents a second live service from sharing the slot.
Unfinished runs become `interrupted`; they are never rerun automatically. The old
guardian normally observes control-pipe EOF, stops, reaps and writes its receipt.
Recovery waits within a bounded budget and requires matching receipt/run/process
identity plus guardian exit. Proven authentication and AI usage survive even when
the outer service lifecycle is interrupted.

Without valid evidence, only known process identities are signaled. The record
remains active/`cleanup_required`, execution availability becomes false and new
starts stay blocked across further restarts. An interrupted snapshot is not a claim
of verified cleanup; no final execution event is emitted for this uncertain path.
Malformed, stale, mismatched or symlinked receipts cannot clear it. Operator-owned
cleanup/recovery is required; recreating the coordinator or restarting is not a
bypass. Service startup never initiates OAuth, email requests or replacement runs.

## Artifact boundary

Only private producer registration can authorize an artifact ID. HTTP has no path
or registration input. Reads use directory-relative file descriptors, reject
traversal and symlinks at every component, require regular files, and bound reads
to 10 MiB for PNG or 1 MiB for JSON. Raw trace/session files are ineligible. Missing
or rejected files return a generic 404 without a private path.

Step 7 must supply sanitization and register actual evidence. Current screenshots
are not automatically registered/served, and extension/path checks alone do not
make an arbitrary producer's image or JSON safe.

## Verification

The user-requested **Deepseek coding** Paseo subagent ran all test/build commands.
Coverage includes durable snapshots, request idempotency/conflicts, concurrent
start, stop/completion races, graceful shutdown, input privacy, import/factory
isolation, traversal/symlink/FIFO rejection, replay/resync, and recovery evidence.
Real TCP tests disconnect/reconnect SSE mid-action and kill/restart the API process
mid-run, verifying cleanup and no automatic rerun. Other tests seed missing/corrupt/
wrong/symlinked receipts and confirm that uncertainty remains blocked.

```sh
conda run -n autoweb python -m pytest -q --tb=short
conda run -n autoweb npm run build
conda run -n autoweb python scripts/generate_studio_contracts.py --check
conda run -n autoweb autoweb-api --help
```

Verified on 2026-11-06 by the configured Deepseek coding subagent:

| Check | Result |
| --- | --- |
| Final deterministic suite | 497 passed, 6 skipped in 174.50 s |
| Frontend build (`tsc` + Vite) | Passed |
| Generated contract/schema freshness | Passed |
| Installed `autoweb-api --help` entry point | Passed |

The six skips remain five live tests plus the opt-in desktop headed test. Step 5
adds 43 deterministic cases over the 454-pass Step 4 baseline. All project test
and build execution was delegated; the test subagent made no implementation edits.


Step 7 adds an optional `artifacts` inventory to `RunRecord`, containing only
producer-approved artifact IDs, kinds and stable step IDs. Registration, inventory
and the observation event are committed together, so Evidence can survive replay
retention loss. Existing rows default to an empty inventory without a database
schema migration. See [the evidence producer and privacy boundary](STUDIO_VERIFICATION.md).
