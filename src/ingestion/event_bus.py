"""Event Bus — async pub/sub with concurrent dispatch and backpressure.

Producers publish events, consumers subscribe by event type.
Dispatch is concurrent per handler with error isolation.
Critical consumers (storage) are separated from non-critical (features).
"""

import asyncio
import time
from collections import defaultdict
from typing import Callable, Awaitable, Any

import structlog

from .events import Event, EventType

logger = structlog.get_logger()

Subscriber = Callable[[Event], Awaitable[None]]


class SubscriberInfo:
    """Tracks a subscriber and its performance metrics."""
    __slots__ = ("handler", "name", "critical", "events", "errors", "total_ms", "slow_count")

    def __init__(self, handler: Subscriber, name: str, critical: bool = False) -> None:
        self.handler = handler
        self.name = name
        self.critical = critical
        self.events = 0
        self.errors = 0
        self.total_ms = 0.0
        self.slow_count = 0  # calls > 100ms


class EventBus:
    """Async event bus with concurrent dispatch and per-subscriber metrics."""

    def __init__(self, max_queue_size: int = 10_000, slow_threshold_ms: float = 100) -> None:
        self._subscribers: dict[EventType | None, list[SubscriberInfo]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._slow_threshold_ms = slow_threshold_ms
        self._stats: dict[str, int] = defaultdict(int)

    def subscribe(self, handler: Subscriber, event_type: EventType | None = None,
                  name: str | None = None, critical: bool = False) -> None:
        """Subscribe to events.

        Args:
            handler: async callback
            event_type: None = all events
            name: display name for metrics
            critical: if True, errors are logged as errors; if False, as warnings
        """
        sub_name = name or handler.__qualname__
        info = SubscriberInfo(handler, sub_name, critical)
        self._subscribers[event_type].append(info)
        logger.info("event_bus.subscribe", name=sub_name, event_type=event_type, critical=critical)

    async def publish(self, event: Event) -> None:
        """Publish an event. Drops if queue is full (backpressure)."""
        try:
            self._queue.put_nowait(event)
            self._stats[event.type.value] += 1
        except asyncio.QueueFull:
            self._stats["dropped"] += 1
            if self._stats["dropped"] % 1000 == 1:
                logger.warning("event_bus.queue_full", dropped=self._stats["dropped"],
                               queue_size=self._queue.maxsize)

    async def start(self) -> None:
        """Process events from queue — dispatches concurrently to all handlers."""
        self._running = True
        logger.info("event_bus.started")

        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Gather all matching subscribers
            handlers = list(self._subscribers.get(event.type, []))
            handlers.extend(self._subscribers.get(None, []))

            if not handlers:
                continue

            # Dispatch concurrently — each handler runs independently
            await asyncio.gather(
                *(self._dispatch(sub, event) for sub in handlers),
                return_exceptions=True,
            )

    async def _dispatch(self, sub: SubscriberInfo, event: Event) -> None:
        """Dispatch to a single subscriber with timing and error handling."""
        start = time.monotonic()
        try:
            await sub.handler(event)
            sub.events += 1
        except Exception:
            sub.errors += 1
            if sub.critical:
                logger.exception("event_bus.critical_handler_error", handler=sub.name, coin=event.coin)
            else:
                logger.warning("event_bus.handler_error", handler=sub.name, coin=event.coin)
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            sub.total_ms += elapsed_ms
            if elapsed_ms > self._slow_threshold_ms:
                sub.slow_count += 1

    async def stop(self) -> None:
        self._running = False
        logger.info("event_bus.stopped", stats=dict(self._stats), subscribers=self.subscriber_stats)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def subscriber_stats(self) -> list[dict[str, Any]]:
        """Per-subscriber performance metrics."""
        result = []
        for event_type, subs in self._subscribers.items():
            for sub in subs:
                avg_ms = sub.total_ms / sub.events if sub.events > 0 else 0
                result.append({
                    "name": sub.name,
                    "event_type": event_type.value if event_type else "all",
                    "critical": sub.critical,
                    "events": sub.events,
                    "errors": sub.errors,
                    "avg_ms": round(avg_ms, 2),
                    "slow_count": sub.slow_count,
                })
        return result
