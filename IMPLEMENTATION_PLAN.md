# AutoWeb implementation plan

Status: Phases I-III implemented at the user's request. Phases IV-VII remain pending.

Phase I verification: editable installation in conda autoweb (Python 3.10.18); 49 configuration tests passed; schema-validation CLI passed; pip check reported no broken requirements. The installation repaired a pre-existing missing pytest dependency (exceptiongroup 1.3.1). Browser execution and live Gmail/Gemini validation remain in later phases.

## Objective and first release

Repurpose the two Python projects into a modular Playwright testing framework, starting with a reusable login capability. Retain separate modules for Gmail access, browser interaction, login orchestration, configuration, and test execution.

The first release supports configured websites with password login, email OTP login, and password plus email OTP. A successful login must be established by explicit assertions and must leave an authenticated browser session available for subsequent tests.

Adding a website should normally mean adding a site profile, credential references, and a login test. Unusual flows may need a small site adapter. Arbitrary websites cannot be assumed to share one login flow; universal automatic discovery is a longer-term goal.

Use the conda environment autoweb for every project command, dependency operation, script, and test. Ignore trial-codes/ entirely. Do not move or delete its contents as part of this work.

## Proposed folder boundaries

The tree below describes the target structure. Phase I establishes the package boundaries and configuration; execution modules, shared runtime contracts, further test folders, and runtime artifacts are added in their respective phases.

~~~text
src/autoweb/
  browser/          Browser lifecycle, locator resolution, reusable UI actions
  gmail/            Gmail OAuth/API access, MIME decoding, verification polling
  login/            Login flow, credential steps, challenges, success assertions
  config/           Validated settings, site profiles, credential references
  ai/               Optional Gemini integration and validated response parsing
  reporting/        Step results, diagnostics, failure artifacts
  contracts.py      Small shared request/result types and errors
configs/
  sites/            Versioned, non-secret site profiles
scripts/            Thin login runner and Gmail diagnostic entry points
tests/              Unit, integration, end-to-end tests, and fixtures
artifacts/          Ignored traces, screenshots, reports, optional session state
pyproject.toml      Packaging and development/test configuration
.env.example        Documented environment variable names without real values
~~~

Test folders: tests/unit/, tests/integration/, tests/e2e/, and tests/fixtures/.

Functions should perform focused tasks. Use classes where they naturally own resources or dependencies, such as a browser session or Gmail client. Avoid a general-purpose helper module becoming the home for unrelated functionality.

Dependency direction: entry points and tests call login orchestration; login calls browser actions and an injected verification provider; Gmail implements that provider. Gmail does not import Playwright or login orchestration. Shared contracts do not import either implementation. AI supplies extraction or locator suggestions; it does not own the flow or decide that login succeeded.

## Existing code migration

The source paths in the table below refer to the original repository at D:/RAJ ARYAN/Codec/scaile-tech/scaile-backlink. AutoWeb runs independently; legacy source and local OAuth files were retained there during the standalone migration.

| Current source | Proposed responsibility | Changes needed |
| --- | --- | --- |
| gmail-auto/email_client.py | gmail/client.py | Retain OAuth/API behavior; explicit setup, errors, pagination, configurable local token paths |
| gmail-auto/email_poller.py | gmail/poller.py | Return one fresh verification result; continue until success/deadline; remove browser callbacks |
| gmail-auto/code_parser.py | gmail/parser.py | Separate MIME decoding from validated extraction; correct regex; optional AI fallback |
| gmail-auto/main.py | scripts/read_verification.py | Thin standalone diagnostic entry point, without browser navigation |
| shadowdom-auto/core/helper_functions.py | browser/actions.py and browser/locators.py | Separate actions and targeting; nested frame support; explicit outcomes |
| shadowdom-auto/core/browser_handler.py | browser actions plus optional ai/locator_resolver.py | Remove mandatory screenshot/AI dependency and fixed delays |
| shadowdom-auto/core/automation_engine.py | login/flow.py and scripts/run_login.py | Replace the demonstration task loop with a login lifecycle and session handoff |
| shadowdom-auto/utils/task_loader.py | config/loader.py | Preserve each step's parameters and validate the complete profile |
| Both Gemini integrations | ai/client.py with purpose-specific adapters | Central configuration, lazy initialization, response validation, no eval |
| Existing JSON configs | configs/sites/ plus runtime settings | Separate site behavior, mailbox settings, and secrets |

Migrate incrementally. Keep one authoritative implementation for each responsibility. Legacy entry points remain outside AutoWeb; do not maintain duplicate engines in this repository. Preserve local credential/token files and configure their paths rather than silently relocating or overwriting them.

## Core contracts and flow

A site profile defines the entry URL, login mode, ordered steps and their individual parameters, target locators/frame scope, optional steps, Gmail filters, challenge format, timeout policy, and success/failure assertions. Steps retain their own details rather than sharing a dictionary that later steps overwrite.

