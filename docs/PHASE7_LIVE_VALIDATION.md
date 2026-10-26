# Phase VII Part 2: live validation

Status on 2026-10-26: **incomplete**. The user resumed Part 2, selected
`gemini-3.8-flash`, and approved the public Better Auth and Stytch magic-link demos,
then the existing Expand Testing OTP profile, with the configured Gmail test mailbox.
No new website may be authenticated against
without the user's confirmation.

## Observed results

| Check | Result |
| --- | --- |
| Effective configuration | `AUTOWEB_AI_MODEL=gemini-3.8-flash` saved in ignored `.env`; default AI enablement remains false |
| Google model discovery | Model-list request succeeded and included the selected model; this does not establish generation access |
| Selected-model extraction | Initial requests timed out; after the user verified/replaced the key, the latest synthetic request returned HTTP 503 (`ai_service_unavailable`) |
| Alternate-model diagnostic | User approved one synthetic `gemini-2.5-flash` check; it returned HTTP 404 (`ai_model_unavailable`); the configured model was not changed |
| Live locator/image inference | Pending a successful selected-model generation check; no real account screenshots were submitted |
| Existing Gmail access | Existing OAuth credentials loaded and scoped message reads succeeded; no consent flow was started |
| Better Auth demo | User approved `https://cf-demo.better-auth.com/sign-in`; its client attempted a CSRF preflight, and `/api/auth/csrf` and `/api/auth/get-session` returned 404. No matching link email was found. No authentication success was reported |
| Stytch demo | User approved `https://www.stytchdemo.com/`; its email recipe at `/recipes/sdk-ui-oauth` returned HTTP 401 for the one email-link request. The attempt stopped without retry or login |
| Expand Testing OTP | **Passed**: fresh Gmail OTP login, protected-page reuse in the same session, and logout followed by denied protected access. AI was enabled and the shared counted client recorded zero Gemini calls |
| Mail acknowledgment | No messages were marked read or otherwise relabeled |

AI Studio generation reportedly works for the user's selected model. The local
REST check has not yet succeeded; the latest observed API response is HTTP 503.
No claim is made that the service is unavailable globally or that the key is invalid.
The key, mailbox address, tokens, raw email bodies and links were not printed or
committed. Diagnostic curl headers used private temporary files removed afterward.

## Changes from live validation

The shared client now exposes static failure reasons and an optional numeric HTTP
status, without including response bodies or request headers in errors. Reasons
distinguish timeout, authentication failure, unavailable model, invalid request,
rate limit, service failure and transport failure. The fallback's normal login
outcome remains `ai_unavailable`; detailed diagnostics do not affect success rules.

An apparent overescape in the diagnostic representation of the Expand Testing
regex was ruled out by direct extraction. The stored pattern was already correct;
no profile correction was needed. A new saved-profile regression now explicitly
proves deterministic six-digit extraction, including leading zeros.

The full regression run passed **339 tests, with 4 live tests skipped**, before
the final profile regression/status additions. All **50 focused Phase VII unit
checks** passed afterward. The current five live cases were verified to skip
without `--autoweb-live`. `pip check` reported no broken requirements.

## Reproducible checks

Run from the repository root in conda `autoweb`. The Gemini tests send only
synthetic data; two locator cases use an isolated local page, with and without a
masked synthetic PNG:

```sh
AUTOWEB_AI_ENABLED=true AUTOWEB_AI_REQUEST_TIMEOUT_SECONDS=30 conda run -n autoweb python -m pytest tests/e2e/test_live_ai.py --autoweb-live -q
```

For an individual generation check, select
`tests/e2e/test_live_ai.py::test_live_gemini_extracts_synthetic_code`. Do not
continuously retry a failing service or silently substitute another model.

The existing Expand Testing profile's explicit login, protected-page reuse and
logout check passed after user approval: **1 live test passed in 20.04 seconds**.
It verified that stable controls and deterministic mail extraction needed zero
Gemini calls even with AI enabled. The same context remained authenticated through
a protected-page reload; after logout, that protected URL redirected to the login
page. Email acknowledgment was `never` and tracing was off. The ignored concise
record is `artifacts/phase7/expandtesting-live.json`.

Reproduction command (website/mailbox authorization is recorded in `AGENTS.md`):

```sh
AUTOWEB_AI_ENABLED=true AUTOWEB_HEADLESS=true AUTOWEB_SLOW_MO_MS=0 conda run -n autoweb python -m pytest tests/e2e/test_live_expandtesting.py --autoweb-live --autoweb-provider gmail --autoweb-site configs/sites/expandtesting-otp.example.json --autoweb-trace off -q
```

This OTP check does not replace the pending magic-link validation. No verified
magic-link site profile was published from the failed public demos. A working,
user-approved target is still required to establish live magic-link login,
authenticated-session reuse and logout. The local controlled magic-link coverage
from Part 1 remains passing.
