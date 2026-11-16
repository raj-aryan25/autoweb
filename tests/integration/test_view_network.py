"""Real loopback HTTP live-view acceptance, including a masked Chromium capture.

The in-process suite covers the state matrix; this file proves that the same
endpoints work over a real TCP server and that the served bytes are the actual
masked frame, not a placeholder or a persisted image.
"""

from __future__ import annotations

import asyncio
import base64
import socket
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx

from autoweb_api import live_transport
from tests.fixtures.api_support import ROOT, configure, finished, handshake, start_body

pytestmark = pytest.mark.asyncio

MAGENTA_MIN = 500


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def start_server(root, state, port):
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(ROOT / "tests/fixtures/api_server.py"),
        "--root", str(root), "--state", str(state), "--port", str(port),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    async def ready():
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1,
                                     trust_env=False) as client:
            while True:
                if process.returncode is not None:
                    pytest.fail("Local API process exited before readiness")
                try:
                    if (await client.get("/api/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
    try:
        await asyncio.wait_for(ready(), 20)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    return process


async def stop_server(process):
    if process.returncode is None:
        process.terminate()
        await asyncio.wait_for(process.wait(), 20)


def start_view_body(live=True, request_id=None):
    body = start_body(request_id or "view-" + uuid4().hex)
    body["overrides"].update(headless=True, live_view_enabled=live,
                             login_timeout_seconds=20, verification_timeout_seconds=5)
    return body


async def wait_view(client, run_id, predicate, timeout=20):
    async def poll():
        while True:
            response = await client.get(f"/api/runs/{run_id}/view")
            if response.status_code == 200 and predicate(response.json()):
                return response.json()
            await asyncio.sleep(0.02)
    return await asyncio.wait_for(poll(), timeout)


async def mask_stats(page, data):
    return await page.evaluate(
        """async ({ data }) => {
            const image = new Image();
            image.src = 'data:image/jpeg;base64,' + data;
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = image.naturalWidth;
            canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            context.drawImage(image, 0, 0);
            const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
            let masked = 0;
            for (let i = 0; i < pixels.length; i += 4) {
                if (pixels[i] > 200 && pixels[i + 1] < 110 && pixels[i + 2] > 200) masked++;
            }
            return { masked, total: canvas.width * canvas.height,
                     width: canvas.width, height: canvas.height };
        }""", {"data": base64.b64encode(data).decode()})


def assert_private_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "etag" not in response.headers


def assert_no_pixels(state, frames):
    encoded = [base64.b64encode(frame).decode() for frame in frames]
    for path in [path for path in Path(state).rglob("*") if path.is_file()]:
        data = path.read_bytes()
        for frame, text in zip(frames, encoded):
            assert frame not in data
            assert text.encode() not in data
    assert not list(Path(state).rglob("*.jpg"))


async def test_real_server_serves_masked_frames_and_cleans_up_on_stop(tmp_path, session):
    configure(tmp_path, block=True)
    port, state = free_port(), tmp_path / "state"
    process = await start_server(tmp_path, state, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20,
                                     trust_env=False) as client:
            await handshake(client)
            body = start_view_body()
            response = await client.post("/api/runs", json=body)
            assert response.status_code == 202
            run_id = response.json()["snapshot"]["run_id"]

            view = await wait_view(client, run_id, lambda value: value["status"]["state"] == "live")
            assert view["frame"] is not None
            metadata = view["frame"]

            response = await client.get(f"/api/runs/{run_id}/view/frame?after=0")
            assert response.status_code == 200
            frame_bytes = response.content
            assert response.headers["content-type"] == "image/jpeg"
            assert frame_bytes.startswith(b"\xff\xd8") and frame_bytes.endswith(b"\xff\xd9")
            assert live_transport.jpeg_dimensions(frame_bytes) == (1280, 720)
            assert response.headers["x-autoweb-run-id"] == run_id
            assert response.headers["x-autoweb-view-state"] == "live"
            assert int(response.headers["x-autoweb-frame-byte-length"]) == len(frame_bytes)
            assert response.headers["content-length"] == str(len(frame_bytes))
            assert datetime.fromisoformat(
                response.headers["x-autoweb-frame-captured-at"]).tzinfo is not None
            assert_private_headers(response)

            # Decode the real frame and prove known form controls were masked.
            last_sequence = 0
            stats = None
            async def masked():
                nonlocal last_sequence, stats
                while True:
                    current = (await client.get(f"/api/runs/{run_id}/view")).json()
                    if current["frame"] and current["frame"]["sequence"] > last_sequence:
                        sequence = current["frame"]["sequence"]
                        image = await client.get(
                            f"/api/runs/{run_id}/view/frame?after={sequence - 1}")
                        assert image.status_code == 200
                        candidate = await mask_stats(session.page, image.content)
                        last_sequence = sequence
                        if candidate["masked"] >= MAGENTA_MIN:
                            stats = candidate
                            return
                    await asyncio.sleep(0.03)
            await asyncio.wait_for(masked(), 15)
            assert stats["masked"] >= MAGENTA_MIN
            assert stats["masked"] < stats["total"] * 0.9
            assert (stats["width"], stats["height"]) == (1280, 720)

            response = await client.get(
                f"/api/runs/{run_id}/view/frame?after={last_sequence}")
            assert response.status_code == 204 and response.content == b""
            assert response.headers["x-autoweb-frame-sequence"] == str(last_sequence)
            assert_private_headers(response)

            # A viewer that drops its stream must not own Stop or cleanup.
            frame_url = f"/api/runs/{run_id}/view/frame?after=0"
            for _ in range(3):
                try:
                    async with client.stream("GET", frame_url) as streamed:
                        assert streamed.status_code in {200, 204}
                except (httpx.HTTPError, RuntimeError):
                    pass

            observed = []

            async def watch():
                while True:
                    try:
                        answer = await client.get(frame_url)
                    except httpx.HTTPError:
                        return
                    observed.append(answer.status_code)
                    await asyncio.sleep(0.01)

            viewer = asyncio.create_task(watch())
            stopped = await asyncio.wait_for(client.post(f"/api/runs/{run_id}/stop"), 15)
            assert stopped.status_code in {200, 202}
            record = await finished(client, run_id)
            viewer.cancel()
            await asyncio.gather(viewer, return_exceptions=True)
            assert record["snapshot"]["state"] == "cancelled"
            assert record["cleanup_verified"] and not record["active"]
            assert observed and set(observed) <= {200, 204, 409, 410}

            ended = (await client.get(f"/api/runs/{run_id}/view")).json()
            assert ended == {"run_id": run_id,
                             "status": {"state": "ended", "diagnostic": None},
                             "frame": None}
            response = await client.get(f"/api/runs/{run_id}/view/frame?after=0")
            assert response.status_code == 410
            assert response.json() == {"code": "view_ended"}
            assert_private_headers(response)

        assert not list(Path("/tmp").glob(
            live_transport._prefix(str(Path(state).resolve())) + "*"))
        assert_no_pixels(state, [frame_bytes])
    finally:
        await stop_server(process)


async def test_completed_real_run_has_no_replay_and_no_preview_persistence(tmp_path):
    configure(tmp_path)
    port, state = free_port(), tmp_path / "state"
    process = await start_server(tmp_path, state, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20,
                                     trust_env=False) as client:
            await handshake(client)
            response = await client.post("/api/runs", json=start_view_body())
            assert response.status_code == 202
            run_id = response.json()["snapshot"]["run_id"]
            record = await finished(client, run_id)
            assert record["snapshot"]["state"] == "succeeded"
            assert record["cleanup_verified"] and not record["active"]

            for _ in range(2):
                value = (await client.get(f"/api/runs/{run_id}/view")).json()
                assert value == {"run_id": run_id,
                                 "status": {"state": "ended", "diagnostic": None},
                                 "frame": None}
                response = await client.get(f"/api/runs/{run_id}/view/frame?after=0")
                assert response.status_code == 410 and response.json() == {"code": "view_ended"}

            replay = (await client.get(f"/api/runs/{run_id}/events",
                                       headers={"Last-Event-ID": str(record["snapshot"]["cursor"])}))
            assert replay.status_code == 204
            history = (await client.get(f"/api/runs/{run_id}/events?after=0")).text
            assert "frame" not in history and "jpeg" not in history.lower()

        assert not list(Path("/tmp").glob(
            live_transport._prefix(str(Path(state).resolve())) + "*"))
        assert no_jpeg_files(state)
    finally:
        await stop_server(process)


def no_jpeg_files(state):
    return not list(Path(state).rglob("*.jpg"))


async def test_real_server_view_read_proof_before_any_run_exists(tmp_path):
    configure(tmp_path)
    port, state = free_port(), tmp_path / "state"
    process = await start_server(tmp_path, state, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10,
                                     trust_env=False) as client:
            await handshake(client)
            token = client.headers.get("X-AutoWeb-CSRF")
            path = "/api/runs/unknown-run/view/frame"
            origin = client.headers.get("Origin")

            response = await client.get(path, headers={"X-AutoWeb-CSRF": ""})
            assert response.status_code == 403 and response.json() == {"code": "csrf_required"}
            assert_private_headers(response)

            response = await client.get(path, headers={"Sec-Fetch-Site": "cross-site"})
            assert response.status_code == 403 and response.json() == {"code": "invalid_origin"}

            response = await client.get(path, headers={"Sec-Fetch-Site": "same-site"})
            assert response.status_code == 403

            response = await client.get(path, headers={"Origin": "http://evil.example"})
            assert response.status_code == 403 and response.json() == {"code": "invalid_origin"}

            response = await client.get(path, headers={"Sec-Fetch-Site": "same-origin"})
            assert response.status_code == 404 and response.json() == {"code": "not_found"}

            response = await client.get(path, headers={"Origin": origin})
            assert response.status_code == 404

            client.cookies.clear()
            response = await client.get(path, headers={"X-AutoWeb-CSRF": token})
            assert response.status_code == 401 and response.json() == {"code": "session_required"}
    finally:
        await stop_server(process)
