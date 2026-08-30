"""Event bus for Alan."""

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
from weakref import WeakSet

from alan.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Event:
    """Base event class."""

    name: str
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: Optional[str] = None
    correlation_id: Optional[str] = None


class EventBus:
    """Async event bus with pub/sub pattern."""

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._async_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._wildcard_handlers: List[Callable] = []
        self._async_wildcard_handlers: List[Callable] = []
        self._running = False
        self._event_history: List[Event] = []
        self._max_history = 1000

    async def start(self) -> None:
        """Start the event bus."""
        self._running = True
        logger.debug("Event bus started")

    async def stop(self) -> None:
        """Stop the event bus."""
        self._running = False
        self._handlers.clear()
        self._async_handlers.clear()
        self._wildcard_handlers.clear()
        self._async_wildcard_handlers.clear()
        logger.debug("Event bus stopped")

    def subscribe(
        self,
        event_name: str,
        handler: Callable,
        async_handler: bool = False,
    ) -> None:
        """Subscribe to an event."""
        if async_handler:
            self._async_handlers[event_name].append(handler)
        else:
            self._handlers[event_name].append(handler)
        logger.debug("Subscribed to event", event=event_name, async=async_handler)

    def subscribe_async(self, event_name: str, handler: Callable) -> None:
        """Subscribe to an event with async handler."""
        self.subscribe(event_name, handler, async_handler=True)

    def subscribe_all(self, handler: Callable, async_handler: bool = False) -> None:
        """Subscribe to all events."""
        if async_handler:
            self._async_wildcard_handlers.append(handler)
        else:
            self._wildcard_handlers.append(handler)
        logger.debug("Subscribed to all events", async=async_handler)

    def unsubscribe(self, event_name: str, handler: Callable) -> bool:
        """Unsubscribe from an event."""
        for handlers in [self._handlers, self._async_handlers]:
            if handler in handlers[event_name]:
                handlers[event_name].remove(handler)
                logger.debug("Unsubscribed from event", event=event_name)
                return True

        if handler in self._wildcard_handlers:
            self._wildcard_handlers.remove(handler)
            return True
        if handler in self._async_wildcard_handlers:
            self._async_wildcard_handlers.remove(handler)
            return True
        return False

    async def publish(self, event: Event) -> None:
        """Publish an event."""
        if not self._running:
            logger.warning("Event bus not running, dropping event", event=event.name)
            return

        # Add to history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        # Call specific handlers
        await self._call_handlers(event.name, event, self._handlers[event.name])
        await self._call_async_handlers(event.name, event, self._async_handlers[event.name])

        # Call wildcard handlers
        await self._call_handlers("*", event, self._wildcard_handlers)
        await self._call_async_handlers("*", event, self._async_wildcard_handlers)

    async def _call_handlers(
        self, event_name: str, event: Event, handlers: List[Callable]
    ) -> None:
        """Call synchronous handlers."""
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.exception(
                    "Error in event handler",
                    event=event_name,
                    handler=handler.__name__,
                    error=str(e),
                )

    async def _call_async_handlers(
        self, event_name: str, event: Event, handlers: List[Callable]
    ) -> None:
        """Call asynchronous handlers."""
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.exception(
                    "Error in async event handler",
                    event=event_name,
                    handler=handler.__name__,
                    error=str(e),
                )

    async def publish_and_wait(
        self, event: Event, timeout: float = 30.0
    ) -> List[Any]:
        """Publish event and wait for all handlers to complete."""
        results = []

        async def collect_result(handler: Callable) -> None:
            try:
                result = await handler(event)
                results.append(result)
            except Exception as e:
                logger.exception("Handler error", error=str(e))
                results.append(e)

        # Gather all handlers
        handlers = (
            self._handlers[event.name]
            + self._async_handlers[event.name]
            + self._wildcard_handlers
            + self._async_wildcard_handlers
        )

        if handlers:
            await asyncio.gather(
                *[collect_result(h) for h in handlers],
                return_exceptions=True,
            )

        return results

    def get_history(self, event_name: Optional[str] = None, limit: int = 100) -> List[Event]:
        """Get event history."""
        history = self._event_history
        if event_name:
            history = [e for e in history if e.name == event_name]
        return history[-limit:]

    def clear_history(self) -> None:
        """Clear event history."""
        self._event_history.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    def get_subscriber_count(self, event_name: str) -> int:
        """Get number of subscribers for an event."""
        return (
            len(self._handlers[event_name])
            + len(self._async_handlers[event_name])
            + len(self._wildcard_handlers)
            + len(self._async_wildcard_handlers)
        )


# Global event bus
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus."""
    global _event_bus
    _event_bus = bus


# Convenience functions
async def publish(event: Event) -> None:
    """Publish an event."""
    await get_event_bus().publish(event)


def subscribe(event_name: str, handler: Callable) -> None:
    """Subscribe to an event."""
    get_event_bus().subscribe(event_name, handler)


def subscribe_async(event_name: str, handler: Callable) -> None:
    """Subscribe to an event with async handler."""
    get_event_bus().subscribe_async(event_name, handler)