A verification request carries the site/account identity, sender/recipient/subject filters, challenge start time, baseline message IDs, expected code format, and deadline. A verification result contains the validated value, message ID, received timestamp, and extraction method. Preserve codes as strings so leading zeros survive.

A login result records status, failed step/reason, elapsed time, artifact paths, and the active authenticated session when successful. It excludes passwords, OTP values, and tokens from normal logs. Distinguish invalid credentials, missing controls, rejected/expired codes, mailbox errors, unsupported challenges, and timeouts.

Login lifecycle:

1. Load and validate settings; initialize required dependencies before interacting with the website.
2. Open an isolated browser context and navigate to the configured login entry.
3. Handle optional banners, open the form, and execute configured credential steps.
4. Immediately before an action that requests an email code, establish the mailbox baseline and challenge timestamp.
5. Submit credentials or request the OTP, then wait for the expected next state.
6. If email verification is required, retrieve a fresh matching code and fill/submit it in the same browser context.
7. Assert configured authenticated UI/state; a completed click or disappearing form is insufficient.
8. Acknowledge the selected email after successful consumption according to policy; retain its ID during the attempt to prevent reuse.
9. Return the session to the caller for subsequent tests. The caller owns eventual cleanup. On failure, capture diagnostics before cleanup.

Use Playwright's async API consistently. Wrap blocking Gmail API operations in a controlled async boundary, with per-call transport timeouts and an overall deadline. Use async waiting between polls; do not block the event loop with time.sleep. Initially run Gmail-backed attempts sequentially; shared-mailbox parallelism needs explicit account/message isolation later.

## Phase 1: Package structure and configuration

- [x] Verify project tooling and dependencies inside conda autoweb.
- [x] Establish the package layout, imports, and one dependency definition; record working dependency versions when implementing.
- [x] Centralize runtime settings: browser options, timeout budgets, mailbox credentials paths, AI enablement/model, and artifact location.
- [x] Create validated site profiles with ordered steps, per-step parameters, flow type, locators, and success/failure checks.
- [x] Replace configured passwords with environment variable references and add non-secret examples.
- [x] Resolve configuration and token paths explicitly so running from another directory works.
- [x] Reject unknown actions, missing required fields, and unavailable credential references before opening the browser.
- [x] Update ignore rules for secrets, session state, and generated artifacts at their new locations.

Acceptance: settings and profiles load consistently; imports have no authentication, browser, or AI API side effects; invalid configuration fails clearly.

## Phase 2: Reliable Playwright browser primitives

- [x] Extract lifecycle functions for launching the browser, creating contexts/pages, and guaranteed cleanup.
- [x] Implement focused navigation, banner handling, click, fill, submit, and state-wait functions.
- [x] Prefer configured stable locators and semantic locators; scope ambiguous controls to the expected form.
- [x] Support password inputs and both single-field and split-field OTP controls.
- [x] Resolve inputs and buttons across configured nested frame paths; use Playwright locators for open Shadow DOM.
- [x] Replace fixed sleeps and unconditional networkidle waits with element/navigation/state conditions.
- [x] Treat absent optional banners as a skip and failed required actions as failures.
- [x] Return explicit outcomes or propagate typed errors rather than printing and continuing.

Acceptance: controlled pages exercise ordinary DOM, open Shadow DOM, and nested iframes; required actions cannot silently fail.

Verification: 113 tests passed in conda autoweb, including real headless Chromium checks against loopback-only fixtures. Coverage includes scoped/ambiguous targets, password and OTP inputs, nested-frame controls, optional banners, state/navigation waits, HTTP/timeout failures, independent browser launch timing, and cleanup after failures or cancellation during shutdown. The matching Chromium/FFmpeg binaries were installed. No live website, Gmail, or Gemini calls were required.

## Phase 3: Complete password login

- [x] Implement the login lifecycle independently of Gmail and AI.
- [x] Support username/password on one screen and username-then-password flows.
- [x] Fill passwords, submit the form, and detect rejected credentials.
- [x] Define success using an authenticated UI assertion, with URL checks where appropriate; allow a configured session/API assertion when needed.
- [x] Keep a successful context/page available for post-login tests; clean up failures reliably.
- [x] Add one example profile and a deterministic local end-to-end password-login test.

Acceptance: a test logs in, proves authentication, and performs a protected post-login action. Incorrect credentials produce a failed test with a useful reason.

Verification: 141 tests passed in conda autoweb, including both single-screen and username-then-password login against a controlled cookie-authenticated local site, protected actions in the same session, rejected credentials, missing controls, deadlines, false success indicators, API session assertions, CLI outcomes, and cancellation cleanup. The autoweb-login console entry point was installed and its help command verified. Two runnable local profiles and synthetic demo environment values are included. The legacy external sites were not logged into; they still require verified profiles and real test-account credential references.

## Phase 4: Independent Gmail verification module

