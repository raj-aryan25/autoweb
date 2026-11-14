"""Owned loopback Studio acceptance: UI -> API -> controlled password package.

Run only through the delegated test runner. Set AUTOWEB_STUDIO_HEADED=1 to
exercise the default headed executor on a desktop. Never selects live profiles.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / '.impeccable/review/studio-local'
HEADED = os.environ.get('AUTOWEB_STUDIO_HEADED') == '1'


async def capture(page, filename):
    await page.evaluate('window.scrollTo(0, 0)')
    await page.screenshot(path=str(OUTPUT / filename), full_page=True)


async def wait_record(page, run_id, terminal=True):
    for _ in range(150):
        response = await page.request.get(f'/api/runs/{run_id}')
        value = await response.json()
        if not terminal or not value['active']:
            return value
        await asyncio.sleep(.2)
    raise AssertionError(f'Run did not finalize: {run_id}')


async def configure(page, slow=200, profile_id='controlled-password'):
    await expect(page.get_by_role('button', name='Run configuration', exact=True)).to_be_enabled(timeout=15000)
    await page.get_by_role('button', name='Run configuration', exact=True).click()
    await page.get_by_label('Site profile', exact=True).select_option(profile_id)
    await page.get_by_label('Browser', exact=True).select_option('false' if HEADED else 'true')
    await page.get_by_label('Slow motion (ms)', exact=True).fill(str(slow))
    await page.get_by_role('button', name='Apply configuration', exact=True).click()


async def selected_id(page):
    await expect(page.locator('.run-id')).not_to_have_text('New run', timeout=15000)
    return await page.locator('.run-id').inner_text()


def assert_private(value):
    text = json.dumps(value)
    for secret in ('test@example.test', 'test-password', '001234', 'token=', 'autoweb_test_session=', 'challenge='):
        assert secret not in text, f'Sensitive fixture value leaked: {secret}'


async def verify_verification_profiles(page, starts):
    for profile_id, action, title in [('controlled-otp', 'fill_otp', 'Fill otp'),
                                       ('controlled-magic-link', 'follow_magic_link', 'Follow magic link')]:
        await configure(page, 100, profile_id)
        await page.get_by_role('button', name='Start run', exact=True).click()
        run_id = await selected_id(page)
        record = await wait_record(page, run_id)
        assert record['snapshot']['state'] == record['authentication'] == 'succeeded'
        await expect(page.locator('.run-metadata')).to_contain_text('Succeeded', timeout=15000)
        await page.locator('.step-title').filter(has_text=title).click()
        consumer = next(step for step in record['manifest']['steps'] if step.get('action') == action)
        await page.get_by_role('tab', name='Events').click()
        await expect(page.locator('.events-table')).to_contain_text('Verification received', timeout=10000)
        async with page.expect_download() as download_info:
            await page.get_by_role('button', name='Export JSON', exact=True).click()
        exported = json.loads(Path(await (await download_info.value).path()).read_text())
        assert exported['step_id'] == consumer['id']
        waits = [event for event in exported['events'] if event['payload']['type'] == 'verification_wait']
        assert [event['payload']['status'] for event in waits] == ['started', 'received']
        assert_private([record, exported])
        await page.locator('.step-title').filter(has_text='Final outcome').click()
        await page.get_by_role('tab', name='Evidence').click()
        report_link = page.get_by_role('link', name='Sanitized report')
        await expect(report_link).to_be_visible()
        report = await (await page.request.get(await report_link.get_attribute('href'))).json()
        assert report['execution_state'] == report['authentication'] == 'succeeded'
        assert report['ai_usage'] is None
        assert_private(report)
        for width in [1440, 768, 390, 320]:
            await page.set_viewport_size({'width': width, 'height': 900})
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{profile_id} overflow {width}'
            await capture(page, f'{profile_id}-{width}.png')
        await page.set_viewport_size({'width': 1440, 'height': 900})
    # Stop at an actual observed verification wait, without sending another request.
    await configure(page, 0, 'controlled-otp')
    await page.get_by_role('button', name='Start run', exact=True).click()
    run_id = await selected_id(page)
    await expect(page.locator('.step.waiting')).to_be_visible(timeout=15000)
    await page.get_by_role('button', name='Stop run', exact=True).evaluate('(button) => button.click()')
    record = await wait_record(page, run_id)
    assert record['snapshot']['state'] == 'cancelled' and record['cleanup_verified']
    await expect(page.locator('.run-metadata')).to_contain_text('Cancelled', timeout=15000)
    await capture(page, 'verification-stopped.png')
    assert_private(record)
    assert all(request['profile_id'].startswith('controlled-') for request in starts)


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='autoweb-studio-ui-') as state:
        with (OUTPUT / 'server.log').open('w+') as log:
            server = subprocess.Popen([sys.executable, '-m', 'autoweb_api.cli', '--root', str(ROOT), '--port', str(port), '--data-directory', state, '--ui-directory', str(ROOT / 'apps/web/dist')], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                async with httpx.AsyncClient(trust_env=False) as client:
                    for _ in range(150):
                        try:
                            if (await client.get(url + '/api/health')).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert server.poll() is None, 'Owned API exited; see server.log'
                        await asyncio.sleep(.1)
                    else:
                        raise AssertionError('Owned API did not become ready')
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    context = await browser.new_context(base_url=url, viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                    page = await context.new_page()
                    errors, remote, starts = [], [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: remote.append(request.url) if not request.url.startswith(url) and not request.url.startswith(('data:', 'blob:')) else None)
                    page.on('request', lambda request: starts.append(request.post_data_json) if request.method == 'POST' and request.url == url + '/api/runs' else None)
                    await page.goto('/')
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · connected', timeout=15000)
                    await expect(page.get_by_role('heading', name='Controlled password', exact=True)).to_be_visible()
                    for width in [1440, 768, 390, 320]:
                        await page.set_viewport_size({'width': width, 'height': 900})
                        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width}'
                        await capture(page, f'local-{width}.png')
                    await page.set_viewport_size({'width': 1440, 'height': 900})
                    await configure(page)
                    # Two synchronous clicks exercise the immediate ref guard, before React rerenders.
                    await page.get_by_role('button', name='Start run', exact=True).evaluate('(button) => { button.click(); button.click(); }')
                    first = await selected_id(page)
                    await page.reload()
                    await expect(page.locator('.run-id')).to_have_text(first, timeout=15000)
                    result = await wait_record(page, first)
                    assert result['snapshot']['state'] == 'succeeded', result
                    assert result['authentication'] == 'succeeded' and result['cleanup_verified']
                    await expect(page.locator('.run-metadata')).to_contain_text('Succeeded', timeout=15000)
                    assert len(starts) == 1, starts
                    async with page.expect_download() as download_info:
                        await page.get_by_role('button', name='Export run JSON', exact=True).click()
                    export = json.loads(Path(await (await download_info.value).path()).read_text())
                    assert export['record']['snapshot'] == result['snapshot']
                    assert export['events'] and [event['sequence'] for event in export['events']] == sorted({event['sequence'] for event in export['events']})
                    assert all(event['timestamp'] and event['run_id'] == first for event in export['events'])
                    assert export['events'][-1]['payload']['type'] == 'final_outcome'
                    assert export['record']['manifest']['effective']['browser']['headless'] is (not HEADED)
                    await page.locator('.step-list button').first.click()
                    await expect(page.get_by_label('Follow active step')).not_to_be_checked()
                    await page.get_by_role('tab', name='Details', exact=True).focus()
                    await page.keyboard.press('End')
                    await expect(page.get_by_role('tab', name='Events')).to_have_attribute('aria-selected', 'true')
                    await page.get_by_role('tab', name='Evidence').click()
                    await expect(page.get_by_role('heading', name='No evidence captured')).to_be_visible()
                    await page.get_by_label('Follow active step').check()
                    await capture(page, 'success.png')
                    await page.get_by_role('link', name='Run history').click()
                    await expect(page.locator('.history-table')).to_contain_text(first)
                    await page.reload()
                    await expect(page.locator('.history-table')).to_contain_text(first, timeout=15000)
                    await page.get_by_role('button', name='Inspect', exact=True).first.click()
                    await expect(page.locator('.run-id')).to_have_text(first)
                    # Deliberately cut the initial stream: execution remains server-owned.
                    cut = False
                    async def cut_stream(route):
                        nonlocal cut
                        if not cut:
                            cut = True
                            await route.abort()
                        else:
                            await route.continue_()
                    await page.route('**/api/runs/*/events?*', cut_stream)
                    await configure(page, 1000)
                    await page.get_by_role('button', name='Start run', exact=True).click()
                    second = await selected_id(page)
                    await expect(page.get_by_role('button', name='Stop run', exact=True)).to_be_enabled()
                    await page.get_by_role('button', name='Stop run', exact=True).click()
                    stopped = await wait_record(page, second)
                    assert stopped['snapshot']['state'] == 'cancelled' and stopped['cleanup_verified'], stopped
                    await expect(page.locator('.run-metadata')).to_contain_text('Cancelled', timeout=15000)
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · connected', timeout=15000)
                    assert len(starts) == 2 and first != second
                    await page.unroute('**/api/runs/*/events?*', cut_stream)
                    # Backend accepts a start, then its HTTP response is lost. Retrying uses the same key.
                    lost_response = False
                    async def lose_start(route):
                        nonlocal lost_response
                        if route.request.method == 'POST' and not lost_response:
                            lost_response = True
                            await route.fetch()
                            await route.abort()
                        else:
                            await route.continue_()
                    await page.route('**/api/runs', lose_start)
                    await configure(page, 500)
                    await page.get_by_role('button', name='Start run', exact=True).click()
                    await expect(page.get_by_role('heading', name='Start request awaiting confirmation')).to_be_visible(timeout=15000)
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · disconnected', timeout=15000)
                    await page.get_by_role('button', name='Reconnect', exact=True).click()
                    await expect(page.get_by_role('button', name='Resolve start', exact=True)).to_be_enabled(timeout=15000)
                    await page.get_by_role('button', name='Resolve start', exact=True).click()
                    third = await selected_id(page)
                    accepted = await wait_record(page, third)
                    assert accepted['snapshot']['state'] == 'succeeded', accepted
                    assert starts[-1]['request_id'] == starts[-2]['request_id'] and len(starts) == 4
                    await page.unroute('**/api/runs', lose_start)
                    await expect(page.get_by_role('button', name='Run configuration', exact=True)).to_be_enabled(timeout=15000)
                    # Explicit resync discards the event projection and restores the authoritative snapshot.
                    resync = False
                    async def force_resync(route):
                        nonlocal resync
                        if not resync:
                            resync = True
                            await route.fulfill(status=200, content_type='text/event-stream', body='event: resync_required\ndata: {}\n\n')
                        else:
                            await route.continue_()
                    await page.route('**/api/runs/*/events?*', force_resync)
                    await page.reload()
                    await expect(page.locator('.run-metadata')).to_contain_text('Succeeded', timeout=15000)
                    await page.get_by_role('tab', name='Events').click()
                    await expect(page.get_by_text('Earlier events expired.', exact=False)).to_be_visible(timeout=15000)
                    await page.get_by_role('tab', name='Evidence').click()
                    await expect(page.get_by_role('link', name='Sanitized report')).to_be_visible()
                    assert len(starts) == 4
                    await page.unroute('**/api/runs/*/events?*', force_resync)
                    # A graceful API shutdown control event also requests reconnection.
                    shutdown = False
                    async def force_shutdown(route):
                        nonlocal shutdown
                        if not shutdown:
                            shutdown = True
                            await route.fulfill(status=200, content_type='text/event-stream', body='event: stream_end\ndata: {"code":"service_shutdown"}\n\n')
                        else:
                            await route.continue_()
                    await page.route('**/api/runs/*/events?*', force_shutdown)
                    await page.reload()
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · reconnecting', timeout=15000)
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · connected', timeout=15000)
                    await expect(page.locator('.run-id')).to_have_text(third)
                    assert len(starts) == 4
                    await page.unroute('**/api/runs/*/events?*', force_shutdown)
                    # Unavailable runner is an error state, with no simulated replacement.
                    await page.route('**/api/**', lambda route: route.abort())
                    await page.get_by_role('link', name='Connections', exact=True).click()
                    await page.get_by_role('button', name='Reconnect to local runner').click()
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · disconnected', timeout=15000)
                    await page.get_by_role('link', name='Workspace', exact=True).click()
                    await expect(page.get_by_role('button', name='Run again', exact=True)).to_be_disabled()
                    await expect(page.locator('.run-id')).to_have_text(third)
                    await expect(page.locator('.run-metadata')).to_contain_text('Succeeded')
                    await capture(page, 'disconnected.png')
                    await page.unroute('**/api/**')
                    await page.get_by_role('button', name='Reconnect', exact=True).click()
                    await expect(page.locator('.demo-badge')).to_have_text('Local runner · connected', timeout=15000)
                    history = await (await page.request.get('/api/runs')).json()
                    assert history['total'] == 3 and history['active_run_id'] is None, history
                    if os.environ.get('AUTOWEB_STUDIO_VERIFICATION') == '1':
                        await verify_verification_profiles(page, starts)
                        print('Step 7 UI acceptance passed: controlled OTP and magic link, real waits and report evidence, secret-free exports, stop during verification and four responsive widths.')
                    assert not errors, errors
                    assert not remote, remote
                    await browser.close()
                    print(f'Local Studio acceptance passed: {"headed" if HEADED else "headless"} controlled password, double-click guard, refresh, events/export, keyboard/follow, durable history, SSE reconnect, stop cleanup, lost-start idempotency, resync, unavailable/reconnect, four responsive widths, zero external requests.')
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == '__main__':
    asyncio.run(main())
