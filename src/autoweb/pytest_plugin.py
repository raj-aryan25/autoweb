"""Opt-in pytest interface: load with ``-p autoweb.pytest_plugin``."""

from contextlib import AsyncExitStack
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio

from autoweb.browser import open_session
from autoweb.config import load_configuration
from autoweb.config.loader import resolve_path
from autoweb.login import LoginError, run_login, validate_login
from autoweb.login.diagnostics import capture_failure
from autoweb.reporting.json_report import write_login_report
from autoweb.testing import FakeVerificationProvider


def pytest_addoption(parser):
    group = parser.getgroup("autoweb")
    group.addoption("--autoweb-site", action="append", default=[], metavar="JSON",
                    help="Site profile; repeat to parameterize autoweb_configuration")
    group.addoption("--autoweb-root", default=None)
    group.addoption("--autoweb-settings", default="configs/settings.json")
    group.addoption("--autoweb-env-file", default=None)
    group.addoption("--autoweb-live", action="store_true", default=False,
                    help="Enable live tests and external sites (never starts OAuth consent)")
    group.addoption("--autoweb-provider", choices=["fake", "gmail"], default="fake")
    group.addoption("--autoweb-trace", choices=["off", "retain-on-failure", "on"], default="off",
                    help="Opt in to SENSITIVE raw traces, including credentials/cookies/network data")


def pytest_configure(config):
    for marker, description in (
        ("unit", "Fast isolated tests"),
        ("integration", "Controlled browser tests"),
        ("e2e", "Reusable login fixture and protected actions"),
        ("live", "External website/mailbox; requires --autoweb-live"),
    ):
        config.addinivalue_line("markers", f"{marker}: {description}")
    if config.getoption("autoweb_provider") == "gmail" and not config.getoption("autoweb_live"):
        raise pytest.UsageError("--autoweb-provider=gmail requires --autoweb-live")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("autoweb_live"):
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(pytest.mark.skip(reason="Live tests require --autoweb-live"))


def pytest_generate_tests(metafunc):
    if "autoweb_configuration" not in metafunc.fixturenames:
        return
    # Respect explicit indirect parametrization supplied by a test module.
    if any("autoweb_configuration" in (
        [name.strip() for name in mark.args[0].split(",")]
        if isinstance(mark.args[0], str) else mark.args[0]
    ) for mark in metafunc.definition.iter_markers("parametrize")):
        return
    sites = metafunc.config.getoption("autoweb_site")
    if sites:
        metafunc.parametrize("autoweb_configuration", sites, indirect=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"_autoweb_{report.when}", report)


def _local_url(url):
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture
def autoweb_configuration(request):
    """Override this fixture or indirectly parameterize it with profile paths."""
    site = getattr(request, "param", None)
    if site is None:
        pytest.skip("Select --autoweb-site or override autoweb_configuration")
    return load_configuration(
        site, request.config.getoption("autoweb_settings"),
        project_root=request.config.getoption("autoweb_root"),
        env_file=request.config.getoption("autoweb_env_file"),
    )


@pytest.fixture
def autoweb_settings(autoweb_configuration):
    return autoweb_configuration.settings


@pytest.fixture
def autoweb_site_profile(autoweb_configuration):
    return autoweb_configuration.site


@pytest.fixture
def autoweb_fake_provider():
    return FakeVerificationProvider()


@pytest.fixture
def autoweb_ai_client(autoweb_configuration):
    if not autoweb_configuration.settings.ai.enabled:
        return None
    from autoweb.ai.client import client_from_configuration

    return client_from_configuration(autoweb_configuration)


@pytest_asyncio.fixture
async def autoweb_verification_provider(request, autoweb_configuration, autoweb_fake_provider, autoweb_ai_client):
    validate_login(autoweb_configuration)
    if not autoweb_configuration.site.verification:
        yield None
    elif request.config.getoption("autoweb_provider") == "fake":
        yield autoweb_fake_provider
    else:
        from autoweb.gmail.provider import open_gmail_provider

        async with open_gmail_provider(autoweb_configuration, ai_client=autoweb_ai_client) as provider:
            yield provider


@pytest_asyncio.fixture
async def autoweb_session(request, autoweb_configuration, autoweb_verification_provider):
    """One fixture owns the browser/context, including login and post-login failures."""
    configuration = autoweb_configuration
    live = request.config.getoption("autoweb_live")
    if not live and not _local_url(configuration.site.entry_url):
        pytest.fail("External profiles require --autoweb-live", pytrace=False)
    if not live and configuration.settings.ai.enabled:
        pytest.fail("AI access requires --autoweb-live; disable AI for deterministic tests", pytrace=False)
    directory = resolve_path(configuration.settings.artifacts_dir, configuration.project_root) / "pytest" / uuid4().hex
    request.node._autoweb_directory = directory
    mode = request.config.getoption("autoweb_trace")
    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(open_session(configuration.settings))
        if not live:
            # Prevent redirects/subresources from silently turning a local test into a live one.
            async def local_only(route):
                if _local_url(route.request.url):
                    await route.continue_()
                else:
                    await route.abort()
            await session.context.route("**/*", local_only)
        if mode != "off":
            await session.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            yield session
        finally:
            failed = any(getattr(request.node, f"_autoweb_{phase}", None)
                         and getattr(request.node, f"_autoweb_{phase}").failed
                         for phase in ("setup", "call"))
            if failed:
                paths = await capture_failure(session.page, configuration.site, directory,
                                              timeout_ms=1000)
                for path in paths:
                    request.node.add_report_section("teardown", "autoweb screenshot", str(path))
            if mode != "off":
                from playwright.async_api import Error

                try:
                    if mode == "on" or failed:
                        directory.mkdir(parents=True, exist_ok=True)
                        trace = directory / "trace.zip"
                        await session.context.tracing.stop(path=str(trace))
                        request.node.add_report_section("teardown", "autoweb sensitive trace", str(trace))
                    else:
                        await session.context.tracing.stop()
                except (Error, OSError):
                    request.node.add_report_section("teardown", "autoweb trace", "Trace unavailable")


@pytest_asyncio.fixture
async def autoweb_login(request, autoweb_session, autoweb_configuration, autoweb_verification_provider, autoweb_ai_client):
    """Return a successful LoginResult with a session available until test teardown."""
    try:
        result = await run_login(autoweb_session, autoweb_configuration, autoweb_verification_provider,
                                 ai_client=autoweb_ai_client)
    except LoginError as error:
        _report(request, error.result)
        raise
    _report(request, result)
    return result


def _report(request, result):
    try:
        path = write_login_report(result, request.node._autoweb_directory / "login.json")
    except OSError:
        request.node.add_report_section("setup", "autoweb report", "Report unavailable")
    else:
        request.node.add_report_section("setup", "autoweb report", str(path))
