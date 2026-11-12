"""Optional bounded observation, independent of services, transports and clients.

Use one ProgressObserver per login/session, on its execution event loop. Producers
never await consumers or execute user callbacks. Consumers own buffer lifetime.
"""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from autoweb.config.models import SiteProfile


def action_step_ids(profile: SiteProfile) -> tuple[str, ...]:
    """Content-based IDs; repeated identical actions have distinct occurrences."""
    occurrences: Counter[str] = Counter()
    identities = []
    for step in profile.steps:
        digest = sha256(step.model_dump_json().encode()).hexdigest()[:20]
        occurrences[digest] += 1
        identities.append(f"action:{digest}:{occurrences[digest]}")
    return tuple(identities)


@dataclass(frozen=True)
class ProgressEvent:
    """Observed boundaries only; never includes exceptions, URLs or secret values.

    Ordinals belong to this observer, not the future durable service sequence.
    A login outcome precedes owned cleanup; it is not a final service outcome.
    """

    ordinal: int
    timestamp: datetime
    elapsed_seconds: float
    kind: Literal["preflight", "browser_startup", "navigation", "step", "verification_wait", "verification_baseline", "authentication", "acknowledgment", "cleanup", "login_outcome"]
    status: Literal["started", "succeeded", "skipped", "failed", "cancelled", "error"]
    step_id: str
    phase_id: str
    action: str | None = None
    index: int | None = None
    informational: bool = False


class ProgressObserver:
    """Pull-based mailbox with a reserved transition budget for one profile.

    At most 6 * profile actions + 64 critical transitions are retained, even if
    nobody consumes them. Repetitive informational updates coalesce by phase in
    16 separate slots, so they cannot evict transitions. No consumer task/thread
    is created. close() is an explicit disconnect and discards pending data.
    """

    def __init__(self):
        self._critical: deque[ProgressEvent] = deque()
        self._information: OrderedDict[tuple[str, str, str], ProgressEvent] = OrderedDict()
        self._available = asyncio.Event()
        self._started: float | None = None
        self._capacity = 0
        self._ordinal = 0
        self._closed = False
        self.overflowed = False

    def _prepare(self, count: int) -> None:
        if self._started is None:
            self._started = monotonic()
            self._capacity = 6 * count + 64

    @property
    def started_monotonic(self) -> float | None:
        """Observation clock origin, for an owning worker to align its timeline."""
        return self._started

    def _publish(self, *, informational: bool = False, **fields) -> None:
        if self._closed or self._started is None:
            return
        self._ordinal += 1
        event = ProgressEvent(
            ordinal=self._ordinal, timestamp=datetime.now(timezone.utc),
            elapsed_seconds=monotonic() - self._started, informational=informational, **fields,
        )
        if informational:
            key = (event.kind, event.step_id, event.phase_id)
            self._information.pop(key, None)
            if len(self._information) == 16:
                self._information.popitem(last=False)
            self._information[key] = event
        elif len(self._critical) < self._capacity:
            self._critical.append(event)
        else:
            # Defensive misuse/producer bug. Never block execution or silently
            # imply the resulting observation is complete.
            self.overflowed = True
            return
        self._available.set()

    def drain(self) -> tuple[ProgressEvent, ...]:
        events = tuple(sorted((*self._critical, *self._information.values()), key=lambda item: item.ordinal))
        self._critical.clear()
        self._information.clear()
        self._available.clear()
        return events

    async def next_event(self) -> ProgressEvent | None:
        """Wait for an event; return None after explicit close. One consumer only."""
        while not self._closed:
            if self._critical or self._information:
                first_info = next(iter(self._information.values()), None)
                if self._critical and (first_info is None or self._critical[0].ordinal < first_info.ordinal):
                    return self._critical.popleft()
                return self._information.popitem(last=False)[1]
            self._available.clear()
            await self._available.wait()
        return None

    def close(self) -> None:
        self._closed = True
        self._critical.clear()
        self._information.clear()
        self._available.set()


class Progress:
    """Failure-isolating producer used only by package orchestration."""

    def __init__(self, observer: ProgressObserver | None, profile: SiteProfile):
        self.observer = observer
        self.ids: tuple[str, ...] = ()
        self._open: dict[tuple[str, str], dict] = {}
        if observer is not None:
            try:
                observer._prepare(len(profile.steps))
                self.ids = action_step_ids(profile)
            except Exception:
                self.observer = None

    def emit(self, kind, status, *, step_id=None, phase_id=None, action=None, index=None):
        if self.observer is None:
            return
        try:
            self.observer._publish(
                kind=kind, status=status, step_id=step_id or kind,
                phase_id=phase_id or kind, action=action, index=index,
            )
        except Exception:
            # Observation cannot replace login, provider or cleanup decisions.
            self.observer = None

    def step_id(self, index: int) -> str:
        return self.ids[index - 1] if self.ids else f"step:{index}"

    def start(self, kind, **fields):
        if self.observer is not None:
            self._open[(kind, fields.get("step_id", kind))] = fields
            self.emit(kind, "started", **fields)

    def end(self, kind, status="succeeded", *, step_id=None):
        fields = self._open.pop((kind, step_id or kind), None)
        if fields is not None:
            self.emit(kind, status, **fields)

    def finish_open(self, status):
        for (kind, step_id) in reversed(tuple(self._open)):
            self.end(kind, status, step_id=step_id)
