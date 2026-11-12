"""Controlled API→worker→package checks and durable HTTP contracts."""

import asyncio
import json
import os
from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx
from starlette.requests import Request

from autoweb_api.api import create_app
from autoweb_api.contracts import StartRun
from autoweb_api.settings import ServiceSettings
from tests.fixtures.api_support import BASE, configure, finished, local_client, start_body

pytestmark = pytest.mark.asyncio


async def test_factory_defers_lifecycle_and_health_profiles_do_not_create_clients(tmp_path, monkeypatch):
    configure(tmp_path)
    factory = Mock(side_effect=AssertionError("Service must not be created during factory import"))
    settings = ServiceSettings(tmp_path)
    create_app(settings, service_factory=factory)
    factory.assert_not_called()
    assert not settings.data_directory.exists()
    monkeypatch.setattr("autoweb_api.configuration.load_environment", Mock(side_effect=AssertionError("No environment lookup")))
    async with local_client(tmp_path) as (client, service, app):
        assert (await client.get("/api/health")).json()["status"] == "ready"
        result = await client.get("/api/profiles")
        assert result.status_code == 200
        profiles = result.json()
        assert len(profiles) == 5
        assert next(item for item in profiles if item["id"] == "controlled-otp")["readiness"] == "unavailable"
        assert service.coordinator.active is None and not service.tasks


async def test_preflight_is_nonexecuting_and_external_services_stay_unverified(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        result = await client.post("/api/preflight", json={"profile_id": "controlled-password"})
        assert result.status_code == 200 and result.json()["status"] == "ready"
        assert result.json()["external_services"] == "unverified"
        assert result.json()["manifest"]["effective"]["browser"]["headless"] is False
        unavailable = await client.post("/api/preflight", json={"profile_id": "controlled-magic-link"})
        assert unavailable.json()["status"] == "unavailable"
        assert service.store.page().total == 0 and service.coordinator.active is None
        assert not (tmp_path / "run-artifacts").exists()


async def test_real_run_persists_and_repeated_start_is_not_reexecuted(tmp_path):
    configure(tmp_path)
    state = tmp_path / "state"
    async with local_client(tmp_path, state=state) as (client, service, app):
        response = await client.post("/api/runs", json=start_body())
        assert response.status_code == 202
        run_id = response.json()["snapshot"]["run_id"]
        first = await finished(client, run_id)
        assert first["snapshot"]["state"] == "succeeded" and first["cleanup_verified"]
        assert first["manifest"]["effective"]["browser"]["headless"] is True
        assert first["authentication"] == "succeeded"
        receipt = service.receipts / f"{run_id}.json"
        assert receipt.exists() and receipt.stat().st_mode & 0o077 == 0
        response = await client.post("/api/runs", json=start_body())
        assert response.status_code == 200 and response.json()["snapshot"]["run_id"] == run_id
        assert service.store.page().total == 1
        stop = await client.post(f"/api/runs/{run_id}/stop")
        assert stop.status_code == 200 and stop.json()["snapshot"]["cursor"] == first["snapshot"]["cursor"]
    async with local_client(tmp_path, state=state) as (client, service, app):
        value = (await client.get(f"/api/runs/{run_id}")).json()
        assert value["snapshot"] == first["snapshot"]
        duplicate = await client.post("/api/runs", json=start_body())
        assert duplicate.status_code == 200
        assert service.store.page().total == 1 and service.coordinator.active is None
        assert "test-password" not in str(value) and "credential:" not in str(value)


async def test_concurrent_start_duplicate_conflict_stop_and_refresh(tmp_path):
    configure(tmp_path, block=True)
    async with local_client(tmp_path) as (client, service, app):
        responses = await asyncio.gather(*(client.post("/api/runs", json=start_body()) for _ in range(3)))
        assert sorted(item.status_code for item in responses) == [200, 200, 202]
        ids = {item.json()["snapshot"]["run_id"] for item in responses}
        assert len(ids) == 1
        run_id = ids.pop()
        conflict = await client.post("/api/runs", json=start_body("other-id"))
        assert conflict.status_code == 409 and conflict.json()["code"] == "run_active"
        changed = start_body()
        changed["overrides"]["slow_mo_ms"] = 10
        conflict = await client.post("/api/runs", json=changed)
        assert conflict.status_code == 409 and conflict.json()["code"] == "request_id_conflict"
        assert (await client.get("/api/runs")).json()["active_run_id"] == run_id
        for _ in range(2):
            assert (await client.post(f"/api/runs/{run_id}/stop")).status_code in {200, 202}
        result = await finished(client, run_id)
        assert result["snapshot"]["state"] == "cancelled"
        assert (await client.post(f"/api/runs/{run_id}/stop")).status_code == 200
        assert (await client.get("/api/runs?state=cancelled&limit=1")).json()["total"] == 1


async def test_sse_replay_resume_resync_and_terminal_close(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path, retention=3) as (client, service, app):
        run_id = (await client.post("/api/runs", json=start_body())).json()["snapshot"]["run_id"]
        result = await finished(client, run_id)
        cursor = result["snapshot"]["cursor"]
        response = await client.get(f"/api/runs/{run_id}/events?after=0")
        assert response.status_code == 200 and "event: resync_required" in response.text
        assert '"events":[]' in response.text
        response = await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(cursor - 2)})
        sequences = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
        assert sequences == [cursor - 1, cursor]
        assert "event: stream_end" in response.text
        assert (await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(cursor)})).status_code == 204
        assert (await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "bad-private-value"})).json() == {"code": "invalid_cursor"}


