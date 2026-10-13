"""Real headless Chromium and loopback-only pages for browser integration tests."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio

from autoweb.browser import open_session
from autoweb.config.models import RuntimeSettings, TimeoutSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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
