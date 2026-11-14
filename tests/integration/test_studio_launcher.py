"""Real loopback launcher lifecycle and active-run graceful shutdown."""

import asyncio
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys

import httpx
import pytest

from tests.fixtures.api_support import ROOT, configure, handshake, start_body

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGINT])
async def test_launcher_serves_same_origin_and_signal_drains_active_run(tmp_path, stop_signal):
    configure(tmp_path, block=True)
    ui = tmp_path / 'built-ui'
    ui.mkdir()
    (ui / 'index.html').write_text('<!doctype html><title>Owned Studio acceptance</title>')
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    state = tmp_path / 'state'
    log = tmp_path / 'server.log'
    with log.open('w') as output:
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'autoweb_api.studio',
            '--root', str(tmp_path), '--ui-directory', str(ui), '--data-directory', str(state), '--port', str(port),
            stdout=output, stderr=output)
        run_id = None
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=3) as client:
                async def ready():
                    while True:
                        assert process.returncode is None, log.read_text()
                        try:
                            if (await client.get('/api/health')).status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.05)
                await asyncio.wait_for(ready(), 15)
                assert 'Owned Studio acceptance' in (await client.get('/')).text
                assert (await client.get('/api/health', headers={'Host': 'evil.test'})).status_code == 403
                await handshake(client)
                response = await client.post('/api/runs', json=start_body('shutdown-acceptance'))
                assert response.status_code == 202
                run_id = response.json()['snapshot']['run_id']
                async def running():
                    while True:
                        value = (await client.get(f'/api/runs/{run_id}')).json()
                        if value['snapshot']['state'] == 'running':
                            return
                        await asyncio.sleep(.02)
                await asyncio.wait_for(running(), 15)
                # Keep SSE open while stopping the launcher. It must close the
                # subscription before waiting for HTTP drain and browser cleanup.
                async with client.stream('GET', f'/api/runs/{run_id}/events') as stream:
                    assert stream.status_code == 200
                    process.send_signal(stop_signal)
                    await asyncio.wait_for(process.wait(), 20)
            assert process.returncode in {0, -stop_signal, 128 + stop_signal}
            with sqlite3.connect(state / 'history.sqlite3') as database:
                import json
                record = json.loads(database.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone()[0])
                assert not record['active'] and record['cleanup_verified'] and not record['cleanup_required']
                assert record['snapshot']['state'] == 'cancelled'
            assert 'http://127.0.0.1:' in log.read_text()
            # The port and SQLite ownership lock are released for a subsequent launch.
            from autoweb_api.storage import RunStore
            store = RunStore(state / 'history.sqlite3')
            store.open()
            store.close()
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 25)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