- [ ] Separate OAuth setup from message retrieval and handle unusable or expired credentials clearly.
- [ ] Preserve recursive MIME decoding; handle base64 padding, text/HTML selection, entities, and HTML links deliberately.
- [ ] Correct the overescaped regex and validate code length/character rules; allow extraction without a capture group.
- [ ] Use configured deterministic extraction first; enable Gemini only as an optional fallback with the same validation rules.
- [ ] Filter by sender, recipient/account, subject, challenge timing, and baseline IDs; check message metadata rather than relying on unread status alone.
- [ ] Handle pagination and candidate ordering so stale or unrelated messages cannot win by list order.
- [ ] Poll until a valid result or deadline; continue past malformed/nonmatching candidates instead of stopping after the first batch.
- [ ] Distinguish API/authentication failures from an empty mailbox; bound retries and API request duration.
- [ ] Return one result through the verification-provider contract, including its source message ID.
- [ ] Make email acknowledgment explicit; do not mark every fetched email read regardless of extraction or login success.
- [ ] Preserve a standalone Gmail diagnostic script that does not perform website actions or reveal codes by default.

Acceptance: saved synthetic message fixtures cover plain text, HTML, nested MIME, leading zeros, multiple candidates, stale mail, malformed payloads, API failures, and timeout. Gmail can be exercised without importing the browser package.

## Phase 5: End-to-end email verification login

- [ ] Inject the Gmail provider into the login flow, with a fake provider for deterministic tests.
- [ ] Add email OTP-only and password-plus-email-OTP flow profiles.
- [ ] Capture the baseline before requesting the code; use one challenge deadline across polling and submission.
- [ ] Fill and submit the selected code without closing or replacing the active browser context.
- [ ] Detect incorrect/expired codes and challenge transitions; bound any resend/retry and refresh correlation data for a new challenge.
- [ ] Avoid automatic retries of non-idempotent submit/resend actions without inspecting the resulting state.
- [ ] Verify actual authentication before reporting success or acknowledging the email.
- [ ] Perform an opt-in live Gmail/site smoke test when a target test account and site profile are available.

Acceptance: deterministic tests complete all three login modes and a protected action afterward. No-code, stale-code, wrong-code, and expired-code cases fail predictably without consuming unrelated mail.

## Phase 6: Testing framework entry points and diagnostics

- [ ] Use pytest with async fixtures aligned with the existing async Playwright API; keep browser/context ownership in one fixture layer.
- [ ] Provide fixtures for settings, site profiles, isolated browser sessions, and real/fake verification providers.
- [ ] Keep production login functions independent of pytest and usable from a thin standalone runner.
- [ ] Separate fast unit tests, controlled browser integration tests, and explicitly selected live-site tests.
- [ ] Parameterize login tests by site profile and ensure failures produce nonzero test/runner outcomes.
- [ ] Record concise step results, failure screenshots, and Playwright traces with sensitive-data handling.
- [ ] Document optional session-state reuse and expiry checks; default test isolation remains explicit.
- [ ] Update README setup, conda autoweb commands, profile authoring, standalone scripts, and test examples.

Acceptance: one documented test command runs deterministic coverage without Gmail or Gemini access, and live tests require explicit selection. Successful login is reusable as a fixture for future tests.

## Phase 7: Optional AI fallback and later login variants

- [ ] Consolidate Gemini behind an injected, lazily initialized client; verify supported SDK/model choices during implementation.
- [ ] Replace eval with validated structured parsing; handle ambiguous or invalid suggestions without guessing a control.
- [ ] Make field prompts describe the actual target, including passwords and OTPs; recheck suggested locators against current page state.
- [ ] Mask sensitive inputs before external screenshot analysis and redact results/artifacts as appropriate.
- [ ] Keep configured login tests working with AI disabled or unavailable.
- [ ] Add magic-link login after OTP behavior is stable, preserving the browser session and validating configured destination domains.
- [ ] Add site adapters for genuinely different flows as demonstrated needs arise.

Acceptance: enabling AI can assist discovery without changing success criteria; disabling it does not break configured flows.

## Delivery order and scope limits

Implement phases 1-3 first to establish one complete password-login path. Implement phase 4 as the independently testable Gmail module, then phase 5 to connect it. Add tests and failure diagnostics alongside each phase; phase 6 standardizes the public testing interface. Phase 7 follows the deterministic login foundation.

Initial execution is sequential and Chromium-based, matching the current project. Broader browser matrices, shared-mailbox parallel runs, signup, social/enterprise SSO, SMS/TOTP/device approvals, CAPTCHA, passkeys, and unrelated website actions are later work. Unsupported challenges should produce a clear outcome rather than false success.

The first live website/test account has not been selected. Local controlled fixtures and fake verification data allow implementation and testing to progress independently; live validation depends on that profile and account.

Definition of done for the first release: clean module boundaries; all three initial login modes verified on controlled fixtures; an authenticated session usable by later tests; explicit errors and deadlines; optional Gmail/AI dependencies; non-secret configuration examples; documented autoweb setup; trial-codes ignored.
