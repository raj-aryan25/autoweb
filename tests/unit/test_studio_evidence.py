"""Only fixed producer names and closed result fields may cross evidence boundary."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoweb_api.contracts import AIUsage, RunEvent, StartRun
from autoweb_api.evidence import approved_artifact, publish_evidence
from autoweb_api.storage import RunStore


def objects(classification='controlled'):
    events = SimpleNamespace(authentication='failed', emit=Mock())
    prepared = SimpleNamespace(registration=SimpleNamespace(id='controlled-otp', classification=classification))
    return events, prepared


@pytest.mark.parametrize('usage', [None, AIUsage(calls=0, prompt_tokens=0, candidate_tokens=0, total_tokens=0),
                                   AIUsage(calls=2, prompt_tokens=4, candidate_tokens=3, total_tokens=7)])
def test_reports_only_closed_fields_and_preserves_null_vs_actual_zero(tmp_path, usage):
    events, prepared = objects()
    failure = SimpleNamespace(artifacts=(), reason='private-code-001234', session='secret-cookie', site='private@email.test')
    publish_evidence(tmp_path, events, prepared, 'failed', 'verification_timeout', usage, failure)
    path = tmp_path / 'evidence/report.json'
    data = json.loads(path.read_text())
    assert data['ai_usage'] == (usage.model_dump() if usage else None)
    assert data['diagnostic'] == 'verification_timeout'
    assert 'private' not in path.read_text() and 'cookie' not in path.read_text()
    assert path.stat().st_mode & 0o077 == 0
    assert events.emit.call_count == 1


@pytest.mark.parametrize('case', ['live', 'missing', 'symlink', 'outside', 'not_png'])
def test_unproven_screenshots_remain_unregistered(tmp_path, case):
    events, prepared = objects('live' if case == 'live' else 'controlled')
    folder = tmp_path / 'login-failures'
    folder.mkdir()
    source = folder / 'failure.png'
    if case == 'outside':
        source = tmp_path / 'other.png'
    if case != 'missing':
        source.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic' if case != 'not_png' else b'private-body')
    if case == 'symlink':
        source.unlink()
        target = tmp_path / 'private.png'
        target.write_bytes(b'\x89PNG\r\n\x1a\nprivate')
        source.symlink_to(target)
    publish_evidence(tmp_path, events, prepared, 'failed', 'verification_timeout', None,
                     SimpleNamespace(artifacts=(source,)))
    assert not (tmp_path / 'evidence/failure.png').exists()
    assert events.emit.call_count == 1  # Structured report is still useful.


def test_controlled_masked_failure_can_be_registered(tmp_path):
    events, prepared = objects()
    source = tmp_path / 'login-failures/failure.png'
    source.parent.mkdir()
    data = b'\x89PNG\r\n\x1a\nsynthetic-controlled-mask'
    source.write_bytes(data)
    publish_evidence(tmp_path, events, prepared, 'failed', 'verification_timeout', None,
                     SimpleNamespace(artifacts=(source,)))
    assert (tmp_path / 'evidence/failure.png').read_bytes() == data
    assert events.emit.call_args.args[0]['artifact_id'] == 'failure-screenshot'


@pytest.mark.parametrize('artifact_id,kind', [('trace', 'masked_screenshot'), ('run-report', 'masked_screenshot'),
                                             ('../raw', 'sanitized_report')])
def test_only_fixed_evidence_ids_and_matched_kinds(artifact_id, kind):
    with pytest.raises((ValueError, KeyError)):
        approved_artifact(artifact_id, kind)


def test_registration_and_event_are_one_transaction_and_missing_capture_is_consumed(tmp_path):
    store = RunStore(tmp_path / 'runs.sqlite3')
    store.open()
    try:
        store.reserve('evidence-run', StartRun(request_id='evidence-request', profile_id='controlled-otp'))
        event = RunEvent(schema_version=1, run_id='evidence-run', sequence=1,
                         timestamp='2026-11-12T00:00:00Z', elapsed_seconds=1,
                         step_id='outcome', phase_id='outcome', payload={
                             'type': 'artifact_registered', 'artifact_id': 'run-report', 'kind': 'sanitized_report'})
        with pytest.raises(ValueError):
            store.worker_event(event, artifact_path='private.json')
        with pytest.raises(KeyError):
            store.artifact('evidence-run', 'run-report')
        store.worker_event(event, artifact_path='evidence/report.json')
        assert store.artifact('evidence-run', 'run-report') == ('evidence/report.json', 'sanitized_report')
        assert store.record('evidence-run').artifacts[0].artifact_id == 'run-report'
        # A replayed observation must not duplicate artifact registration.
        store.worker_event(event, artifact_path='evidence/report.json')
        missing = event.model_copy(update={'sequence': 2, 'payload': type(event.payload)(
            type='artifact_registered', artifact_id='failure-screenshot', kind='masked_screenshot')})
        before = store.record('evidence-run').snapshot.cursor
        store.worker_event(missing)
        assert store.record('evidence-run').snapshot.cursor == before
        assert len(store.record('evidence-run').artifacts) == 1
        with pytest.raises(KeyError):
            store.artifact('evidence-run', 'failure-screenshot')
    finally:
        store.close()
