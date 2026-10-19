# Independent Gmail verification

Phase IV retrieves one fresh verification code without importing Playwright or
performing website actions. Phase V injects this provider into login flows for
OTP-only and password-plus-OTP profiles, keeping the same browser context. See
[email login setup](../../../docs/EMAIL_LOGIN_SETUP.md) for environment files and
execution. This diagnostic remains independent and never submits a website code.

## Setup

Run project commands in conda `autoweb`, from the AutoWeb repository root:

```powershell
conda run -n autoweb python -m pip install -e ".[gmail,test]"
```

For live use, enable the Gmail API in your Google Cloud project and configure an
OAuth client of type Desktop app. If your OAuth application is in testing mode,
add the test mailbox as a test user. Save the client JSON at the configured
`gmail.credentials_file`. Use a test mailbox that receives your site's emails.

Existing client/token files can remain in the original repository. Set absolute
`AUTOWEB_GMAIL_CREDENTIALS_FILE` and `AUTOWEB_GMAIL_TOKEN_FILE` overrides in `.env`
to reuse their locations. They are never silently moved. Relative paths resolve
against the selected AutoWeb project root. Avoid sharing a token path with a
different application that expects other scopes.

Authorization is explicit:

```powershell
conda run -n autoweb python -m autoweb.gmail --authorize
```

This command opens Google's consent page, waits up to 180 seconds for the
loopback callback, and writes the configured token atomically after successful
consent. It replaces that configured token if one exists. Ordinary retrieval
never starts consent: it loads a token, refreshes it if needed, or raises a clear
setup/refresh/scope error. Failed refreshes and failed token writes preserve the
previous token. OAuth HTTP calls use the configured request timeout. Token files
remain local and ignored; on Windows, protect their directory with your account's
filesystem permissions.

The default scope is `gmail.modify` for eventual acknowledgment after successful
login. A read-only deployment can use `gmail.readonly` and `acknowledge: "never"`.
Changing scopes requires new consent; a token lacking recorded required scopes
is rejected rather than assumed to grant access.

## Standalone live diagnostic

Create an ignored `configs/sites/*.local.json` profile from an email-OTP example.
Configure the actual sender address, subject, and recipient environment reference.
Supply that recipient in `.env`; password credentials are unnecessary for this
independent diagnostic. Then run:

```powershell
conda run -n autoweb python -m autoweb.gmail --site configs/sites/my-site.local.json
```

Equivalent entry points are `autoweb-gmail` and `scripts/read_verification.py`.
The diagnostic captures a mailbox baseline, then prints **Request a new
verification email now**. Request the code manually on your test website at that
point. Mail already present at baseline or received before the challenge timestamp
cannot satisfy the diagnostic. It never navigates to a website or marks mail read.
Codes are hidden by default; `--show-code` explicitly enables sensitive output.
No message bodies or OAuth tokens appear in diagnostic output.

Options include `--settings`, `--project-root`, and `--env-file`. Exit codes are
0 for a verified extraction or completed authorization, 1 for Gmail/API/auth
failure, 2 for invalid configuration, 3 for verification timeout, and 130 for
interruption. Configuration validation alone does not validate OAuth files.

## Provider contract

`VerificationRequest`, `VerificationResult`, and `VerificationProvider` live in
`autoweb.contracts` without dependencies on Gmail or browser implementations.

1. Load existing OAuth credentials explicitly with `autoweb.gmail.auth.load_credentials`.
   In async applications, call this blocking operation through `asyncio.to_thread`
   with an outer timeout, as the diagnostic does.
2. Create `GmailClient(credentials, request_timeout_seconds=...)` in an async
   context manager and inject it into `GmailVerificationProvider`.
3. Call `provider.baseline(sender, recipient, subject, deadline=...)` immediately
   before requesting a new code. Use `time.monotonic()` for all deadlines.
4. Record an aware UTC challenge timestamp immediately before triggering the
   request. Construct `VerificationRequest` with site/account identity, filters,
   baseline IDs, code rules, timestamp, and an absolute monotonic deadline.
5. Await `provider.receive(request)`. It returns a code **as a string**, message ID,
   UTC receipt timestamp, and extraction method (`regex` or `gemini`). Its normal
   representation hides the code and account identity.
