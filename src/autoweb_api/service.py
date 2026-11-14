"""Durable orchestration; HTTP disconnects never own execution tasks."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from time import monotonic
from uuid import uuid4

from autoweb.config import ConfigurationError

from .api_contracts import PreflightResult
from .artifacts import read_artifact
from .configuration import ProfileUnavailable, prepare_run, registered_site
from .contracts import ProfileDescriptor, StartRun
from .evidence import approved_artifact
from .process_tree import ProcessIdentity, owned_snapshot, supported
from .profiles import REGISTRY, display_steps, resolve_registration
from .protocol import MAX_LINE, Manifest, EffectiveSettings, RecoveryReceipt, encode
from .storage import Conflict, RunStore
from .worker import CleanupUncertain, WorkerCoordinator


def identity(value):
    if (not isinstance(value, dict) or set(value) != {"pid", "started"}
            or type(value["pid"]) is not int or value["pid"] <= 1
            or type(value["started"]) is not int or value["started"] < 0):
        raise ValueError("invalid_process_identity")
    return ProcessIdentity(value["pid"], 0, value["started"], "R")


class RunService:
    def __init__(self, settings, *, coordinator_factory=WorkerCoordinator):
        self.settings = settings
        self.store = RunStore(settings.data_directory / "history.sqlite3", settings.event_retention)
        self.receipts = settings.data_directory / "receipts"
        self.coordinator = coordinator_factory(settings.root, artifacts=settings.artifacts,
                                               receipts=self.receipts, on_spawn=self._record_owner)
        self.handles = {}
        self.tasks = set()
        self.stop_ids = set()
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.generation = 0
        self.closing = False
        self.storage_failed = False

    def notify(self):
        self.generation += 1
        self.changed.set()

    async def open(self):
        try:
            self.store.open()
            await self.recover()
        except BaseException:
            self.store.close()
            raise RuntimeError("local_service_unavailable") from None

    def _record_owner(self, handle):
        self.store.owner(handle.run_id, handle.guardian_identity)

    async def recover(self):
        for row in self.store.recovery_rows():
            run_id = row["id"]
            owner = json.loads(row["owner"]) if row["owner"] else None
            if owner is None:
                # Durable ownership is committed before Launch is written. No
                # owner means no executor was authorized, even across a crash.
                self.store.interrupted(run_id, clean=True)
                continue
            root = identity(owner)
            deadline = monotonic() + self.settings.recovery_seconds
            receipt = None
            while monotonic() < deadline:
                try:
                    path = self.receipts / f"{run_id}.json"
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        data = os.read(descriptor, MAX_LINE + 1)
                    finally:
                        os.close(descriptor)
                    candidate = RecoveryReceipt.model_validate_json(data)
                    if (len(data) > MAX_LINE or candidate.run_id != run_id
                            or candidate.guardian_pid != root.pid or candidate.guardian_started != root.started):
                        raise ValueError("invalid_cleanup_receipt")
                    receipt = candidate
                except (OSError, ValueError):
                    pass
                if not root.alive():
                    break
                # The previous service's control pipe has closed. Its guardian
                # cooperatively stops and writes a receipt after draining children.
                await asyncio.sleep(0.05)
            if receipt is not None and not root.alive():
                self.store.interrupted(run_id, clean=True, authentication=receipt.outcome.authentication,
                                       ai_usage=receipt.outcome.ai_usage)
            else:
                tracked = [identity(item) for item in json.loads(row["tracked"])]
                if root.alive():
                    tracked.extend(owned_snapshot(root))
                for process in reversed(tracked):
                    try:
                        process.send(signal.SIGKILL)
                    except OSError:
                        pass
                try:
                    root.send(signal.SIGKILL)
                except OSError:
                    pass
                self.store.interrupted(run_id, clean=False)
        self.notify()

    async def start(self, request: StartRun):
        request = StartRun.model_validate_json(request.model_dump_json())
        resolve_registration(request.profile_id)
        async with self.lock:
            if self.closing or self.storage_failed:
                raise Conflict("service_unavailable")
            record, created = self.store.reserve(uuid4().hex, request)
            if created:
                task = asyncio.create_task(self._execute(record.snapshot.run_id, request))
                self.tasks.add(task)
                task.add_done_callback(self._task_done)
                self.notify()
            return record, created

    def _task_done(self, task):
        self.tasks.discard(task)
        if task.cancelled() or task.exception() is not None:
            self.storage_failed = True
            self.notify()

    async def _execute(self, run_id, request):
        handle = None
        reader = None
        try:
            if self.closing:
                self.store.interrupted(run_id, clean=True)
                return
            handle = await self.coordinator.start(request, run_id=run_id)
            self.handles[run_id] = handle
            if run_id in self.stop_ids or self.closing:
                handle.request_stop()
            cursor = 0
            saved_manifest = False
            reader = asyncio.create_task(handle.next_event(cursor))
            while True:
                done, _ = await asyncio.wait({reader}, timeout=0.1)
                if handle.guardian_identity is not None:
                    self.store.owner(run_id, handle.guardian_identity, handle.tracked.values())
                if handle.manifest is not None and not saved_manifest:
                    self.store.manifest(run_id, handle.manifest)
                    saved_manifest = True
                    self.notify()
                if not done:
                    continue
                event = reader.result()
                if event is None:
                    break
                artifact_path = None
                if event.payload.type == "artifact_registered":
                    relative = approved_artifact(event.payload.artifact_id, event.payload.kind)
                    try:
                        read_artifact(self.settings.artifacts, run_id, relative, event.payload.kind)
                        artifact_path = relative
                    except KeyError:
                        pass  # Missing captures remain absent, while consuming the worker cursor.
                self.store.worker_event(event, artifact_path=artifact_path)
                cursor = event.sequence
                self.notify()
                reader = asyncio.create_task(handle.next_event(cursor))
            await handle.wait()
        except CleanupUncertain:
            self.store.interrupted(run_id, clean=False, authentication=handle.authentication,
                                   diagnostic="cleanup_failed")
        except BaseException:
            try:
                if handle is not None and handle.process is not None:
                    await handle.stop()
                    # Even if persistence failed mid-stream, retain real observed
                    # authentication and a verified interrupted lifecycle outcome.
                    self.store.interrupted(run_id, clean=True, authentication=handle.authentication,
                                           diagnostic="worker_failed")
                else:
                    self.store.failed_before_spawn(run_id)
            except BaseException:
                self.storage_failed = True
                try:
                    self.store.interrupted(run_id, clean=False, diagnostic="cleanup_failed")
                except BaseException:
                    pass
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            self.handles.pop(run_id, None)
            self.stop_ids.discard(run_id)
            self.notify()

    def stop(self, run_id):
        record = self.store.record(run_id)
        if record.cleanup_required:
            raise Conflict("cleanup_required")
        if record.active:
            self.store.stop_requested(run_id)
            self.stop_ids.add(run_id)
            if run_id in self.handles:
                self.handles[run_id].request_stop()
            self.notify()
        return self.store.record(run_id)

    def request_shutdown(self):
        self.closing = True
        self.notify()
        for handle in self.handles.values():
            handle.request_stop()

    async def close(self):
        self.request_shutdown()
        if self.coordinator.active is not None:
            try:
                await self.coordinator.close()
            except CleanupUncertain:
                pass
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        self.store.close()

    def preflight(self, value):
        try:
            if not supported():
                return PreflightResult(status="unavailable")
            prepared = prepare_run(StartRun(request_id="preflight", profile_id=value.profile_id,
                                            overrides=value.overrides), self.settings.root,
                                   self.settings.artifacts / "preflight")
            settings = prepared.configuration.settings
            manifest = Manifest(type="manifest", profile_id=value.profile_id,
                                steps=display_steps(prepared.configuration.site), effective=EffectiveSettings(
                                    browser=settings.browser, timeouts=settings.timeouts,
                                    ai_enabled=settings.ai.enabled, ai_model=settings.ai.model))
            encode(manifest)
            return PreflightResult(status="ready", manifest=manifest)
        except ProfileUnavailable:
            return PreflightResult(status="unavailable")
        except (ValueError, ConfigurationError, OSError):
            return PreflightResult(status="invalid_configuration")

    def profiles(self):
        names = {"controlled-password": "Controlled password", "controlled-otp": "Controlled OTP",
                 "controlled-magic-link": "Controlled magic link", "practice-test-password": "Practice Test Automation",
                 "expandtesting-otp": "Expand Testing"}
        results = []
        for item in REGISTRY:
            controlled = item.classification == "controlled"
            available = True
            steps = []
            mode = "email_magic_link" if item.id == "controlled-magic-link" else "email_otp" if item.id in {"controlled-otp", "expandtesting-otp"} else "password"
            if available:
                try:
                    site = registered_site(item, self.settings.root)
                    mode, steps = site.login_mode, display_steps(site)
                except (ConfigurationError, ValueError):
                    available = False
            capabilities = ["browser"]
            if controlled:
                capabilities.append("fixture_server")
                if item.provider == "in_memory":
                    capabilities.append("in_memory_verification")
            elif item.provider == "gmail":
                capabilities.append("gmail")
            results.append(ProfileDescriptor(id=item.id, display_name=names[item.id], login_mode=mode,
                           classification=item.classification, authorization="controlled_local" if controlled else "explicit_selection_required",
                           readiness="unverified" if available else "unavailable", required_capabilities=capabilities, steps=steps))
        return results
