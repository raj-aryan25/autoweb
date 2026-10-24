"""Live selection cannot accidentally start clients or a browser."""

from pathlib import Path

import pytest

from autoweb.pytest_plugin import _local_url

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8765/login", True), ("http://[::1]/login", True),
    ("http://localhost/login", True), ("https://example.com", False),
    ("http://localhost.example.com", False),
])
def test_loopback_detection(url, expected):
    assert _local_url(url) is expected


def test_live_tests_skip_before_fixture_setup(pytester):
    pytester.makeconftest('pytest_plugins = ["autoweb.pytest_plugin"]')
    pytester.makepyfile('''
        import pytest
        @pytest.fixture
        def forbidden():
            raise AssertionError("Must not initialize")
        @pytest.mark.live
        def test_live(forbidden):
            pass
    ''')
    pytester.runpytest_subprocess("-q").assert_outcomes(skipped=1)
    pytester.runpytest_subprocess("-q", "--autoweb-live").assert_outcomes(errors=1)


def test_gmail_selection_requires_live_flag(pytester):
    pytester.makeconftest('pytest_plugins = ["autoweb.pytest_plugin"]')
    result = pytester.runpytest_subprocess("--autoweb-provider=gmail", "-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*requires --autoweb-live*"])


def test_external_profile_is_rejected_before_browser_startup(pytester):
    pytester.makeconftest('''
        import pytest
        from autoweb.config import load_configuration
        pytest_plugins = ["autoweb.pytest_plugin"]
        @pytest.fixture
        def autoweb_configuration(monkeypatch):
            def forbidden(*args, **kwargs):
                raise AssertionError("Browser must not start")
            monkeypatch.setattr("autoweb.pytest_plugin.open_session", forbidden)
            return load_configuration("configs/sites/password.example.json",
                project_root=REPO,
                environ={"EXAMPLE_USERNAME": "test", "EXAMPLE_PASSWORD": "synthetic"})
    '''.replace("project_root=REPO", f"project_root={str(REPO)!r}"))
    pytester.makepyfile('''
        import pytest
        @pytest.mark.asyncio
        async def test_external(autoweb_login):
            pass
    ''')
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*External profiles require --autoweb-live*"])
