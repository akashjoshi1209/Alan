"""The memory commands -- CLI and in-chat -- must talk to the configured backend.

`alan memory` and `ingest` used to construct ``CogneeMemory`` directly,
bypassing ``build_memory_provider`` entirely. The effect was that the moment
memory stopped defaulting to the sidecar, both reported an unreachable cognee
service while a perfectly healthy SQLite store sat one line away -- and
``forget --list`` listed nothing, because it reached for internals only two
providers still exposed. The in-chat slash commands had the same disease from
the other direction: they called methods only the cognee client had, so
``/remember``, ``/recall``, ``/memory`` and ``/forget`` each raised on first
use against the default backend. One factory, one truth.

`conftest.py` points ``ALAN_MEMORY_DIR`` at scratch, so these tests exercise
the real factory and the real store without touching the live one.
"""

from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest import mock

from rich.console import Console

from alan import cli
from alan.config import load_settings
from alan.interfaces.types import MemoryItem, MemoryLevel


def _settings():
    """Real settings with the default backend named explicitly.

    `auto` would resolve to the same store here, but naming it keeps the test
    from passing merely because a sidecar happened to be up or down on the day.
    """
    settings = load_settings()
    settings.memory.enabled = True
    settings.memory.backend = "local"
    return settings


class CliMemoryCommandsTest(unittest.TestCase):
    """The CLI must reach memory through the provider factory."""

    def setUp(self) -> None:
        self.out = Console(file=io.StringIO(), width=100)
        patcher = mock.patch.object(cli, "console", self.out)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings_patcher = mock.patch.object(cli, "load_settings", _settings)
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)
        # One store per test. These commands share a process-wide database, so
        # without this a count assertion would depend on which test ran first.
        cli.forget(contains=None, everything=True, yes=True, show=False)
        self.clear()

    def rendered(self) -> str:
        self.out.file.seek(0)
        return self.out.file.read()

    def clear(self) -> str:
        """Everything printed so far, then start a fresh buffer.

        These commands print as they go, so an assertion that runs after two
        calls must not be reading the first one's output -- that mistake turns
        a passing behaviour into a red test that says nothing.
        """
        text = self.rendered()
        self.out.file.seek(0)
        self.out.file.truncate()
        return text

    def test_memory_reports_the_backend_it_is_actually_using(self) -> None:
        cli.memory(query=None)
        text = self.rendered()
        self.assertIn("memory: local", text)
        self.assertNotIn("cognee", text.lower(),
                         "the command must not report a sidecar it never opened")

    def test_memory_reports_the_store_contents(self) -> None:
        cli.ingest(text="a durable note the report can count".split())
        self.clear()
        cli.memory(query=None)
        text = self.rendered()
        self.assertIn("items:", text)
        self.assertIn("sessions:", text)
        self.assertIn("L4_USER", text, "the level breakdown should be visible")

    def test_ingest_stores_a_durable_fact_and_recall_finds_it(self) -> None:
        note = "the invoices for 2026 live in the archive folder"
        cli.ingest(text=note.split())
        self.assertIn("stored in memory", self.rendered())
        self.clear()

        # A durable fact, not a session note: it has to survive this session.
        cli.memory(query="where are the 2026 invoices")
        self.assertIn("archive folder", self.rendered(),
                      "an ingested note must be recallable immediately")

    def test_ingest_refuses_to_store_nothing(self) -> None:
        with self.assertRaises(cli.typer.Exit) as caught:
            cli.ingest(text=[])
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertIn("nothing to store", self.rendered())

    def test_forget_removes_a_note_by_text(self) -> None:
        cli.ingest(text="scratch note for the forget test".split())
        self.clear()
        cli.forget(contains="scratch note", everything=False, yes=False, show=False)
        self.assertIn("removed 1 item(s)", self.rendered())
        self.clear()

        # ...and it is genuinely gone, not merely not-shown.
        cli.memory(query="scratch note")
        self.assertNotIn("scratch note", self.rendered())

    def test_forget_all_still_demands_a_confirmation(self) -> None:
        with self.assertRaises(cli.typer.Exit) as caught:
            cli.forget(contains=None, everything=True, yes=False, show=False)
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertIn("Add --yes to confirm", self.rendered())

    def test_forget_list_shows_what_is_stored(self) -> None:
        cli.ingest(text="a note about the budget".split())
        self.clear()
        cli.forget(contains=None, everything=False, yes=False, show=True)
        self.assertIn("a note about the budget", self.rendered())

    def test_no_command_reaches_for_the_sidecar_client(self) -> None:
        """The regression itself, pinned.

        `CogneeMemory` is still a shipped backend; it just must not be what
        these commands reach for. Not merely "the module attribute is patched"
        -- the CLI must hold no reference to it at all, so re-adding the
        import fails here rather than in production with cognee down.
        """
        self.assertFalse(
            hasattr(cli, "CogneeMemory"),
            "the CLI must build memory through the provider factory, not a "
            "hand-picked backend",
        )
        cli.memory(query="does this touch the sidecar")
        self.assertNotIn("cognee", self.rendered().lower())


