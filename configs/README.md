# Configuration reference

## Runtime settings

configs/settings.json contains the shared defaults. Every object rejects unknown fields. Timeout values must be positive and finite, and the verification budget must not exceed the total login budget. Browser engines are chromium, firefox, or webkit.

AI is disabled by default. Enabling it requires an explicit model and, during credential preflight, the API-key environment variable referenced in ai.api_key. Model availability is not checked by configuration validation.

Environment variables override these settings:

| Variable | Setting |
| --- | --- |
| AUTOWEB_BROWSER | browser.engine |
| AUTOWEB_HEADLESS | browser.headless; true or false |
| AUTOWEB_SLOW_MO_MS | browser.slow_mo_ms |
| AUTOWEB_LAUNCH_TIMEOUT_MS | browser.launch_timeout_ms |
| AUTOWEB_ACTION_TIMEOUT_MS | timeouts.action_ms |
| AUTOWEB_NAVIGATION_TIMEOUT_MS | timeouts.navigation_ms |
| AUTOWEB_LOGIN_TIMEOUT_SECONDS | timeouts.login_seconds |
| AUTOWEB_VERIFICATION_TIMEOUT_SECONDS | timeouts.verification_seconds |
| AUTOWEB_GMAIL_CREDENTIALS_FILE | gmail.credentials_file |
| AUTOWEB_GMAIL_TOKEN_FILE | gmail.token_file |
| AUTOWEB_GMAIL_POLL_INTERVAL_SECONDS | gmail.poll_interval_seconds |
| AUTOWEB_GMAIL_REQUEST_TIMEOUT_SECONDS | gmail.request_timeout_seconds |
| AUTOWEB_AI_ENABLED | ai.enabled; true or false |
| AUTOWEB_AI_MODEL | ai.model |
| AUTOWEB_ARTIFACTS_DIR | artifacts_dir |
| AUTOWEB_ROOT | Configuration project root |

## Site profiles

Each file defines schema_version (1), name, entry_url, login_mode, credentials, steps, success, and optional failure and verification fields. The supplied profiles demonstrate password, email_otp, and password_email_otp modes.

Credential entries use objects such as {"env": "EXAMPLE_PASSWORD"}; plaintext passwords are not accepted. Each fill step declares which credential it uses. Preflight checks all declared references, the verification recipient, and the enabled AI key for nonempty values. Process variables override dotenv entries.

Steps retain their individual parameters:

| Action | Parameters |
| --- | --- |
| click, submit, accept_cookies, open_login | target locator |
| fill | target locator and credential (username, email, or password) |
| wait_for | target locator and state (visible, hidden, attached, detached) |
| request_otp | target locator for the click that requests verification |
| fill_otp | targets: one locator, or one locator per code character |

Every step can set optional and timeout_ms. Required credential entry, OTP request/fill, and submission cannot be replaced by optional steps. Email flows require one request_otp followed by one fill_otp and a submit; the request action can also submit the password form in a password-plus-OTP flow.

Locators define by (role, label, placeholder, text, css, test_id), value, and optional exact, name, frame_path, and scope. name applies only to role locators. frame_path is an ordered list of iframe CSS selectors from outermost to innermost; scope is a CSS selector within that frame. Phase II implements these locator contracts for actions and state waits; duplicate matches fail rather than selecting the first match.

Success and failure checks are element assertions (target plus visible/hidden state), URL assertions (pattern), or API assertions (same-origin GET URL, expected_status, and nonempty expected_json). Success requires at least one visible authenticated-element check; a URL or disappearing form alone is insufficient. The password runner requires all success assertions and treats any matching failure assertion as failure. API checks use the browser context's cookies and do not follow redirects.

Email verification defines sender, optional subject, recipient (an environment reference), code_regex, code_length (1-128), code_characters (default `[0-9]`, a regex matched against each character), optional timeout_seconds, and acknowledge (never or on_success). Both regexes must compile. Extraction supports a named `code` capture, the first capture, or the full match without a capture. The runtime checks polling/deadline consistency; acknowledging a message requires gmail.modify in the scope list. Configuration loading does not retrieve or consume emails. The independent [Gmail diagnostic](../src/autoweb/gmail/README.md) uses these filters without password credentials; the password runner continues to reject OTP modes until Phase V.

## Local files

Copy an example to a site profile for your target and adapt its locators, assertions, and email filters. Use configs/sites/*.local.json for ignored account-specific profiles. Alternative settings can be supplied with --settings; configs/settings.local.json is ignored. Paths in all configuration files are relative to the selected project root, not to the JSON file's directory.

Run checks with the autoweb conda environment. Schema checks help review non-secret profiles; use the default credential preflight before an execution runner initializes browser/provider clients.
