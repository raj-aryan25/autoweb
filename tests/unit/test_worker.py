"""Run copies, safe protocol and process identity rules without opening a browser."""

import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from autoweb.config import ConfigurationError, load_settings, load_site_profile
from autoweb.config.loader import configuration_from_models
from autoweb.login import LoginError, LoginResult
from autoweb_api.configuration import ProfileUnavailable, prepare_run
from autoweb_api.contracts import RunOverrides, StartRun
from autoweb_api.executor import diagnostic
from autoweb_api.guardian import LifecycleLimits, Lines, supervise
from autoweb_api.process_tree import ProcessIdentity
from autoweb_api.protocol import MAX_LINE, Launch, decode
from autoweb_api.worker import WorkerCoordinator

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def configured_root(tmp_path):
    (tmp_path / "configs/sites").mkdir(parents=True)
    for relative in ["configs/settings.json", "configs/sites/local-password.example.json"]:
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    return tmp_path


def test_worker_defaults_and_copies_preserve_environment_and_files(configured_root):
    before_environment = dict(os.environ)
    path = configured_root / "configs/settings.json"
    before_file = path.read_bytes()
    request = StartRun(request_id="copy-check", profile_id="controlled-password")
    prepared = prepare_run(request, configured_root, configured_root / "private-run")
    settings = prepared.configuration.settings
    assert settings.browser.engine == "chromium" and settings.browser.headless is False
    assert settings.timeouts.login_seconds == 120
    assert not settings.ai.enabled
    assert settings.artifacts_dir == configured_root / "private-run"
    assert dict(os.environ) == before_environment and path.read_bytes() == before_file
    assert "test-password" not in repr(prepared.configuration)


def test_per_run_overrides_validate_nested_copies(configured_root):
    overrides = RunOverrides(headless=True, slow_mo_ms=75, login_timeout_seconds=20, verification_timeout_seconds=10)
    prepared = prepare_run(StartRun(request_id="override-check", profile_id="controlled-password", overrides=overrides),
                           configured_root, configured_root / "private-run")
    assert prepared.configuration.settings.browser.slow_mo_ms == 75
    assert prepared.configuration.settings.browser.headless is True
    assert prepared.configuration.settings.timeouts.login_seconds == 20
    original = load_settings(project_root=configured_root, environ={})
    assert original.browser.slow_mo_ms == 0 and original.timeouts.login_seconds == 120


def test_model_configuration_resolves_a_copy(configured_root):
    site = load_site_profile("configs/sites/local-password.example.json", project_root=configured_root)
    settings = load_settings(project_root=configured_root, environ={})
    result = configuration_from_models(site, settings, {
        "LOCAL_LOGIN_USERNAME": "synthetic", "LOCAL_LOGIN_PASSWORD": "synthetic-private",
    }, project_root=configured_root)
    result.settings.browser.slow_mo_ms = 55
    result.site.steps[0].optional = False
    assert settings.browser.slow_mo_ms == 0 and site.steps[0].optional is True


@pytest.mark.parametrize("profile", ["../site.json", "https://example.test", "stytch", "better-auth"])
def test_worker_does_not_resolve_unregistered_inputs(configured_root, profile):
    with pytest.raises(ValueError, match="Unknown registered profile ID"):
        prepare_run(StartRun(request_id="registry-check", profile_id=profile)
                    if "/" not in profile else Mock(profile_id=profile), configured_root, configured_root / "private-run")


@pytest.mark.parametrize("profile", ["controlled-otp", "controlled-magic-link"])
def test_deferred_fixture_is_unavailable_before_clients(configured_root, profile):
    with pytest.raises(ProfileUnavailable):
        prepare_run(StartRun(request_id="fixture-check", profile_id=profile), configured_root,
                    configured_root / "private-run")


def test_controlled_runs_do_not_enable_external_ai(configured_root):
    with pytest.raises(ConfigurationError):
        prepare_run(StartRun(request_id="ai-check", profile_id="controlled-password",
                             overrides=RunOverrides(ai_enabled=True)), configured_root, configured_root / "private-run")


@pytest.mark.asyncio
async def test_unvalidated_request_copy_cannot_start_worker(configured_root):
    owner = WorkerCoordinator(configured_root)
    overrides = RunOverrides().model_copy(update={"slow_mo_ms": 999999})
    with pytest.raises(ValidationError):
        await owner.start(StartRun(request_id="invalid-copy", profile_id="controlled-password", overrides=overrides))
    assert owner.active is None


@pytest.mark.asyncio
async def test_unsupported_containment_fails_before_spawn(configured_root, monkeypatch):
    monkeypatch.setattr("autoweb_api.worker.supported", lambda: False)
    owner = WorkerCoordinator(configured_root)
    with pytest.raises(RuntimeError, match="containment"):
        await owner.start(StartRun(request_id="platform-check", profile_id="controlled-password"))
    assert owner.active is None


def test_pidfd_signal_checks_start_identity(monkeypatch):
    from autoweb_api import process_tree

    signal = Mock()
    close = Mock()
    monkeypatch.setattr(process_tree, "open_pidfd", lambda pid: 42)
    monkeypatch.setattr(process_tree, "read_identity", lambda pid: ProcessIdentity(pid, 1, 999, "R"))
    monkeypatch.setattr(process_tree, "send_pidfd", signal)
    monkeypatch.setattr(process_tree.os, "close", close)
    ProcessIdentity(123, 1, 100, "R").send(15)
    signal.assert_not_called()
    close.assert_called_once_with(42)


def test_guardian_cannot_spawn_before_verified_subreaper(monkeypatch):
    def unavailable():
        raise RuntimeError("containment_unavailable")
    spawn = Mock()
    monkeypatch.setattr("autoweb_api.guardian.become_subreaper", unavailable)
    monkeypatch.setattr("autoweb_api.guardian.subprocess.Popen", spawn)
    with pytest.raises(RuntimeError, match="containment_unavailable"):
        supervise([], None, None, 0)
    spawn.assert_not_called()


@pytest.mark.parametrize("value", [b'x' * MAX_LINE, b'{"type":"unknown","secret":"private"}\n', b'{}'])
def test_protocol_rejects_unbounded_or_unknown_frames(value):
    with pytest.raises(ValueError):
        if value.startswith(b"x"):
            list(Lines().feed(value))
        else:
            decode(value)


def test_exception_diagnostics_do_not_transport_unknown_reason_text():
    result = LoginResult("synthetic", "failed", 0, reason="private-code-001234")
    assert diagnostic(LoginError(result)) == "login_failed"
    assert diagnostic(RuntimeError("private-url-and-token")) == "worker_failed"


def test_launch_protocol_does_not_allow_commands_or_credential_values():
    value = {"type": "start", "run_id": "safe-run", "request": {
        "request_id": "safe-request", "profile_id": "controlled-password", "password": "private",
    }}
    with pytest.raises(ValidationError):
        Launch.model_validate(value)
    value["request"].pop("password")
    value["command"] = "arbitrary"
    with pytest.raises(ValidationError):
        Launch.model_validate(value)


@pytest.mark.parametrize("values", [(0, 5, 240), (61, 5, 240), (5, 31, 240), (5, 5, 901), (True, 5, 240)])
def test_lifecycle_budgets_cannot_be_unbounded(values):
    with pytest.raises(ValueError, match="invalid_lifecycle_limits"):
        LifecycleLimits(*values)