class MemorySlashCommandsTest(unittest.TestCase):
    """The in-chat memory commands, driven directly.

    `cli._memory_slash` used to live inline in the chat loop, calling methods
    that only the cognee client had: `remember(text, background=...)`,
    `recall(...)`, `health()`, `datasets()`, `dataset`. On the default backend
    each one raised, so the four commands a person reaches for to check that
    memory works were the four that provably did not.
    """

    def setUp(self) -> None:
        self.out = Console(file=io.StringIO(), width=100)
        patcher = mock.patch.object(cli, "console", self.out)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings_patcher = mock.patch.object(cli, "load_settings", _settings)
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)
        cli.forget(contains=None, everything=True, yes=True, show=False)
        self.clear()

        provider, _note = cli._open_memory()
        self.assertIsNotNone(provider)
        self.store = provider
        self.addCleanup(provider.close)
        self.alan = SimpleNamespace(memory=provider,
                                    session_id="session-slash")

    def rendered(self) -> str:
        self.out.file.seek(0)
        return self.out.file.read()

    def clear(self) -> str:
        text = self.rendered()
        self.out.file.seek(0)
        self.out.file.truncate()
        return text

    def test_an_ordinary_line_is_not_a_memory_command(self) -> None:
        self.assertFalse(cli._memory_slash(self.alan, "hello there"))
        self.assertFalse(cli._memory_slash(self.alan, "/recallx"))
        self.assertEqual(self.rendered(), "", "it must stay out of the way")

    def test_remember_then_recall_round_trips(self) -> None:
        self.assertTrue(cli._memory_slash(self.alan, "/remember the api key lives in the vault"))
        self.assertIn("stored", self.rendered())
        self.clear()

        self.assertTrue(cli._memory_slash(self.alan, "/recall where does the api key live"))
        self.assertIn("api key lives in the vault", self.rendered())

    def test_remember_needs_some_text(self) -> None:
        cli._memory_slash(self.alan, "/remember")
        self.assertIn("usage: /remember TEXT", self.rendered())

    def test_recall_needs_a_query_and_must_not_dump_the_store(self) -> None:
        self.store.remember(MemoryItem(content="a stored fact",
                                       level=MemoryLevel.L4_USER))
        self.clear()
        cli._memory_slash(self.alan, "/recall")
        self.assertIn("usage: /recall QUERY", self.rendered())
        self.assertNotIn("a stored fact", self.rendered(),
                         "an empty query must not become 'match everything'")

    def test_memory_reports_backend_and_counts(self) -> None:
        self.store.remember(MemoryItem(content="a stored fact",
                                       level=MemoryLevel.L4_USER))
        self.clear()
        cli._memory_slash(self.alan, "/memory")
        text = self.rendered()
        self.assertIn("memory ready", text)
        self.assertIn("backend: local", text)
        self.assertIn("1 items", text)

    def test_forget_clears_the_session_and_not_the_durable_facts(self) -> None:
        self.store.remember(MemoryItem(content="a durable user fact",
                                       level=MemoryLevel.L4_USER))
        self.store.remember(MemoryItem(content="a session episode",
                                       level=MemoryLevel.L2_SESSION,
                                       session_id="session-slash"))
        self.clear()

        cli._memory_slash(self.alan, "/forget")
        text = self.rendered()
        self.assertIn("cleared 1 item(s) from this session", text)
        self.assertIn("alan forget --all --yes", text)

        hits = self.store.search("durable user fact", top_k=5)
        self.assertTrue(any("durable user fact" in h.content for h in hits),
                        "/forget must not erase facts the session did not own")
        self.assertFalse(
            any("session episode" in h.content for h in self.store.items()),
            "the session's own memory is what goes",
        )

    def test_a_failing_store_reports_instead_of_crashing_chat(self) -> None:
        with mock.patch.object(self.store, "remember",
                               side_effect=RuntimeError("disk full")):
            self.assertTrue(cli._memory_slash(self.alan, "/remember anything"))
        self.assertIn("could not store memory: disk full", self.rendered())

    def test_memory_disabled_still_answers(self) -> None:
        bare = SimpleNamespace(memory=None, session_id="s")
        self.assertTrue(cli._memory_slash(bare, "/memory"))
        self.assertIn("memory is disabled", self.rendered())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
