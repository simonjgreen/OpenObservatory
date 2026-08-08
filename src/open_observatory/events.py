"""Event envelope and the in-process bus.

The envelope is the one in ``docs/api/API_AND_INTEGRATIONS.md``, unchanged, so
the same JSON that reaches a browser over the WebSocket can be published to MQTT
or a webhook without translation.

ADR-009: this is an asyncio fan-out with bounded per-subscriber queues, standing
in for Redis Streams during the first capture prototype. The bounded queue and
recorded drop count are the important part — they are the back-pressure
behaviour a real stream transport would also impose, so swapping the transport
does not change how the system behaves under load.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Bumped 1.0 -> 1.1 when the MQTT publisher (Milestone 6) became the first
#: consumer outside this repository: schemas/detection-event.schema.json had
#: additionalProperties:false and omitted `rank` and `taxonomic_group`, which
#: every internal detection record actually carries (HANDOVER.md section 6.3
#: item 9). A silent widening would have been invisible to in-process
#: consumers but broken any external MQTT/webhook validator that trusted the
#: 1.0 schema strictly, so the version number moves instead. See ADR-022.
SCHEMA_VERSION = "1.1"


class EventType:
    """Event types, grouped by the pipeline stage that emits them."""

    CAPTURE_STARTED = "capture.started"
    CAPTURE_STOPPED = "capture.stopped"
    CAPTURE_GAP = "capture.gap"
    CAPTURE_LEVELS = "capture.levels"

    WINDOW_EMITTED = "window.emitted"
    WINDOW_DROPPED = "window.dropped"

    DETECTOR_STATE = "detector.state"
    DETECTION_CREATED = "detection.created"

    CLIP_WRITTEN = "clip.written"

    HEALTH_EVENT = "health.event"
    STATION_STATUS = "station.status"


def make_event(
    event_type: str,
    data: dict[str, Any],
    *,
    station_id: uuid.UUID | str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "station_id": str(station_id) if station_id else None,
        "data": data,
    }


class Subscription:
    """One consumer's bounded view of the bus."""

    def __init__(self, bus: EventBus, types: frozenset[str] | None, maxsize: int, label: str) -> None:
        self._bus = bus
        self.types = types
        self.label = label
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self.delivered = 0

    def wants(self, event_type: str) -> bool:
        return self.types is None or event_type in self.types

    def offer(self, event: dict[str, Any]) -> None:
        """Non-blocking delivery. A slow consumer loses events, not the producer.

        Dropping the *oldest* keeps the live view current, which is what a
        real-time display wants; the drop counter keeps that honest.
        """
        try:
            self.queue.put_nowait(event)
            self.delivered += 1
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            self.dropped += 1

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self.queue.get()
            if event.get("event_type") == "_bus.closed":
                return
            yield event

    def close(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    """Fan-out event bus with a replayable recent-history tail."""

    def __init__(self, history: int = 500) -> None:
        self._subscriptions: list[Subscription] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self.published = 0

    def subscribe(
        self,
        types: Iterable[str] | None = None,
        *,
        maxsize: int = 512,
        label: str = "anonymous",
    ) -> Subscription:
        subscription = Subscription(
            self, frozenset(types) if types is not None else None, maxsize, label
        )
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)
        subscription.offer({"event_type": "_bus.closed"})

    def publish(self, event: dict[str, Any]) -> None:
        """Synchronous, non-blocking. Safe to call from the capture hot path."""
        self.published += 1
        self._history.append(event)
        event_type = event.get("event_type", "")
        for subscription in self._subscriptions:
            if subscription.wants(event_type):
                subscription.offer(event)

    def emit(self, event_type: str, data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        event = make_event(event_type, data, **kwargs)
        self.publish(event)
        return event

    def recent(self, limit: int = 100, types: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = frozenset(types) if types is not None else None
        items = [
            event
            for event in self._history
            if wanted is None or event.get("event_type") in wanted
        ]
        return items[-limit:]

    def stats(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "subscribers": len(self._subscriptions),
            "history": len(self._history),
            "per_subscriber": [
                {
                    "label": s.label,
                    "queued": s.queue.qsize(),
                    "delivered": s.delivered,
                    "dropped": s.dropped,
                }
                for s in self._subscriptions
            ],
        }

    def close(self) -> None:
        for subscription in list(self._subscriptions):
            self.unsubscribe(subscription)
