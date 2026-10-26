# Phase VII: optional AI and magic links

Phase VII is split at the user's request:

1. Implementation and controlled local tests with synthetic mail and injected AI responses.
2. Live Gemini and selected test-site validation after the user configures `.env` and explicitly
   notifies the agent to continue. The user resumed Part 2 on 2026-10-26; it remains incomplete after external-service failures. See [live results](PHASE7_LIVE_VALIDATION.md). No consent is automatic.

No API key, mailbox change, or external account is needed for Part 1.

## Shared AI client

`autoweb.ai.client.GeminiClient` is the sole Gemini transport. Construction/imports perform
no I/O. Code extraction and locator assistance accept an injected `StructuredClient`. The
owned login entry point and public pytest fixtures share that client across adapters.
Requests have bounded async/transport timeouts, response/image sizes, serialization, and no
automatic retries. After timeout/cancellation the client refuses new calls because its
blocking transport may still be finishing.

Google recommends maintained `google-genai` over deprecated `google-generativeai`.
AutoWeb uses the supported REST `generateContent` endpoint and structured JSON responses,
so no Gemini SDK is required. The obsolete, unused dependency was removed; the empty `ai`
extra preserves existing installation commands. See [library guidance](https://ai.google.dev/gemini-api/docs/libraries),
[REST reference](https://ai.google.dev/api/generate-content), and
[structured outputs](https://ai.google.dev/gemini-api/docs/structured-output).

No model is hardcoded or silently substituted. Select an account-accessible model with
text/structured-output support and, if needed, image input. Consult the [model catalog](https://ai.google.dev/gemini-api/docs/models).
Real model availability, quota, and response compatibility will be verified in Part 2.
For that later setup, put values in your ignored `.env`, never in a profile or message:

```dotenv
AUTOWEB_AI_ENABLED=true
AUTOWEB_AI_MODEL=your-account-accessible-model
GOOGLE_API_KEY=your-local-key
AUTOWEB_AI_LOCATOR_FALLBACK=true
AUTOWEB_AI_SCREENSHOTS=false
AUTOWEB_AI_REQUEST_TIMEOUT_SECONDS=10
```

Keep AI disabled for normal deterministic operation. Enabled AI requires model/key
configuration at preflight. A Gemini outage does not affect stable configured controls:
they complete without an AI request. Mail extraction also tries deterministic parsing
first and continues polling if optional AI extraction fails.

## Locator fallback and sensitive data

Fallback additionally requires `ai.locator_fallback=true` and individual-step opt-in:

```json
{
  "action": "fill", "credential": "password",
  "target": {"by": "label", "value": "Password"},
  "ai_fallback": true,
  "ai_hint": "Account password input in the sign-in form",
  "timeout_ms": 5000
}
```

Only an absent required control triggers assistance, before any action. Present controls
retain normal strict/actionability checks. Optional controls and waits remain deterministic.
Credential fills, clicks/submits, email-request clicks, and single-field OTP support
fallback. Split OTP requires configured locators.

The model receives bounded control metadata within the configured frame/form scope.
Current values, page URLs, and link destinations are omitted; known credential values are
redacted from metadata/hints. Tasks distinguish username, email, password and OTP fields.
The only permitted answer is one existing candidate ID or null. The model cannot provide
selectors, executable code, navigation commands, or an authentication verdict.
Malformed/extra output, duplicate JSON keys, unknown IDs, and identical ambiguous controls
fail clearly. The chosen control is rechecked for uniqueness, visibility, enablement,
editability when filling, and unchanged metadata. Password candidates require password
inputs. No Python `eval` or action retry is used. Authentication assertions stay unchanged.

Screenshots default off. If enabled, all inputs, textareas, contenteditable regions, frames,
and known secret text are masked before creating an in-memory PNG. Capture/masking failure
prevents the external call. PNGs are not saved. Other visible page content can still be sent;
enable this only for content you intend to share with Gemini. AI email extraction shares
matching-email text and inherently its verification code with Gemini.

Normal reports omit AI payloads/responses. Failure screenshots mask all editable controls
and frames, including AI-recovered controls. Opt-in raw Playwright traces remain sensitive:
they can contain unmasked DOM, screenshots, network data and tokens. See [trace handling](TESTING.md).

## Magic-link login

The [example profile](../configs/sites/email-magic-link.example.json) illustrates the schema;
its example.com values are not a runnable live login. Configure `email_magic_link` mode,
`verification.kind=magic_link`, one required `request_magic_link` click followed by one
`follow_magic_link`, sender/recipient/subject filters, exact `allowed_origins`, an initial
`link_path_prefix`, and explicit authenticated UI assertions. API/URL and rejected/expired
assertions are supported. All required credential fills precede the link request.

Baseline and timestamp precede the one request click. The provider excludes stale,
baseline, unrelated and already-selected mail. It extracts one allowed URL from plain text
or HTML anchor destinations, decoding HTML entities. Multiple distinct allowed links are
ambiguous and skipped. AI does not choose or invent links.

Origins match scheme, host and effective port, without wildcards, user credentials, paths
or queries. HTTPS is required except for explicitly allowed loopback HTTP test sites.
The initial path equals the configured prefix or descends from it at a slash boundary.
Fragments, backslashes, control characters, traversal and misleading hosts fail. The
engine repeats these checks for results from injected providers.

Consumption uses the same page/context and challenge cookies. Main-frame HTTP redirects
are fetched with automatic redirects disabled, validated, and replaced by fresh browser
navigations. One-use links are fetched once and context cookies are retained. Unapproved
HTTP/script destinations and loops exceeding ten navigations fail with
`verification_link_rejected`. The guard remains through authentication/acknowledgment.
This handling supports GET navigation; POST-based magic-link navigation is unsupported.

Retrieval/navigation alone never acknowledges email. Authentication must pass first.
Missing/stale/ambiguous mail times out; invalid provider results fail before navigation.
One challenge deadline bounds retrieval, consumption and authentication. Links/tokens
stay out of normal result/error text, but may remain in browser history and raw traces.
The independent Gmail diagnostic also accepts link profiles: its explicit `--show-code`
flag reveals the sensitive verification value, so do not use it in shared logs.

## Verification and Part 2

```sh
conda run -n autoweb python -m pytest -q
conda run -n autoweb python -m pytest tests/unit/test_phase7.py tests/integration/test_ai_locators.py tests/integration/test_magic_login.py -q
```

The controlled site binds links to requesting cookies, consumes them once, follows local
redirects, establishes authentication, and supports a protected action. Tests cover mail
correlation, exact destination rules, malicious provider results, expiry, missing mail,
HTTP/script redirects, loops and injected AI behavior on real DOM controls.

No site adapter was needed for these demonstrated flows. Add a focused adapter only if a
selected site's behavior requires one. CAPTCHA, SSO, SMS/TOTP, passkeys and signup remain
outside scope. Part 2 live attempts and outstanding checks are recorded in [the live validation report](PHASE7_LIVE_VALIDATION.md). Live tests require explicit selection, each new website requires the user's confirmation, and OAuth consent is never automatic.
