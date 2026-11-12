"""Explicit FastAPI factory for loopback Studio execution; no global application."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from .api_contracts import PreflightRequest
from .artifacts import read_artifact
from .contracts import RunState, SafeID, StartRun
from .security import COOKIE, LocalGuard, Sessions
from .service import RunService
from .settings import ServiceSettings
from .storage import Conflict
from .process_tree import supported


def create_app(settings: ServiceSettings, *, service_factory=RunService):
    sessions = Sessions()

    @asynccontextmanager
    async def lifespan(app):
        service = service_factory(settings)
        await service.open()
        app.state.service = service
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="AutoWeb local API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalGuard, settings=settings, sessions=sessions)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"code": "invalid_request"}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"code": "not_found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"code": error.code}, status_code=503 if error.code == "service_unavailable" else 409)

    @app.get("/api/health")
    async def health():
        service = app.state.service
        active = service.store.active_id()
        blocked = active is not None and service.store.record(active).cleanup_required
        return {"status": "ready", "execution_available": supported() and not blocked and not service.storage_failed and not service.closing,
                "active_run_id": active, "cleanup_required": blocked}

    @app.get("/api/session")
    async def session(request: Request):
        cookie, csrf = sessions.issue(request.cookies.get(COOKIE))
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie(COOKIE, cookie, httponly=True, samesite="strict", path="/api", max_age=8 * 3600)
        return response

    @app.get("/api/profiles")
    async def profiles():
        return app.state.service.profiles()

    @app.post("/api/preflight")
    async def preflight(value: PreflightRequest):
        return app.state.service.preflight(value)

    @app.post("/api/runs")
    async def start(value: StartRun):
        try:
            record, created = await app.state.service.start(value)
        except ValueError:
            return JSONResponse({"code": "unknown_profile"}, status_code=404)
        return JSONResponse(record.model_dump(mode="json"), status_code=202 if created else 200)

    @app.get("/api/runs")
    async def runs(offset: Annotated[int, Query(ge=0, le=9007199254740991)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                   state: RunState | None = None, profile_id: SafeID | None = None):
        return app.state.service.store.page(offset=offset, limit=limit, state=state, profile_id=profile_id)

    @app.get("/api/runs/{run_id}")
    async def run(run_id: SafeID):
        return app.state.service.store.record(run_id)

    @app.post("/api/runs/{run_id}/stop")
    async def stop(run_id: SafeID):
        record = app.state.service.stop(run_id)
        return JSONResponse(record.model_dump(mode="json"), status_code=202 if record.active else 200)

    @app.get("/api/runs/{run_id}/events")
    async def events(request: Request, run_id: SafeID, after: Annotated[int, Query(ge=0, le=9007199254740991)] = 0):
        service = app.state.service
        last = request.headers.get("last-event-id")
        if last is not None:
            if not re.fullmatch(r"[0-9]{1,16}", last) or int(last) > 9007199254740991:
                return JSONResponse({"code": "invalid_cursor"}, status_code=400)
            after = int(last)
        record = service.store.record(run_id)
        if (not record.active or record.cleanup_required) and after == record.snapshot.cursor:
            return Response(status_code=204)

        async def stream():
            cursor = after
            while not service.closing:
                version = service.generation
                replay = service.store.replay(run_id, cursor)
                if replay.status == "resync_required":
                    yield f"event: resync_required\ndata: {replay.model_dump_json()}\n\n"
                    return
                for event in replay.events:
                    yield f"id: {event.sequence}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
                    cursor = event.sequence
                current = service.store.record(run_id)
                if current.snapshot.cursor != cursor:
                    continue
                if not current.active or current.cleanup_required:
                    code = "cleanup_required" if current.cleanup_required else "complete"
                    yield f"event: stream_end\ndata: {json.dumps({'code': code, 'cursor': cursor})}\n\n"
                    return
                if version != service.generation:
                    continue
                service.changed.clear()
                try:
                    await asyncio.wait_for(service.changed.wait(), 10)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
            yield 'event: stream_end\ndata: {"code":"service_shutdown"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    async def artifact(run_id: SafeID, artifact_id: SafeID):
        relative, kind = app.state.service.store.artifact(run_id, artifact_id)
        data, media_type = read_artifact(settings.artifacts, run_id, relative, kind)
        return Response(data, media_type=media_type, headers={"Content-Disposition": "inline"})

    if settings.ui_directory is not None:
        app.mount("/", StaticFiles(directory=settings.ui_directory, html=True, follow_symlink=False), name="studio")
    return app
