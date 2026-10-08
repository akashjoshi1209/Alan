"""Live background feed for the chat loop (Phase 4 "richer TUI").

A chat session owns the scheduler. Tasks start, wait for approval and finish
while a reply is streaming or while the user is typing, and printing from those
threads straight into ``console.input`` garbles the prompt the user is reading.
So the feed *collects* short, rendered lines for the events it cares about and
the chat loop drains them at the safe moment -- after a reply, before the next
prompt.

Everything here is plain console text (``[dim]``-ready), not markdown: feed
lines go through `rich`'s markup and must survive `print(markup=False)`-style
handling untouched.
"""

from __future__ import annotations

import threading
from typing import Optional

from .core.events import Event, Events

#: How much of a message may appear in one feed line.
_MAX_LINE = 80


def render_event_line(event: Event) -> Optional[str]:
    """One dim line for a background event, or ``None`` to skip it entirely."""
    fields = event.fields or {}
    task_id = str(fields.get("task_id", ""))
    name = event.name

    if name == Events.TASK_STARTED:
        request = str(fields.get("request", ""))[:_MAX_LINE]
        return f"\u2022 task {task_id} started: {request}"
    if name == Events.TASK_FINISHED:
        mark = "done" if fields.get("ok") else "finished"
        summary = str(fields.get("summary", ""))[:_MAX_LINE]
        return f"\u2022 task {task_id} {mark}: {summary}"
    if name == Events.TASK_FAILED:
        error = str(fields.get("error") or fields.get("summary", ""))[:_MAX_LINE]
        return f"\u2022 task {task_id} FAILED: {error}"
    if name == Events.TASK_WAITING:
        return (
            f"\u2022 task {task_id} is waiting for your approval "
            "(see /activity; /approve <id>)"
        )
    if name == Events.TASK_PAUSED:
        return f"\u2022 task {task_id} paused"
    if name == Events.TASK_RESUMED:
        return f"\u2022 task {task_id} resumed"
    if name == Events.SCHEDULER_STARTED:
        return "\u2022 scheduler started"
    if name == Events.SCHEDULER_STOPPED:
        return "\u2022 scheduler stopped"
    if name == Events.SCHEDULER_ERROR:
        return f"\u2022 scheduler error: {str(fields.get('error', ''))[:_MAX_LINE]}"
    return None


class ChatFeed:
    """Thread-safe collector; the chat loop drains it between turns."""

    def __init__(self, bus) -> None:
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._unsubs = [
            bus.subscribe("task.*", self._capture),
            bus.subscribe("scheduler.*", self._capture),
        ]

    def _capture(self, event: Event) -> None:
        line = render_event_line(event)
        if line is None:
            return
        with self._lock:
            self._lines.append(line)

    def drain(self) -> list[str]:
        """Return everything collected since the last drain, and clear."""
        with self._lock:
            lines, self._lines = self._lines, []
            return lines

    def close(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover - never break shutdown
                pass
        self._unsubs.clear()