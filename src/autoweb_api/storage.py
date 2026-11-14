"""Single-writer SQLite journal and transactional snapshots, without external clients."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .api_contracts import RunPage, RunRecord
from .contracts import FinalOutcome, Replay, RunEvent, RunSnapshot, StartRun, StepSnapshot
from .protocol import Manifest

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class Conflict(RuntimeError):
    def __init__(self, code="run_active"):
        self.code = code
        super().__init__(code)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(request):
    return hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class RunStore:
    """Use only from the owning service loop; open/close belong to its lifespan."""

    def __init__(self, path: Path, retention=1024):
        if type(retention) is not int or not 1 <= retention <= 2048:
            raise ValueError("invalid_event_retention")
        self.path, self.retention = Path(path), retention
        self.connection = None
        self._lock = None

    def open(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = open(str(self.path) + ".lock", "a+b")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.connection = sqlite3.connect(self.path, timeout=2)
            os.chmod(self.path, 0o600)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    request TEXT NOT NULL, record TEXT NOT NULL, active INTEGER NOT NULL,
                    worker_cursor INTEGER NOT NULL DEFAULT 0, owner TEXT,
                    tracked TEXT NOT NULL DEFAULT '[]', starts TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active ON runs(active) WHERE active=1;
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL,
                    event TEXT NOT NULL, PRIMARY KEY(run_id,sequence)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL,
                    relative_path TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(run_id,id)
                );
            ''')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _row(self, run_id):
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("run_not_found")
        return row

    def record(self, run_id) -> RunRecord:
        return RunRecord.model_validate_json(self._row(run_id)["record"])

    def active_id(self):
        row = self.connection.execute("SELECT id FROM runs WHERE active=1").fetchone()
        return row[0] if row else None

    def reserve(self, run_id: str, request: StartRun):
        """Commit the idempotency key BEFORE any executor can be launched."""
        with self.connection:
            old = self.connection.execute("SELECT id,fingerprint FROM runs WHERE request_id=?", (request.request_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint(request):
                    raise Conflict("request_id_conflict")
                return self.record(old["id"]), False
            active = self.active_id()
            if active is not None:
                raise Conflict("cleanup_required" if self.record(active).cleanup_required else "run_active")
            now = utc_now()
            snapshot = RunSnapshot(schema_version=1, run_id=run_id, profile_id=request.profile_id,
                                   state="created", cursor=0, steps=[])
            record = RunRecord(snapshot=snapshot, request_id=request.request_id, overrides=request.overrides,
                               created_at=now, updated_at=now, active=True, cleanup_verified=False,
                               cleanup_required=False, authentication="not_observed")
            self.connection.execute("INSERT INTO runs(id,request_id,fingerprint,request,record,active) VALUES(?,?,?,?,?,1)",
                                    (run_id, request.request_id, fingerprint(request), request.model_dump_json(), record.model_dump_json()))
            self._append(run_id, {"type": "run_state_changed", "state": "created"})
            return self.record(run_id), True

    def _save(self, record, **private):
        run_id = record.snapshot.run_id
        record = record.model_copy(update={"updated_at": datetime.now(timezone.utc)})
        self.connection.execute("UPDATE runs SET record=?, active=? WHERE id=?",
                                (record.model_dump_json(), int(record.active), run_id))
        for key, value in private.items():
            if key not in {"starts", "worker_cursor", "owner", "tracked"}:
                raise ValueError("invalid_storage_field")
            self.connection.execute(f"UPDATE runs SET {key}=? WHERE id=?", (value, run_id))

    def manifest(self, run_id, manifest: Manifest):
        with self.connection:
            record = self.record(run_id)
            known = {step.step_id: step for step in record.snapshot.steps}
            steps = [known.get(step.id, StepSnapshot(step_id=step.id, state="pending")) for step in manifest.steps]
            snapshot = record.snapshot.model_copy(update={"steps": steps})
            self._save(record.model_copy(update={"snapshot": snapshot, "manifest": manifest}))

    def _append(self, run_id, payload, *, source=None):
        row = self._row(run_id)
        record = RunRecord.model_validate_json(row["record"])
        previous = self.connection.execute("SELECT event FROM events WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
        elapsed = json.loads(previous[0])["elapsed_seconds"] if previous else 0.0
        event = RunEvent(schema_version=1, run_id=run_id, sequence=record.snapshot.cursor + 1,
                         timestamp=source.timestamp if source else datetime.now(timezone.utc),
                         elapsed_seconds=max(elapsed, source.elapsed_seconds if source else elapsed),
                         step_id=source.step_id if source else "cleanup" if isinstance(payload, dict) and payload.get("type") == "cleanup" else "outcome",
                         phase_id=source.phase_id if source else "service", payload=payload)
        state = record.snapshot.state
        outcome = record.snapshot.outcome
        steps = {step.step_id: step for step in record.snapshot.steps}
        starts = json.loads(row["starts"])
        authentication = record.authentication
        kind = event.payload.type
        if kind == "run_state_changed":
            state = event.payload.state
        elif kind == "final_outcome":
            outcome = event.payload
            state = outcome.state
            authentication = outcome.authentication
            if "outcome" in steps:
                steps["outcome"] = StepSnapshot(step_id="outcome", state=state if state != "interrupted" else "failed")
            for key, step in tuple(steps.items()):
                if step.state == "pending":
                    steps[key] = step.model_copy(update={"state": "not_executed"})
        else:
            step_id = event.step_id
            status = getattr(event.payload, "status", None)
            if kind == "authentication":
                if status == "succeeded":
                    authentication = "succeeded"
                elif status == "failed" and authentication != "succeeded":
                    authentication = "failed"
            if kind not in {"verification_baseline", "acknowledgment", "artifact_registered"} or step_id == kind:
                old = steps.get(step_id, StepSnapshot(step_id=step_id, state="pending"))
                if kind == "step_started":
                    status = "started"
                if kind == "verification_wait":
                    status = {"started": "waiting", "received": "running"}.get(status, status)
                status = {"started": "running", "ready": "succeeded"}.get(status, status)
                if status in {"running", "waiting", "succeeded", "skipped", "failed", "cancelled"}:
                    duration = old.elapsed_seconds
                    if status == "running" and kind != "verification_wait":
                        starts[step_id] = event.elapsed_seconds
                    elif status not in {"running", "waiting"} and step_id in starts:
                        duration = (duration or 0) + max(0, event.elapsed_seconds - starts.pop(step_id))
                    steps[step_id] = StepSnapshot(step_id=step_id, state=status, elapsed_seconds=duration,
                                                  diagnostic=getattr(event.payload, "diagnostic", None))
        snapshot = RunSnapshot(schema_version=1, run_id=run_id, profile_id=record.snapshot.profile_id,
                               state=state, cursor=event.sequence, steps=list(steps.values()), outcome=outcome)
        final = kind == "final_outcome"
        self._save(record.model_copy(update={"snapshot": snapshot, "authentication": authentication,
                                            "active": False if final else record.active,
                                            "cleanup_verified": True if final else record.cleanup_verified,
                                            "cleanup_required": False if final else record.cleanup_required}),
                   starts=json.dumps(starts))
        self.connection.execute("INSERT INTO events VALUES(?,?,?)", (run_id, event.sequence, event.model_dump_json()))
        self.connection.execute("DELETE FROM events WHERE run_id=? AND sequence<=?", (run_id, event.sequence - self.retention))
        return event

    def worker_event(self, event: RunEvent, *, artifact_path=None):
        with self.connection:
            row = self._row(event.run_id)
            cursor = row["worker_cursor"]
            if event.sequence <= cursor:
                return
            if event.sequence != cursor + 1:
                raise ValueError("worker_event_gap")
            record = self.record(event.run_id)
            skip = (event.payload.type == "run_state_changed" and
                    (event.payload.state == "created" or record.snapshot.state == "stopping"))
            if event.payload.type == "artifact_registered":
                from .evidence import approved_artifact

                expected = approved_artifact(event.payload.artifact_id, event.payload.kind)
                skip = artifact_path is None
                if artifact_path is not None:
                    if artifact_path != expected:
                        raise ValueError("unapproved_artifact")
                    self.connection.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (
                        event.run_id, event.payload.artifact_id, artifact_path, event.payload.kind))
                    from .api_contracts import ArtifactDescriptor

                    descriptor = ArtifactDescriptor(**event.payload.model_dump(), step_id=event.step_id)
                    self._save(record.model_copy(update={"artifacts": [*record.artifacts, descriptor]}))
            if not skip:
                self._append(event.run_id, event.payload, source=event)
            self.connection.execute("UPDATE runs SET worker_cursor=? WHERE id=?", (event.sequence, event.run_id))

    def owner(self, run_id, identity, tracked=()):
        with self.connection:
            data = None if identity is None else {"pid": identity.pid, "started": identity.started}
            self.connection.execute("UPDATE runs SET owner=?,tracked=? WHERE id=?", (
                json.dumps(data), json.dumps([{"pid": item.pid, "started": item.started} for item in tracked]), run_id))

    def recovery_rows(self):
        return self.connection.execute("SELECT * FROM runs WHERE active=1").fetchall()

    def stop_requested(self, run_id):
        with self.connection:
            record = self.record(run_id)
            if record.snapshot.state not in TERMINAL and record.snapshot.state != "stopping":
                self._append(run_id, {"type": "run_state_changed", "state": "stopping"})

    def interrupted(self, run_id, *, clean, authentication=None, diagnostic="service_restart", ai_usage=None):
        with self.connection:
            record = self.record(run_id)
            outcome = FinalOutcome(type="final_outcome", state="interrupted",
                                   authentication=authentication or record.authentication, diagnostic=diagnostic,
                                   ai_usage=ai_usage)
            if clean:
                self._append(run_id, outcome)
            else:
                self._append(run_id, {"type": "cleanup", "status": "failed", "diagnostic": "cleanup_failed"})
                record = self.record(run_id)
                snapshot = record.snapshot.model_copy(update={"state": "interrupted", "outcome": outcome})
                self._save(record.model_copy(update={"snapshot": snapshot, "cleanup_required": True,
                                                    "authentication": outcome.authentication}))

    def failed_before_spawn(self, run_id):
        with self.connection:
            self._append(run_id, {"type": "final_outcome", "state": "failed", "authentication": "not_observed",
                                  "diagnostic": "worker_failed"})

    def replay(self, run_id, after) -> Replay:
        with self.connection:
            snapshot = self.record(run_id).snapshot
            rows = self.connection.execute("SELECT event FROM events WHERE run_id=? AND sequence>? ORDER BY sequence", (run_id, after)).fetchall()
            events = [RunEvent.model_validate_json(row[0]) for row in rows]
            available = (after <= snapshot.cursor and (not events or events[0].sequence == after + 1))
            if after < snapshot.cursor and not events:
                available = False
            return Replay(status="replay" if available else "resync_required", after_sequence=after,
                          snapshot=snapshot, events=events if available else [])

    def page(self, *, offset=0, limit=20, state=None, profile_id=None):
        terms, values = [], []
        if state is not None:
            terms.append("json_extract(record,'$.snapshot.state')=?")
            values.append(state)
        if profile_id is not None:
            terms.append("json_extract(record,'$.snapshot.profile_id')=?")
            values.append(profile_id)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        total = self.connection.execute("SELECT count(*) FROM runs" + where, values).fetchone()[0]
        rows = self.connection.execute("SELECT record FROM runs" + where + " ORDER BY rowid DESC LIMIT ? OFFSET ?", (*values, limit, offset))
        records = [RunRecord.model_validate_json(row[0]) for row in rows]
        return RunPage(runs=records, total=total, offset=offset,
                       limit=limit, active_run_id=self.active_id())

    def register_artifact(self, run_id, artifact_id, relative_path, kind):
        from pydantic import TypeAdapter
        from .contracts import SafeID

        TypeAdapter(SafeID).validate_python(artifact_id)
        path = PurePosixPath(relative_path)
        if (kind not in {"masked_screenshot", "sanitized_report"} or path.is_absolute()
                or not path.parts or any(part in {".", ".."} for part in path.parts)
                or any(char in relative_path for char in "\\:%")
                or path.suffix != (".png" if kind == "masked_screenshot" else ".json")):
            raise ValueError("invalid_artifact")
        with self.connection:
            self._row(run_id)
            self.connection.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (run_id, artifact_id, relative_path, kind))

    def artifact(self, run_id, artifact_id):
        self._row(run_id)
        row = self.connection.execute("SELECT relative_path,kind FROM artifacts WHERE run_id=? AND id=?", (run_id, artifact_id)).fetchone()
        if row is None:
            raise KeyError("artifact_not_found")
        return row[0], row[1]
