"""Loopback-only login demo. All credentials are synthetic test data.

Run: conda run -n autoweb python tests/fixtures/login_site.py --port 8765
"""

import argparse
import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

DEMO_USERNAME = "test-user"
DEMO_PASSWORD = "test-password"


def login_html(two_step=False, scenario="normal"):
    username = '<label for="username">Username</label><input id="username" name="username" autocomplete="username">'
    password = '<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password"><button type="submit">Sign in</button>'
    if two_step:
        forms = f'<form id="username-form">{username}<button type="submit">Continue</button></form><form id="password-form" hidden>{password}</form>'
        binding = '''
        document.querySelector('#username-form').onsubmit = event => {
          event.preventDefault();
          if (document.querySelector('#username').value !== 'test-user') {
            reject(); return;
          }
          document.querySelector('#username-form').hidden = true;
          document.querySelector('#password-form').hidden = false;
        };
        document.querySelector('#password-form').onsubmit = submitLogin;
        '''
    else:
        forms = f'<form id="login-form">{username}{password}</form>'
        binding = "document.querySelector('#login-form').onsubmit = submitLogin;"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Local login demo</title></head><body>
      <section id="cookies"><button onclick="document.querySelector('#cookies').remove()">Accept all</button></section>
      <h1>Sign in</h1>{forms}<p data-testid="login-error" role="alert" hidden>Invalid credentials</p>
      <script>
        const scenario = {json.dumps(scenario)};
        function reject() {{ document.querySelector('[data-testid="login-error"]').hidden = false; }}
        async function submitLogin(event) {{
          event.preventDefault();
          if (scenario === 'fake' || scenario === 'both') {{
            history.pushState({{}}, '', '/protected');
            const account = document.createElement('div'); account.dataset.testid = 'account-menu';
            account.textContent = 'Apparent account'; document.body.appendChild(account);
            if (scenario === 'both') reject();
            return;
          }}
          const body = new URLSearchParams({{
            username: document.querySelector('#username').value,
            password: document.querySelector('#password').value
          }});
          const response = await fetch('/session', {{method: 'POST', body}});
          if (response.ok) location.href = '/protected';
          else reject();
        }}
        {binding}
      </script></body></html>'''


PROTECTED_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Protected page</title></head><body>
  <h1>Account dashboard</h1><div data-testid="account-menu">Test account</div>
  <button id="protected-action">Perform protected action</button><p data-testid="protected-result" hidden></p>
  <script>
    document.querySelector('#protected-action').onclick = async () => {
      const response = await fetch('/api/protected');
      const result = await response.json();
      const output = document.querySelector('[data-testid="protected-result"]');
      output.textContent = result.message; output.hidden = false;
    };
  </script></body></html>'''


class LoginSite(ThreadingHTTPServer):
    def __init__(self, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.sessions = set()
        self.lock = Lock()
        self.attempt_count = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def authenticated(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        token = cookie.get("autoweb_test_session")
        with self.server.lock:
            return token is not None and token.value in self.server.sessions

    def respond(self, status, body, content_type="text/html; charset=utf-8", headers=()):
        content = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def respond_json(self, status, body, headers=()):
        self.respond(status, json.dumps(body), "application/json", headers)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route in {"/login", "/login-two-step", "/fake-success", "/both-outcomes"}:
            scenario = {"/fake-success": "fake", "/both-outcomes": "both"}.get(route, "normal")
            self.respond(200, login_html(route == "/login-two-step", scenario))
        elif route == "/protected":
            if self.authenticated():
                self.respond(200, PROTECTED_HTML)
            else:
                self.respond(302, "", headers=[("Location", "/login")])
        elif route == "/api/session":
            self.respond_json(200, {"authenticated": self.authenticated()})
        elif route == "/api/protected":
            if self.authenticated():
                self.respond_json(200, {"message": "Protected action completed"})
            else:
                self.respond_json(401, {"message": "Authentication required"})
        else:
            self.respond(404, "Not found")

    def do_POST(self):
        if urlsplit(self.path).path != "/session":
            self.respond(404, "Not found")
            return
        values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
        with self.server.lock:
            self.server.attempt_count += 1
        if values.get("username") == [DEMO_USERNAME] and values.get("password") == [DEMO_PASSWORD]:
            token = uuid4().hex
            with self.server.lock:
                self.server.sessions.add(token)
            self.respond_json(200, {"accepted": True}, headers=[
                ("Set-Cookie", f"autoweb_test_session={token}; Path=/; HttpOnly; SameSite=Lax"),
            ])
        else:
            self.respond_json(401, {"accepted": False})


def make_server(port=0):
    return LoginSite(port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"Local login demo: http://127.0.0.1:{server.server_port}/login", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
