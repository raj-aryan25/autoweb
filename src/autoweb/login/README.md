# Password and email verification login

The runner executes one selected, validated password, email OTP, or password-plus-email-OTP profile. Phase V connects an injected verification provider to the same browser session. Signup, social login, and AI discovery remain later work. The original websites in shadowdom-auto/tasks/tasks.json are legacy demonstrations; they are not automatically migrated or executed by this runner. See [environment and live-site setup](../../../docs/EMAIL_LOGIN_SETUP.md) for the exact files to customize.

## What runs

The flow navigates to entry_url and executes steps in their configured order: optional banners, identifier entry, password entry, clicks, submission, and explicit waits. An identifier can be a username, email address, or account ID; map it to the appropriate credential reference and locator. Passwords come from environment references, not JSON literals.

Both single-screen and username-then-password forms are supported. Each step retains its own timeout and parameters. Missing optional controls are skipped; present but noninteractive controls and ambiguous matches fail. Required actions are not silently skipped. Submission is not automatically retried.

The login deadline covers navigation, steps, and authentication checks once the browser session is ready. Browser startup has its separate launch timeout. Step timeouts are capped by the remaining login budget.

## Proving authentication

All configured success checks must match. At least one visible authenticated UI element is required by the profile schema. URL checks can confirm the expected destination, and an optional API assertion can check the site's authenticated session:

~~~json
{
  "type": "api",
  "url": "/api/session",
  "expected_status": 200,
  "expected_json": {"authenticated": true}
}
~~~

API checks use GET through the same browser context and its cookies, with redirects disabled. Root-relative URLs resolve against the profile's entry origin, not a redirect destination. Absolute API URLs must use that same origin. expected_json is a nonempty subset of the response JSON; primitive value types must also match. A 200 response alone is insufficient.

Failure checks should identify a rejected state, such as an error banner, rather than the normal signed-out state. They are observed during step execution and authentication checks. A rejection wins over an apparent success indicator. Authentication conditions are polled within the remaining deadline; there are no unconditional readiness sleeps.

## Reusing the active session

The convenience context manager owns startup and cleanup and yields only after authentication has been confirmed:

~~~python
from autoweb.config import load_configuration
from autoweb.login import authenticated_session

configuration = load_configuration("configs/sites/local-password.example.json",
                                 env_file="configs/demo.env.example")

async def test_protected_action():
    async with authenticated_session(configuration) as result:
        page = result.session.page
        await page.get_by_role("button", name="Perform protected action").click()
        # Perform assertions or additional tests while the same session is open.
~~~

Run async functions through pytest or an async application entry point in conda autoweb. Exiting the async with closes the context/pages, browser, and driver. Exceptions and cancellation also clean up owned resources.

For a session whose lifetime you already own, use run_login(session, configuration). It returns the same active session on success and does not close caller-owned resources. If it fails, the owner must still close its context.

Success returns LoginResult with site, status, elapsed_seconds, completed/skipped step results, and the active session. Failure raises LoginError with a structured failed result containing failed_step, reason, and available local artifact paths. Failure results do not expose a supposedly authenticated session. Unresolved credentials are rejected before browser startup.

For OTP modes, `authenticated_session(configuration, verification_provider=None)` opens an owned Gmail provider before the browser when none is supplied. OAuth setup remains explicit and separate. Pass a fake or application-owned provider to avoid Gmail initialization. `run_login(session, configuration, verification_provider)` requires an explicit provider for email modes and never closes caller-owned resources. Providers implement the protocol in `autoweb.contracts` and raise `VerificationError` with a static, secret-safe reason. Gmail implements this contract without importing the browser/login packages.

The baseline is captured before `request_otp`; a timezone-aware timestamp is taken immediately before the request click. One challenge deadline covers baseline, request, receive, fill, submit, authentication, and acknowledgment, capped by the overall login deadline. Polling uses the challenge budget independently of browser action timeouts. Single and split OTP fields preserve leading zeros. Optional `verification.rejected`/`expired` assertions distinguish rejected and expired codes. No request, submit, or resend is automatically retried. Only proven authentication allows `on_success` acknowledgment; `never` leaves mail untouched. An acknowledgment failure is reported at that stage without resubmitting the form.

## Diagnostics and command line

Failures attempt a bounded screenshot under artifacts/login-failures/. Configured identifier/password/OTP inputs and password-type inputs are masked. Screenshot failures do not replace the original login error. Filled values, cookie tokens, response bodies, and raw Playwright DOM dumps are excluded from result/error messages. Broader trace/reporting support belongs to Phase VI.

The CLI validates one selected site profile, runs its login flow, reports success/failure, and closes the session. Use the Python context manager for post-login work:

~~~powershell
conda run -n autoweb python -m autoweb.login --site configs/sites/local-password.example.json --env-file configs/demo.env.example
~~~

Equivalent entry points are scripts/run_login.py and the installed autoweb-login command. Options include --settings, --project-root, --env-file, --headed, and --slowmo [MS]. Use --slowmo for a 700 ms delay per browser action or --slowmo 1200 for a custom delay. This overrides the configured delay for this run in both headed and headless modes; --slowmo 0 disables a configured delay. Without the flag, the existing settings/environment value applies (default 0). Exit codes are 0 for confirmed login, 1 for login/browser failure, 2 for invalid configuration, and 130 for interruption. Credentials are never supplied as command-line arguments.

## Local demo

Start the controlled site in one terminal:

~~~powershell
conda run -n autoweb python tests/fixtures/login_site.py --port 8765
~~~

In another terminal, run the CLI command above. For the second-screen variant, select configs/sites/local-password-two-step.example.json. configs/demo.env.example contains synthetic credentials accepted only by this test website.

These two profiles are runnable against the local demo. The earlier example.com profiles remain schema examples. Real websites require their own verified selectors, success/failure conditions, and test-account environment variables; the current work does not assert that the legacy sites are working.

## Verification

~~~powershell
conda run -n autoweb python -m pytest -q
~~~

Tests cover complete single-screen and two-screen login, real session-cookie checks, a protected action afterward, invalid credentials, false success indicators, missing controls, step/overall deadlines, optional steps, CLI exit codes, and cancellation cleanup. All browser integration tests use local controlled sites and synthetic accounts.
