"""Fault injection for the actual guardian, including detached/late descendants."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from autoweb_api.contracts import RunOverrides, StartRun
from autoweb_api.process_tree import ProcessIdentity, supported
from autoweb_api.worker import WorkerCoordinator

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not supported(), reason="Linux containment required")]


@pytest.mark.parametrize("mode", ["ignore", "late", "crash", "flood", "auth"])
async def test_guardian_reaps_uncooperative_detached_descendants(tmp_path, mode):
    record = tmp_path / "owned-pids.jsonl"

    class FaultCoordinator(WorkerCoordinator):
        def _command(self, limits):
            return [sys.executable, str(ROOT / "tests/fixtures/worker_faults.py"), "guardian", mode, str(record)]

    owner = FaultCoordinator(ROOT)
    unrelated = await asyncio.create_subprocess_exec("sleep", "30")
    try:
        handle = await owner.start(StartRun(request_id="fault-check", profile_id="controlled-password",
                                           overrides=RunOverrides(headless=True)))
        async def ready():
            cursor = 0
            while (event := await handle.next_event(cursor)) is not None:
                cursor = event.sequence
                if event.payload.type == ("authentication" if mode == "auth" else "run_state_changed"):
                    if mode == "auth" or event.payload.state == "running":
                        return
        await asyncio.wait_for(ready(), 5)
        if mode in {"crash", "flood"}:
            result = await asyncio.wait_for(handle.wait(), 10)
            assert result.outcome.state == "interrupted"
        else:
            await asyncio.sleep(0.2)
            result = await asyncio.wait_for(handle.stop(), 10)
            assert result.outcome.state == "cancelled"
        assert result.forced_cleanup
        assert result.outcome.authentication == ("succeeded" if mode == "auth" else "not_observed")
        assert handle.cleanup_verified and owner.active is None
        identities = [json.loads(line) for line in record.read_text().splitlines()]
        unique = {(item["pid"], item["started"]) for item in identities}
        assert len(unique) >= 2
        if mode == "late":
            assert len(unique) > 2
        assert all(not ProcessIdentity(item["pid"], 0, item["started"], "R").alive() for item in identities)
        assert not any(identity.alive() for identity in handle.tracked.values())
        assert unrelated.returncode is None
        assert "private-secret" not in repr(handle.events)
        assert len([event for event in handle.events if event.payload.type == "final_outcome"]) == 1
    finally:
        unrelated.terminate()
        await unrelated.wait()
        if owner.active is not None and not owner.active.cleanup_uncertain:
            await owner.close()
