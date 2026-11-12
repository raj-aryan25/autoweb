# Optional package progress observation — Step 3

The login package now accepts a keyword-only `observer=None` in both
`run_login` and `authenticated_session`. Existing positional calls, CLI behavior,
`LoginResult`, `StepResult`, provider protocol, session ownership and exception
semantics are preserved. Both entry points share the same observed execution core.
This delivery does not implement the worker, API, SSE or React integration.

## Using the observer

```python
from autoweb.login import ProgressObserver, authenticated_session

observer = ProgressObserver()
try:
    async with authenticated_session(configuration, provider, observer=observer) as result:
        # The authenticated session remains available for protected work here.
        pass
finally:
    events = observer.drain()
    observer.close()
```

Use one observer for one login/session on its execution event loop. `drain()` returns
pending observations in order. For live consumption, a single consumer can await
`next_event()`; `close()` wakes it with `None` and discards pending data. Drain before
closing if pending events are needed. Cancelling a waiting consumer leaves the
producer open. Consumers own their tasks and observer lifetime; the login package
creates no consumer tasks or threads and never waits for a consumer.

The future worker must consume the observer independently of UI connections.
Disconnecting a UI must not close the worker's buffer. Closing or breaking the
observer itself still cannot change authentication decisions, retry requests, or
prevent owned-resource cleanup. Arbitrary callbacks are deliberately not part of
the observation API, so a slow callback cannot run inside a browser action.

## Actual observations

Each frozen `ProgressEvent` contains an observer-local ordinal, timezone-aware
timestamp, monotonic elapsed seconds, kind/status, stable step and phase IDs,
optional profile action/index and an informational flag. Elapsed observation time
starts with the entry point and includes startup/owned lifetime when applicable;
it is distinct from the unchanged `LoginResult.elapsed_seconds` login budget.

Observed boundaries include:

- Credential/provider preflight, and browser startup for an owned session.
- Initial navigation, every executed action, and authentication assertion.
- Verification baseline before the single email-request action.
- Verification waiting inside `fill_otp` or `follow_magic_link`, ending successfully
  only after the provider result passes the existing freshness/identity/value policy.
- Acknowledgment only when `on_success` actually invokes the provider.
- Login outcome, and owned stack cleanup on context exit.

`started` boundaries close with `succeeded`, `skipped`, `failed`, `cancelled` or
`error`, as observed. Optional-step absence remains `skipped`. Handled login errors
and deadline timeouts close active nested phases as `failed`; caller cancellation
closes them as `cancelled` and propagates `CancelledError`. Unexpected exceptions
close as `error` and propagate unchanged. There is no synthetic execution event for
later actions that did not run. Handled failures close their active boundaries
before failure screenshot capture, rather than using capture time as action time.

The package login outcome is emitted before owned cleanup. Successful login stays
successful if subsequent protected work fails; cleanup events report the stack's
exit separately. `run_login` never emits browser startup/cleanup for a caller-owned
session. Cleanup success means the resource stack returned without raising. Existing
resource exits can suppress teardown errors while preserving an original failure;
these events do not prove process-tree termination. Worker lifecycle guarantees and
forced-stop fallback remain Step 4 work.

`action_step_ids` is now shared between library observation and service display-step
metadata, preserving the content-and-occurrence IDs established in Steps 1–2. The
package does not import `autoweb_api`. A frozen run configuration and mapping remain
the future worker's responsibility.

## Bounded delivery and privacy

The buffer reserves `6 * profile_action_count + 64` critical transition slots for a
single execution. Current producers emit fewer than this bound, so a stopped or slow
consumer cannot evict action/phase transitions. Up to 16 independent informational
slots coalesce repeated updates by kind/step/phase, separately from critical events.
Current orchestration emits only critical boundaries; no Gmail poll counters or
intermediate AI usage are invented. Defensive overflow sets `observer.overflowed`
without blocking login. A future worker must treat that flag as incomplete
observation, rather than treating a truncated stream as a complete record.

Events contain no exception/reason text, credential values/references, locators,
email identities or bodies, verification values/message IDs, link URLs, cookies,
tokens, artifact paths or session objects. Existing results/reports are unchanged;
their separate sanitization and artifact registration still belong to the service.
Actual AI counters remain available through the existing result when provided.

These observations are not service `RunEvent` DTOs. The worker will assign run IDs,
durable contiguous sequences, safe diagnostic mappings and final service states.
Observer ordinals can have gaps when informational updates coalesce. In particular,
the worker must not turn the early package login outcome into a final service
outcome before cleanup, or mistake a received link/code for authenticated login.

## Validation

Controlled tests cover password success, optional skips, action failure, OTP
success/timeout/cancellation, and magic-link success/rejected redirect. They assert
stable IDs, real-time waiting before completion, balanced ordered boundaries,
exactly one email request, acknowledgment after authentication, redaction and
unchanged resource ownership. OTP uses an injected fake provider; magic link uses
the existing in-memory fixture mailbox adapter, with no external Gmail access.
Buffer tests cover floods, coalescing, explicit overflow, consumer cancellation,
disconnect and producer failure. Lifecycle tests preserve startup/body/cleanup
exceptions and the original exception context passed to resource exits.

```sh
conda run -n autoweb python -m pytest tests/unit/test_progress.py -q
conda run -n autoweb python -m pytest tests/integration/test_login.py tests/integration/test_email_login.py tests/integration/test_magic_login.py -q
conda run -n autoweb python -m pytest -q
```

Verified on 2026-11-06 in conda `autoweb`:

| Check | Result |
| --- | --- |
| Progress + existing login + Studio contract unit tests | 64 passed in 2.07 s (including 12 new progress/lifecycle cases) |
| Controlled password/OTP/magic-link integration tests | 51 passed in 80.81 s (including 10 new observation cases) |
| Frontend TypeScript check | Passed |
| Final full deterministic suite | 412 passed, 5 live tests skipped in 168.96 s |

A restricted-sandbox unit run produced two timeouts in existing mocked Gmail
executor tests (`test_client_pagination_deduplicates_and_detects_repeating_tokens`
and `test_client_retries_are_bounded_and_errors_are_preserved`). They reproduced
in that sandbox and both passed outside it in 0.30 s. No Gmail implementation or
test budgets were changed; the final full run uses the established loopback/browser
permissions required by controlled integration tests.

Live tests remain opt-in; this step does not contact approved or new live websites,
initiate consent, or complete Phase VII live validation.
