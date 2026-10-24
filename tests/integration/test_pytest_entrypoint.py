"""Run the public pytest plugin as a downstream project would use it."""

import json
from pathlib import Path
import zipfile

import pytest

from tests.fixtures.login_site import DEMO_PASSWORD, DEMO_USERNAME


@pytest.fixture
def downstream(request, configuration_factory, monkeypatch):
    from playwright.sync_api import sync_playwright

    # pytester changes HOME. Preserve Playwright's resolved cache path without
    # changing HOME back or assuming an OS-specific browser-cache location.
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    cache = next(parent.parent for parent in executable.parents if parent.name.startswith("chromium-"))
    pytester = request.getfixturevalue("pytester")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    configuration = configuration_factory()
    monkeypatch.setenv("LOCAL_LOGIN_USERNAME", DEMO_USERNAME)
    monkeypatch.setenv("LOCAL_LOGIN_PASSWORD", DEMO_PASSWORD)
    monkeypatch.setenv("AUTOWEB_AI_ENABLED", "false")
    monkeypatch.setenv("AUTOWEB_HEADLESS", "true")
    pytester.makeini("[pytest]\nasyncio_mode = strict\n")
    pytester.makeconftest('''
        from contextlib import asynccontextmanager
        from pathlib import Path
        import pytest
        import autoweb.pytest_plugin as plugin

        @pytest.fixture(autouse=True)
        def observe_cleanup(monkeypatch):
            original = plugin.open_session
            @asynccontextmanager
            async def observed(settings):
                async with original(settings) as session:
                    yield session
                assert session.page.is_closed()
                assert not session.browser.is_connected()
                with Path("closed.txt").open("a") as output:
                    output.write("closed\\n")
            monkeypatch.setattr(plugin, "open_session", observed)
    ''')
    return pytester, configuration, [
        "-p", "autoweb.pytest_plugin",
        "--autoweb-root", str(configuration.project_root),
        "--autoweb-site", "site.json", "-q",
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_downstream_reports_traces_and_failure_outcomes(downstream, fail):
    pytester, configuration, args = downstream
    pytester.makepyfile(
        'import pytest\n'
        '@pytest.mark.asyncio\n'
        'async def test_protected(autoweb_login):\n'
        '    await autoweb_login.session.page.get_by_role("button", name="Perform protected action").click()\n'
        '    await autoweb_login.session.page.get_by_test_id("protected-result").wait_for()\n'
        + ('    assert False, "post-login failure"\n' if fail else "")
    )
    result = pytester.runpytest_subprocess(*args, "--autoweb-trace=retain-on-failure", timeout=60)
    result.assert_outcomes(**({"failed": 1} if fail else {"passed": 1}))
    assert (pytester.path / "closed.txt").read_text() == "closed\n"
    directory = configuration.settings.artifacts_dir
    reports = list(directory.rglob("login.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["status"] == "succeeded"
    assert DEMO_PASSWORD not in reports[0].read_text()
    traces = list(directory.rglob("trace.zip"))
    screenshots = list(directory.rglob("*.png"))
    assert bool(traces) == bool(screenshots) == fail
    if fail:
        with zipfile.ZipFile(traces[0]) as trace:
            assert any(name.endswith(".trace") for name in trace.namelist())


def test_repeated_site_option_parameterizes_downstream_login(downstream):
    pytester, configuration, args = downstream
    pytester.makepyfile('''
        import pytest
        @pytest.mark.asyncio
        async def test_login(autoweb_login):
            assert autoweb_login.status == "succeeded"
    ''')
    pytester.runpytest_subprocess(*args, "--autoweb-site", "site.json", timeout=60).assert_outcomes(passed=2)
    assert len(list(configuration.settings.artifacts_dir.rglob("login.json"))) == 2
    assert (pytester.path / "closed.txt").read_text() == "closed\nclosed\n"
    assert not list(configuration.settings.artifacts_dir.rglob("trace.zip"))


def test_login_setup_failure_records_report_and_trace(downstream, monkeypatch):
    pytester, configuration, args = downstream
    monkeypatch.setenv("LOCAL_LOGIN_PASSWORD", "synthetic-wrong-password")
    pytester.makepyfile('''
        import pytest
        @pytest.mark.asyncio
        async def test_login(autoweb_login):
            assert False, "Failed login must never yield"
    ''')
    pytester.runpytest_subprocess(*args, "--autoweb-trace=retain-on-failure", timeout=60).assert_outcomes(errors=1)
    reports = list(configuration.settings.artifacts_dir.rglob("login.json"))
    assert json.loads(reports[0].read_text())["reason"] == "credentials_rejected"
    assert (pytester.path / "closed.txt").read_text() == "closed\n"
    assert list(configuration.settings.artifacts_dir.rglob("trace.zip"))
