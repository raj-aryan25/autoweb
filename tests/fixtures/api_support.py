"""Controlled configuration and same-origin clients for API checks."""

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from autoweb_api.api import create_app
from autoweb_api.settings import ServiceSettings

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8766"


def configure(root, *, block=False):
    (root / "configs/sites").mkdir(parents=True, exist_ok=True)
    for name in ["configs/settings.json", "configs/sites/local-password.example.json"]:
        shutil.copyfile(ROOT / name, root / name)
    if block:
        path = root / "configs/sites/local-password.example.json"
        value = json.loads(path.read_text())
        value["steps"].insert(1, {"action": "wait_for", "target": {"by": "css", "value": "#never-ready"}, "timeout_ms": 10000})
        path.write_text(json.dumps(value))


def start_body(request_id="api-request"):
    return {"request_id": request_id, "profile_id": "controlled-password",
            "overrides": {"headless": True, "login_timeout_seconds": 10, "verification_timeout_seconds": 1}}


async def handshake(client):
    response = await client.get("/api/session")
    assert response.status_code == 200
    client.headers.update({"X-AutoWeb-CSRF": response.json()["csrf_token"], "Origin": str(client.base_url).rstrip("/"),
                           "Content-Type": "application/json"})


@asynccontextmanager
async def local_client(root, *, retention=1024, state=None):
    settings = ServiceSettings(root, data_directory=state or root / "state", artifacts=root / "run-artifacts",
                               event_retention=retention, recovery_seconds=1)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
            await handshake(client)
            yield client, app.state.service, app


async def finished(client, run_id):
    async def wait():
        while True:
            response = await client.get(f"/api/runs/{run_id}")
            assert response.status_code == 200
            value = response.json()
            if not value["active"] or value["cleanup_required"]:
                return value
            await asyncio.sleep(0.02)
    return await asyncio.wait_for(wait(), 30)
