"""Structured events and tracing.

Every user request gets a trace id, and every meaningful step emits a typed
event. This is what lets Alan answer "what did you do?" later (brief
section 14) and what the recovery layer uses to see failures.

Ported in spirit from DeepSeek Harness's event/service model (ADR-001): an
event bus is the one channel through which the orchestrator, agents, tools and
observers communicate, so no component needs a reference to another.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Iterable

from ..interfaces.types import new_id, redact, utcnow


class Events:
    """Canonical event names (brief section 14)."""

    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_FAILED = "tool.failed"
    MEMORY_RETRIEVED = "memory.retrieved"
    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_FORGOTTEN = "memory.forgotten"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_DENIED = "permission.denied"
    MODEL_STARTED = "model.started"
    MODEL_FINISHED = "model.finished"
    MODEL_FAILED = "model.failed"
    SUBAGENT_STARTED = "subagent.started"
    SUBAGENT_FINISHED = "subagent.finished"
    PLAN_CREATED = "plan.created"
    RECOVERY_APPLIED = "recovery.applied"
    LESSON_RECALLED = "lesson.recalled"
    LESSON_RECORDED = "lesson.recorded"
    REQUEST_STARTED = "request.started"
    REQUEST_FINISHED = "request.finished"

    # Always-on operation: scheduler and approval queue (Phase 1).
    SCHEDULER_STARTED = "scheduler.started"
    SCHEDULER_STOPPED = "scheduler.stopped"
    SCHEDULER_ERROR = "scheduler.error"
    TASK_SCHEDULED = "task.scheduled"
    TASK_REMOVED = "task.removed"
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"
    TASK_FAILED = "task.failed"
    TASK_WAITING = "task.waiting_approval"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    APPROVAL_SUBMITTED = "approval.submitted"
    APPROVAL_DECIDED = "approval.decided"
    ACTIVITY_ACCESSED = "activity.accessed"


@dataclass
class Event:
    """One observation. ``fields`` carries the event-specific detail."""

    name: str
    trace_id: str | None = None
    session_id: str | None = None
    agent: str | None = None
    duration_ms: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "agent": self.agent,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            **self.fields,
        }

    def __str__(self) -> str:  # pragma: no cover - logging aid
        return f"{self.name} trace={self.trace_id} {json.dumps(self.fields, default=str)}"


Subscriber = Callable[[Event], None]


def _matches(pattern: str, name: str) -> bool:
    """Does subscription ``pattern`` apply to event ``name``?

    Supports exact names, ``*`` (everything) and prefix wildcards such as
    ``tool.*``.
    """
    if pattern == "*" or pattern == name:
        return True
    if pattern.endswith(".*"):
        return name.startswith(pattern[:-1])
    return False


class EventBus:
    """Thread-safe publish/subscribe with wildcard support and a trace buffer.

    Subscribers are isolated: one raising callback must never break the caller
    or the other subscribers, because observability failing should not take the
    assistant down with it.
    """

    def __init__(self, *, history: int = 2000) -> None:
        self._subs: dict[str, list[Subscriber]] = {}
        self._lock = threading.RLock()
        self._history: Deque[Event] = deque(maxlen=history)

    # -- subscription --------------------------------------------------
    def subscribe(self, pattern: str, callback: Subscriber) -> Callable[[], None]:
        """Subscribe to an event name or ``prefix.*`` wildcard.

        Returns an unsubscribe callable.
        """
        with self._lock:
            self._subs.setdefault(pattern, []).append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                listeners = self._subs.get(pattern, [])
                if callback in listeners:
                    listeners.remove(callback)

        return _unsubscribe

    # -- emission ------------------------------------------------------
    def emit(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
        **fields: Any,
    ) -> Event:
        """Publish an event. Redacts string fields before they are recorded."""
        safe = {
            key: (redact(value) if isinstance(value, str) else value)
            for key, value in fields.items()
        }
        event = Event(
            name=name,
            trace_id=trace_id,
            session_id=session_id,
            agent=agent,
            fields=safe,
        )
        with self._lock:
            self._history.append(event)
            listeners: list[Subscriber] = []
            for pattern, callbacks in self._subs.items():
                if _matches(pattern, name):
                    listeners.extend(callbacks)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # An observer must never break the flow it observes.
                continue
        return event

    # -- introspection -------------------------------------------------
    def history(
        self, *, trace_id: str | None = None, names: Iterable[str] | None = None
    ) -> list[Event]:
        """Recorded events, oldest first, optionally filtered."""
        with self._lock:
            events = list(self._history)
        if trace_id:
            events = [e for e in events if e.trace_id == trace_id]
        if names:
            wanted = set(names)
            events = [e for e in events if e.name in wanted]
        return events

    def trace(self, trace_id: str) -> list[Event]:
        return self.history(trace_id=trace_id)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


class JsonlTrace:
    """Appends events to a JSONL file so traces outlive the process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: Event) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), default=str) + "\n")
        except OSError:
            pass  # tracing is best-effort

    def attach(self, bus: EventBus) -> Callable[[], None]:
        return bus.subscribe("*", self)


def new_trace(prefix: str = "tr_") -> str:
    """Start a new trace id for one user request."""
    return new_id(prefix)
