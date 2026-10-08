"""Offline tests for Alan's cognee memory client - no network, stdlib only.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import alan.memory.cognee_client as cog
from alan.assistant import Alan
from alan.config import MemorySettings, load_settings
from alan.interfaces.memory import MemoryProvider
from alan.interfaces.types import MemoryItem, MemoryLevel
from alan.llm.base import Provider
from alan.memory.cognee_client import CogneeMemory, _entry_text
from alan.memory.providers import build_memory_provider
from alan.memory.providers.json_store import JsonMemory
from alan.runtime import build_runtime


class _Response:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeRequests:
    class RequestException(Exception):
        pass

    def __init__(self, get_response=None, post_response=None) -> None:
        self._get = get_response
        self._post = post_response
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._get

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._post


class _StubProvider(Provider):
    name = "stub"
    model = "stub"
    reply = "stub reply"

    def chat(self, messages, system=None):  # type: ignore[override]
        return self.reply


class _FakeMemory(MemoryProvider):
    """A MemoryProvider stub, so these tests exercise Alan's own contract.

    The old version took a bare string and had no ``search``; the interface it
    stands in for does neither.
    """

    name = "fake"

    def __init__(self) -> None:
        self.remembered: list[str] = []
        self.searched: list[str] = []
        self.blocks: list[str] = []

    def available(self):
        return True, "fake"

    def context_block(self, query, *, max_chars=2000, top_k=5):
        self.blocks.append(query)
        return "Relevant memories:\n- a remembered fact"
    def remember(self, item, **kwargs):
        self.remembered.append(item.content)
        return item

    def forget(self, item_id=None, *, everything=False):
        return 0

    def update(self, item_id, item):
        return True

    def retrieve(self, item_id):
        return None

    def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
        self.searched.append(query)
        return [
            MemoryItem(content="a remembered fact", level=MemoryLevel.L4_USER)
        ]

    def summarize(self, query="", *, top_k=20):
        return "a remembered fact"

    def promote_to_long_term(self, item):
        item.level = MemoryLevel.L4_USER
        return item


class _StubCognee(_FakeMemory):
    """Named ``cognee`` so it stands in for the real default backend."""

    name = "cognee"


def _memory(**overrides) -> CogneeMemory:
    settings = load_settings().memory
    for key, value in overrides.items():
        setattr(settings, key, value)
    return CogneeMemory(settings)


class EntryTextTest(unittest.TestCase):
    def test_priority_order(self) -> None:
        self.assertEqual(_entry_text({"text": "T"}), "T")
        self.assertEqual(_entry_text({"answer": "A"}), "A")
        self.assertEqual(_entry_text({"content": "C"}), "C")
        self.assertEqual(_entry_text({"context": "X"}), "X")

    def test_blank_and_missing(self) -> None:
        self.assertEqual(_entry_text({}), "")
        self.assertEqual(_entry_text({"text": "   "}), "")


class RecallTest(unittest.TestCase):
    def test_parses_and_dedups(self) -> None:
        payload = [
            {"source": "graph", "text": "Sam likes teal."},
            {"source": "graph", "text": "Sam likes teal."},
            {"source": "session", "answer": "Sam lives in Dublin."},
            {"source": "graph", "text": ""},
        ]
        fake = _FakeRequests(post_response=_Response(200, payload))
        mem = _memory()
        with mock.patch.object(cog, "requests", fake):
            hits = mem.recall("where does Sam live?")
        self.assertEqual(hits, ["Sam likes teal.", "Sam lives in Dublin."])
        _, url, kwargs = fake.calls[0]
        self.assertTrue(url.endswith("/api/v1/recall"))
        self.assertEqual(kwargs["json"]["searchType"], "CHUNKS")
        self.assertEqual(kwargs["json"]["datasets"], [mem.dataset])

    def test_no_data_404_is_empty(self) -> None:
        fake = _FakeRequests(post_response=_Response(404, None))
        with mock.patch.object(cog, "requests", fake):
            self.assertEqual(_memory().recall("anything"), [])

    def test_blank_query_short_circuits(self) -> None:
        fake = _FakeRequests(post_response=_Response(200, []))
        with mock.patch.object(cog, "requests", fake):
            self.assertEqual(_memory().recall("   "), [])
        self.assertEqual(fake.calls, [])


class WriteTest(unittest.TestCase):
    def test_remember_multipart(self) -> None:
        fake = _FakeRequests(post_response=_Response(200, {"status": "running"}))
        with mock.patch.object(cog, "requests", fake):
            self.assertTrue(_memory().remember("hello memory"))
        _, url, kwargs = fake.calls[0]
        self.assertTrue(url.endswith("/api/v1/remember"))
        self.assertEqual(kwargs["files"][0][0], "raw_data")
        self.assertEqual(kwargs["data"]["datasetName"], "alan")
        self.assertEqual(kwargs["data"]["self_improvement"], "false")

    def test_remember_blank(self) -> None:
        fake = _FakeRequests(post_response=_Response(200, {}))
        with mock.patch.object(cog, "requests", fake):
            self.assertFalse(_memory().remember("   "))
        self.assertEqual(fake.calls, [])

    def test_forget_memory_only_payload(self) -> None:
        fake = _FakeRequests(post_response=_Response(200, {}))
        with mock.patch.object(cog, "requests", fake):
            self.assertTrue(_memory().forget(memory_only=True))
        _, url, kwargs = fake.calls[0]
        self.assertTrue(url.endswith("/api/v1/forget"))
        self.assertEqual(kwargs["json"]["dataset"], "alan")
        self.assertTrue(kwargs["json"]["memoryOnly"])


class ContextBlockTest(unittest.TestCase):
    def test_formats_hits(self) -> None:
        mem = _memory()
        mem.recall = lambda query, **kwargs: ["one fact", "two fact"]  # type: ignore[assignment]
        block = mem.context_block("q")
        self.assertIn("one fact", block)
        self.assertIn("two fact", block)

    def test_truncates_to_budget(self) -> None:
        mem = _memory()
        mem.recall = lambda query, **kwargs: ["x" * 100, "y" * 100]  # type: ignore[assignment]
        block = mem.context_block("q", max_chars=30)
        self.assertEqual(block, "")

    def test_empty_when_no_hits(self) -> None:
        mem = _memory()
        mem.recall = lambda query, **kwargs: []  # type: ignore[assignment]
        self.assertEqual(mem.context_block("q"), "")


class AssistantMemoryTest(unittest.TestCase):
    """The assistant delegates every memory decision to the orchestrator.

    These tests pin the behaviour that replaced the old "queue every turn"
    design: a transcript is not a memory, and only the gate decides.
    """

    def _assistant(self, **memory_settings) -> Alan:
        settings = load_settings()
        settings.memory.auto_recall = memory_settings.get("auto_recall", True)
        settings.memory.auto_remember = memory_settings.get("auto_remember", True)
        settings.memory.backend = "json"
        # The stub is not a local model, and a memory request must stay local by
        # default. Relax the rule so these tests can reach the decision gate; the
        # strict behaviour is covered in test_core.
        settings.permissions.local_only = False
        return Alan(settings=settings, provider=_StubProvider())

    def test_recall_reaches_the_streaming_prompt(self) -> None:
        bot = self._assistant()
        fake = _FakeMemory()
        bot.memory = fake
        bot.runtime.memory = fake  # type: ignore[assignment]
        bot.runtime.orchestrator.memory = fake
        chunks = list(bot.stream("what do you know?"))
        self.assertEqual(chunks, [_StubProvider.reply])
        self.assertTrue(
            fake.blocks, "stream() must consult memory before answering"
        )

    def test_no_memory_backend_still_answers(self) -> None:
        bot = self._assistant()
        bot.memory = None
        bot.runtime.memory = None  # type: ignore[assignment]
        bot.runtime.orchestrator.memory = None
        chunks = list(bot.stream("hello"))
        self.assertEqual(chunks, [_StubProvider().reply])

    def test_plain_chatter_is_not_remembered(self) -> None:
        """The regression this gate exists for."""
        bot = self._assistant()
        fake = _FakeMemory()
        bot.runtime.memory = fake  # type: ignore[assignment]
        bot.runtime.orchestrator.memory = fake
        bot.ask("hello there")
        bot.flush_memory()
        self.assertEqual(
            fake.remembered, [], "a greeting must not become a durable memory"
        )
        self.assertEqual(bot.pending_memories(), 0)

    def test_explicit_remember_is_stored(self) -> None:
        bot = self._assistant()
        fake = _FakeMemory()
        bot.runtime.memory = fake  # type: ignore[assignment]
        bot.runtime.orchestrator.memory = fake
        bot.ask("remember that I prefer dark mode")
        self.assertTrue(any("dark mode" in text for text in fake.remembered))
        self.assertEqual(bot.pending_memories(), 1)

    def test_remember_disabled_leaves_nothing_pending(self) -> None:
        bot = self._assistant(auto_remember=False)
        fake = _FakeMemory()
        bot.runtime.memory = fake  # type: ignore[assignment]
        bot.runtime.orchestrator.memory = fake
        bot.ask("remember that I prefer dark mode")
        self.assertEqual(bot.pending_memories(), 0)

    def test_history_records_the_turn(self) -> None:
        bot = self._assistant()
        bot.ask("hello")
        self.assertEqual(len(bot.history), 2)
        self.assertEqual(bot.history[0]["role"], "user")
        self.assertEqual(bot.history[1]["role"], "assistant")


class MemorySettingsTest(unittest.TestCase):
    def test_defaults_present(self) -> None:
        mem = load_settings().memory
        self.assertTrue(mem.enabled)
        self.assertIn("8012", mem.base_url)
        self.assertTrue(mem.dataset)


class BackendFallbackNoteTest(unittest.TestCase):
    """A silent memory fallback is a correctness problem, not a cosmetic one.

    cognee gives semantic recall; the JSON store gives keyword lookup. If cognee
    is not running, Alan still answers and still remembers -- just differently,
    with nothing in its output to say so. These tests pin that the degradation
    is reported, with the reason and the command to fix it.
    """

    def _build(self, backend: str) -> tuple[object, str]:
        settings = MemorySettings(enabled=True, backend=backend)
        with mock.patch(
            "alan.memory.providers.CogneeProvider.available",
            return_value=(False, "cognee sidecar not reachable at http://x"),
        ):
            return build_memory_provider(settings)

    def test_fallback_note_keeps_the_primarys_reason(self) -> None:
        """Dropping the reason leaves the reader unable to act on it."""
        provider, note = self._build("cognee")
        self.assertEqual(provider.name, "json")
        self.assertIn("cognee was unavailable", note)
        self.assertIn("not reachable", note)

    def test_auto_backend_reports_the_unavailable_primary(self) -> None:
        _provider, note = self._build("auto")
        self.assertIn("cognee unavailable", note)
        self.assertIn("using json", note)

    def test_runtime_names_the_command_to_start_cognee(self) -> None:
        settings = load_settings()
        settings.memory.backend = "cognee"
        with mock.patch(
            "alan.runtime.build_memory_provider",
            return_value=(JsonMemory(None), "using json fallback"),
        ):
            runtime = build_runtime(settings, register_tools=False)
        try:
            note = runtime.status()["memory"]["note"]
        finally:
            runtime.close()
        self.assertIn("cognee-serve.ps1", note)

    def test_no_command_is_suggested_when_the_choice_was_deliberate(self) -> None:
        """Asking for json and getting json is not a failure, so it must not
        be reported as one -- otherwise the hint nags a correct install."""
        settings = load_settings()
        settings.memory.backend = "json"
        with mock.patch(
            "alan.runtime.build_memory_provider",
            return_value=(JsonMemory(None), "ready"),
        ):
            runtime = build_runtime(settings, register_tools=False)
        try:
            note = runtime.status()["memory"]["note"]
        finally:
            runtime.close()
        self.assertNotIn("cognee-serve.ps1", note)

    def test_auto_with_cognee_running_is_not_a_fallback(self) -> None:
        """In `auto` mode cognee is the first choice, so cognee being active is
        success. Getting this wrong nags users to start the sidecar they are
        already running."""
        settings = load_settings()
        settings.memory.backend = "auto"
        cognee = _StubCognee()
        with mock.patch(
            "alan.runtime.build_memory_provider", return_value=(cognee, "ready")
        ):
            runtime = build_runtime(settings, register_tools=False)
        try:
            note = runtime.status()["memory"]["note"]
        finally:
            runtime.close()
        self.assertNotIn("cognee-serve.ps1", note)


if __name__ == "__main__":
    unittest.main()
