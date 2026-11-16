"""Run-scoped live-view HTTP reads: statuses, cursors, privacy and passivity.

These are in-process ASGI checks with synthetic receiver frames; the separate
network suite exercises a real server and a real masked Chromium capture.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from autoweb_api.contracts import LiveViewStatus, RunOverrides, StartRun
from autoweb_api.live_capture import PreviewFrame
from tests.fixtures.api_support import BASE, configure, local_client
from tests.fixtures.frames import REAL_HEIGHT, REAL_JPEG, REAL_WIDTH, jpeg

pytestmark = pytest.mark.asyncio

MAX_SAFE_INT = 9007199254740991


def request_id():
    return "view-" + uuid4().hex


def reserve(service, *, live=True):
    request = StartRun(request_id=request_id(), profile_id="controlled-password",
                       overrides=RunOverrides(live_view_enabled=live))
    return service.store.reserve(uuid4().hex, request)[0].snapshot.run_id


def frame(sequence=1, *, data=REAL_JPEG, width=REAL_WIDTH, height=REAL_HEIGHT, captured_at=None):
    return PreviewFrame(sequence, captured_at or datetime.now(timezone.utc),
                        width, height, data)


class FakePreview:
    def __init__(self, run_id, *, state="live", diagnostic=None, latest=None):
        self.run_id = run_id
        self.status = LiveViewStatus(state=state, diagnostic=diagnostic)
        self.latest = latest


class FakeHandle:
    def __init__(self, run_id, *, preview=None, process=object(), cleanup_verified=False,
                 cleanup_uncertain=False):
        self.run_id = run_id
        self.preview = preview
        self.process = process
        self.cleanup_verified = cleanup_verified
        self.cleanup_uncertain = cleanup_uncertain
        self.stopped = False

    def request_stop(self):
        self.stopped = True


def attach(service, run_id, **values):
    handle = FakeHandle(run_id, **values)
    service.handles[run_id] = handle
    return handle


def live_handle(service, run_id, *, state="live", latest=None):
    return attach(service, run_id, preview=FakePreview(run_id, state=state, latest=latest))


def assert_security_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


def assert_frame_headers(response, run_id, state, metadata=None):
    assert_security_headers(response)
    assert response.headers["x-autoweb-run-id"] == run_id
    assert response.headers["x-autoweb-view-state"] == state
    for name in ("x-autoweb-frame-sequence", "x-autoweb-frame-captured-at",
                 "x-autoweb-frame-width", "x-autoweb-frame-height",
                 "x-autoweb-frame-byte-length"):
        assert (name in response.headers) is (metadata is not None)
    if metadata is not None:
        assert response.headers["x-autoweb-frame-sequence"] == str(metadata["sequence"])
        assert response.headers["x-autoweb-frame-captured-at"] == metadata["captured_at"]
        assert response.headers["x-autoweb-frame-width"] == str(metadata["width"])
        assert response.headers["x-autoweb-frame-height"] == str(metadata["height"])
        assert response.headers["x-autoweb-frame-byte-length"] == str(metadata["byte_length"])


# --- unknown/history/disabled precedence -----------------------------------


async def test_unknown_runs_are_404_and_do_not_create_work(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        for suffix in ("view", "view/frame?after=0"):
            response = await client.get(f"/api/runs/unknown-run/{suffix}")
            assert response.status_code == 404
            assert response.json() == {"code": "not_found"}
            assert_security_headers(response)
        assert service.store.page().total == 0
        assert not service.tasks and service.coordinator.active is None
        for bad in ("bad run", "x" * 129, "run/other"):
            assert (await client.get(f"/api/runs/{bad}/view")).status_code in {404, 422}


async def test_known_inactive_runs_report_ended_even_when_viewing_was_disabled(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        enabled = reserve(service, live=True)
        service.store.failed_before_spawn(enabled)
        disabled = reserve(service, live=False)
        service.store.failed_before_spawn(disabled)

        for run_id in (enabled, disabled):
            response = await client.get(f"/api/runs/{run_id}/view")
            assert response.status_code == 200
            assert response.json() == {"run_id": run_id,
                                       "status": {"state": "ended", "diagnostic": None},
                                       "frame": None}
            assert_security_headers(response)
            response = await client.get(f"/api/runs/{run_id}/view/frame?after=0")
            assert response.status_code == 410 and response.json() == {"code": "view_ended"}
            assert_frame_headers(response, run_id, "ended")
        assert service.store.page().total == 2


async def test_active_disabled_run_is_disabled_and_frame_conflicts(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service, live=False)
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.status_code == 200
        assert response.json()["status"] == {"state": "disabled", "diagnostic": None}
        assert response.json()["frame"] is None
        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 409 and response.json() == {"code": "view_disabled"}
        assert_frame_headers(response, run_id, "disabled")


# --- active startup/transport states ---------------------------------------


async def test_active_starting_and_unavailable_states_are_empty_204(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.json()["status"] == {"state": "starting", "diagnostic": None}
        assert response.json()["frame"] is None
        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 204 and response.content == b""
        assert_frame_headers(response, run_id, "starting")

        attach(service, run_id, preview=None, process=None)
        assert (await client.get(f"/api/runs/{run_id}/view")).json()["status"]["state"] == "starting"

        attach(service, run_id, preview=None, process=object())
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.json()["status"] == {"state": "unavailable",
                                             "diagnostic": "transport_unavailable"}
        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 204
        assert_frame_headers(response, run_id, "unavailable")

        attach(service, run_id, preview=FakePreview("other-run"),
               process=object())
        assert (await client.get(f"/api/runs/{run_id}/view")).json()["status"]["state"] == "unavailable"
        wrong_handle = attach(service, run_id, preview=FakePreview(run_id),
                              process=object())
        wrong_handle.run_id = "other-run"
        assert (await client.get(f"/api/runs/{run_id}/view")).json()["status"]["state"] == "unavailable"


async def test_cleanup_and_closing_take_precedence_over_frames(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        live_handle(service, run_id, latest=frame(2))
        service.handles[run_id].cleanup_verified = True
        assert (await client.get(f"/api/runs/{run_id}/view")).json()["status"]["state"] == "ended"
        assert (await client.get(f"/api/runs/{run_id}/view/frame")).status_code == 410

        service.handles[run_id].cleanup_verified = False
        service.store.interrupted(run_id, clean=False)
        assert service.store.record(run_id).cleanup_required
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.json()["status"]["state"] == "ended" and response.json()["frame"] is None
        assert (await client.get(f"/api/runs/{run_id}/view/frame")).json() == {"code": "view_ended"}


async def test_service_closing_reports_ended_without_pixels(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        live_handle(service, run_id, latest=frame(1))
        service.request_shutdown()
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.json() == {"run_id": run_id,
                                   "status": {"state": "ended", "diagnostic": None},
                                   "frame": None}
        assert (await client.get(f"/api/runs/{run_id}/view/frame")).status_code == 410


# --- metadata, image bytes and cursors -------------------------------------


async def test_live_status_and_frame_pair_exact_metadata_and_bytes(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        pixels = frame(8)
        live_handle(service, run_id, latest=pixels)

        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.status_code == 200
        body = response.json()
        expected = {"sequence": 8, "captured_at": pixels.captured_at.isoformat(),
                    "width": REAL_WIDTH, "height": REAL_HEIGHT,
                    "byte_length": len(REAL_JPEG), "format": "jpeg"}
        assert body["run_id"] == run_id
        assert body["status"] == {"state": "live", "diagnostic": None}
        assert {key: body["frame"][key] for key in expected if key != "captured_at"} == \
               {key: value for key, value in expected.items() if key != "captured_at"}
        assert datetime.fromisoformat(
            body["frame"]["captured_at"].replace("Z", "+00:00")) == pixels.captured_at
        assert base64.b64encode(REAL_JPEG).decode() not in response.text
        assert REAL_JPEG not in response.content

        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == REAL_JPEG
        assert response.headers["content-length"] == str(len(REAL_JPEG))
        assert "etag" not in response.headers and "content-disposition" not in response.headers
        assert_frame_headers(response, run_id, "live", expected)

        response = await client.get(f"/api/runs/{run_id}/view/frame?after=8")
        assert response.status_code == 204 and response.content == b""
        assert_frame_headers(response, run_id, "live", expected)

        for after in (7, 0):
            response = await client.get(f"/api/runs/{run_id}/view/frame?after={after}")
            assert response.status_code == 200 and response.content == REAL_JPEG


async def test_active_ended_receiver_can_still_serve_its_terminal_frame(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        pixels = frame(3)
        live_handle(service, run_id, state="ended", latest=pixels)

        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.status_code == 200
        assert response.json()["status"]["state"] == "ended"
        assert response.json()["frame"]["sequence"] == 3
        response = await client.get(f"/api/runs/{run_id}/view/frame?after=2")
        assert response.status_code == 200 and response.content == REAL_JPEG
        assert_frame_headers(response, run_id, "ended", {
            "sequence": 3, "captured_at": pixels.captured_at.isoformat(),
            "width": REAL_WIDTH, "height": REAL_HEIGHT,
            "byte_length": len(REAL_JPEG)})
        assert (await client.get(f"/api/runs/{run_id}/view/frame?after=3")).status_code == 204


async def test_invalid_receiver_frame_is_suppressed_as_capture_failed(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        live_handle(service, run_id, latest=frame(1, data=jpeg(32, 16),
                                                  width=REAL_WIDTH, height=REAL_HEIGHT))
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.json() == {"run_id": run_id,
                                   "status": {"state": "unavailable",
                                              "diagnostic": "capture_failed"},
                                   "frame": None}
        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 204 and response.content == b""
        assert_frame_headers(response, run_id, "unavailable")


async def test_frame_cursor_is_bounded_and_validation_does_not_echo_input(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        live_handle(service, run_id, latest=frame(5))
        for after in (0, 4, 5, 6, MAX_SAFE_INT):
            response = await client.get(f"/api/runs/{run_id}/view/frame?after={after}")
            assert response.status_code in {200, 204}
        for after in (-1, MAX_SAFE_INT + 1):
            response = await client.get(f"/api/runs/{run_id}/view/frame?after={after}")
            assert response.status_code == 422 and response.json() == {"code": "invalid_request"}
        for after in ("abc", "1.5", "", "true"):
            response = await client.get(f"/api/runs/{run_id}/view/frame?after={after}")
            assert response.status_code == 422 and response.json() == {"code": "invalid_request"}
            if after:
                assert after not in response.text


# --- isolation, passivity, concurrency and privacy -------------------------


async def test_one_run_never_serves_another_runs_pixels(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        history = reserve(service)
        service.store.failed_before_spawn(history)
        active = reserve(service)
        marker = jpeg(REAL_WIDTH, REAL_HEIGHT, comment=b"ACTIVE-RUN-ONLY")
        live_handle(service, active, latest=frame(1, data=marker))

        response = await client.get(f"/api/runs/{active}/view/frame")
        assert response.status_code == 200 and response.content == marker
        response = await client.get(f"/api/runs/{history}/view/frame")
        assert response.status_code == 410 and response.json() == {"code": "view_ended"}
        encoded = base64.b64encode(marker).decode()
        assert encoded not in response.text and marker[:40].decode("latin-1") not in response.text
        assert (await client.get(f"/api/runs/{history}/view")).json()["frame"] is None


async def test_view_reads_are_passive_concurrent_and_pair_one_snapshot(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        pixels = frame(10)
        handle = live_handle(service, run_id, latest=pixels)
        record_before = service.store.record(run_id).model_dump(mode="json")
        handle_before = dict(vars(handle))
        preview_before = dict(vars(handle.preview))
        tasks_before = set(service.tasks)
        names = {task.get_name() for task in asyncio.all_tasks()}

        async def read(path):
            return await client.get(path)

        paths = [f"/api/runs/{run_id}/view" for _ in range(15)]
        paths += [f"/api/runs/{run_id}/view/frame?after=0" for _ in range(10)]
        paths += [f"/api/runs/{run_id}/view/frame?after=10" for _ in range(10)]
        responses = await asyncio.gather(*(read(path) for path in paths))

        for response, path in zip(responses, paths):
            if path.endswith("after=10"):
                assert response.status_code == 204 and response.content == b""
            elif path.endswith("after=0"):
                assert response.status_code == 200 and response.content == REAL_JPEG
            else:
                assert response.status_code == 200
                assert response.json()["frame"]["sequence"] == 10
        assert set(service.tasks) == tasks_before
        assert service.coordinator.active is None
        assert service.store.page().total == 1
        assert service.store.record(run_id).model_dump(mode="json") == record_before
        assert vars(handle) == handle_before and vars(handle.preview) == preview_before
        added = {task.get_name() for task in asyncio.all_tasks()} - names
        assert not any("preview" in name or "capture" in name for name in added)


async def test_view_never_exposes_private_transport_configuration(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        pixels = frame(1)
        preview = FakePreview(run_id, latest=pixels)
        preview.connection = SimpleNamespace(socket_path="/tmp/private-socket/frames.sock",
                                             token="f" * 64)
        attach(service, run_id, preview=preview)

        responses = [await client.get(f"/api/runs/{run_id}/view"),
                     await client.get(f"/api/runs/{run_id}/view/frame")]
        text = "\n".join(response.text for response in responses)
        headers = "\n".join(str(response.headers) for response in responses)
        for secret in ("private-socket", "frames.sock", "f" * 64):
            assert secret not in text and secret not in headers
        captured = responses[0].json()["frame"]["captured_at"].replace("Z", "+00:00")
        assert datetime.fromisoformat(captured) == pixels.captured_at


# --- read proof and fetch metadata -----------------------------------------


REJECT_CASES = [
    ("missing_token", 403, "csrf_required"),
    ("blank_token", 403, "csrf_required"),
    ("bad_token", 403, "csrf_required"),
    ("missing_session", 401, "session_required"),
    ("bad_host", 403, "invalid_host"),
    ("bad_origin", 403, "invalid_origin"),
    ("null_origin", 403, "invalid_origin"),
    ("cross_site", 403, "invalid_origin"),
    ("same_site", 403, "invalid_origin"),
    ("duplicate_host", 400, "invalid_headers"),
    ("duplicate_origin", 400, "invalid_headers"),
    ("duplicate_cookie", 400, "invalid_headers"),
    ("duplicate_token", 400, "invalid_headers"),
]


def apply_security_case(case, client):
    """Mutate the same-origin client; duplicate cases need raw header pairs."""
    if case == "missing_token":
        client.headers.pop("X-AutoWeb-CSRF")
    elif case == "blank_token":
        client.headers["X-AutoWeb-CSRF"] = ""
    elif case == "bad_token":
        client.headers["X-AutoWeb-CSRF"] = "private-wrong-token"
    elif case == "missing_session":
        client.cookies.clear()
    elif case == "bad_host":
        client.headers["Host"] = "evil.example"
    elif case == "bad_origin":
        client.headers["Origin"] = "http://evil.example"
    elif case == "null_origin":
        client.headers["Origin"] = "null"
    elif case == "cross_site" or case == "same_site":
        client.headers["Sec-Fetch-Site"] = "cross-site" if case == "cross_site" else "same-site"
    elif case.startswith("duplicate_"):
        cookie = client.cookies.get("autoweb_studio_session")
        token = client.headers.get("X-AutoWeb-CSRF")
        host = BASE.removeprefix("http://")
        values = {
            "duplicate_host": ("Host", [host, host]),
            "duplicate_origin": ("Origin", [BASE, BASE]),
            "duplicate_cookie": ("Cookie", [f"autoweb_studio_session={cookie}"] * 2),
            "duplicate_token": ("X-AutoWeb-CSRF", [token, token]),
        }[case]
        return [(values[0], item) for item in values[1]]
    return None


@pytest.mark.parametrize("case,status,code", REJECT_CASES)
async def test_view_rejects_missing_or_weak_read_proof(tmp_path, case, status, code):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        headers = apply_security_case(case, client)
        for suffix in ("view", "view/frame"):
            response = await client.get(f"/api/runs/{run_id}/{suffix}", headers=headers)
            assert response.status_code == status
            if code is not None:
                assert response.json() == {"code": code}
            assert_security_headers(response)
            assert "private-wrong-token" not in response.text
        assert service.store.record(run_id).active and not service.tasks


ALLOWED_CASES = [
    ("same_origin_fetch", {"Sec-Fetch-Site": "same-origin"}),
    ("fetch_none", {"Sec-Fetch-Site": "none"}),
    ("no_origin_or_fetch", {"Origin": None}),
    ("vite_proxy_origin", {"Host": "127.0.0.1:5173",
                           "Origin": "http://127.0.0.1:5173",
                           "Sec-Fetch-Site": "same-origin"}),
]


@pytest.mark.parametrize("case,values", ALLOWED_CASES)
async def test_view_accepts_explicit_same_origin_and_nonbrowser_token_reads(tmp_path, case, values):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        run_id = reserve(service)
        for name, value in values.items():
            if value is None:
                client.headers.pop(name)
            else:
                client.headers[name] = value
        response = await client.get(f"/api/runs/{run_id}/view")
        assert response.status_code == 200
        assert response.json()["status"]["state"] == "starting"
        assert_security_headers(response)
        response = await client.get(f"/api/runs/{run_id}/view/frame")
        assert response.status_code == 204
        assert_frame_headers(response, run_id, "starting")