async def test_sse_does_not_lose_final_event_between_snapshot_and_yield(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        service.store.reserve("race-run", StartRun(request_id="race-request", profile_id="controlled-password"))
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/runs/{run_id}/events")
        request = Request({"type": "http", "headers": [], "method": "GET", "path": "/api/runs/race-run/events"})
        response = await endpoint(request, "race-run", 0)
        first = await response.body_iterator.__anext__()
        assert "id: 1" in first
        service.store.failed_before_spawn("race-run")
        service.notify()
        remaining = "".join([item async for item in response.body_iterator])
        assert "id: 2" in remaining and "final_outcome" in remaining


@pytest.mark.parametrize("case", ["host", "origin", "missing_origin", "csrf", "cookie", "body", "unknown_fields"])
async def test_request_guards_prevent_execution_and_do_not_echo_secrets(tmp_path, case):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        headers = {}
        payload = start_body()
        if case == "host": headers["Host"] = "evil.example"
        if case == "origin": headers["Origin"] = "http://evil.example"
        if case == "missing_origin": client.headers.pop("Origin")
        if case == "csrf": headers["X-AutoWeb-CSRF"] = "private-secret"
        if case == "cookie": client.cookies.clear()
        if case == "unknown_fields": payload["password"] = "private-secret"
        if case == "body":
            response = await client.post("/api/runs", content=b"private-secret" * 2000,
                                         headers={"Content-Type": "application/json"})
        else:
            response = await client.post("/api/runs", json=payload, headers=headers)
        assert response.status_code in {400, 401, 403, 413, 422}
        assert "private-secret" not in response.text
        assert service.store.page().total == 0 and not service.tasks


async def test_artifacts_are_registered_only_and_cannot_follow_symlinks_or_fifos(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        service.store.reserve("artifact-run", StartRun(request_id="artifact-request", profile_id="controlled-password"))
        service.store.failed_before_spawn("artifact-run")
        directory = service.settings.artifacts / "artifact-run"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text('{"status":"failed"}')
        service.store.register_artifact("artifact-run", "safe-report", "report.json", "sanitized_report")
        response = await client.get("/api/runs/artifact-run/artifacts/safe-report")
        assert response.status_code == 200 and response.json() == {"status": "failed"}
        assert response.headers["x-content-type-options"] == "nosniff"
        private = tmp_path / "private.json"
        private.write_text('{"token":"private-secret"}')
        (directory / "link.json").symlink_to(private)
        os.mkfifo(directory / "pipe.json")
        for filename in ["link.json", "pipe.json"]:
            artifact_id = filename.split(".")[0]
            service.store.register_artifact("artifact-run", artifact_id, filename, "sanitized_report")
            response = await asyncio.wait_for(client.get(f"/api/runs/artifact-run/artifacts/{artifact_id}"), 1)
            assert response.status_code == 404 and "private-secret" not in response.text
        assert (await client.get("/api/runs/artifact-run/artifacts/report.json")).status_code == 404
        assert (await client.get("/api/runs/artifact-run/artifacts/%2e%2e%2fprivate.json")).status_code in {404, 422}


async def test_failed_preflight_has_history_but_never_starts_browser(tmp_path):
    configure(tmp_path)
    (tmp_path / "configs/sites/local-password.example.json").write_text('{"invalid":"private-secret"}')
    async with local_client(tmp_path) as (client, service, app):
        response = await client.post("/api/runs", json=start_body())
        run_id = response.json()["snapshot"]["run_id"]
        result = await finished(client, run_id)
        assert result["snapshot"]["state"] == "failed"
        replay = service.store.replay(run_id, 0)
        assert not any(item.payload.type == "browser_startup" for item in replay.events)
        assert "private-secret" not in json.dumps(result)


async def test_graceful_service_shutdown_waits_for_active_cleanup(tmp_path):
    configure(tmp_path, block=True)
    async with local_client(tmp_path) as (client, service, app):
        run_id = (await client.post("/api/runs", json=start_body())).json()["snapshot"]["run_id"]
        async def running():
            while True:
                record = (await client.get(f"/api/runs/{run_id}")).json()
                if record["snapshot"]["state"] == "running":
                    return
                await asyncio.sleep(0.02)
        await asyncio.wait_for(running(), 15)
    async with local_client(tmp_path) as (client, service, app):
        record = (await client.get(f"/api/runs/{run_id}")).json()
        assert record["snapshot"]["state"] == "cancelled" and record["cleanup_verified"]
        assert record["active"] is False and service.coordinator.active is None


async def test_history_cursor_bounds_are_rejected_before_sqlite_binding(tmp_path):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, app):
        result = await client.get("/api/runs?offset=9007199254740992")
        assert result.status_code == 422 and result.json() == {"code": "invalid_request"}
