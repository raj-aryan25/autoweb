"""Loopback email challenge site and synthetic mailbox; never sends real mail."""

import base64
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME, Handler, LoginSite

DEMO_EMAIL = "test@example.test"
DEMO_CODE = "001234"


def message(message_id, code=DEMO_CODE, *, sender="noreply@example.test", received_at=None):
    received_at = received_at or datetime.now(timezone.utc)
    return {
        "id": message_id, "internalDate": str(int(received_at.timestamp() * 1000)),
        "payload": {"mimeType": "text/plain", "headers": [
            {"name": "From", "value": sender}, {"name": "To", "value": DEMO_EMAIL},
            {"name": "Subject", "value": "Your verification code"},
        ], "body": {"data": base64.urlsafe_b64encode(f"Your code: {code}".encode()).decode()}},
    }


def email_html(password=False, split=False, scenario="normal"):
    credentials = ('<label>Username<input name="username"></label>'
                   '<label>Password<input name="password" type="password"></label>' if password
                   else '<label>Email<input name="email"></label>')
    controls = (''.join(f'<input aria-label="Digit {i + 1}" name="digit">' for i in range(6))
                if split else '<label>Verification code<input name="code"></label>')
    return f'''<!doctype html><html><body>
    <form id="request">{credentials}<button>Send code</button></form>
    <form id="verify" hidden>{controls}<button>Verify</button></form>
    <p data-testid="login-error" hidden>Invalid credentials</p>
    <p data-testid="otp-error" hidden>Incorrect code</p>
    <p data-testid="otp-expired" hidden>Expired code</p>
    <script>
    document.querySelector('#request').onsubmit = async event => {{
      event.preventDefault();
      const response = await fetch('/challenge?scenario={scenario}', {{
        method: 'POST', body: new URLSearchParams(new FormData(event.target))
      }});
      if (response.ok) {{ event.target.hidden = true; document.querySelector('#verify').hidden = false; }}
      else document.querySelector('[data-testid="login-error"]').hidden = false;
    }};
    document.querySelector('#verify').onsubmit = async event => {{
      event.preventDefault();
      const fields = [...event.target.querySelectorAll('input')];
      const response = await fetch('/verify', {{method: 'POST', body: new URLSearchParams({{code: fields.map(f => f.value).join('')}})}});
      if (response.ok) location.href = '/protected';
      else document.querySelector(response.status === 410 ? '[data-testid="otp-expired"]' : '[data-testid="otp-error"]').hidden = false;
    }};
    </script></body></html>'''


class EmailSite(LoginSite):
    def __init__(self):
        super().__init__()
        # Replace the handler chosen by the password demo.
        self.RequestHandlerClass = EmailHandler
        self.messages = {}
        self.challenges = {}
        self.request_count = 0
        self.verify_count = 0


class EmailHandler(Handler):
    def do_GET(self):
        path = urlsplit(self.path)
        if path.path in {"/email-login", "/password-email-login"}:
            query = parse_qs(path.query)
            self.respond(200, email_html(path.path == "/password-email-login",
                                       query.get("split") == ["true"],
                                       query.get("scenario", ["normal"])[0]))
        else:
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path)
        if path.path not in {"/challenge", "/verify"}:
            return super().do_POST()
        values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        if path.path == "/challenge":
            with self.server.lock:
                self.server.request_count += 1
            if not (values.get("email") == [DEMO_EMAIL] or (
                values.get("username") == [DEMO_USERNAME] and values.get("password") == [DEMO_PASSWORD]
            )):
                return self.respond_json(401, {})
            scenario = parse_qs(path.query).get("scenario", ["normal"])[0]
            challenge_id = uuid4().hex
            with self.server.lock:
                self.server.challenges[challenge_id] = scenario
                if scenario != "no-mail":
                    self.server.messages[challenge_id] = message(challenge_id)
            self.respond_json(200, {}, headers=[("Set-Cookie", f"challenge={challenge_id}; Path=/; HttpOnly")])
        else:
            from http.cookies import SimpleCookie

            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            challenge_id = cookies.get("challenge")
            with self.server.lock:
                self.server.verify_count += 1
                scenario = self.server.challenges.get(challenge_id.value) if challenge_id else None
            if scenario == "expired":
                return self.respond_json(410, {})
            if scenario is None or values.get("code") != [DEMO_CODE]:
                return self.respond_json(401, {})
            token = uuid4().hex
            with self.server.lock:
                self.server.sessions.add(token)
            self.respond_json(200, {}, headers=[
                ("Set-Cookie", f"autoweb_test_session={token}; Path=/; HttpOnly; SameSite=Lax"),
            ])
