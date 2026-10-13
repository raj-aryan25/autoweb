"""Explicit configuration loading and credential preflight, without global mutation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TypeVar

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr, ValidationError

from .errors import ConfigurationError
from .models import RuntimeSettings, SiteProfile

ModelType = TypeVar("ModelType", bound=BaseModel)


@dataclass(frozen=True)
class Configuration:
    project_root: Path
    settings: RuntimeSettings
    site: SiteProfile
    secrets: Mapping[str, SecretStr] = field(repr=False)


def resolve_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def get_project_root(project_root: str | Path | None = None) -> Path:
    root = project_root or os.environ.get("AUTOWEB_ROOT")
    return Path(root).expanduser().resolve() if root else Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError):
        raise ConfigurationError(f"Cannot read configuration file: {path}") from None
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}"
        ) from None
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration must be a JSON object: {path}")
    return data


def _validate(model: type[ModelType], data: dict, label: str) -> ModelType:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        descriptions = []
        for problem in error.errors(include_input=False, include_context=False, include_url=False):
            location = ".".join(str(part) for part in problem["loc"]) or "profile"
            # Only our own static validator messages are safe to include verbatim.
            message = problem["msg"] if problem["type"] == "value_error" else problem["type"]
            descriptions.append(f"{location}: {message}")
        raise ConfigurationError(f"Invalid {label}: " + "; ".join(descriptions)) from None


def load_environment(
    project_root: Path,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge the explicit root .env with process values; do not modify os.environ."""
    env_path = resolve_path(env_file or ".env", project_root)
    values: dict[str, str] = {}
    if env_file is not None or env_path.exists():
        try:
            with env_path.open(encoding="utf-8") as handle:
                parsed = dotenv_values(stream=handle, interpolate=False)
        except (OSError, UnicodeError):
            raise ConfigurationError(f"Cannot read environment file: {env_path}") from None
        values.update({key: value for key, value in parsed.items() if value is not None})
    values.update(os.environ if environ is None else environ)
    return values


# Each variable overrides one validated setting, not arbitrary JSON keys.
_OVERRIDES = {
    "AUTOWEB_BROWSER": ("browser", "engine", "str"),
    "AUTOWEB_HEADLESS": ("browser", "headless", "bool"),
    "AUTOWEB_SLOW_MO_MS": ("browser", "slow_mo_ms", "int"),
    "AUTOWEB_LAUNCH_TIMEOUT_MS": ("browser", "launch_timeout_ms", "int"),
    "AUTOWEB_ACTION_TIMEOUT_MS": ("timeouts", "action_ms", "int"),
    "AUTOWEB_NAVIGATION_TIMEOUT_MS": ("timeouts", "navigation_ms", "int"),
    "AUTOWEB_LOGIN_TIMEOUT_SECONDS": ("timeouts", "login_seconds", "float"),
    "AUTOWEB_VERIFICATION_TIMEOUT_SECONDS": ("timeouts", "verification_seconds", "float"),
    "AUTOWEB_GMAIL_CREDENTIALS_FILE": ("gmail", "credentials_file", "str"),
    "AUTOWEB_GMAIL_TOKEN_FILE": ("gmail", "token_file", "str"),
    "AUTOWEB_GMAIL_POLL_INTERVAL_SECONDS": ("gmail", "poll_interval_seconds", "float"),
    "AUTOWEB_GMAIL_REQUEST_TIMEOUT_SECONDS": ("gmail", "request_timeout_seconds", "float"),
    "AUTOWEB_AI_ENABLED": ("ai", "enabled", "bool"),
    "AUTOWEB_AI_MODEL": ("ai", "model", "str"),
}


def _override_value(name: str, value: str, kind: str):
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "bool":
            if value.lower() not in {"true", "false"}:
                raise ValueError
            return value.lower() == "true"
        return value
    except ValueError:
        raise ConfigurationError(f"Invalid environment override: {name}") from None


def _load_settings(root: Path, settings_file: str | Path, environment: Mapping[str, str]) -> RuntimeSettings:
    data = _read_json(resolve_path(settings_file, root))
    for variable, (section, key, kind) in _OVERRIDES.items():
        if variable not in environment:
            continue
        section_data = data.setdefault(section, {})
        if not isinstance(section_data, dict):
            raise ConfigurationError(f"Settings section must be an object: {section}")
        section_data[key] = _override_value(variable, environment[variable], kind)
    if "AUTOWEB_ARTIFACTS_DIR" in environment:
        data["artifacts_dir"] = environment["AUTOWEB_ARTIFACTS_DIR"]
    settings = _validate(RuntimeSettings, data, "runtime settings")
    settings.gmail.credentials_file = resolve_path(settings.gmail.credentials_file, root)
    settings.gmail.token_file = resolve_path(settings.gmail.token_file, root)
    settings.artifacts_dir = resolve_path(settings.artifacts_dir, root)
    return settings


def load_settings(
    settings_file: str | Path = "configs/settings.json",
    *,
    project_root: str | Path | None = None,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    root = get_project_root(project_root)
    environment = load_environment(root, env_file, environ)
    return _load_settings(root, settings_file, environment)


def load_site_profile(
    site_file: str | Path,
    *,
    project_root: str | Path | None = None,
) -> SiteProfile:
    root = get_project_root(project_root)
    return _validate(SiteProfile, _read_json(resolve_path(site_file, root)), "site profile")


def _resolve_secrets(
    site: SiteProfile, settings: RuntimeSettings, environment: Mapping[str, str]
) -> dict[str, SecretStr]:
    references = {f"credential:{name}": ref.env for name, ref in site.credentials.items()}
    if site.verification is not None:
        references["verification:recipient"] = site.verification.recipient.env
    if settings.ai.enabled:
        references["ai:api_key"] = settings.ai.api_key.env
    missing = sorted({name for name in references.values()
                      if not environment.get(name, "").strip()})
    if missing:
        raise ConfigurationError("Missing environment variables: " + ", ".join(missing))
    return {key: SecretStr(environment[name]) for key, name in references.items()}


def load_configuration(
    site_file: str | Path,
    settings_file: str | Path = "configs/settings.json",
    *,
    project_root: str | Path | None = None,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    schema_only: bool = False,
) -> Configuration:
    """Validate configuration before a runner starts clients or opens a browser.

    schema_only skips secret resolution for non-secret examples and CI schema checks.
    Runners must use the default preflight mode.
    """
    root = get_project_root(project_root)
    environment = load_environment(root, env_file, environ)
    settings = _load_settings(root, settings_file, environment)
    site = load_site_profile(site_file, project_root=root)
    if site.verification is not None:
        timeout = site.verification.timeout_seconds or settings.timeouts.verification_seconds
        if timeout > settings.timeouts.login_seconds:
            raise ConfigurationError("Site verification timeout exceeds the login timeout")
        if settings.gmail.poll_interval_seconds >= timeout:
            raise ConfigurationError("Gmail polling interval must be shorter than the verification timeout")
        if site.verification.acknowledge == "on_success" and (
            "https://www.googleapis.com/auth/gmail.modify" not in settings.gmail.scopes
        ):
            raise ConfigurationError("Acknowledging Gmail messages requires the gmail.modify scope")
    secrets = {} if schema_only else _resolve_secrets(site, settings, environment)
    return Configuration(root, settings, site, secrets)
