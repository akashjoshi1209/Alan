"""Phase 4: the live chat feed ("richer TUI").

The chat loop owns the scheduler, so task lines arrive while a reply is
streaming or a prompt is waiting. The feed captures them at the thread-safe
boundary and the loop drains them at the safe moment -- never mid-prompt.

What is pinned here: which events render and which are skipped, that rendered
lines stay short, and that a drained feed is empty.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.chatfeed import ChatFeed, render_event_line  # noqa: E402
from alan.core.events import Event, EventBus, Events  # noqa: E402


class RenderTest(unittest.TestCase):
    def _line(self, name, **fields):
        return render_event_line(Event(name=name, fields=fields))

    def test_task_started_names_the_request(self) -> None:
        line = self._line(Events.TASK_STARTED, task_id="tsk_a", request="check the news")
        self.assertIn("tsk_a", line)
        self.assertIn("check the news", line)
        self.assertIn("started", line)

    def test_finished_ok_and_failed_are_distinct(self) -> None:
        done = self._line(Events.TASK_FINISHED, task_id="tsk_a", ok=True, summary="all good")
        self.assertIn("tsk_a done", done)
        failed = self._line(Events.TASK_FAILED, task_id="tsk_b", error="boom")
        self.assertIn("tsk_b FAILED", failed)
        self.assertIn("boom", failed)

    def test_waiting_task_points_at_the_decision_surface(self) -> None:
        line = self._line(Events.TASK_WAITING, task_id="tsk_c")
        self.assertIn("tsk_c", line)
        self.assertIn("waiting for your approval", line)
        self.assertIn("/approve", line)

    def test_scheduler_bookends_render(self) -> None:
        self.assertIn("scheduler", self._line(Events.SCHEDULER_STARTED))
        self.assertIn("scheduler", self._line(Events.SCHEDULER_STOPPED))
        self.assertIn("error", self._line(Events.SCHEDULER_ERROR, error="disk full"))

    def test_unrelated_events_are_skipped(self) -> None:
        self.assertIsNone(self._line(Events.TOOL_STARTED, tool="web.search"))
        self.assertIsNone(self._line(Events.APPROVAL_SUBMITTED, approval_id="apr_x"))
        self.assertIsNone(self._line(Events.MODEL_STARTED, provider="ollama"))

    def test_lines_are_bounded(self) -> None:
        line = self._line(Events.TASK_STARTED, task_id="tsk_a", request="x" * 500)
        self.assertLessEqual(len(line), 200)


class FeedTest(unittest.TestCase):
    def test_collects_then_drains_once(self) -> None:
        bus = EventBus()
        feed = ChatFeed(bus)
        bus.emit(Events.TASK_STARTED, task_id="tsk_a", request="hello")
        bus.emit(Events.TOOL_STARTED, tool="x")  # not rendered
        bus.emit(Events.TASK_FAILED, task_id="tsk_b", error="nope")
        lines = feed.drain()
        self.assertEqual(len(lines), 2)
        self.assertEqual(feed.drain(), [], "a drain is a one-shot")
        feed.close()

    def test_close_detaches_from_the_bus(self) -> None:
        bus = EventBus()
        feed = ChatFeed(bus)
        feed.close()
        bus.emit(Events.TASK_STARTED, task_id="tsk_a", request="hello")
        self.assertEqual(feed.drain(), [])


if __name__ == "__main__":
    unittest.main()