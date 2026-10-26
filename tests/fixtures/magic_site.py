"""One-use magic links tied to a requesting browser cookie; no real mailbox."""

import base64
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from tests.fixtures.email_site import DEMO_EMAIL, EmailSite, message
from tests.fixtures.login_site import Handler

LOGIN = '''<!doctype html><form>
<label>Email<input name="email"></label><button>Send sign-in link</button></form>
<p id="waiting" hidden>Check your email</p><script>
document.querySelector('form').onsubmit = async event => {
  event.preventDefault();
  await fetch('/magic-challenge' + location.search, {
    method: 'POST', body: new URLSearchParams(new FormData(event.target))});
  document.querySelector('#waiting').hidden = false;
};</script>'''


class MagicSite(EmailSite):
    def __init__(self):
        super().__init__()
        self.RequestHandlerClass = MagicHandler
        self.consumed = set()
        self.outside_count = 0


class MagicHandler(Handler):
    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/magic-challenge":
            return super().do_POST()
        body = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        if body.get("email") != [DEMO_EMAIL]:
            return self.respond_json(401, {})
        identity = uuid4().hex
        scenario = parse_qs(parsed.query).get("scenario", ["normal"])[0]
        origin = f"http://127.0.0.1:{self.server.server_port}"
        link = f"{origin}/magic/consume?token={identity}&purpose=login"
        item = message(identity)
        item["payload"]["headers"][-1]["value"] = "Your sign-in link"
        item["payload"]["mimeType"] = "text/html"
        item["payload"]["body"]["data"] = base64.urlsafe_b64encode(
            f'<p>Sign in: <a href="{link.replace("&", "&amp;")}">Continue</a></p>'.encode()).decode()
        with self.server.lock:
            self.server.request_count += 1
            self.server.challenges[identity] = scenario
            if scenario != "no-mail":
                self.server.messages[identity] = item
        self.respond_json(200, {}, headers=[("Set-Cookie", f"challenge={identity}; Path=/; HttpOnly")])

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/magic-login":
            return self.respond(200, LOGIN)
        if parsed.path == "/outside":
            self.server.outside_count += 1
            return self.respond(200, "Must not be visited")
        if parsed.path == "/magic/consume":
            token = parse_qs(parsed.query).get("token", [""])[0]
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get("challenge")
            with self.server.lock:
                self.server.verify_count += 1
                scenario = self.server.challenges.get(token)
                invalid = token in self.server.consumed or cookie is None or cookie.value != token
                self.server.consumed.add(token)
            if scenario == "expired":
                return self.respond(410, '<p data-testid="link-expired">Expired link</p>')
            if scenario is None or invalid:
                return self.respond(401, '<p data-testid="link-error">Invalid link</p>')
            if scenario == "external-redirect":
                # Same local server, different host: the exact allowed origin must win.
                return self.respond(302, "", headers=[("Location", f"http://localhost:{self.server.server_port}/outside")])
            if scenario == "js-external":
                return self.respond(200, f'<script>location.href="http://localhost:{self.server.server_port}/outside"</script>')
            if scenario == "redirect-loop":
                return self.respond(302, "", headers=[("Location", "/magic/loop")])
            token = uuid4().hex
            self.server.sessions.add(token)
            return self.respond(302, "", headers=[
                ("Set-Cookie", f"autoweb_test_session={token}; Path=/; HttpOnly; SameSite=Lax"),
                ("Location", "/magic/redirect"),
            ])
        if parsed.path == "/magic/redirect":
            return self.respond(303, "", headers=[("Location", "/protected")])
        if parsed.path == "/magic/loop":
            return self.respond(302, "", headers=[("Location", "/magic/loop")])
        return super().do_GET()
