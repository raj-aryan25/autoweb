"""Smoke-test the local UI only; never calls AutoWeb or an external website."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / '.impeccable' / 'review' / 'studio'
URL = os.environ.get('AUTOWEB_UI_URL', 'http://127.0.0.1:5173/')
if urlparse(URL).hostname not in {'127.0.0.1', 'localhost', '::1'}:
    raise ValueError('UI verification must target a loopback server.')
parts = urlparse(URL)
URL = urlunparse(parts._replace(query=urlencode({**dict(parse_qsl(parts.query)), 'mode': 'demo'})))


async def capture(page, filename):
    await page.evaluate('window.scrollTo(0, 0)')
    await page.screenshot(path=str(OUTPUT / filename), full_page=True)


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
        failures, remote = [], []
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.on('request', lambda request: remote.append(request.url) if urlparse(request.url).hostname not in {'127.0.0.1', 'localhost', '::1', None} else None)
        await page.goto(URL, wait_until='networkidle')
        await expect(page.get_by_role('heading', name='Expand Testing', exact=True)).to_be_visible()
        await page.evaluate('document.fonts.ready')
        for width, name in [(1440, 'desktop'), (1280, 'desktop-1280'), (1024, 'tablet'), (768, 'tablet-768'), (390, 'mobile'), (320, 'mobile-320')]:
            await page.set_viewport_size({'width': width, 'height': 900 if width > 740 else 844})
            await capture(page, f'{name}.png')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal page overflow at {width}'
        await page.set_viewport_size({'width': 1440, 'height': 900})
        await page.get_by_role('tab', name='Evidence').click()
        await expect(page.get_by_role('heading', name='No evidence captured')).to_be_visible()
        await capture(page, 'empty-evidence.png')
        await page.get_by_role('tab', name='Events').click()
        async with page.expect_download() as download_info:
            await page.get_by_role('button', name='Export JSON').click()
        download = await download_info.value
        payload = json.loads(Path(await download.path()).read_text())
        assert payload['demo'] is True and payload['step'] == 'Wait for verification email'
        await page.get_by_role('tab', name='Events').focus()
        await page.keyboard.press('Home')
        await expect(page.get_by_role('tab', name='Details')).to_have_attribute('aria-selected', 'true')
        await page.get_by_role('button', name='Confirm authentication').click()
        await expect(page.get_by_role('heading', name='No events for this step')).to_be_visible()
        await page.get_by_role('button', name='Wait for verification email').click()
        await page.get_by_role('button', name='Continue demo', exact=True).click()
        await expect(page.get_by_text('Demo ended', exact=True)).to_be_visible(timeout=20000)
        await expect(page.locator('.inspector-heading')).to_contain_text('Completed')
        await capture(page, 'success.png')
        await page.get_by_role('button', name='Wait for verification email').click()
        await page.get_by_role('tab', name='Evidence').click()
        await expect(page.get_by_role('heading', name='Message match record')).to_be_visible()
        await page.get_by_role('link', name='Run history').click()
        await expect(page.locator('.history-table')).to_contain_text('DEMO-024')
        await page.get_by_role('button', name='Inspect DEMO-024').click()
        await expect(page.get_by_role('heading', name='Expand Testing', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Run again', exact=True).click()
        await page.get_by_role('button', name='Stop demo', exact=True).click()
        await expect(page.locator('.inspector-heading')).to_contain_text('Stopped')
        await capture(page, 'cancelled.png')
        await page.get_by_role('link', name='Run history').click()
        await page.get_by_label('Search runs').fill('no-matching-profile')
        await expect(page.get_by_role('heading', name='No matching runs')).to_be_visible()
        await page.get_by_role('button', name='Clear filters').click()
        await expect(page.locator('.history-table tbody tr')).to_have_count(2)
        await page.get_by_role('link', name='Workspace', exact=True).first.click()
        await page.get_by_role('button', name='Run configuration').click()
        await page.get_by_label('Demo outcome').select_option('timeout')
        await page.get_by_label('Email timeout (seconds)').fill('0')
        await page.get_by_role('button', name='Apply configuration').click()
        await expect(page.get_by_role('heading', name='Run configuration', exact=True)).to_be_visible()
        await page.get_by_label('Email timeout (seconds)').fill('5')
        await page.get_by_label('Slow motion (ms)').fill('0')
        await capture(page, 'configuration.png')
        await page.get_by_role('button', name='Apply configuration').click()
        await page.get_by_role('button', name='Start demo', exact=True).click()
        await expect(page.get_by_role('heading', name='Verification email timed out')).to_be_visible(timeout=20000)
        await capture(page, 'timeout.png')
        await page.set_viewport_size({'width': 390, 'height': 844})
        await capture(page, 'mobile-timeout.png')
        await page.get_by_role('link', name='Site profiles').click()
        await page.locator('.profile-item').filter(has_text='Local password demo').get_by_role('button', name='Use profile').click()
        await expect(page.get_by_role('heading', name='Local password demo', exact=True)).to_be_visible()
        await expect(page.locator('.step-list > li')).to_have_count(5)
        await page.get_by_role('link', name='Connections').click()
        await expect(page.get_by_text('Not connected in this UI', exact=True)).to_have_count(3)
        await page.get_by_role('link', name='Settings', exact=True).click()
        await expect(page.get_by_label('Browser engine')).to_be_visible()
        await page.get_by_label('Browser engine').select_option('Firefox')
        await page.get_by_role('button', name='Apply configuration').click()
        await expect(page.locator('.run-metadata')).to_contain_text('Firefox')
        await page.get_by_role('button', name='Open login page').click()
        await expect(page.locator('.inspector-heading')).to_contain_text('Open login page')
        await capture(page, 'mobile-selected.png')
        assert not failures, failures
        assert not remote, f'Unexpected external requests: {remote}'
        await browser.close()
        print('UI smoke checks passed: responsive layouts, inspection tabs, keyboard navigation, export, success, stop, history/filtering, timeout, profiles, settings, and no external requests.')


if __name__ == '__main__':
    asyncio.run(main())
