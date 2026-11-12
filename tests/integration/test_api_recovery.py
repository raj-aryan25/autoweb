"""Recovery evidence is durable; uncertain cleanup never becomes permission to rerun."""

import json

import pytest

pytest.importorskip("fastapi")

from autoweb_api.contracts import AIUsage, FinalOutcome, StartRun
from autoweb_api.guardian import write_receipt
from autoweb_api.process_tree import ProcessIdentity
from autoweb_api.protocol import RecoveryReceipt
from autoweb_api.storage import RunStore
from tests.fixtures.api_support import configure, local_client, start_body

pytestmark = pytest.mark.asyncio
FAKE_PID = 2147483647


def unfinished(root, *, owner=True):
    state = root / "state"
    store = RunStore(state / "history.sqlite3")
    store.open()
    store.reserve("unfinished-run", StartRun(request_id="unfinished-request", profile_id="controlled-password"))
    if owner:
        store.owner("unfinished-run", ProcessIdentity(FAKE_PID, 0, 1, "R"))
    store.close()
    return state


async def test_unlaunched_reservation_is_interrupted_without_execution(tmp_path):
    configure(tmp_path)
    state = unfinished(tmp_path, owner=False)
    async with local_client(tmp_path, state=state) as (client, service, app):
        record = (await client.get("/api/runs/unfinished-run")).json()
        assert record["snapshot"]["state"] == "interrupted" and record["cleanup_verified"]
        assert record["snapshot"]["outcome"]["diagnostic"] == "service_restart"
        assert service.store.page().total == 1 and not service.tasks
        assert service.coordinator.active is None and not service.store.active_id()


async def test_valid_receipt_preserves_observed_authentication_and_ai_counts(tmp_path):
    configure(tmp_path)
    state = unfinished(tmp_path)
    usage = AIUsage(calls=1, prompt_tokens=10, candidate_tokens=3, total_tokens=13)
    write_receipt(state / "receipts/unfinished-run.json", RecoveryReceipt(
        run_id="unfinished-run", guardian_pid=FAKE_PID, guardian_started=1, forced=False,
        outcome=FinalOutcome(type="final_outcome", state="succeeded", authentication="succeeded", ai_usage=usage)))
    async with local_client(tmp_path, state=state) as (client, service, app):
        record = (await client.get("/api/runs/unfinished-run")).json()
        assert record["snapshot"]["state"] == "interrupted"
        assert record["snapshot"]["outcome"]["authentication"] == "succeeded"
        assert record["snapshot"]["outcome"]["ai_usage"] == usage.model_dump()
        assert record["cleanup_verified"] and not record["active"]
        assert not service.tasks and service.store.page().total == 1


@pytest.mark.parametrize("receipt", ["missing", "corrupt", "wrong_pid", "wrong_run", "symlink"])
async def test_unverified_cleanup_stays_blocked_across_restarts(tmp_path, receipt):
    configure(tmp_path)
    state = unfinished(tmp_path)
    path = state / "receipts/unfinished-run.json"
    path.parent.mkdir(exist_ok=True)
    value = RecoveryReceipt(run_id="wrong-run" if receipt == "wrong_run" else "unfinished-run",
                            guardian_pid=FAKE_PID - 1 if receipt == "wrong_pid" else FAKE_PID,
                            guardian_started=1, forced=False,
                            outcome=FinalOutcome(type="final_outcome", state="failed", authentication="not_observed"))
    if receipt in {"wrong_pid", "wrong_run"}:
        write_receipt(path, value)
    elif receipt == "corrupt":
        path.write_text('{"secret":"private-secret"}')
    elif receipt == "symlink":
        target = tmp_path / "untrusted-receipt.json"
        target.write_text(value.model_dump_json())
        path.symlink_to(target)
    for _ in range(2):
        async with local_client(tmp_path, state=state) as (client, service, app):
            health = (await client.get("/api/health")).json()
            assert health["cleanup_required"] and not health["execution_available"]
            record = (await client.get("/api/runs/unfinished-run")).json()
            assert record["snapshot"]["state"] == "interrupted" and record["active"]
            assert record["cleanup_required"] and not record["cleanup_verified"]
            assert "private-secret" not in json.dumps(record)
            response = await client.post("/api/runs", json=start_body("new-request"))
            assert response.status_code == 409 and response.json()["code"] == "cleanup_required"
            assert not service.tasks and service.store.page().total == 1
