"""Real HTTP/SSE disconnect and server-crash acceptance against controlled sites."""

import asyncio
import json
import socket
import sqlite3
import sys

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from autoweb_api.process_tree import ProcessIdentity
from tests.fixtures.api_support import ROOT, configure, finished, handshake, start_body

pytestmark = pytest.mark.asyncio


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def start_server(root, state, port):
    process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "tests/fixtures/api_server.py"),
        "--root", str(root), "--state", str(state), "--port", str(port),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    async def ready():
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False) as client:
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


async def wait_for_action(client, run_id):
    async def wait():
        while True:
            value = (await client.get(f"/api/runs/{run_id}")).json()
            manifest = value["manifest"]
            if manifest:
                targets = {step["id"] for step in manifest["steps"] if step["action"] == "wait_for"}
                if any(step["step_id"] in targets and step["state"] == "running" for step in value["snapshot"]["steps"]):
                    return value
            await asyncio.sleep(0.02)
    return await asyncio.wait_for(wait(), 15)


async def test_real_sse_disconnect_reconnect_does_not_stop_or_restart_run(tmp_path):
    configure(tmp_path, block=True)
    port, state = free_port(), tmp_path / "state"
    process = await start_server(tmp_path, state, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False) as client:
            await handshake(client)
            run_id = (await client.post("/api/runs", json=start_body())).json()["snapshot"]["run_id"]
            first_ids = []
            async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("id: "):
                        first_ids.append(int(line[4:]))
                        if len(first_ids) == 3:
                            break
            value = await wait_for_action(client, run_id)
            assert value["active"] and value["snapshot"]["state"] == "running"
            assert (await client.get("/api/runs")).json()["total"] == 1
            async def resumed():
                identifiers = []
                async with client.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(first_ids[-1])}) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("id: "):
                            identifiers.append(int(line[4:]))
                return identifiers
            reader = asyncio.create_task(resumed())
            await client.post(f"/api/runs/{run_id}/stop")
            ids = await asyncio.wait_for(reader, 20)
            result = await finished(client, run_id)
            assert ids == list(range(first_ids[-1] + 1, result["snapshot"]["cursor"] + 1))
            assert result["snapshot"]["state"] == "cancelled" and result["cleanup_verified"]
            assert (await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(ids[-1])})).status_code == 204
    finally:
        await stop_server(process)


async def test_backend_crash_recovers_receipt_without_automatic_rerun(tmp_path):
    configure(tmp_path, block=True)
    port, state = free_port(), tmp_path / "state"
    process = await start_server(tmp_path, state, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False) as client:
            await handshake(client)
            run_id = (await client.post("/api/runs", json=start_body())).json()["snapshot"]["run_id"]
            await wait_for_action(client, run_id)
            with sqlite3.connect(state / "history.sqlite3") as database:
                owner = json.loads(database.execute("SELECT owner FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            old_owner = ProcessIdentity(owner["pid"], 0, owner["started"], "R")
            process.kill()
            await process.wait()
            process = await start_server(tmp_path, state, port)
            assert (await client.get(f"/api/runs/{run_id}")).status_code == 401
            await handshake(client)
            value = (await client.get(f"/api/runs/{run_id}")).json()
            assert value["snapshot"]["state"] == "interrupted"
            assert value["snapshot"]["outcome"]["diagnostic"] == "service_restart"
            assert value["cleanup_verified"] and not value["active"]
            assert not old_owner.alive()
            duplicate = await client.post("/api/runs", json=start_body())
            assert duplicate.status_code == 200 and duplicate.json()["snapshot"]["run_id"] == run_id
            assert (await client.get("/api/runs")).json()["total"] == 1
            assert (await client.get("/api/health")).json()["active_run_id"] is None
    finally:
        await stop_server(process)
