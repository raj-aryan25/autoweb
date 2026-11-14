"""Real controlled OTP/magic-link workers and producer-registered safe evidence."""

import json
from unittest.mock import patch

import pytest

from autoweb.login import LoginError, authenticated_session
from autoweb.login.progress import ProgressObserver
from autoweb_api.configuration import controlled_site, prepare_run
from autoweb_api.contracts import RunOverrides, StartRun
from tests.fixtures.api_support import configure, finished, local_client

pytestmark = pytest.mark.asyncio

SECRET_VALUES = ("test@example.test", "test-password", "001234", "token=", "challenge=", "autoweb_test_session=")


def assert_sanitized(value):
    text = json.dumps(value, default=str)
    for secret in SECRET_VALUES:
        assert secret not in text


@pytest.mark.parametrize("profile_id,kind", [("controlled-otp", "otp"), ("controlled-magic-link", "magic_link")])
async def test_controlled_worker_persists_real_verification_and_safe_report(tmp_path, profile_id, kind):
    configure(tmp_path)
    async with local_client(tmp_path) as (client, service, _):
        profiles = (await client.get('/api/profiles')).json()
        descriptor = next(item for item in profiles if item['id'] == profile_id)
        assert descriptor['readiness'] == 'unverified' and descriptor['steps']
        body = {'profile_id': profile_id, 'overrides': {'headless': True, 'verification_timeout_seconds': 3}}
        # Listing/preflight cannot construct runtime providers or initiate OAuth/browser work.
        with patch('autoweb.testing.studio.FixtureVerificationProvider', side_effect=AssertionError('runtime only')), \
             patch('autoweb.gmail.provider.open_gmail_provider', side_effect=AssertionError('no Gmail')):
            assert (await client.post('/api/preflight', json=body)).json()['status'] == 'ready'
        response = await client.post('/api/runs', json={**body, 'request_id': 'verification-success'})
        assert response.status_code == 202
        run_id = response.json()['snapshot']['run_id']
        record = await finished(client, run_id)
        assert record['snapshot']['state'] == record['authentication'] == 'succeeded'
        assert record['cleanup_verified'] and record['snapshot']['outcome']['ai_usage'] is None
        replay = service.store.replay(run_id, 0)
        events = [event.model_dump(mode='json') for event in replay.events]
        waits = [event for event in events if event['payload']['type'] == 'verification_wait']
        assert [event['payload']['status'] for event in waits] == ['started', 'received']
        assert all(event['payload']['kind'] == kind for event in waits)
        consumer = next(step for step in descriptor['steps'] if step.get('phases'))
        assert {event['step_id'] for event in waits} == {consumer['id']}
        requests = [event for event in events if event['payload']['type'] == 'step_completed'
                    and event['payload']['action'] in {'request_otp', 'request_magic_link'}]
        assert len(requests) == 1
        assert requests[0]['sequence'] < waits[0]['sequence']
        artifacts = [event['payload'] for event in events if event['payload']['type'] == 'artifact_registered']
        assert artifacts == [{'type': 'artifact_registered', 'artifact_id': 'run-report', 'kind': 'sanitized_report'}]
        assert record['artifacts'][0]['artifact_id'] == 'run-report'
        response = await client.get(f'/api/runs/{run_id}/artifacts/run-report')
        assert response.status_code == 200
        report = response.json()
        assert report['execution_state'] == report['authentication'] == 'succeeded'
        assert report['cleanup'] == 'await_guardian_snapshot'
        assert_sanitized([record, events, report])
        for row in service.store.connection.execute('SELECT record FROM runs'):
            assert_sanitized(json.loads(row[0]))
        for table in ('runs', 'events', 'artifacts'):
            for row in service.store.connection.execute(f'SELECT * FROM {table}'):
                assert_sanitized(tuple(row))
        artifact = service.settings.artifacts / run_id / 'evidence/report.json'
        assert artifact.stat().st_mode & 0o077 == 0
        # A private receipt or raw trace has no registered route.
        assert (await client.get(f'/api/runs/{run_id}/artifacts/trace')).status_code == 404


@pytest.mark.parametrize('profile_id', ['controlled-otp', 'controlled-magic-link'])
async def test_controlled_challenge_uses_one_real_request_and_acknowledges_only_success(tmp_path, profile_id):
    configure(tmp_path)
    request = StartRun(request_id='fixture-boundary', profile_id=profile_id,
                       overrides=RunOverrides(headless=True, verification_timeout_seconds=3))
    prepared = prepare_run(request, tmp_path, tmp_path / 'private')
    with controlled_site(prepared) as (configuration, provider):
        with patch('autoweb.gmail.provider.open_gmail_provider', side_effect=AssertionError('must use injected provider')):
            async with authenticated_session(configuration, provider):
                pass
        assert len(provider.requests) == len(provider.acknowledged) == 1
        assert_sanitized(configuration.site.name)


