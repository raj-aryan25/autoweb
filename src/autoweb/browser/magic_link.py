"""Consume a magic link in the active page, checking every navigation destination."""

import asyncio
import json
from time import monotonic
from urllib.parse import urljoin

from autoweb.contracts import VerificationError
from autoweb.links import allowed_destination, valid_magic_link


class MagicLinkNavigation:
    """Inspect HTTP redirects before following them, including cross-origin hops.

    Playwright routes alone do not intercept each HTTP redirect. Fetch with zero
    redirects, then replace an approved redirect with a fresh browser navigation.
    Cookies stay in the same context and one-time URLs are fetched only once.
    """

    def __init__(self, page, verification, deadline):
        self.page = page
        self.policy = verification
        self.rejected = asyncio.Event()
        self.hops = 0
        self.deadline = deadline
        self.failure_reason = "verification_link_rejected"

    async def __aenter__(self):
        self._handler = self._route
        await self.page.context.route("**/*", self._handler)
        return self

    async def __aexit__(self, *args):
        await self.page.context.unroute("**/*", self._handler)

    async def _route(self, route):
        from playwright.async_api import Error, TimeoutError

        request = route.request
        if not request.is_navigation_request() or request.frame != self.page.main_frame:
            return await route.fallback()
        self.hops += 1
        if (self.hops > 10 or request.method != "GET"
                or not allowed_destination(request.url, self.policy.allowed_origins)):
            self.rejected.set()
            return await route.abort()
        response = None
        try:
            response = await route.fetch(max_redirects=0, timeout=min(10_000, max(1, (self.deadline - monotonic()) * 1000)))
            if response.status in {301, 302, 303, 307, 308}:
                destination = urljoin(request.url, response.headers.get("location", ""))
                if (not response.headers.get("location")
                        or not allowed_destination(destination, self.policy.allowed_origins)):
                    self.rejected.set()
                    return await route.abort()
                literal = json.dumps(destination).replace("<", "\\u003c")
                await route.fulfill(status=200, content_type="text/html",
                                    body=f"<!doctype html><script>location.replace({literal})</script>")
            else:
                await route.fulfill(response=response)
        except Error as error:
            self.failure_reason = ("verification_timeout" if isinstance(error, TimeoutError)
                                   else "verification_navigation_failed")
            self.rejected.set()
            try:
                await route.abort()
            except Error:
                pass
        finally:
            if response is not None:
                try:
                    await response.dispose()
                except Error:
                    pass

    async def wait(self, operation):
        task = asyncio.create_task(operation)
        rejected = asyncio.create_task(self.rejected.wait())
        try:
            done, _ = await asyncio.wait([task, rejected], return_when=asyncio.FIRST_COMPLETED)
            if rejected in done:
                raise VerificationError(self.failure_reason)
            return await task
        finally:
            for pending in (task, rejected):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, rejected, return_exceptions=True)

    async def follow(self, value: str, *, timeout_ms: float):
        from .actions import navigate

        if not valid_magic_link(value, self.policy.allowed_origins, self.policy.link_path_prefix):
            raise VerificationError("verification_link_rejected")
        return await self.wait(navigate(self.page, value, timeout_ms=timeout_ms))
