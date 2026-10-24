"""Real headless Chromium and loopback-only pages for browser integration tests."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio

from autoweb.browser import open_session
from autoweb.config import load_configuration
from autoweb.config.models import RuntimeSettings, TimeoutSettings
from tests.fixtures.email_site import DEMO_EMAIL, EmailSite
from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def fixture_url():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(FIXTURES), **kwargs)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest_asyncio.fixture
async def session():
    settings = RuntimeSettings(timeouts=TimeoutSettings(action_ms=1000, navigation_ms=5000))
    async with open_session(settings) as active:
        yield active


@pytest_asyncio.fixture
async def page(session, fixture_url):
    await session.page.goto(f"{fixture_url}/browser.html", wait_until="domcontentloaded")
    yield session.page


@pytest.fixture(scope="session")
def login_site():
    from tests.fixtures.login_site import make_server

    server = make_server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def configuration_factory(tmp_path, login_site):
    def build(filename="local-password.example.json", *, username=DEMO_USERNAME,
              password=DEMO_PASSWORD, change=None, login_seconds=3):
        profile = json.loads((REPO / "configs/sites" / filename).read_text())
        route = "/login-two-step" if "two-step" in filename else "/login"
        profile["entry_url"] = f"http://127.0.0.1:{login_site.server_port}{route}"
        if change:
            change(profile)
        settings = json.loads((REPO / "configs/settings.json").read_text())
        settings["timeouts"].update(action_ms=500, navigation_ms=5000,
                                    login_seconds=login_seconds, verification_seconds=0.1)
        settings["artifacts_dir"] = "artifacts"
        (tmp_path / "configs").mkdir(exist_ok=True)
        (tmp_path / "configs/settings.json").write_text(json.dumps(settings))
        (tmp_path / "site.json").write_text(json.dumps(profile))
        configuration = load_configuration("site.json", project_root=tmp_path, environ={
            "LOCAL_LOGIN_USERNAME": username, "LOCAL_LOGIN_PASSWORD": password,
        })
        return configuration
    return build


@pytest.fixture
def email_site():
    server = EmailSite()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def email_configuration(tmp_path, email_site):
    def build(mode="email_otp", *, scenario="normal", split=False, timeout=2):
        filename = "email-otp.example.json" if mode == "email_otp" else "password-email-otp.example.json"
        profile = json.loads((REPO / "configs/sites" / filename).read_text())
        route = "email-login" if mode == "email_otp" else "password-email-login"
        profile["entry_url"] = f"http://127.0.0.1:{email_site.server_port}/{route}?scenario={scenario}&split={str(split).lower()}"
        profile["success"][1] = {"type": "api", "url": "/api/session", "expected_json": {"authenticated": True}}
        profile["verification"].update(sender="noreply@example.test", timeout_seconds=timeout,
            rejected=[{"type": "element", "target": {"by": "test_id", "value": "otp-error"}}],
            expired=[{"type": "element", "target": {"by": "test_id", "value": "otp-expired"}}])
        request = next(step for step in profile["steps"] if step["action"] == "request_otp")
        request["target"] = {"by": "role", "value": "button", "name": "Send code"}
        if split:
            next(step for step in profile["steps"] if step["action"] == "fill_otp")["targets"] = [
                {"by": "label", "value": f"Digit {i + 1}"} for i in range(6)]
        settings = json.loads((REPO / "configs/settings.json").read_text())
        settings["timeouts"].update(login_seconds=5, verification_seconds=timeout, action_ms=700)
        settings["gmail"]["poll_interval_seconds"] = 0.01
        (tmp_path / "settings.json").write_text(json.dumps(settings))
        (tmp_path / "site.json").write_text(json.dumps(profile))
        return load_configuration("site.json", "settings.json", project_root=tmp_path, environ={
            "EXAMPLE_EMAIL": DEMO_EMAIL, "EXAMPLE_USERNAME": "test-user", "EXAMPLE_PASSWORD": "test-password",
        })
    return build
