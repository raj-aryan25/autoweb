"""Passive view mapping contracts: states, metadata pairing and no side effects.

No HTTP, browser, persistence or transport is involved. ``snapshot_view`` must
only read the caller-owned record/handle and return one paired metadata+pixels
snapshot; invalid internal frames degrade to an unavailable view, never bytes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoweb_api.api_contracts import RunView, ViewFrameMetadata
from autoweb_api.contracts import LiveViewStatus
from autoweb_api.live_capture import MAX_FRAME_BYTES, PreviewFrame
from autoweb_api.live_view import snapshot_view
from tests.fixtures.frames import REAL_HEIGHT, REAL_JPEG, REAL_WIDTH, jpeg

MAX_SAFE_INT = 9007199254740991


def record(*, active=True, live_view_enabled=True, cleanup_required=False, run_id="run-1"):
    return SimpleNamespace(
        snapshot=SimpleNamespace(run_id=run_id),
        active=active,
        cleanup_required=cleanup_required,
        overrides=SimpleNamespace(live_view_enabled=live_view_enabled),
    )


def frame(sequence=1, *, data=REAL_JPEG, width=REAL_WIDTH, height=REAL_HEIGHT, captured_at=None):
    return PreviewFrame(sequence, captured_at or datetime.now(timezone.utc),
                        width, height, data)


def preview(run_id="run-1", *, state="live", diagnostic=None, latest=None):
    return SimpleNamespace(run_id=run_id,
                           status=LiveViewStatus(state=state, diagnostic=diagnostic),
                           latest=latest)


def handle(run_id="run-1", *, view=None, process=object(), cleanup_verified=False,
           cleanup_uncertain=False):
    return SimpleNamespace(run_id=run_id, preview=view, process=process,
                           cleanup_verified=cleanup_verified, cleanup_uncertain=cleanup_uncertain)


def assert_empty(result, state, diagnostic=None):
    value, pixels = result
    assert pixels is None
    assert value == RunView(run_id="run-1", status=LiveViewStatus(state=state,
                                                                 diagnostic=diagnostic))


# --- terminal/history/disabled precedence ----------------------------------


@pytest.mark.parametrize("live_view_enabled", [True, False])
def test_known_inactive_run_is_ended_without_a_frame_even_when_never_enabled(live_view_enabled):
    result = snapshot_view(record(active=False, live_view_enabled=live_view_enabled),
                           handle(view=preview(latest=frame())))
    assert_empty(result, "ended")
    assert result[0].frame is None


def test_cleanup_required_history_is_ended_not_disabled():
    result = snapshot_view(record(live_view_enabled=False, cleanup_required=True),
                           handle(view=preview(latest=frame())))
    assert_empty(result, "ended")


def test_closing_service_reports_ended_without_pixels():
    result = snapshot_view(record(), handle(view=preview(latest=frame())), closing=True)
    assert_empty(result, "ended")


def test_verified_or_uncertain_handle_cleanup_is_ended():
    for flag in ("cleanup_verified", "cleanup_uncertain"):
        result = snapshot_view(record(), handle(view=preview(latest=frame()), **{flag: True}))
        assert_empty(result, "ended")


def test_active_disabled_view_has_no_frame_and_no_pixels():
    result = snapshot_view(record(live_view_enabled=False), handle())
    assert_empty(result, "disabled")


# --- active startup and transport availability -----------------------------


def test_active_run_without_a_handle_is_starting():
    assert_empty(snapshot_view(record(), None), "starting")


@pytest.mark.parametrize("process,state", [(None, "starting"), (object(), "unavailable")])
def test_missing_preview_uses_process_presence_for_starting_or_unavailable(process, state):
    result = snapshot_view(record(), handle(view=None, process=process))
    assert_empty(result, state, None if state == "starting" else "transport_unavailable")


def test_wrong_run_handle_or_preview_is_unavailable():
    result = snapshot_view(record(), handle(run_id="other-run", view=preview()))
    assert_empty(result, "unavailable", "transport_unavailable")
    result = snapshot_view(record(), handle(view=preview(run_id="other-run")))
    assert_empty(result, "unavailable", "transport_unavailable")


@pytest.mark.parametrize("state,diagnostic", [
    ("starting", None),
    ("unavailable", "transport_unavailable"),
    ("unavailable", "capture_failed"),
])
def test_receiver_lifecycle_without_a_frame_is_passed_through(state, diagnostic):
    result = snapshot_view(record(), handle(view=preview(state=state, diagnostic=diagnostic)))
    assert_empty(result, state, diagnostic)


@pytest.mark.parametrize("state", ["starting", "unavailable", "disabled"])
def test_non_live_receiver_states_never_expose_a_frame(state):
    result = snapshot_view(record(), handle(view=preview(state=state, latest=frame())))
    assert_empty(result, state)


# --- paired metadata and pixels --------------------------------------------


def test_live_frame_returns_exact_pixels_and_bounded_metadata():
    captured = datetime.now(timezone.utc) - timedelta(milliseconds=250)
    pixels = frame(sequence=7, captured_at=captured)
    view = preview(run_id="run-7", latest=pixels)
    value, returned = snapshot_view(record(run_id="run-7"), handle(run_id="run-7", view=view))

    assert returned is pixels
    assert returned.data is pixels.data
    assert value.run_id == "run-7"
    assert value.status == LiveViewStatus(state="live")
    assert value.frame == ViewFrameMetadata(sequence=7, captured_at=captured,
                                            width=REAL_WIDTH, height=REAL_HEIGHT,
                                            byte_length=len(REAL_JPEG))
    assert value.frame.format == "jpeg"


def test_ended_receiver_may_retain_the_last_frame_while_run_is_active():
    pixels = frame(sequence=3)
    view = preview(state="ended", latest=pixels)
    value, returned = snapshot_view(record(), handle(view=view))
    assert returned is pixels
    assert value.status == LiveViewStatus(state="ended")
    assert value.frame is not None and value.frame.sequence == 3


def test_ended_receiver_without_frame_is_ended_and_empty():
    assert_empty(snapshot_view(record(), handle(view=preview(state="ended"))), "ended")


def test_live_receiver_without_frame_degrades_to_capture_failed():
    assert_empty(snapshot_view(record(), handle(view=preview(state="live", latest=None))),
                 "unavailable", "capture_failed")


@pytest.mark.parametrize("bad", [
    frame(data=b""),
    frame(data=b"not-a-jpeg"),
    frame(data=REAL_JPEG[:-2]),
    frame(data=bytearray(REAL_JPEG)),
    frame(data=REAL_JPEG + b"x" * (MAX_FRAME_BYTES + 1)),
    frame(data=jpeg(32, 16), width=REAL_WIDTH, height=REAL_HEIGHT),
    frame(sequence=0),
    frame(sequence=MAX_SAFE_INT + 1),
    frame(width=0),
    frame(width=1921),
    frame(height=1081),
    frame(captured_at=datetime.now()),
])
def test_invalid_internal_frames_never_expose_bytes(bad):
    assert_empty(snapshot_view(record(), handle(view=preview(latest=bad))),
                 "unavailable", "capture_failed")


def test_non_preview_frame_object_is_rejected():
    bogus = SimpleNamespace(sequence=1, captured_at=datetime.now(timezone.utc),
                            width=REAL_WIDTH, height=REAL_HEIGHT, data=REAL_JPEG)
    assert_empty(snapshot_view(record(), handle(view=preview(latest=bogus))),
                 "unavailable", "capture_failed")


def test_snapshot_reads_do_not_mutate_the_handle_or_record():
    pixels = frame(sequence=5)
    view = preview(latest=pixels)
    owner = handle(view=view)
    history = record()
    before_handle = dict(vars(owner))
    before_view = dict(vars(view))
    before_record = dict(vars(history))

    value, returned = snapshot_view(history, owner)

    assert returned is pixels and value.frame is not None
    assert vars(owner) == before_handle
    assert vars(view) == before_view
    assert vars(history) == before_record


@pytest.mark.asyncio
async def test_snapshot_view_creates_no_tasks_or_waiters():
    before = set(asyncio.all_tasks())
    snapshot_view(record(), handle(view=preview(latest=frame())))
    assert set(asyncio.all_tasks()) == before
    assert not [task for task in asyncio.all_tasks()
                if "preview" in task.get_name() or "capture" in task.get_name()]


# --- generated metadata DTO bounds -----------------------------------------


def test_view_frame_metadata_requires_aware_time_and_strict_bounds():
    captured = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        ViewFrameMetadata(sequence=1, captured_at=datetime.now(),
                          width=REAL_WIDTH, height=REAL_HEIGHT, byte_length=1)
    for sequence in (0, MAX_SAFE_INT + 1, True):
        with pytest.raises(ValidationError):
            ViewFrameMetadata(sequence=sequence, captured_at=captured, width=64,
                              height=48, byte_length=1)
    for width, height in ((0, 48), (1921, 720), (64, 0), (1280, 1081), (True, 48)):
        with pytest.raises(ValidationError):
            ViewFrameMetadata(sequence=1, captured_at=captured, width=width,
                              height=height, byte_length=1)
    for size in (0, MAX_FRAME_BYTES + 1, True):
        with pytest.raises(ValidationError):
            ViewFrameMetadata(sequence=1, captured_at=captured, width=64,
                              height=48, byte_length=size)
    with pytest.raises(ValidationError):
        ViewFrameMetadata(sequence=1, captured_at=captured, width=64, height=48,
                          byte_length=1, format="png")
    with pytest.raises(ValidationError):
        ViewFrameMetadata(sequence=1, captured_at=captured, width=64, height=48,
                          byte_length=1, source="private-path")


def test_run_view_enforces_frame_availability_rules():
    captured = datetime.now(timezone.utc)
    metadata = ViewFrameMetadata(sequence=1, captured_at=captured, width=64,
                                 height=48, byte_length=len(REAL_JPEG))
    with pytest.raises(ValidationError):
        RunView(run_id="run-1", status=LiveViewStatus(state="live"), frame=None)
    for state in ("starting", "unavailable", "disabled"):
        with pytest.raises(ValidationError):
            RunView(run_id="run-1", status=LiveViewStatus(state=state), frame=metadata)
    assert RunView(run_id="run-1", status=LiveViewStatus(state="ended"),
                   frame=metadata).frame == metadata
    with pytest.raises(ValidationError):
        RunView(run_id="run-1", status=LiveViewStatus(state="live"), frame=metadata,
                pixels="private")
