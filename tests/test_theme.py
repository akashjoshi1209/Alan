"""Tests for the terminal surface: shared paint, the /help screen, the chat
line reader, the streaming reply renderer, and doctor's status mapping.

These are all pure-presentation paths -- no model, no network, no sidecar --
so they can be pinned exactly: what a human sees at the prompt is a contract
like any other, and the pieces most likely to rot are the ones nobody stares
at until they break (an icon that lies, a completer that drifts from /help, a
reply renderer that swallows Ctrl+C).
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan import cli, theme  # noqa: E402


class ThemeTest(unittest.TestCase):
    """The shared paint."""

    def test_elapsed_reads_like_a_human(self) -> None:
        self.assertEqual(theme.elapsed(0.42), "0.42s")
        self.assertEqual(theme.elapsed(3.14), "3.1s")
        self.assertEqual(theme.elapsed(12.8), "12.8s")
        self.assertEqual(theme.elapsed(125), "2m 05s")

    def test_mark_wires_each_state_to_its_glyph_and_style(self) -> None:
        self.assertEqual(theme.mark("ok"), "[green]✔[/green]")
        self.assertEqual(theme.mark("warn"), "[yellow]▲[/yellow]")
        self.assertEqual(theme.mark("err"), "[red]✖[/red]")
        self.assertEqual(theme.mark("off"), "[dim]○[/dim]")

    def test_an_unknown_state_is_neutral_not_a_crash(self) -> None:
        """Paint must never be why a command dies."""
        self.assertEqual(theme.mark("greenish"), theme.mark("info"))

    def test_each_state_has_its_own_shape_not_only_a_color(self) -> None:
        """A monochrome console (or a color-blind operator) must still see
        which rows are healthy."""
        glyphs = [theme.mark(state).split("]")[1][0] for state in
                  ("ok", "warn", "err", "off")]
        self.assertEqual(len(set(glyphs)), 4, glyphs)


class HelpScreenTest(unittest.TestCase):
    """The /help table and the completer that is built from it."""

    def test_help_lists_every_group_and_its_commands(self) -> None:
        console = Console(record=True, width=100)
        console.print(cli._help_table())
        text = console.export_text()
        for needle in (
            "conversation", "memory", "always-on",
            "/help", "/reset, /new", "/remember TEXT", "/recall QUERY",
            "/schedule add TEXT", "/schedule cancel TERM", "/exit",
        ):
            self.assertIn(needle, text)

    def test_completions_are_the_command_words_and_cannot_drift_from_help(self) -> None:
        options = cli._completion_options()
        for expected in ("/help", "/exit", "/reset", "/load", "/recall",
                         "/approve", "/schedule"):
            self.assertIn(expected, options)
        for option in options:
            self.assertNotIn(" ", option, "Tab completes whole command words")
            self.assertTrue(option.startswith("/"))


class LineReaderTest(unittest.TestCase):
    """The chat prompt must degrade, never fail: no console -> plain input."""

    def test_without_a_console_the_reader_is_plain_and_save_is_a_noop(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("typed")), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            reader = cli._LineReader(Path("unused") / "history.txt")
        self.assertFalse(reader.editing)
        reader.save()  # must not raise and must not create anything

    def test_the_completer_answers_readline_style(self) -> None:
        # "/re" is ambiguous on purpose: /recall, /remember and /reset all
        # share the prefix, and readline asks for matches by index.
        self.assertEqual(cli._LineReader._complete("/re", 0), "/recall")
        self.assertEqual(cli._LineReader._complete("/re", 1), "/remember")
        self.assertEqual(cli._LineReader._complete("/re", 2), "/reset")
        self.assertIsNone(cli._LineReader._complete("/re", 3), "past the matches")
        self.assertIsNone(cli._LineReader._complete("/nothing-here", 0))


class DrawReplyTest(unittest.TestCase):
    """The streamed reply: spinner, markdown, footer, and Ctrl+C semantics."""

    def _recording_console(self) -> Console:
        return Console(record=True, width=100)

    def test_the_whole_reply_is_returned_and_rendered_as_markdown(self) -> None:
        console = self._recording_console()
        with mock.patch.object(cli, "console", console):
            text = cli._draw_reply(iter(["Hello ", "**world**"]), show_time=False)
        self.assertEqual(text, "Hello **world**")
        rendered = console.export_text()
        self.assertIn("Hello", rendered)
        self.assertIn("world", rendered)
        self.assertNotIn("**", rendered, "markdown must be rendered, not dumped")

    def test_an_empty_reply_says_so(self) -> None:
        console = self._recording_console()
        with mock.patch.object(cli, "console", console):
            text = cli._draw_reply(iter([]), show_time=False)
        self.assertEqual(text, "")
        self.assertIn("no reply", console.export_text())

    def test_ctrl_c_cancels_the_reply_and_reports_it(self) -> None:
        """Ctrl+C mid-reply cancels that reply; the session (and the caller's
        loop) survives with the partial text already returned."""

        def interrupted():
            yield "partial "
            raise KeyboardInterrupt

        console = self._recording_console()
        with mock.patch.object(cli, "console", console):
            text = cli._draw_reply(interrupted(), show_time=False)
        self.assertEqual(text, "partial ")
        self.assertIn("cancelled", console.export_text())

    def test_a_normal_reply_never_says_cancelled(self) -> None:
        console = self._recording_console()
        with mock.patch.object(cli, "console", console):
            cli._draw_reply(iter(["done"]), show_time=False)
        self.assertNotIn("cancelled", console.export_text())


def _status(**overrides) -> dict:
    """A runtime.status()-shaped dict with every key _doctor_rows reads."""
    base = {
        "model_note": "1 backend(s): groq:qwen",
        "models": [
            {"provider": "groq", "model": "qwen", "local": False, "available": True}
        ],
        "memory": {"backend": "cognee", "note": "cognee 0.6"},
        "knowledge": {"enabled": True, "note": "3 document(s)"},
        "browser": {"provider": None, "note": "browser sidecar not reachable at http://x"},
        "internet": {"provider": "internet", "note": "internet ready (Exa + Jina)"},
        "vibecoder": "C:\\npm\\vibecoder.CMD",
        "ecc": "293 skills",
        "voice": {"state": "ready", "note": "ready on http://127.0.0.1:8181"},
        "telegram": {
            "enabled": False, "running": False,
            "note": "not configured (set bot_token and chat_id)", "chat_id": "",
        },
        "scheduler": {
            "enabled": True, "running": True, "tasks": 3,
            "waiting": 1, "due": 0, "note": None,
        },
        "pending_approvals": 0,
        "tools": ["open", "grep"],
        "agents": ["orchestrator", "browser"],
        "risk_ceiling": "privileged",
        "trace_file": "D:\\Alan\\data\\logs\\events.jsonl",
    }
    base.update(overrides)
    return base


class DoctorRowsTest(unittest.TestCase):
    """The status-to-icon mapping: the part of doctor that can lie."""

    def _state(self, status: dict, part: str) -> str:
        rows = cli._doctor_rows(status)
        return next(state for name, state, _note in rows if name == part)

    def test_healthy_install_reads_all_green(self) -> None:
        status = _status()
        self.assertEqual(self._state(status, "models"), "ok")
        self.assertEqual(self._state(status, "memory"), "ok")
        self.assertEqual(self._state(status, "internet"), "ok")
        self.assertEqual(self._state(status, "voice"), "ok")

    def test_an_unreachable_sidecar_is_red_not_green(self) -> None:
        status = _status(
            browser={"provider": "browser", "note": "sidecar not reachable at http://x"}
        )
        self.assertEqual(self._state(status, "browser"), "err")

    def test_the_json_memory_fallback_is_a_warning(self) -> None:
        """Working, but nothing smarter answered: worth a triangle, not a check."""
        status = _status(memory={"backend": "json", "note": "json store"})
        self.assertEqual(self._state(status, "memory"), "warn")

    def test_broken_memory_is_red_even_through_the_fallback(self) -> None:
        status = _status(
            memory={"backend": "json", "note": "cognee sidecar not reachable"}
        )
        self.assertEqual(self._state(status, "memory"), "err")

    def test_voice_states_map_to_their_own_glyphs(self) -> None:
        self.assertEqual(
            self._state(_status(voice={"state": "off", "note": "off"}), "voice"), "off")
        self.assertEqual(
            self._state(_status(voice={"state": "configured", "note": "x"}), "voice"),
            "warn")
        self.assertEqual(
            self._state(_status(voice={"state": "something-new", "note": "x"}), "voice"),
            "err", "an unknown state must not draw a checkmark")

    def test_telegram_distinguishes_off_configured_and_running(self) -> None:
        self.assertEqual(self._state(_status(), "telegram"), "off")
        enabled = {"enabled": True, "running": False, "note": "stopped", "chat_id": "1"}
        self.assertEqual(self._state(_status(telegram=enabled), "telegram"), "warn")
        enabled["running"] = True
        self.assertEqual(self._state(_status(telegram=enabled), "telegram"), "ok")

    def test_scheduler_distinguishes_disabled_stopped_and_running(self) -> None:
        disabled = {"enabled": False, "running": False, "note": "disabled in settings"}
        self.assertEqual(self._state(_status(scheduler=disabled), "scheduler"), "off")
        stopped = {"enabled": True, "running": False, "tasks": 2, "note": None}
        self.assertEqual(self._state(_status(scheduler=stopped), "scheduler"), "warn")
        self.assertEqual(self._state(_status(), "scheduler"), "ok")

    def test_a_stopped_loop_with_nothing_scheduled_is_a_fact_not_a_warning(self) -> None:
        idle = {"enabled": True, "running": False, "tasks": 0, "note": None}
        self.assertEqual(self._state(_status(scheduler=idle), "scheduler"), "info")

    def test_brain_enabled_but_unreachable_is_red_not_green(self) -> None:
        status = _status(knowledge={
            "enabled": True, "note": "nomic-embed-text: Ollama unreachable at http://x",
        })
        self.assertEqual(self._state(status, "brain"), "err")
        off = _status(knowledge={"enabled": False, "note": "disabled"})
        self.assertEqual(self._state(off, "brain"), "off")

    def test_a_pending_approval_is_a_triangle_something_waits_on_you(self) -> None:
        self.assertEqual(self._state(_status(), "approvals"), "info")
        rows = cli._doctor_rows(_status(pending_approvals=2))
        state, note = next(
            (s, n) for name, s, n in rows if name == "approvals")
        self.assertEqual(state, "warn")
        self.assertIn("2", note)

    def test_no_available_model_is_an_error(self) -> None:
        status = _status(models=[
            {"provider": "ollama", "model": "qwen", "local": True, "available": False}
        ])
        self.assertEqual(self._state(status, "models"), "err")


if __name__ == "__main__":
    unittest.main()
