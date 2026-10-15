"""Phase I contracts: validation, secret handling, paths, and import isolation."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autoweb.config import (
    ConfigurationError, load_configuration, load_settings, load_site_profile,
)
from autoweb.config.cli import main

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    settings = json.loads((REPO / "configs/settings.json").read_text())
    profile = json.loads((REPO / "configs/sites/password.example.json").read_text())
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/settings.json").write_text(json.dumps(settings))
    (tmp_path / "site.json").write_text(json.dumps(profile))
    return tmp_path, settings, profile


def save_profile(project, profile):
    root, _, _ = project
    (root / "site.json").write_text(json.dumps(profile))
    return root


@pytest.mark.parametrize("filename,mode", [
    ("password.example.json", "password"),
    ("email-otp.example.json", "email_otp"),
    ("password-email-otp.example.json", "password_email_otp"),
])
def test_examples_preserve_step_parameters(filename, mode):
    configuration = load_configuration(
        f"configs/sites/{filename}", project_root=REPO, environ={}, schema_only=True,
    )
    profile = configuration.site
    assert profile.login_mode == mode
    if mode == "password":
        assert profile.steps[1].credential == "username"
        assert profile.steps[2].credential == "password"
        assert profile.steps[1].target.value != profile.steps[2].target.value
    else:
        assert re.search(profile.verification.code_regex, "Your code is 001234")[0] == "001234"
    assert configuration.secrets == {}


@pytest.mark.parametrize("change", [
    lambda p: p["steps"][1].update(action="unknown_action"),
    lambda p: p["steps"][1].pop("target"),
    lambda p: p["steps"][1].update(credential="undeclared"),
    lambda p: p["credentials"].pop("password"),
    lambda p: p["steps"].pop(),
    lambda p: p["steps"][2].update(optional=True),
    lambda p: p.update(success=[{"type": "url", "pattern": "https://example.com/account*"}]),
    lambda p: p.update(success=[{"type": "element", "target": {"by": "css", "value": "#login"}, "state": "hidden"}]),
    lambda p: p["credentials"]["password"].update(env="invalid-variable"),
    lambda p: p["steps"][1]["target"].update(name="invalid for label"),
    lambda p: p.update(unexpected_field=True),
    lambda p: p.update(entry_url="https://user:secret@example.com/"),
    lambda p: p.update(entry_url="example.com/login"),
    lambda p: p.update(entry_url="https://example.com:bad/login"),
    lambda p: p.update(entry_url="https://example.com /login"),
])
def test_invalid_profile_is_rejected(project, change):
    _, _, profile = project
    change(profile)
    with pytest.raises(ConfigurationError):
        load_site_profile("site.json", project_root=save_profile(project, profile))


@pytest.mark.parametrize("change", [
    lambda p: p.pop("verification"),
    lambda p: p["verification"].update(code_regex="["),
    lambda p: p["verification"].update(code_length=0),
    lambda p: p["verification"].update(code_length=129),
    lambda p: p["verification"].update(code_characters="["),
    lambda p: p["steps"][1].update(optional=True),
    lambda p: p["steps"].reverse(),
    lambda p: p["steps"][2].update(targets=[{"by": "css", "value": "#a"}, {"by": "css", "value": "#b"}]),
])
def test_invalid_verification_flow_is_rejected(project, change):
    profile = json.loads((REPO / "configs/sites/email-otp.example.json").read_text())
    change(profile)
    with pytest.raises(ConfigurationError):
        load_site_profile("site.json", project_root=save_profile(project, profile))


def test_preflight_requires_nonempty_credentials(project):
    root, _, _ = project
    with pytest.raises(ConfigurationError, match="EXAMPLE_PASSWORD"):
        load_configuration("site.json", project_root=root,
                           environ={"EXAMPLE_USERNAME": "someone", "EXAMPLE_PASSWORD": "   "})


def test_secrets_are_resolved_and_not_represented(project):
    root, _, _ = project
    config = load_configuration("site.json", project_root=root, environ={
        "EXAMPLE_USERNAME": "test-user", "EXAMPLE_PASSWORD": "private-password",
    })
    assert config.secrets["credential:password"].get_secret_value() == "private-password"
    assert "private-password" not in repr(config)
    assert "private-password" not in repr(config.secrets)


def test_environment_precedence_and_no_global_mutation(project, monkeypatch):
    root, _, _ = project
    monkeypatch.delenv("EXAMPLE_USERNAME", raising=False)
    (root / ".env").write_text("EXAMPLE_USERNAME=file-user\nEXAMPLE_PASSWORD=file-pass\nAUTOWEB_HEADLESS=false\n")
    config = load_configuration("site.json", project_root=root,
                               environ={"EXAMPLE_USERNAME": "process-user"})
    assert config.secrets["credential:username"].get_secret_value() == "process-user"
    assert config.secrets["credential:password"].get_secret_value() == "file-pass"
    assert config.settings.browser.headless is False
    assert "EXAMPLE_USERNAME" not in os.environ


def test_paths_are_relative_to_project_not_working_directory(project, monkeypatch, tmp_path):
    root, _, _ = project
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = load_configuration("site.json", project_root=root, environ={}, schema_only=True)
    assert config.settings.gmail.credentials_file == root / "secrets/gmail/credentials.json"
    assert config.settings.gmail.token_file == root / "secrets/gmail/token.json"
    assert config.settings.artifacts_dir == root / "artifacts"
    assert not config.settings.artifacts_dir.exists()


def test_default_root_works_outside_repository(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOWEB_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    settings = load_settings(environ={})
    assert settings.gmail.credentials_file == REPO / "secrets/gmail/credentials.json"


def test_explicit_root_environment_override(project, monkeypatch):
    root, _, _ = project
    monkeypatch.setenv("AUTOWEB_ROOT", str(root))
    assert load_settings(environ={}).artifacts_dir == root / "artifacts"


@pytest.mark.parametrize("environ", [
    {"AUTOWEB_HEADLESS": "maybe"},
    {"AUTOWEB_BROWSER": "unsupported"},
    {"AUTOWEB_ACTION_TIMEOUT_MS": "0"},
    {"AUTOWEB_SLOW_MO_MS": "-1"},
    {"AUTOWEB_LAUNCH_TIMEOUT_MS": "0"},
    {"AUTOWEB_LOGIN_TIMEOUT_SECONDS": "nan"},
    {"AUTOWEB_AI_ENABLED": "true"},
    {"AUTOWEB_VERIFICATION_TIMEOUT_SECONDS": "121"},
])
def test_invalid_environment_overrides_are_rejected(project, environ):
    root, _, _ = project
    with pytest.raises(ConfigurationError):
        load_settings(project_root=root, environ=environ)


def test_enabled_ai_requires_key_at_preflight(project):
    root, _, _ = project
    with pytest.raises(ConfigurationError, match="GOOGLE_API_KEY"):
        load_configuration("site.json", project_root=root, environ={
            "EXAMPLE_USERNAME": "user", "EXAMPLE_PASSWORD": "password",
            "AUTOWEB_AI_ENABLED": "true", "AUTOWEB_AI_MODEL": "configured-model",
        })


def test_otp_recipient_is_resolved_separately(project):
    profile = json.loads((REPO / "configs/sites/password-email-otp.example.json").read_text())
    root = save_profile(project, profile)
    with pytest.raises(ConfigurationError, match="EXAMPLE_EMAIL"):
        load_configuration("site.json", project_root=root, environ={
            "EXAMPLE_USERNAME": "user", "EXAMPLE_PASSWORD": "password",
        })


@pytest.mark.parametrize("setting,value", [
    ("poll_interval_seconds", 60),
    ("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
])
def test_verification_runtime_policy_is_validated(project, setting, value):
    root, settings, _ = project
    profile = json.loads((REPO / "configs/sites/email-otp.example.json").read_text())
    save_profile(project, profile)
    settings["gmail"][setting] = value
    (root / "configs/settings.json").write_text(json.dumps(settings))
    with pytest.raises(ConfigurationError):
        load_configuration("site.json", project_root=root, environ={}, schema_only=True)


@pytest.mark.parametrize("content", ["{bad", "[]", "null"])
def test_invalid_json_is_reported_without_dumping_contents(project, content):
    root, _, _ = project
    (root / "site.json").write_text(content)
    with pytest.raises(ConfigurationError):
        load_site_profile("site.json", project_root=root)


def test_missing_explicit_env_file_fails(project):
    root, _, _ = project
    with pytest.raises(ConfigurationError, match="environment file"):
        load_settings(project_root=root, env_file="missing.env", environ={})


def test_unknown_settings_fields_are_rejected(project):
    root, settings, _ = project
    settings["browser"]["typo"] = True
    (root / "configs/settings.json").write_text(json.dumps(settings))
    with pytest.raises(ConfigurationError, match="extra_forbidden"):
        load_settings(project_root=root, environ={})


def test_cli_schema_check(project, capsys):
    root, _, _ = project
    assert main(["--site", "site.json", "--project-root", str(root), "--schema-only"]) == 0
    assert "Valid schema" in capsys.readouterr().out


def test_cli_preflight_fails_without_printing_secret(project, monkeypatch, capsys):
    root, _, _ = project
    monkeypatch.delenv("EXAMPLE_USERNAME", raising=False)
    monkeypatch.setenv("EXAMPLE_PASSWORD", "private-password")
    with pytest.raises(SystemExit) as result:
        main(["--site", "site.json", "--project-root", str(root)])
    assert result.value.code == 2
    captured = capsys.readouterr()
    assert "EXAMPLE_USERNAME" in captured.err
    assert "private-password" not in captured.err + captured.out


def test_package_imports_do_not_initialize_provider_clients(tmp_path):
    # A fresh interpreter verifies optional provider imports are not even attempted.
    script = '''
import importlib, sys
modules = ["autoweb", "autoweb.config", "autoweb.config.models", "autoweb.config.loader",
           "autoweb.browser", "autoweb.gmail", "autoweb.login", "autoweb.ai", "autoweb.reporting"]
for module in modules:
    importlib.import_module(module)
for forbidden in ("playwright", "google.generativeai", "googleapiclient", "google_auth_oauthlib"):
    assert not any(name == forbidden or name.startswith(forbidden + ".") for name in sys.modules), forbidden
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
