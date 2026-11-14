"""Remaining UI -> API -> real package cases on isolated controlled roots.

The magic rejection server is a test-only guardian/executor harness. No scenario
selection is added to the production API or launcher.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile

import httpx
from playwright.async_api import async_playwright, expect

from verify_local_ui import HEADED, ROOT, assert_private, configure, selected_id, wait_record

OUTPUT = ROOT / '.impeccable/review/studio-acceptance'
CASES = [('password-rejection', 'controlled-password', 'failed'),
         ('optional-skip', 'controlled-password', 'succeeded'),
         ('otp-timeout', 'controlled-otp', 'failed'),
         ('magic-rejection', 'controlled-magic-link', 'failed')]


def configure_root(root, case):
    (root / 'configs/sites').mkdir(parents=True)
    for name in ['configs/settings.json', 'configs/sites/local-password.example.json']:
        shutil.copyfile(ROOT / name, root / name)
    path = root / 'configs/sites/local-password.example.json'
    profile = json.loads(path.read_text())
    if case == 'password-rejection':
        profile['credentials']['username']['env'] = 'LOCAL_LOGIN_PASSWORD'
    elif case == 'optional-skip':
        profile['steps'][0]['target']['name'] = 'Absent optional fixture target'
        profile['steps'][0]['timeout_ms'] = 100
    path.write_text(json.dumps(profile))


async def verify_case(playwright, case, profile_id, expected):
    with tempfile.TemporaryDirectory(prefix='studio-acceptance-') as temporary:
        root = Path(temporary)
        state = root / 'state'
        configure_root(root, case)
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        if case == 'magic-rejection':
            command = [sys.executable, str(ROOT / 'tests/fixtures/studio_acceptance.py'), 'server',
                       '--state', str(state)]
        else:
            command = [sys.executable, '-m', 'autoweb_api.studio', '--data-directory', str(state),
                       '--ui-directory', str(ROOT / 'apps/web/dist')]
        command += ['--root', str(root), '--port', str(port)]
        with (OUTPUT / f'{case}-server.log').open('w+') as log:
            server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            browser = None
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                    for _ in range(150):
                        assert server.poll() is None, f'{case} API exited; see server log'
                        try:
                            if (await client.get(url + '/api/health')).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.1)
                    else:
                        raise AssertionError(f'{case} API did not become ready')
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(base_url=url, viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                page = await context.new_page()
                errors, remote = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: remote.append(request.url) if not request.url.startswith((url, 'blob:', 'data:')) else None)
                await page.goto('/')
                await expect(page.locator('.demo-badge')).to_have_text('Local runner · connected', timeout=15000)
                await configure(page, 100, profile_id)
                if case == 'otp-timeout':
                    await page.get_by_role('button', name='Run configuration', exact=True).click()
                    await page.get_by_label('Verification timeout (seconds)').fill('1')
                    await page.get_by_role('button', name='Apply configuration', exact=True).click()
                await page.get_by_role('button', name='Start run', exact=True).click()
                run_id = await selected_id(page)
                record = await wait_record(page, run_id)
                assert record['snapshot']['state'] == expected and record['cleanup_verified']
                await expect(page.locator('.run-metadata')).to_contain_text(expected.capitalize(), timeout=15000)
                if case == 'password-rejection':
                    assert record['snapshot']['outcome']['diagnostic'] == 'authentication_rejected'
                    assert record['authentication'] == 'failed'
                if case == 'optional-skip':
                    assert len([step for step in record['snapshot']['steps'] if step['state'] == 'skipped']) == 1
                    await expect(page.locator('.step.skipped')).to_contain_text('Optional target absent')
                    assert record['authentication'] == 'succeeded'
                if case == 'otp-timeout':
                    assert record['snapshot']['outcome']['diagnostic'] == 'verification_timeout'
                if case == 'magic-rejection':
                    assert record['authentication'] != 'succeeded'
                await page.get_by_role('tab', name='Events').click()
                async with page.expect_download() as download:
                    await page.get_by_role('button', name='Export run JSON', exact=True).click()
                exported = json.loads(Path(await (await download.value).path()).read_text())
                assert exported['record']['snapshot'] == record['snapshot']
                assert_private(exported)
                events = exported['events']
                if profile_id != 'controlled-password':
                    requests = [event for event in events if event['payload']['type'] == 'step_completed'
                                and event['payload']['action'] in {'request_otp', 'request_magic_link'}]
                    assert len(requests) == 1
                    assert not any(event['payload']['type'] == 'acknowledgment' for event in events)
                await page.get_by_role('tab', name='Evidence').click()
                await expect(page.get_by_role('link', name='Sanitized report')).to_be_visible()
                report = await (await page.request.get(f'/api/runs/{run_id}/artifacts/run-report')).json()
                assert_private(report)
                if expected == 'failed':
                    await expect(page.get_by_role('link', name='Masked screenshot')).to_be_visible()
                    image = await page.request.get(f'/api/runs/{run_id}/artifacts/failure-screenshot')
                    assert image.headers['content-type'] == 'image/png'
                    (OUTPUT / f'{case}-masked.png').write_bytes(await image.body())
                for width in [1440, 768, 390, 320]:
                    await page.set_viewport_size({'width': width, 'height': 900})
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{case}: {width}px overflow'
                    await page.evaluate('window.scrollTo(0, 0)')
                    await page.screenshot(path=str(OUTPUT / f'{case}-{width}.png'), full_page=True)
                await page.set_viewport_size({'width': 1440, 'height': 900})
                await page.get_by_role('tab', name='Details').focus()
                await page.keyboard.press('End')
                await expect(page.get_by_role('tab', name='Events')).to_have_attribute('aria-selected', 'true')
                await page.reload()
                await expect(page.locator('.run-id')).to_have_text(run_id, timeout=15000)
                await expect(page.locator('.run-metadata')).to_contain_text(expected.capitalize(), timeout=15000)
                with sqlite3.connect(state / 'history.sqlite3') as database:
                    for table in ('runs', 'events', 'artifacts'):
                        assert_private(database.execute(f'SELECT * FROM {table}').fetchall())
                log.flush()
                assert_private(log.read_text() if hasattr(log, 'read_text') else Path(log.name).read_text())
                assert not errors, errors
                assert not remote, remote
                print(f'{case}: {expected}, real package, evidence/export/refresh/keyboard/four widths/secret scans passed; headed={HEADED}')
            finally:
                if browser:
                    await browser.close()
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        for case in CASES:
            await verify_case(playwright, *case)
    print('Complete remaining Studio UI acceptance matrix passed; no live services used.')


if __name__ == '__main__':
    asyncio.run(main())
