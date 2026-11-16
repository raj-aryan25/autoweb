# Temporary live-view HTTP API — step 4

Step 4 exposes the runtime preview from step 3 through authenticated, run-scoped
HTTP reads. The frontend browser panel remains step 5. Public run manifests are
static startup hints; use the view status endpoint for current availability.

## Authentication and privacy

Both endpoints require the existing Studio session cookie **and** the session's
`X-AutoWeb-CSRF` header, obtained from `GET /api/session`. The explicit header is
required for GET as well as mutation methods on these sensitive routes. Fetch
the frame as a blob; a cookie-only `<img src>` or direct navigation is rejected.
Do not put session tokens in URLs.

The existing loopback Host and Origin allowlists apply. An Origin header, when
present, must be allowed. `Sec-Fetch-Site`, when present, must be `same-origin`
or `none`; `same-site` and `cross-site` are rejected. Non-browser clients may omit
Origin/fetch metadata while supplying the session cookie and token. Duplicate
security headers are rejected. The Vite `/api` proxy preserves Host and browser
metadata, so requests through the configured development origin follow the same
policy. No CORS access is enabled.

Every API response, including empty/error responses, carries `Cache-Control:
no-store` and `X-Content-Type-Options: nosniff`. Images contain known-field masking,
but arbitrary visible website text remains private runtime content. The API does
not register frames as evidence or include them in SQLite, progress events,
exports, reports or logs. No socket path, producer token, target URL or credentials
are included in metadata or errors.

## GET /api/runs/{run_id}/view

Returns 200 with the generated `RunView` contract:

```json
{
  "run_id": "generated-run-id",
  "status": {"state": "live", "diagnostic": null},
  "frame": {
    "sequence": 8,
    "captured_at": "2026-11-16T15:00:00Z",
    "width": 1280,
    "height": 720,
    "byte_length": 42000,
    "format": "jpeg"
  }
}
```

UTC JSON timestamps use `Z`; response headers use the equivalent `+00:00` offset.
Both are timezone-aware ISO 8601 values.

The status is separate from authentication/execution outcome. Metadata is null
when no frame can be served. Unknown run IDs return the normal 404/not_found.

| Runtime state | View status and image |
| --- | --- |
| Inactive/historical run, cleanup required, or service closing | ended, no frame (including runs that originally disabled viewing) |
| Active run with viewing disabled | disabled, no frame |
| Run reserved before a handle exists | starting, no frame |
| Handle exists, preview not allocated, guardian process not yet created | starting, no frame |
| Preview setup failed after process creation | unavailable/transport_unavailable, no frame |
| Receiver reports capture or transport failure | unavailable with its allowlisted diagnostic, no frame |
| Valid active receiver frame | live with matching metadata |
| Producer ended, final frame retained, cleanup still pending | ended with matching final-frame metadata |
| Worker reports verified or uncertain cleanup | ended, no frame |

Handles and receivers must belong to the requested run. A mismatched association
is unavailable and cannot serve another run's image. Invalid/oversized bytes,
dimensions or metadata are suppressed as unavailable/capture_failed.

## GET /api/runs/{run_id}/view/frame?after=0

`after` is an optional sequence cursor from 0 through 9007199254740991. The default
is 0. It is scoped to this run; reset it when switching runs. Malformed cursors
or invalid IDs return 422/invalid_request without echoing input.

| Response | Meaning |
| --- | --- |
| 200 image/jpeg | The latest valid frame has a sequence greater than `after` |
| 204, empty body | No frame yet, unavailable preview, or no newer frame (including a cursor ahead of the producer) |
| 409/view_disabled | Active run explicitly disabled viewing |
| 410/view_ended | Known run has no retrievable frame and viewing has ended |
| 404/not_found | Unknown run |
| 401/session_required | Missing/expired session |
| 403/csrf_required or invalid_origin/invalid_host | Invalid read proof or origin/host policy |

The body is one JPEG of at most 1 MiB, with dimensions at most 1920 × 1080.
Successful and unchanged frame responses include metadata headers:

- `X-AutoWeb-Run-ID`
- `X-AutoWeb-View-State`
- `X-AutoWeb-Frame-Sequence`
- `X-AutoWeb-Frame-Captured-At` (timezone-aware ISO timestamp)
- `X-AutoWeb-Frame-Width` / `X-AutoWeb-Frame-Height`
- `X-AutoWeb-Frame-Byte-Length`

Frame headers are absent when no frame exists. The run/state headers remain on
empty and known disabled/ended responses. There is no ETag, image history, replay,
long polling or subscription. Responses snapshot metadata and immutable bytes in
one synchronous service read so a concurrent capture cannot mix two frames.

## Lifecycle and client usage

Use serial polling while observing an active run. Frame GETs do not create workers,
connect sockets, invoke capture, mutate history, install waiters or extend run
lifetime. Refresh reconnects to the existing run rather than starting another.
Slow or disconnected HTTP clients do not own execution or Stop. An image response
already in flight is independent of the worker's subsequent cleanup; teardown
clears the backend store and later reads cannot retrieve that image again.

History is deliberately status-only (`ended`, null metadata); requests cannot
revive a frame after cleanup or service restart. Step 5 will consume these endpoints
and display the view; step 6 adds viewer lifecycle presentation.

## Verification

Verification was delegated to the configured Deepseek coding Paseo profile using
conda `autoweb`, controlled local fixtures and external command timeouts. No live
websites, Gmail, OAuth consent or framework AI calls were used.

| Check | Result |
| --- | --- |
| Passive mapping unit tests | 37 passed |
| In-process HTTP view tests | 30 passed |
| Real-server view tests | 3 passed |
| Focused API/security/transport/capture regression | 232 passed |
| Final full deterministic suite | 715 passed, 6 skipped in 239.78 seconds |
| Generated contract/schema freshness | Passed |
| Frontend build (`tsc` + Vite) | Passed |

The six full-suite skips remain five opt-in live tests and the explicit desktop
worker check. Coverage includes every status-row transition, unknown/historical
runs, cursor bounds and unchanged 204s, cross-run isolation, concurrent reads,
password/OTP/magic-link transport regression, and missing/expired session,
duplicate-header, Host, Origin, CSRF token, `same-site` and `cross-site` rejection
over both ASGI and a real TCP server. A real blocked controlled run served an
actual masked Chromium JPEG over HTTP: the response carried matching
sequence/time/dimensions/byte-length, decoded at 1280 × 720, and contained masked
form pixels. Dropped viewers and concurrent polling did not delay Stop; teardown
left no socket directory, retrievable frame, or JPEG in the service state, and
history stayed ended-only. No application code was changed by the verifier.
