"""Phase 4: the Telegram approval bridge.

What is pinned here, in behaviour terms:

* the bridge is a *long-poll* client -- it calls ``getUpdates`` with an
  advancing offset and lets Telegram call back nothing at all;
* ``/approve`` and ``/reject`` from the configured chat become the same
  ``decide`` calls the chat loop makes; everyone else is met with total
  silence (fail-closed);
* approvals announced on the event bus reach the configured chat only while
  the bridge is started, and a stopped bridge says nothing;
* the token never appears in a repr, and a bot API failure is counted and
  survived, never fatal.

No test here touches the network: all traffic goes through a fake transport,
and real API calls are only reachable through ``_TelegramHTTP`` which nothing
in this file constructs.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.core.events import EventBus, Events  # noqa: E402
from alan.telegram import TelegramAPIError, TelegramBridge  # noqa: E402


class _FakeTransport:
    """Records calls, hands back canned batches, and can be armed to fail."""

    def __init__(self, batches=None, *, fail_once=False):
        self.batches = [list(b) for b in (batches or [])]
        self.fail_once = fail_once
        self.calls: list[int] = []
        self.poll_seconds: list[float] = []
        self.sent: list[tuple[str, str]] = []

    def get_updates(self, offset, poll_seconds):
        self.calls.append(offset)
        self.poll_seconds.append(poll_seconds)
        if self.fail_once:
            self.fail_once = False
            raise TelegramAPIError("conflict: terminated by other getUpdates request")
        return self.batches.pop(0) if self.batches else []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


def _make(
    transport,
    *,
    decide=None,
    activity=None,
    bus=None,
    chat_id="101",
    notify_tasks=True,
):
    return TelegramBridge(
        "tok-123",
        chat_id,
        decide=decide or (lambda aid, approve: f"decided {aid} app={approve}"),
        activity=activity,
        bus=bus,
        transport=transport,
        retry_seconds=0.2,
        notify_tasks=notify_tasks,
    )


def _update(update_id, chat, text):
    message = {"chat": {"id": chat}}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


class CommandRoutingTest(unittest.TestCase):
    def test_authorized_approve_is_decided(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or f"ok {aid}",
        )
        bridge._handle_update(_update(1, "101", "/approve abc123"))
        self.assertEqual(calls, [("abc123", True)])
        self.assertEqual(transport.sent, [("101", "ok abc123")])

    def test_authorized_reject_is_decided(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "refused",
        )
        bridge._handle_update(_update(2, "101", "/reject  77cc"))
        self.assertEqual(calls, [("77cc", False)])
        self.assertEqual(transport.sent[0][1], "refused")

    def test_short_id_suffixes_are_passed_through(self) -> None:
        # The decide callback owns suffix matching (same as chat's /approve);
        # the bridge only has to pass the raw id along.
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "x",
        )
        bridge._handle_update(_update(10, "101", "/approve 5daf"))
        self.assertEqual(calls, [("5daf", True)])

    def test_foreign_chats_are_met_with_silence(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "aha",
        )
        bridge._handle_update(_update(3, "999", "/approve steal"))
        self.assertEqual(calls, [])
        self.assertEqual(transport.sent, [], "a wrong chat gets no reply at all")

    def test_plain_text_gets_help_not_an_action(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "x",
        )
        bridge._handle_update(_update(4, "101", "hello there"))
        self.assertEqual(calls, [])
        self.assertTrue(transport.sent)
        self.assertIn("/approve", transport.sent[0][1])

    def test_empty_target_is_a_usage_message(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "x",
        )
        bridge._handle_update(_update(5, "101", "/approve"))
        self.assertEqual(calls, [])
        self.assertIn("usage", transport.sent[0][1])

    def test_activity_command_returns_the_view(self) -> None:
        transport = _FakeTransport()
        bridge = _make(transport, activity=lambda: "-- tasks & approvals --")
        bridge._handle_update(_update(6, "101", "/activity"))
        self.assertIn("-- tasks & approvals --", transport.sent[0][1])

    def test_a_failing_decide_never_kills_the_loop(self) -> None:
        transport = _FakeTransport()

        def boom(aid, approve):
            raise RuntimeError("queue offline")

        bridge = _make(transport, decide=boom)
        bridge._handle_update(_update(7, "101", "/approve z9"))
        self.assertIn("could not decide", transport.sent[0][1])

    def test_textless_and_non_message_updates_are_ignored(self) -> None:
        transport = _FakeTransport()
        calls: list = []
        bridge = _make(
            transport,
            decide=lambda aid, app: calls.append((aid, app)) or "x",
        )
        bridge._handle_update(_update(8, "101", None))
        bridge._handle_update({"update_id": 9, "edited_message": {"chat": {"id": "101"}}})
        bridge._handle_update({"update_id": 10})
        self.assertEqual(calls, [])
        self.assertEqual(transport.sent, [])


class PollLoopTest(unittest.TestCase):
    def test_poll_offset_advances_past_received_updates(self) -> None:
        transport = _FakeTransport(
            batches=[
                [_update(7, "101", "/activity"), _update(9, "101", "/activity")],
            ]
        )
        bridge = _make(transport)
        bridge.start()
        try:
            deadline = time.monotonic() + 2.0
            while len(transport.calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            bridge.stop()
        self.assertGreaterEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0], 0, "the first poll starts at offset 0")
        self.assertEqual(transport.calls[1], 10, "offset = max(7, 9) + 1")

    def test_a_bot_api_conflict_is_counted_not_fatal(self) -> None:
        transport = _FakeTransport(fail_once=True)
        bridge = _make(transport)
        bridge.start()
        try:
            deadline = time.monotonic() + 2.0
            while bridge.poll_errors < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            bridge.stop()
        self.assertGreaterEqual(bridge.poll_errors, 1)
        self.assertIn("conflict", bridge.last_error)
        self.assertTrue(bridge.started is False, "stop() must have joined the thread")

    def test_start_and_stop_are_idempotent(self) -> None:
        transport = _FakeTransport()
        bridge = _make(transport)
        self.assertFalse(bridge.started)
        self.assertTrue(bridge.start())
        self.assertTrue(bridge.start(), "a second start is a no-op, not an error")
        self.assertTrue(bridge.started)
        bridge.stop()
        self.assertFalse(bridge.started)
        bridge.stop()
        self.assertFalse(bridge.started)

    def test_repr_never_exposes_the_token(self) -> None:
        bridge = TelegramBridge(
            "sekrit-token-98765", "101",
            decide=lambda aid, app: "x",
            transport=_FakeTransport(),
        )
        self.assertNotIn("sekrit", repr(bridge))
        self.assertIn("101", repr(bridge))


class AnnouncementTest(unittest.TestCase):
    def test_only_a_started_bridge_announces(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bus.emit(
            Events.APPROVAL_SUBMITTED,
            approval_id="apr_1", capability="browser.open", risk="high_risk",
            task_id="tsk_9", status="pending",
        )
        self.assertEqual(transport.sent, [], "a bridge that was never started says nothing")
        bridge.start()
        try:
            bus.emit(
                Events.APPROVAL_SUBMITTED,
                approval_id="apr_2", capability="shell.execute", risk="medium_risk",
                task_id="tsk_9", status="pending",
            )
        finally:
            bridge.stop()
        sent = [text for _, text in transport.sent]
        self.assertTrue(
            any("apr_2" in t and "shell.execute" in t and "tsk_9" in t for t in sent),
            "a pending approval must be announced to the configured chat",
        )

    def test_approval_decisions_are_announced_with_the_outcome(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bridge.start()
        try:
            bus.emit(
                Events.APPROVAL_DECIDED,
                approval_id="apr_3", capability="browser.open",
                task_id="tsk_9", status="approved",
            )
        finally:
            bridge.stop()
        sent = [text for _, text in transport.sent]
        self.assertTrue(
            any("apr_3" in t and "approved" in t and "will run again" in t for t in sent)
        )

    def test_close_detaches_from_the_bus(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bridge.start()
        try:
            bus.emit(
                Events.APPROVAL_SUBMITTED,
                approval_id="before", capability="x", risk="lo", status="pending",
            )
        finally:
            bridge.close()
        self.assertTrue(any("before" in t for _, t in transport.sent))
        before = len(transport.sent)
        bus.emit(
            Events.APPROVAL_SUBMITTED,
            approval_id="after", capability="x", risk="lo", status="pending",
        )
        self.assertEqual(len(transport.sent), before, "a closed bridge is detached")


class TaskNotificationTest(unittest.TestCase):
    def test_task_events_reach_the_chat_while_started(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bridge.start()
        try:
            bus.emit(Events.TASK_STARTED, task_id="tsk_a", request="check news")
            bus.emit(Events.TASK_FINISHED, task_id="tsk_a", ok=True, summary="all good")
            bus.emit(Events.TASK_FAILED, task_id="tsk_b", error="boom")
        finally:
            bridge.stop()
        sent = "\n".join(text for _, text in transport.sent)
        self.assertIn("tsk_a started", sent)
        self.assertIn("tsk_a done", sent)
        self.assertIn("tsk_b FAILED", sent)
        self.assertIn("boom", sent)

    def test_waiting_is_left_to_the_approval_line(self) -> None:
        # A task waiting for approval must not be announced twice on the phone:
        # the approval.submitted line already carries the id and /approve
        # instruction. The task.* variant stays silent.
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bridge.start()
        try:
            bus.emit(Events.TASK_WAITING, task_id="tsk_c", status="waiting_approval")
        finally:
            bridge.stop()
        self.assertEqual(transport.sent, [])

    def test_notify_tasks_can_be_switched_off(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus, notify_tasks=False)
        bridge.start()
        try:
            bus.emit(Events.TASK_STARTED, task_id="tsk_a", request="check news")
        finally:
            bridge.stop()
        self.assertEqual(transport.sent, [], "notify_tasks=false silences task lines")

    def test_task_events_before_start_are_quiet(self) -> None:
        bus = EventBus()
        transport = _FakeTransport()
        bridge = _make(transport, bus=bus)
        bus.emit(Events.TASK_STARTED, task_id="tsk_a", request="check news")
        self.assertEqual(transport.sent, [])


class ConfigAndRuntimeTest(unittest.TestCase):
    def test_telegram_defaults_to_disabled_in_effect(self) -> None:
        from alan.config import Settings

        tg = Settings().telegram
        self.assertEqual(tg.bot_token, "")
        self.assertEqual(tg.chat_id, "")
        self.assertTrue(tg.enabled, "enabled stays on; the blank token is the switch")
        self.assertTrue(tg.notify_tasks)

    def test_telegram_table_is_applied(self) -> None:
        from alan.config import Settings, _apply_tables

        settings = Settings()
        _apply_tables(
            settings,
            {
                "telegram": {
                    "bot_token": "123:abc", "chat_id": "42",
                    "long_poll": 30.0, "retry_seconds": 1.0,
                    "notify_tasks": False,
                }
            },
        )
        self.assertEqual(settings.telegram.bot_token, "123:abc")
        self.assertEqual(settings.telegram.chat_id, "42")
        self.assertEqual(settings.telegram.long_poll, 30.0)
        self.assertEqual(settings.telegram.retry_seconds, 1.0)
        self.assertFalse(settings.telegram.notify_tasks)

    def test_missing_table_keeps_defaults(self) -> None:
        from alan.config import Settings, _apply_tables

        settings = Settings()
        _apply_tables(settings, {})
        self.assertEqual(settings.telegram.bot_token, "")
        self.assertEqual(settings.telegram.chat_id, "")

    def test_runtime_builds_no_bridge_without_configuration(self) -> None:
        from alan.config import load_settings
        from alan.runtime import build_runtime

        settings = load_settings()
        settings.telegram.bot_token = ""
        settings.telegram.chat_id = ""
        runtime = build_runtime(settings, register_tools=False)
        try:
            self.assertIsNone(runtime.telegram)
            entry = runtime.status()["telegram"]
            self.assertFalse(entry["enabled"])
            self.assertIn("not configured", entry["note"])
        finally:
            runtime.close()

    def test_runtime_builds_the_bridge_but_never_starts_it(self) -> None:
        from alan.config import load_settings
        from alan.runtime import build_runtime

        settings = load_settings()
        settings.telegram.bot_token = "123:secret"
        settings.telegram.chat_id = "42"
        runtime = build_runtime(settings, register_tools=False)
        try:
            self.assertIsNotNone(runtime.telegram)
            self.assertFalse(
                runtime.telegram.started,
                "a build must never start the poller (chat owns it)",
            )
            entry = runtime.status()["telegram"]
            self.assertTrue(entry["enabled"])
            self.assertEqual(entry["chat_id"], "42")
        finally:
            runtime.close()


class DoctorSurfaceTest(unittest.TestCase):
    def test_doctor_source_renders_telegram(self) -> None:
        """Source-level like the browser/internet guards: the doctor panel must
        actually list the telegram state line, or an operator debugging a dead
        bridge has no single command that names it."""
        cli_source = (
            Path(__file__).resolve().parents[1] / "src" / "alan" / "cli.py"
        ).read_text(encoding="utf-8")
        doctor_body = cli_source.split("def doctor(", 1)[1].split("@app.command()", 1)[0]
        self.assertIn("status['telegram']['note']", doctor_body)


if __name__ == "__main__":
    unittest.main()