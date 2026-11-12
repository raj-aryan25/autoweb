"""Transport invariants and import safety, without starting the future service."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoweb.config import load_site_profile
from autoweb_api.contracts import ContractScenario, RunEvent, StartRun
from autoweb_api.profiles import REGISTRY, display_steps, resolve_registration

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = json.loads((ROOT / "tests/fixtures/studio/contracts.json").read_text())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item["name"])
def test_shared_scenario_roundtrip(scenario):
    parsed = ContractScenario.model_validate(scenario)
    assert ContractScenario.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("mutation", ["duplicate", "gap", "wrong_run", "decreasing_time", "cursor", "terminal"])
def test_invalid_replays_rejected(mutation):
    scenario = copy.deepcopy(SCENARIOS[0])
    replay = scenario["transport"]
    if mutation == "duplicate": replay["events"][1]["sequence"] = 1
    if mutation == "gap": replay["events"][1]["sequence"] = 3
    if mutation == "wrong_run": replay["events"][1]["run_id"] = "other"
    if mutation == "decreasing_time": replay["events"][1]["elapsed_seconds"] = 0
    if mutation == "cursor": replay["snapshot"]["cursor"] += 1
    if mutation == "terminal": replay["snapshot"]["outcome"]["state"] = "failed"
    with pytest.raises(ValidationError):
        ContractScenario.model_validate(scenario)


@pytest.mark.parametrize("key", ["password", "otp", "cookie", "token", "url", "path", "message", "exception"])
def test_event_payload_excludes_secret_channels(key):
    event = copy.deepcopy(SCENARIOS[0]["transport"]["events"][0])
    event["payload"][key] = "synthetic-secret-001234"
    with pytest.raises(ValidationError):
        RunEvent.model_validate(event)


@pytest.mark.parametrize("update", [
    {"sequence": 0}, {"sequence": True}, {"elapsed_seconds": float("nan")},
    {"timestamp": "2026-11-06T00:00:00"}, {"schema_version": 2},
])
def test_event_envelope_validation(update):
    event = {**SCENARIOS[0]["transport"]["events"][0], **update}
    with pytest.raises(ValidationError):
        RunEvent.model_validate(event)


def test_registry_rejects_arbitrary_inputs_and_does_not_claim_readiness():
    assert len({item.id for item in REGISTRY}) == 5
    assert resolve_registration("expandtesting-otp").provider == "gmail"
    for value in ["../configs/settings.json", "https://example.com", "better-auth", "stytch"]:
        with pytest.raises(ValueError):
            resolve_registration(value)
    with pytest.raises(ValidationError):
        StartRun.model_validate({"request_id": "request-1", "profile_id": "controlled-otp", "password": "secret"})
    with pytest.raises(ValidationError):
        StartRun.model_validate({"request_id": "request-1", "profile_id": "controlled-otp", "overrides": {"command": "arbitrary"}})


def test_display_ids_and_optional_skip_metadata_follow_actual_profile():
    profile = load_site_profile("configs/sites/local-password.example.json", project_root=ROOT)
    before = display_steps(profile)
    modified = profile.model_copy(update={"steps": [profile.steps[-1], *profile.steps]})
    after = display_steps(modified)
    assert before[3].optional is True
    assert before[3].id == after[4].id
    assert len({item.id for item in after}) == len(after)
    assert all(not item.phases for item in before)
    assert "acknowledgment" not in {item.id for item in before}


@pytest.mark.parametrize("filename,consumer", [("email-otp.example.json", "fill_otp"), ("email-magic-link.example.json", "follow_magic_link")])
def test_email_wait_is_nested_under_consumer(filename, consumer):
    profile = load_site_profile(f"configs/sites/{filename}", project_root=ROOT)
    steps = display_steps(profile)
    assert [step.action for step in steps if step.phases] == [consumer]
    assert not any(step.id == "verification_wait" for step in steps)


def test_generated_types_and_fixtures_are_current():
    subprocess.run([sys.executable, str(ROOT / "scripts/generate_studio_contracts.py"), "--check"], check=True, cwd=ROOT)


def test_imports_do_not_load_external_clients_or_read_configuration():
    script = '''
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'playwright', 'google', 'google_auth_oauthlib', 'fastapi', 'uvicorn'} or fullname == 'autoweb.ai.client':
            raise AssertionError('Client imported: ' + fullname)
sys.meta_path.insert(0, Guard())
import autoweb
import autoweb_api
import autoweb_api.contracts
import autoweb_api.profiles
import autoweb_api.worker
import autoweb_api.executor
import autoweb_api.guardian
import autoweb_api.configuration
from unittest.mock import patch
with patch('pathlib.Path.open', side_effect=AssertionError('Configuration read')):
    import importlib
    importlib.reload(autoweb_api.profiles)
'''
    subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)


def test_public_package_imports_do_not_initialize_resources():
    script = '''
import sys
def guard(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Import attempted resource startup: ' + event)
    if event == 'open' and isinstance(args[0], str):
        name = args[0].lower()
        if name.endswith(('.env', 'settings.json', 'token.json', 'credentials.json')):
            raise AssertionError('Import read runtime configuration')
sys.addaudithook(guard)
import autoweb
import autoweb.login
import autoweb.browser
import autoweb.gmail
import autoweb.ai
import autoweb_api.contracts
import autoweb_api.profiles
import autoweb_api.worker
import autoweb_api.executor
import autoweb_api.guardian
import autoweb_api.configuration
'''
    subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)