6. After actual successful consumption, explicitly call
   `provider.acknowledge(result, success=True, policy="on_success", deadline=...)`.
   This removes `UNREAD` from only the selected source message. `success=False`
   and policy `never` do nothing. Retrieval itself never acknowledges mail.

Results are reserved when selected, preventing their reuse within the provider's
lifetime even before acknowledgment. Acknowledgment validates result ownership
and is locally idempotent. Recreating a provider does not retain this in-memory
reservation; a new baseline and challenge timestamp are required for every attempt.
Run Gmail attempts sequentially. Concurrent `receive` calls on the same provider
fail explicitly; shared-mailbox parallelism across providers is later work.

## Matching and extraction

- Server queries use sender, recipient (`to`, `cc`, or `deliveredto`), subject,
  and coarse epoch-second time filters. They never require `UNREAD`.
- Full metadata is checked again: exact sender/recipient addresses, case-insensitive
  subject substring, and Gmail `internalDate` at or after the challenge timestamp.
  Future timestamps, baseline IDs, mismatched IDs, and missing metadata are rejected.
  Recipient checks also support Bcc and original/delivery headers. Configure aliases
  explicitly; the Gmail API does not expand aliases like the Gmail UI.
- Pagination collects all candidate IDs within the deadline. Matching candidates
  are sorted newest first by actual receipt timestamp, with message ID as a stable
  tie-breaker. Malformed, deleted, ambiguous, or unrelated messages do not stop polling.
- Nested MIME parts are decoded recursively, with missing base64 padding repaired
  and declared charsets respected. Inline plain text is preferred; HTML is tried if
  plain text has no valid code. Script/style/head content and URL attributes are
  excluded; anchor text and decoded HTML entities are retained. Attachments,
  attachment-backed text bodies, and payloads beyond the size/depth bounds are skipped.
- Regex extraction uses a named `code` capture, the first capture, or the full
  match when there is no capture. Each value must have `code_length` characters
  and each character must fully match `code_characters` (default `[0-9]`).
  Multiple distinct valid values at the selected MIME preference are ambiguous
  and are never resolved by guessing or AI.

The Gmail client serializes SDK work in one worker and constructs a fresh
`httplib2` transport for each call. Calls have transport and async timeouts;
retryable transport/quota/server failures get at most two retries by default.
Authentication failures remain distinct from an empty mailbox. Poll waits use
`asyncio.sleep`. A timed-out/cancelled in-flight client is discarded because Python
cannot forcibly stop a worker thread; its bounded transport completes and closes
without being reused. Own the client with `async with` or call `aclose()`.

## Optional Gemini fallback

AI remains disabled by default. The purpose-specific `GeminiCodeExtractor` uses
the documented Gemini REST endpoint with a configured model and API key, without
global SDK configuration or a fixed model default. Broader AI discovery and SDK
consolidation remain Phase VII work; this adapter does not need the legacy `ai` extra.

The standalone diagnostic injects it only when `ai.enabled` is true. Set
`AUTOWEB_AI_MODEL` and the key referenced by `ai.api_key` before enabling it.
Enabling fallback sends up to 20,000 characters of the matching email's decoded
text to Gemini. It runs only after deterministic extraction finds no code, accepts
strict JSON with a string `code`, and applies the same length/character validation.
An unavailable or invalid fallback does not break deterministic polling; it cannot
extend the challenge deadline. No live Gemini calls have been validated yet.

## Deterministic verification

```powershell
conda run -n autoweb python -m pytest tests/unit/test_gmail.py tests/unit/test_gmail_auth_cli.py -q
conda run -n autoweb python -m pytest -q
```

Saved synthetic messages are in `tests/fixtures/gmail/messages.json`. Tests use
fake mailbox/transport/OAuth boundaries; neither Gmail access, Gemini access,
browser consent, nor real credentials are required. Live diagnostics are opt-in
and separate from the test suite.

API references: [Gmail filtering](https://developers.google.com/workspace/gmail/api/guides/filtering),
[message timestamps](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages),
[transport thread safety](https://googleapis.github.io/google-api-python-client/docs/thread_safety.html),
and [Gemini generateContent](https://ai.google.dev/api/generate-content).
