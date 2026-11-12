"""Trusted profile resolution and validated run copies. No runtime clients on import."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Thread

from autoweb.config import Configuration, ConfigurationError, load_site_profile
from autoweb.config.loader import configuration_from_models, load_environment, load_settings
from autoweb.config.models import RuntimeSettings, SiteProfile
from autoweb.login import validate_login

from .contracts import StartRun
from .profiles import Registration, resolve_registration
from .protocol import MAX_STEPS


@dataclass(frozen=True)
class PreparedRun:
    configuration: Configuration
    registration: Registration


class ProfileUnavailable(ConfigurationError):
    """Registered policy exists, but its application fixture is not ready yet."""


def prepare_run(request: StartRun, root: Path, run_directory: Path) -> PreparedRun:
    """Called in the executor, before a website, provider or browser is started."""
    registration = resolve_registration(request.profile_id)
    if registration.id in {"controlled-otp", "controlled-magic-link"}:
        raise ProfileUnavailable("Controlled verification fixtures are not available yet")
    controlled = registration.classification == "controlled"
    if controlled and request.overrides.ai_enabled:
        raise ConfigurationError("Controlled runs do not enable external AI")
    # Defaults for Studio are independent of CLI defaults and saved settings.
    overrides = request.overrides
    environment = load_environment(root)
    environment.update({
        "AUTOWEB_BROWSER": "chromium", "AUTOWEB_HEADLESS": str(overrides.headless).lower(),
        "AUTOWEB_SLOW_MO_MS": str(overrides.slow_mo_ms),
        "AUTOWEB_AI_ENABLED": str(overrides.ai_enabled).lower(),
        "AUTOWEB_LOGIN_TIMEOUT_SECONDS": str(overrides.login_timeout_seconds),
        "AUTOWEB_VERIFICATION_TIMEOUT_SECONDS": str(overrides.verification_timeout_seconds),
    })
    source = "configs/sites/local-password.example.json" if controlled else registration.source
    site = load_site_profile(source, project_root=root)
    if len(site.steps) > MAX_STEPS:
        raise ConfigurationError("Profile exceeds the worker action limit")
    data = site.model_dump()
    if controlled:
        from autoweb.testing.password_site import DEMO_PASSWORD, DEMO_USERNAME

        environment.update(LOCAL_LOGIN_USERNAME=DEMO_USERNAME, LOCAL_LOGIN_PASSWORD=DEMO_PASSWORD)
        # The actual ephemeral fixture address is bound after preflight succeeds.
        data["entry_url"] = "http://127.0.0.1:1/login"
    if data["verification"]:
        data["verification"]["timeout_seconds"] = float(overrides.verification_timeout_seconds)
    site = SiteProfile.model_validate(data)
    settings = load_settings(project_root=root, environ=environment)
    values = settings.model_dump()
    values["browser"]["launch_timeout_ms"] = min(settings.browser.launch_timeout_ms, 30000)
    values["timeouts"]["action_ms"] = min(settings.timeouts.action_ms, 10000)
    values["timeouts"]["navigation_ms"] = min(settings.timeouts.navigation_ms, 30000)
    values["gmail"]["request_timeout_seconds"] = min(settings.gmail.request_timeout_seconds, 30)
    values["ai"]["request_timeout_seconds"] = min(settings.ai.request_timeout_seconds, 10)
    values["artifacts_dir"] = run_directory
    settings = RuntimeSettings.model_validate(values)
    configuration = configuration_from_models(site, settings, environment, project_root=root)
    validate_login(configuration)
    if registration.provider == "gmail" and not configuration.settings.gmail.token_file.is_file():
        raise ConfigurationError("Existing Gmail authorization is required")
    return PreparedRun(configuration, registration)


@contextmanager
def controlled_site(prepared: PreparedRun):
    """Own the loopback fixture; production never imports the tests package."""
    if prepared.registration.classification != "controlled":
        yield prepared.configuration
        return
    from autoweb.testing.password_site import make_server

    server = make_server()
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        thread.start()
        started = True
        source = prepared.configuration
        data = source.site.model_dump()
        data["entry_url"] = f"http://127.0.0.1:{server.server_port}/login"
        site = SiteProfile.model_validate(data)
        # Credential references and settings are unchanged by the ephemeral bind.
        yield Configuration(source.project_root, source.settings, site, source.secrets)
    finally:
        if started:
            server.shutdown()
        server.server_close()
        if started:
            thread.join(timeout=2)