async def test_timeout_has_no_ack_and_masked_failure_capture(tmp_path):
    configure(tmp_path)
    request = StartRun(request_id='fixture-timeout', profile_id='controlled-otp',
                       overrides=RunOverrides(headless=True, verification_timeout_seconds=1))
    prepared = prepare_run(request, tmp_path, tmp_path / 'private')
    with controlled_site(prepared) as (configuration, provider):
        provider.delivery_seconds = 3
        observer = ProgressObserver()
        with pytest.raises(LoginError) as failure:
            async with authenticated_session(configuration, provider, observer=observer):
                pytest.fail('delayed mail must time out')
        assert failure.value.result.reason == 'verification_timeout'
        assert not provider.acknowledged
        assert failure.value.result.artifacts
        # No secret is transported by observation, even when the timeout occurred.
        assert_sanitized([event.__dict__ for event in observer.drain()])


async def test_real_failure_registers_only_masked_png_and_safe_report(tmp_path):
    configure(tmp_path)
    profile = tmp_path / 'configs/sites/local-password.example.json'
    data = json.loads(profile.read_text())
    data['steps'].insert(3, {'action': 'wait_for', 'target': {'by': 'css', 'value': '#never-ready'}, 'timeout_ms': 200})
    profile.write_text(json.dumps(data))
    async with local_client(tmp_path) as (client, service, _):
        response = await client.post('/api/runs', json={'request_id': 'failure-evidence', 'profile_id': 'controlled-password',
            'overrides': {'headless': True}})
        assert response.status_code == 202
        run_id = response.json()['snapshot']['run_id']
        record = await finished(client, run_id)
        assert record['snapshot']['state'] == 'failed' and record['cleanup_verified']
        assert record['snapshot']['outcome']['diagnostic'] == 'step_timeout'
        events = [event.model_dump(mode='json') for event in service.store.replay(run_id, 0).events]
        artifacts = [event['payload']['artifact_id'] for event in events if event['payload']['type'] == 'artifact_registered']
        assert artifacts == ['run-report', 'failure-screenshot']
        screenshot = await client.get(f'/api/runs/{run_id}/artifacts/failure-screenshot')
        assert screenshot.status_code == 200 and screenshot.headers['content-type'] == 'image/png'
        assert screenshot.content.startswith(b'\x89PNG\r\n\x1a\n')
        report = await client.get(f'/api/runs/{run_id}/artifacts/run-report')
        assert report.json()['diagnostic'] == 'step_timeout'
        assert_sanitized([record, events, report.json()])
        for secret in SECRET_VALUES:
            assert secret.encode() not in screenshot.content
        # Locally captured raw package filenames are never registered as IDs.
        raw = next((service.settings.artifacts / run_id / 'login-failures').glob('*.png'))
        assert (await client.get(f'/api/runs/{run_id}/artifacts/{raw.stem}')).status_code == 404


@pytest.mark.parametrize('profile_id', ['controlled-password', 'controlled-otp', 'controlled-magic-link'])
async def test_controlled_session_reuse_and_logout_revokes_old_cookie_before_teardown(tmp_path, profile_id):
    configure(tmp_path)
    request = StartRun(request_id='session-acceptance', profile_id=profile_id,
                       overrides=RunOverrides(headless=True, verification_timeout_seconds=5))
    prepared = prepare_run(request, tmp_path, tmp_path / 'private')
    with controlled_site(prepared) as (configuration, provider):
        async with authenticated_session(configuration, provider) as result:
            page = result.session.page
            origin = configuration.site.entry_url.split('/', 3)[:3]
            base = '/'.join(origin)
            context = page.context
            previous = await context.cookies()
            # A second page reuses the same authenticated browser session.
            other = await context.new_page()
            await other.goto(base + '/protected')
            assert await other.get_by_test_id('account-menu').is_visible()
            assert (await context.request.get(base + '/api/protected')).status == 200
            assert (await context.request.post(base + '/logout')).status == 200
            assert (await context.request.get(base + '/api/protected')).status == 401
            # Replaying the formerly valid cookie also fails: this is server
            # revocation, rather than only clearing this browser's cookie jar.
            await context.add_cookies(previous)
            assert (await context.request.get(base + '/api/protected')).status == 401
            await other.goto(base + '/protected')
            assert '/protected' not in other.url
            assert not page.is_closed()  # Checks happened before owned teardown.
        assert page.is_closed()
