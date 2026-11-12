"""Loopback Host/Origin checks and in-memory same-origin CSRF sessions."""

import hmac
import secrets
from http.cookies import SimpleCookie
from time import monotonic

from starlette.responses import JSONResponse

COOKIE = "autoweb_studio_session"
BODY_LIMIT = 16384


class Sessions:
    def __init__(self):
        self.entries = {}

    def lookup(self, cookie):
        value = self.entries.get(cookie)
        if value and monotonic() - value[1] < 8 * 3600:
            return value[0]
        self.entries.pop(cookie, None)
        return None

    def issue(self, cookie=None):
        csrf = self.lookup(cookie)
        if csrf:
            return cookie, csrf
        self.entries = {key: value for key, value in self.entries.items() if monotonic() - value[1] < 8 * 3600}
        if len(self.entries) >= 64:
            self.entries.pop(next(iter(self.entries)))
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.entries[cookie] = (csrf, monotonic())
        return cookie, csrf


class LocalGuard:
    def __init__(self, app, *, settings, sessions):
        self.app, self.settings, self.sessions = app, settings, sessions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [*message.get("headers", []),
                                      (b"x-content-type-options", b"nosniff"),
                                      (b"referrer-policy", b"same-origin")]
                if scope["path"].startswith("/api"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        async def reject(code, status=403):
            await JSONResponse({"code": code}, status_code=status)(scope, receive, safe_send)

        try:
            headers = {}
            for key, value in scope["headers"]:
                key = key.lower()
                if key in headers and key in {b"host", b"origin", b"cookie", b"x-autoweb-csrf"}:
                    return await reject("invalid_headers", 400)
                headers[key] = value.decode("latin-1")
            if headers.get(b"host", "").lower() not in self.settings.authorities:
                return await reject("invalid_host")
            origin = headers.get(b"origin")
            if origin is not None and origin not in self.settings.origins:
                return await reject("invalid_origin")
            if scope["path"].startswith("/api"):
                if headers.get(b"sec-fetch-site") == "cross-site":
                    return await reject("invalid_origin")
                cookies = SimpleCookie()
                cookies.load(headers.get(b"cookie", ""))
                session = cookies.get(COOKIE)
                session = session.value if session is not None else None
                public = scope["path"] in {"/api/health", "/api/session"}
                csrf = self.sessions.lookup(session)
                if not public and csrf is None:
                    return await reject("session_required", 401)
                if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                    if origin not in self.settings.origins:
                        return await reject("invalid_origin")
                    token = headers.get(b"x-autoweb-csrf", "")
                    if csrf is None or not token.isascii() or not hmac.compare_digest(token, csrf):
                        return await reject("csrf_required")
                    if headers.get(b"content-type", "").split(";", 1)[0].strip() != "application/json":
                        return await reject("json_required", 415)
                    body = bytearray()
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > BODY_LIMIT:
                            return await reject("request_too_large", 413)
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
                    original_receive = receive
                    first = True

                    async def buffered_receive():
                        nonlocal first
                        if first:
                            first = False
                            return {"type": "http.request", "body": bytes(body), "more_body": False}
                        return await original_receive()

                    receive = buffered_receive
            await self.app(scope, receive, safe_send)
        except Exception:
            # Never return validation input, exception text or a traceback. This
            # outer ASGI guard also prevents the server from logging raw errors.
            if not started:
                await reject("service_error", 500)
            else:
                try:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                except Exception:
                    pass
