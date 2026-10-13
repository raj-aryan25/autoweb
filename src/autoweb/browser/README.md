# Browser primitives

Phase II provides async Playwright functions for browser sessions, configured locators, and UI interactions. These functions do not authenticate an account or retrieve verification messages. Phase III will orchestrate them into login flows.

## Sessions and ownership

Use open_session(runtime_settings) as an async context manager. It launches the configured engine, creates an isolated browser context, applies the configured action/navigation defaults, and creates a page. The returned BrowserSession exposes browser, context, page, and timeouts. Browser startup has a separate browser.launch_timeout_ms budget (30000 ms by default), so a short navigation budget does not prevent a cold browser launch.

The session remains open throughout the caller's async with block, so a future login test can perform protected actions before exiting. At exit, the context closes all its pages, then the browser and Playwright driver close. Cleanup is attempted after normal completion, exceptions, and cancellation. Cancellation during shutdown waits for the close attempts to finish rather than leaving detached cleanup tasks. If cleanup fails during otherwise successful work, BrowserCleanupError is raised. If another failure is already propagating, cleanup attempts continue without replacing that original failure.

launch_browser, create_context, and create_page are also public for callers that need multiple isolated contexts on one browser. The context owns pages created by create_page; it must remain inside its owner scope. Importing these functions does not import Playwright or launch its driver; Playwright is imported when an operation is called.

## Functions

| Function | Purpose |
| --- | --- |
| navigate | Navigate, waiting for DOM content by default; fail on HTTP status 400 or higher |
| click | Click the configured control using Playwright actionability checks |
| fill | Fill a string or masked SecretStr into the configured input, including passwords |
| submit | Click the configured submit control; callers verify the next state |
| accept_cookies | Click an optional/required banner control; skip only an absent optional control |
| fill_otp | Fill one code input or one input per character, preserving leading zeros |
| wait_for_state | Wait for visible, hidden, attached, or detached element state |
| wait_for_url | Wait for a URL/glob and the selected navigation load state |
| resolve_locator | Build a strict lazy locator from the profile target |

All interaction functions return ActionResult with action, status (succeeded/skipped), elapsed_seconds, and optional reason. Required failures raise BrowserActionError or BrowserActionTimeout; they are not printed and swallowed. Results and error messages contain no filled values or raw Playwright DOM/error dumps. Error reasons distinguish timeout, ambiguous_target, session_closed, interaction_failed, invalid_input, and HTTP failures where applicable.

An action's success means the interaction completed, not that the website accepted credentials. There is no automatic resubmission or retry of a submit control beyond Playwright's pre-click actionability waiting.

## Locators and state waits

Configured locators support role, label, placeholder, text, CSS, and test ID. Role locators can include name; exact text/name matching is enabled by default. Scope ambiguous controls to their form using scope. Matching multiple elements fails; the resolver never selects the first match arbitrarily.

frame_path lists iframe CSS selectors from outermost to innermost. Inputs, buttons, banner controls, and state waits use the same frame resolution. Playwright locators traverse open Shadow DOM, including inside those frames. Closed shadow roots are outside this implementation.

accept_cookies probes physical attachment, including hidden role matches. An absent optional control yields skipped/absent after the configured wait budget. A present hidden or disabled control, ambiguous target, invalid selector, or closed session fails. Delayed banners can appear within the same budget; there is no fixed sleep.

Navigation defaults to domcontentloaded. commit and load are available when needed. Subsequent UI readiness belongs in wait_for_state/wait_for_url; the module does not wait for unconditional networkidle or add fixed delays.

## Timeouts and sensitive inputs

Supply timeout_ms for a profile step override. Public helpers otherwise use TimeoutSettings defaults: 5000 ms for actions and 30000 ms for navigation. When using customized runtime budgets, pass session.timeouts.action_ms or session.timeouts.navigation_ms explicitly to helpers; raw page/context operations inherit the defaults applied by create_context.

A multi-field OTP fill and a banner attachment/click share one total operation budget, rather than receiving a new full timeout for each sub-operation. Zero, negative, non-finite, or nonnumeric timeouts are rejected rather than allowing an unbounded wait.

fill accepts str or SecretStr. fill_otp accepts these same types, rejects empty/whitespace codes and mismatched target counts, and can enforce expected_length. Split fields must have distinct locator specifications. Input validation occurs before filling. Partial input can remain after a later field fails; the caller receives an error and should decide how to recover. Codes are always strings, so leading zeros survive.

## Local integration checks

Install the browser matching the pinned Playwright version and run all tests inside conda autoweb:

~~~powershell
conda run -n autoweb python -m playwright install chromium
conda run -n autoweb python -m pytest -q
~~~

Integration tests serve tests/fixtures/ through a loopback HTTP server and use headless Chromium. They require no Gmail, Gemini, live website, or real account. Unit tests cover lifecycle failure paths and invalid inputs, including driver cleanup after launch/page-creation failures. Browser startup problems raise BrowserStartupError rather than skipping integration coverage.
