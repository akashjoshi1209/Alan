"""Session persistence and its containers.

What is pinned here:

* ``alan.assistant.session_path`` is the only way a session name becomes a
  file path, and it refuses traversal -- ``../x``, ``a\\b``, absolute paths and
  empty names are mistakes, not a way to read an arbitrary file. Sessions live
  in ``data/sessions``, period.
* ``save_session``/``load_session`` round-trip history, and ``load_session``
  re-checks the container even when handed an absolute path.
* a memory decision that fails *after* a streaming reply has already gone out
  must not turn into a failed turn: ``stream`` completes and remembers the
  exchange regardless.

The fake runtime keeps every test local: no model provider, no network, no
memory backend is built.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.assistant import Alan, session_path  # noqa: E402
from alan.config import SESSION_DIR  # noqa: E402


class _FakeOrchestrator:
    def requires_capability(self, text: str) -> bool:
        # Regex-free, tool-free requests are the streaming path.
        return False

    def lesson_hint(self, text: str) -> str:
        return ""

    def store_turn(self, *args, **kwargs):  # pragma: no cover - thin
        raise RuntimeError("memory backend exploded")


class _StubModel:
    name = "stub"
    model = "stub"

    def describe(self) -> str:
        return "stub provider"

    def stream(self, messages, system):
        yield "hello from the stub"


class _FakeRouter:
    def __init__(self) -> None:
        self.providers = [_StubModel()]


class _FakeRuntime:
    memory = None

    def __init__(self) -> None:
        self.orchestrator = _FakeOrchestrator()
        self.router = _FakeRouter()


class SessionPathTest(unittest.TestCase):
    def test_plain_name_resolves_under_the_session_dir(self) -> None:
        self.assertEqual(
            session_path("my-session"), SESSION_DIR / "my-session.json"
        )

    def test_names_are_contained_disallowing_traversal(self) -> None:
        for bad in (
            "../escape",
            "a/b",
            "a\\b",
            "..",
            ".",
            "",
            "  ",
            "/absolute",
            "C:\\Windows\\x",
            "..\\..\\x",
        ):
            with self.assertRaises(ValueError, msg=repr(bad)):
                session_path(bad)

    def test_non_string_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            session_path(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            session_path(7)  # type: ignore[arg-type]


class SaveLoadSessionTest(unittest.TestCase):
    def test_round_trip_preserves_history_and_id(self) -> None:
        alan = Alan(runtime=_FakeRuntime())
        alan.history = [{"role": "user", "content": "hi"}]
        alan.session_id = "session-abc"
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("alan.assistant.SESSION_DIR", Path(td)):
                path = alan.save_session("round-trip")
                self.assertEqual(Path(td) / "round-trip.json", path)

                again = Alan(runtime=_FakeRuntime())
                again.load_session(path)
                self.assertEqual(again.history, [{"role": "user", "content": "hi"}])
                self.assertEqual(again.session_id, "session-abc")

    def test_load_rejects_a_path_outside_the_session_dir(self) -> None:
        alan = Alan(runtime=_FakeRuntime())
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "secrets.json"
            outside.write_text('{"messages": []}', encoding="utf-8")
            with mock.patch("alan.assistant.SESSION_DIR", Path(td) / "sessions"):
                with self.assertRaises(ValueError):
                    alan.load_session(outside)

    def test_load_joins_relative_names_into_the_session_dir(self) -> None:
        alan = Alan(runtime=_FakeRuntime())
        with tempfile.TemporaryDirectory() as td:
            sessions = Path(td) / "sessions"
            sessions.mkdir()
            (sessions / "mine.json").write_text(
                '{"session_id": "x1", "messages": [1]}', encoding="utf-8"
            )
            with mock.patch("alan.assistant.SESSION_DIR", sessions):
                alan.load_session("mine.json")
                self.assertEqual(alan.session_id, "x1")

    def test_load_rejects_traversal_through_a_relative_name(self) -> None:
        alan = Alan(runtime=_FakeRuntime())
        with tempfile.TemporaryDirectory() as td:
            sessions = Path(td) / "sessions"
            sessions.mkdir()
            outside = Path(td) / "escape.json"
            outside.write_text('{"messages": []}', encoding="utf-8")
            with mock.patch("alan.assistant.SESSION_DIR", sessions):
                with self.assertRaises(ValueError):
                    alan.load_session("../escape.json")


class StreamResilienceTest(unittest.TestCase):
    def test_a_failed_memory_decision_does_not_fail_the_turn(self) -> None:
        alan = Alan(runtime=_FakeRuntime())
        chunks = list(alan.stream("hello", remember=True))
        self.assertEqual("".join(chunks), "hello from the stub")
        # The exchange was remembered even though store_turn raised.
        self.assertEqual(len(alan.history), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()