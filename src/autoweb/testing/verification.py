"""An explicit, in-memory verification provider for controlled test sites."""

from collections import deque
from datetime import datetime, timezone

from autoweb.contracts import VerificationError, VerificationResult


class FakeVerificationProvider:
    """Queue synthetic codes before login; never access a mailbox or log values."""

    def __init__(self):
        self._values = deque()
        self.requests = []
        self.acknowledged = []
        self.baseline_ids = frozenset()
        self._sequence = 0

    def enqueue(self, *values: str) -> None:
        self._values.extend(values)

    async def baseline(self, sender, recipient, subject=None, *, deadline):
        return self.baseline_ids

    async def receive(self, request):
        self.requests.append(request)
        if not self._values:
            raise VerificationError("fake_verification_empty")
        self._sequence += 1
        return VerificationResult(
            self._values.popleft(), f"fake-{self._sequence}", datetime.now(timezone.utc),
            "link" if request.kind == "magic_link" else "regex", request.site, request.account,
        )

    async def acknowledge(self, result, *, success, policy, deadline):
        if success and policy == "on_success":
            self.acknowledged.append(result.message_id)
