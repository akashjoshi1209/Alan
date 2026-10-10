"""Phase 5: the headless always-on surface (`alan serve`).

Dots / Grok Bot / Muse all keep working while no app is open. The honest copy
of that is: keep working while *the terminal is closed*. `alan serve`
runs the scheduler and (when configured) the Telegram approval bridge
with no chat surface, stopping on Ctrl+C.

What is pinned here:

* `_start_always_on` reports exactly which loops are on, and the bridge starts
  with the same chat-owned lifecycle as usual (built by the runtime, started
  only by this surface);
* with nothing configured it is an explicit no-op, not a silent background;
* the serve command stops both loops and closes the runtime on the way out
  (source-level guard, same style as the doctor guards);
* tests never touch the network -- the bridge in the start test uses a fake
  transport and a real scheduler is stopped immediately.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.assistant import Alan  # noqa: E402
from alan.cli import _start_always_on  # noqa: E402
from alan.config import load_settings  # noqa: E402
from alan.runtime import build_runtime  # noqa: E402
from alan.telegram import TelegramBridge  # noqa: E402


class _QuietTransport:
    """A transport that never returns updates and records nothing: the poll
    loop paces itself on empty answers, so a start/stop never touches the
    network."""

    def get_updates(self, offset, poll_seconds):  # pragma: no cover - trivial
        return []

    def send_message(self, chat_id, text):  # pragma: no cover - trivial
        return True


class StartAlwaysOnTest(unittest.TestCase):
    def _runtime(self, *, scheduler_on, telegram_on):
        settings = load_settings()
        settings.scheduler.enabled = scheduler_on
        settings.telegram.bot_token = ""
        settings.telegram.chat_id = ""
        runtime = build_runtime(settings, register_tools=False)
        if telegram_on:
            runtime.telegram = TelegramBridge(
                "tok-123",
                "42",
                decide=runtime.decide_approval,
                activity=lambda: runtime.activity_text(),
                transport=_QuietTransport(),
            )
        return runtime

    def test_reports_when_nothing_is_configured(self) -> None:
        runtime = self._runtime(scheduler_on=False, telegram_on=False)
        alan = Alan(runtime=runtime)
        try:
            self.assertEqual(_start_always_on(alan), (False, False))
        finally:
            alan.stop_scheduler()
            alan.stop_telegram()
            alan.close()

    def test_starts_the_scheduler_when_enabled(self) -> None:
        runtime = self._runtime(scheduler_on=True, telegram_on=False)
        alan = Alan(runtime=runtime)
        try:
            self.assertEqual(_start_always_on(alan), (True, False))
        finally:
            alan.stop_scheduler()
            alan.stop_telegram()
            alan.close()

    def test_starts_the_bridge_when_configured(self) -> None:
        runtime = self._runtime(scheduler_on=False, telegram_on=True)
        alan = Alan(runtime=runtime)
        try:
            self.assertEqual(_start_always_on(alan), (False, True))
            self.assertTrue(runtime.telegram.started)
        finally:
            alan.stop_scheduler()
            alan.stop_telegram()
            alan.close()
        self.assertFalse(runtime.telegram.started, "stopping the surface stops the loop")


class ServeSourceGuardTest(unittest.TestCase):
    """Source-level, in the style of the doctor guards: the serve command must
    actually start both loops, name the nothing-to-serve path, and stop both
    loops in its finally block -- or the headless surface silently half-exists."""

    def setUp(self) -> None:
        self.cli_source = (
            Path(__file__).resolve().parents[1] / "src" / "alan" / "cli.py"
        ).read_text(encoding="utf-8")
        serve_body = self.cli_source.split("def serve(", 1)[1].split("\n\n\ndef ", 1)[0]
        self.serve_body = serve_body

    def test_start_and_stop_contract(self) -> None:
        self.assertIn("_start_always_on", self.serve_body)
        self.assertIn("alan.stop_scheduler()", self.serve_body)
        self.assertIn("alan.stop_telegram()", self.serve_body)
        self.assertIn("finally:", self.serve_body)

    def test_nothing_to_serve_is_explicit(self) -> None:
        self.assertIn("nothing to serve", self.serve_body)
        self.assertIn("raise typer.Exit(code=1)", self.serve_body)

    def test_help_lists_the_seconds_knob(self) -> None:
        self.assertIn('"--seconds"', self.serve_body.replace("'", '"'))


if __name__ == "__main__":
    unittest.main()