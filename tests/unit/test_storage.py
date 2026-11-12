"""Durability, snapshot reduction, idempotency and replay without launching clients."""

import json

import pytest

from autoweb.config.models import BrowserSettings, TimeoutSettings
from autoweb_api.contracts import DisplayStep, RunEvent, RunOverrides, StartRun
from autoweb_api.protocol import EffectiveSettings, Manifest
from autoweb_api.storage import Conflict, RunStore


def request(request_id="request-1", **values):
    return StartRun(request_id=request_id, profile_id="controlled-password", overrides=RunOverrides(**values))


@pytest.fixture
def store(tmp_path):
    result = RunStore(tmp_path / "history.sqlite3", retention=3)
    result.open()
    try:
        yield result
    finally:
        result.close()


def manifest():
    return Manifest(type="manifest", profile_id="controlled-password", steps=[
        DisplayStep(id="preflight", kind="phase"),
        DisplayStep(id="action:one", kind="action", action="click", optional=True),
        DisplayStep(id="action:two", kind="action", action="submit"),
        DisplayStep(id="outcome", kind="phase"),
    ], effective=EffectiveSettings(browser=BrowserSettings(), timeouts=TimeoutSettings(), ai_enabled=False))


def event(sequence, payload, step_id="outcome"):
    return RunEvent(schema_version=1, run_id="run-1", sequence=sequence, timestamp="2026-11-06T12:00:00Z",
                    elapsed_seconds=float(sequence), step_id=step_id, phase_id="action", payload=payload)


def test_idempotency_survives_close_and_reopen(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = RunStore(path)
    store.open()
    original, created = store.reserve("run-1", request())
    assert created
    store.failed_before_spawn("run-1")
    store.close()
    store.open()
    try:
        repeated, created = store.reserve("must-not-create-this", request())
        assert not created and repeated.snapshot.run_id == original.snapshot.run_id
        assert repeated.snapshot.state == "failed"
        assert store.page().total == 1
        with pytest.raises(Conflict, match="request_id_conflict"):
            store.reserve("run-2", request(headless=True))
    finally:
        store.close()


def test_active_slot_is_transactional_and_unique(store):
    store.reserve("run-1", request())
    with pytest.raises(Conflict, match="run_active"):
        store.reserve("run-2", request("request-2"))
    assert store.page().total == 1
    store.failed_before_spawn("run-1")
    store.reserve("run-2", request("request-2"))
    assert store.active_id() == "run-2"


def test_replay_resync_preserves_full_snapshot_and_skip(store):
    store.reserve("run-1", request())
    store.manifest("run-1", manifest())
    values = [
        event(1, {"type": "run_state_changed", "state": "created"}),
        event(2, {"type": "step_started", "action": "click"}, "action:one"),
        event(3, {"type": "step_completed", "action": "click", "status": "skipped"}, "action:one"),
        event(4, {"type": "final_outcome", "state": "cancelled", "authentication": "not_observed", "diagnostic": "user_stop"}),
    ]
    for value in values:
        store.worker_event(value)
    record = store.record("run-1")
    assert record.snapshot.cursor == 4
    assert {step.step_id: step.state for step in record.snapshot.steps}["action:one"] == "skipped"
    assert {step.step_id: step.state for step in record.snapshot.steps}["action:two"] == "not_executed"
    assert {step.step_id: step.state for step in record.snapshot.steps}["outcome"] == "cancelled"
    assert record.cleanup_verified and not record.active
    unavailable = store.replay("run-1", 0)
    assert unavailable.status == "resync_required" and not unavailable.events
    replay = store.replay("run-1", 1)
    assert [value.sequence for value in replay.events] == [2, 3, 4]
    assert store.replay("run-1", 5).status == "resync_required"
    assert not store.replay("run-1", 4).events


def test_worker_duplicate_is_ignored_but_gap_is_rejected(store):
    store.reserve("run-1", request())
    first = event(1, {"type": "preflight", "status": "started"}, "preflight")
    store.worker_event(first)
    cursor = store.record("run-1").snapshot.cursor
    store.worker_event(first)
    assert store.record("run-1").snapshot.cursor == cursor
    with pytest.raises(ValueError, match="worker_event_gap"):
        store.worker_event(event(3, {"type": "preflight", "status": "ready"}, "preflight"))


def test_stop_does_not_regress_to_running_and_is_idempotent(store):
    store.reserve("run-1", request())
    store.stop_requested("run-1")
    cursor = store.record("run-1").snapshot.cursor
    store.stop_requested("run-1")
    store.worker_event(event(1, {"type": "run_state_changed", "state": "running"}))
    assert store.record("run-1").snapshot.state == "stopping"
    assert store.record("run-1").snapshot.cursor == cursor


def test_completion_winning_stop_race_preserves_authentication(store):
    store.reserve("run-1", request())
    store.worker_event(event(1, {"type": "authentication", "status": "succeeded"}, "authentication"))
    store.stop_requested("run-1")
    store.worker_event(event(2, {"type": "final_outcome", "state": "succeeded", "authentication": "succeeded"}))
    record = store.record("run-1")
    assert record.snapshot.state == record.authentication == "succeeded"
    cursor = record.snapshot.cursor
    store.stop_requested("run-1")
    assert store.record("run-1").snapshot.cursor == cursor


def test_cleanup_uncertainty_remains_active_without_final_event(store):
    store.reserve("run-1", request())
    store.worker_event(event(1, {"type": "authentication", "status": "succeeded"}, "authentication"))
    store.interrupted("run-1", clean=False, diagnostic="cleanup_failed")
    record = store.record("run-1")
    assert record.authentication == "succeeded"
    assert record.snapshot.state == "interrupted" and record.cleanup_required and record.active
    assert not any(item.payload.type == "final_outcome" for item in store.replay("run-1", record.snapshot.cursor - 2).events)
    with pytest.raises(Conflict):
        store.reserve("new-run", request("new-request"))


def test_only_one_service_can_own_database(store):
    other = RunStore(store.path)
    with pytest.raises(BlockingIOError):
        other.open()
    store.reserve("run-1", request())
    assert store.active_id() == "run-1"


def test_history_filters_and_pagination_are_real(store):
    for index in range(5):
        store.reserve(f"run-{index}", request(f"request-{index}"))
        store.failed_before_spawn(f"run-{index}")
    page = store.page(offset=1, limit=2, state="failed", profile_id="controlled-password")
    assert page.total == 5
    assert [item.snapshot.run_id for item in page.runs] == ["run-3", "run-2"]
    assert not store.page(state="succeeded").runs


@pytest.mark.parametrize("path,kind", [("../private.png", "masked_screenshot"), ("/private.png", "masked_screenshot"),
                                     ("a\\private.png", "masked_screenshot"), ("trace.zip", "masked_screenshot"),
                                     ("private.json", "raw_trace"), ("%2e%2e/private.png", "masked_screenshot")])
def test_artifact_registration_cannot_authorize_paths_or_raw_traces(store, path, kind):
    store.reserve("run-1", request())
    with pytest.raises(ValueError, match="invalid_artifact"):
        store.register_artifact("run-1", "artifact-1", path, kind)


def test_sqlite_records_exclude_unknown_input_secrets(store):
    store.reserve("run-1", request())
    store.manifest("run-1", manifest())
    store.failed_before_spawn("run-1")
    text = "\n".join(store.connection.iterdump())
    assert "test-password" not in text and "credential:" not in text
    assert "gmail/token" not in text
    assert json.loads(store._row("run-1")["request"])["profile_id"] == "controlled-password"
