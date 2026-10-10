"""The local SQLite memory store and the activity observer.

Two things are being pinned here.

**The store is fast because it is local.** The cognee sidecar this replaced
cost 1.47 s warm and 24 s cold *per chat turn* -- an HTTP round trip in front
of the first token -- while returning nothing. ``test_search_is_fast`` and
``test_context_block_stays_off_the_network`` hold the line this replaces it on:
search is milliseconds and never touches a socket.

**Recording what Alan does is not the same as recording what it is told.** The
observer turns tool calls, MCP calls, sub-agent runs and refusals into
session-scoped episodes, and the tests here make sure that does not leak into
durable facts: the orchestrator's gate stays the only thing that decides a
sentence the user said becomes something Alan *knows*.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Sequence
from unittest import mock
from uuid import uuid4

from alan.core.events import EventBus, Events
from alan.interfaces.memory import MemoryProvider
from alan.interfaces.types import MemoryItem, MemoryLevel, MemoryType
from alan.memory.observer import MemoryObserver
from alan.memory.providers.local_store import LocalMemory, derive_kind


class _FakeEmbedder:
    """Deterministic, offline stand-in for the Ollama embedder.

    Vectors are built from token overlap so a "similar" sentence really does
    land near its partner -- which is what makes the reordering assertions
    meaningful rather than circular.

    ``base_url`` is unique per instance on purpose: embedder availability is
    cached by ``(base_url, model)`` across the process, so two fakes sharing a
    key would let one test's "unavailable" answer be overwritten by another
    test's "available". A UUID rather than ``id(self)`` because CPython
    recycles object ids, and a recycled id would silently re-import a stale
    probe answer from an earlier test.
    """

    name = "fake-embed"
    model = "fake-embed"

    def __init__(self, dims: int = 8, available: bool = True, delay: float = 0.0,
                 fail: bool = False) -> None:
        self.dims = dims
        self.ok = available
        self.delay = delay
        self.fail = fail
        self.base_url = f"http://127.0.0.1:0/{uuid4().hex}"
        self.calls: list[list[str]] = []

    def available(self) -> tuple[bool, str]:
        return self.ok, "fake ready" if self.ok else "fake unavailable"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("embedder exploded")
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for index, token in enumerate(sorted(set(text.lower().split()))):
            vec[index % self.dims] += 1.0
        return vec


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


class LocalMemoryContractTest(unittest.TestCase):
    """The seven operations the interface promises, plus persistence."""

    def setUp(self) -> None:
        self.root = _tmp()
        self.mem = LocalMemory(self.root, embedder=None)

    def tearDown(self) -> None:
        self.mem.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_available_reports_the_file_not_a_server(self) -> None:
        ok, reason = self.mem.available()
        self.assertTrue(ok)
        self.assertIn("memory.db", reason)
        self.assertNotIn("http", reason)

    def test_remember_and_search(self) -> None:
        self.mem.remember(MemoryItem(content="User prefers dark mode"))
        hits = self.mem.search("dark mode preference")
        self.assertEqual(len(hits), 1)
        self.assertIn("dark mode", hits[0].content)

    def test_blank_content_is_not_stored(self) -> None:
        item = self.mem.remember(MemoryItem(content="   "))
        self.assertEqual(item.content, "   ")
        self.assertEqual(self.mem.stats()["total"], 0)

    def test_retrieve_returns_the_item_and_freshness(self) -> None:
        written = self.mem.remember(MemoryItem(content="deploy is scripts\\deploy.ps1"))
        found = self.mem.retrieve(written.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.content, written.content)

    def test_retrieve_unknown_is_none(self) -> None:
        self.assertIsNone(self.mem.retrieve("mem_nope"))

    def test_update_replaces_and_keeps_the_id(self) -> None:
        written = self.mem.remember(MemoryItem(content="old text"))
        ok = self.mem.update(written.id, MemoryItem(content="new text"))
        self.assertTrue(ok)
        self.assertEqual(self.mem.retrieve(written.id).content, "new text")
        self.assertEqual(self.mem.stats()["total"], 1)

    def test_update_unknown_id_is_false(self) -> None:
        self.assertFalse(self.mem.update("mem_missing", MemoryItem(content="x")))

    def test_forget_one_and_forget_everything(self) -> None:
        a = self.mem.remember(MemoryItem(content="alpha"))
        self.mem.remember(MemoryItem(content="beta"))
        self.assertEqual(self.mem.forget(a.id), 1)
        self.assertEqual(self.mem.stats()["total"], 1)
        self.assertEqual(self.mem.forget(), 0, "forget(None) must not wipe the store")
        self.assertEqual(self.mem.forget(everything=True), 1)
        self.assertEqual(self.mem.stats()["total"], 0)

    def test_summarize_groups_by_level(self) -> None:
        self.mem.remember(
            MemoryItem(content="prefers metric", level=MemoryLevel.L4_USER)
        )
        self.mem.remember(
            MemoryItem(content="ran tests", level=MemoryLevel.L2_SESSION,
                       type=MemoryType.EPISODE)
        )
        summary = self.mem.summarize("metric")
        self.assertIn("prefers metric", summary)

    def test_promote_moves_to_user_scope(self) -> None:
        item = self.mem.remember(
            MemoryItem(content="temp note", level=MemoryLevel.L2_SESSION,
                       type=MemoryType.EPISODE)
        )
        promoted = self.mem.promote_to_long_term(item)
        self.assertEqual(promoted.level, MemoryLevel.L4_USER)
        self.assertEqual(promoted.type, MemoryType.FACT)

    def test_expiry_is_respected_at_search_time(self) -> None:
        from datetime import timedelta

        from alan.interfaces.types import utcnow

        self.mem.remember(
            MemoryItem(content="ephemeral detail",
                       expires=utcnow() - timedelta(seconds=1))
        )
        self.assertEqual(self.mem.search("ephemeral detail"), [])

    def test_min_confidence_filters(self) -> None:
        self.mem.remember(MemoryItem(content="uncertain guess", confidence=0.2))
        self.assertEqual(
            self.mem.search("uncertain guess", min_confidence=0.5), []
        )
        self.assertEqual(len(self.mem.search("uncertain guess")), 1)

    def test_level_filter(self) -> None:
        self.mem.remember(
            MemoryItem(content="durable fact", level=MemoryLevel.L4_USER)
        )
        self.mem.remember(
            MemoryItem(content="session step", level=MemoryLevel.L2_SESSION)
        )
        hits = self.mem.search("durable session", levels=[MemoryLevel.L4_USER])
        self.assertEqual([h.content for h in hits], ["durable fact"])


class LocalMemoryPersistenceTest(unittest.TestCase):
    """Memory that does not survive a restart is not memory."""

    def setUp(self) -> None:
        self.root = _tmp()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_items_survive_a_new_instance(self) -> None:
        first = LocalMemory(self.root, embedder=None)
        first.remember(MemoryItem(content="server lives in Frankfurt",
                                  level=MemoryLevel.L4_USER))
        first.close()

        second = LocalMemory(self.root, embedder=None)
        try:
            hits = second.search("where is the server")
            self.assertEqual(len(hits), 1)
            self.assertEqual(second.stats()["total"], 1)
        finally:
            second.close()

    def test_two_instances_see_each_others_writes(self) -> None:
        a = LocalMemory(self.root, embedder=None)
        b = LocalMemory(self.root, embedder=None)
        try:
            a.remember(MemoryItem(content="written by a"))
            self.assertEqual(b.stats()["total"], 1)
        finally:
            a.close()
            b.close()


class LocalMemoryRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _tmp()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _store(self) -> LocalMemory:
        return LocalMemory(self.root, embedder=None)

    def test_search_is_fast(self) -> None:
        """The whole point. A slow search here is the bug this store fixes."""
        mem = self._store()
        try:
            for i in range(300):
                mem.remember(MemoryItem(content=f"note {i} about topic {i % 7}"))
            # warm any one-off work, then measure a real query
            mem.search("topic 3")
            started = time.perf_counter()
            for _ in range(5):
                mem.search("topic 3 about note")
            elapsed = (time.perf_counter() - started) / 5
            self.assertLess(elapsed, 0.25, f"search took {elapsed:.3f}s")
        finally:
            mem.close()

    def test_context_block_stays_off_the_network(self) -> None:
        """No embedder means no socket is ever opened to reach a result."""
        mem = self._store()
        try:
            mem.remember(MemoryItem(content="the deploy key lives in secrets"))
            started = time.perf_counter()
            block = mem.context_block("where is the deploy key")
            elapsed = time.perf_counter() - started
            self.assertIn("deploy key", block)
            self.assertLess(elapsed, 0.2, f"context_block took {elapsed:.3f}s")
        finally:
            mem.close()

    def test_repeated_identical_writes_dedupe_within_a_session(self) -> None:
        """A tool that ran the same query twice is one memory, not two."""
        mem = self._store()
        try:
            for _ in range(3):
                mem.remember(MemoryItem(content="searched quantum battery",
                                        session_id="s1"))
            self.assertEqual(mem.stats()["total"], 1)
        finally:
            mem.close()

    def test_the_same_text_in_two_sessions_is_kept(self) -> None:
        """Dedupe is scoped: two sessions asking the same thing is history."""
        mem = self._store()
        try:
            mem.remember(MemoryItem(content="ran the tests", session_id="s1"))
            mem.remember(MemoryItem(content="ran the tests", session_id="s2"))
            self.assertEqual(mem.stats()["total"], 2)
        finally:
            mem.close()

    def test_stopwords_alone_do_not_raise(self) -> None:
        mem = self._store()
        try:
            mem.remember(MemoryItem(content="anything at all"))
            self.assertEqual(mem.search("what is it"), [])
        finally:
            mem.close()


class LocalMemorySessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _tmp()
        self.mem = LocalMemory(self.root, embedder=None)

    def tearDown(self) -> None:
        self.mem.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_session_items_are_boosted_but_not_filtered(self) -> None:
        """A ranking hint, not a filter.

        Two near-identical items from different sessions are the case the
        boost exists for: the one from *this* conversation must come first.
        The durable fact must still be *returned* -- hard-filtering by session
        is what would make persistent memory forget the moment a chat closed.
        """
        self.mem.remember(MemoryItem(content="ran the build twice",
                                     session_id="s1",
                                     level=MemoryLevel.L2_SESSION))
        self.mem.remember(MemoryItem(content="ran the build last week",
                                     session_id="s0",
                                     level=MemoryLevel.L2_SESSION))
        self.mem.remember(MemoryItem(content="the build command is npm run build",
                                     level=MemoryLevel.L4_USER))
        hits = self.mem.search("run the build", top_k=3, session_id="s1")
        contents = [h.content for h in hits]
        self.assertEqual(len(hits), 3, "nothing may be dropped by the hint")
        self.assertIn("the build command is npm run build", contents,
                      "a durable fact must not be filtered out by the hint")
        # The boost's actual job: between two equally-matching items, the one
        # from this conversation wins. A fact that matches *better* still
        # outranks it -- see test_the_session_hint_only_breaks_ties.
        self.assertLess(
            contents.index("ran the build twice"),
            contents.index("ran the build last week"),
            "this session must outrank an older near-duplicate",
        )

    def test_the_session_hint_only_breaks_ties(self) -> None:
        """It must not let a weak same-session match beat a strong one."""
        self.mem.remember(MemoryItem(content="build",
                                     session_id="s1",
                                     level=MemoryLevel.L2_SESSION))
        self.mem.remember(MemoryItem(content="how to run the build pipeline",
                                     level=MemoryLevel.L4_USER))
        hits = self.mem.search("run the build pipeline", top_k=2, session_id="s1")
        self.assertEqual(hits[0].content, "how to run the build pipeline",
                         "lexical fit outranks the session boost")

    def test_context_block_preserves_the_session_order(self) -> None:
        """The ordering `search` computed must be the ordering Alan reads.

        Guards a bug found by hand *after* this suite was already green.
        `MemoryProvider.context_block` calls `relevance.gate` on the hits it is
        handed, and `gate` re-sorts by pure lexical score -- so a store that had
        carefully blended a session bonus and a vector weight into one number
        ended up rendering results in lexical order anyway. Correctly filtered,
        wrong order, every other test still passing, and `search()` alone
        looking perfectly healthy.

        The importances here are not decorative: they are what the observer
        actually writes (a failure matters more than a success), and they are
        the only reason the older item out-scores the current one lexically.
        Give both items the same importance and the bug hides.
        """
        self.mem.remember(MemoryItem(
            content="filesystem.list(path=C:\\tmp\\older) failed: PermissionError",
            level=MemoryLevel.L2_SESSION, type=MemoryType.EPISODE,
            session_id="older", importance=0.7,
        ))
        self.mem.remember(MemoryItem(
            content="Ran filesystem.list(path=D:\\data\\current) -> budget-q4.md",
            level=MemoryLevel.L2_SESSION, type=MemoryType.EPISODE,
            session_id="current", importance=0.45,
        ))
        hits = self.mem.search("what folder did you just list",
                               top_k=2, session_id="current")
        self.assertIn("budget-q4.md", hits[0].content,
                      "precondition: search must already rank this session first")
        block = self.mem.context_block("what folder did you just list",
                                       top_k=2, session_id="current")
        lines = [ln.strip()[3:] for ln in block.splitlines()
                 if ln.strip().startswith("- ")]
        self.assertEqual(len(lines), 2, "the older item must still be present")
        self.assertIn("budget-q4.md", lines[0],
                      f"the block must not re-sort away from search, got: {lines}")

    def test_without_a_session_hint_ordering_is_pure_relevance(self) -> None:
        self.mem.remember(MemoryItem(content="ran the build", session_id="s1"))
        self.mem.remember(MemoryItem(content="ran the build", session_id="s2"))
        hits = self.mem.search("run the build", top_k=2)
        self.assertEqual(len(hits), 2)

    def test_recent_lists_a_session_newest_first(self) -> None:
        self.mem.remember(MemoryItem(content="first", session_id="s1"))
        self.mem.remember(MemoryItem(content="second", session_id="s1"))
        self.mem.remember(MemoryItem(content="other", session_id="s2"))
        recent = self.mem.recent(session_id="s1")
        self.assertEqual([r.content for r in recent], ["second", "first"])

    def test_recent_filters_by_kind(self) -> None:
        self.mem.remember(MemoryItem(content="ran web_search",
                                     session_id="s1",
                                     type=MemoryType.EPISODE,
                                     source="tool:web_search"))
        self.mem.remember(MemoryItem(content="prefers dark mode",
                                     level=MemoryLevel.L4_USER,
                                     type=MemoryType.PREFERENCE))
        tools = self.mem.recent(kind="tool")
        self.assertEqual([t.content for t in tools], ["ran web_search"])

    def test_forget_session_keeps_durable_facts(self) -> None:
        """Forgetting what we did last Tuesday must not forget who the user is."""
        self.mem.remember(MemoryItem(content="ran the tests",
                                     session_id="s1",
                                     level=MemoryLevel.L2_SESSION))
        self.mem.remember(MemoryItem(content="user prefers metric units",
                                     level=MemoryLevel.L4_USER))
        removed = self.mem.forget_session("s1")
        self.assertEqual(removed, 1)
        self.assertEqual(self.mem.stats()["total"], 1)
        self.assertTrue(self.mem.search("prefers metric units"))

    def test_stats_counts_sessions_and_levels(self) -> None:
        self.mem.remember(MemoryItem(content="a", session_id="s1"))
        self.mem.remember(MemoryItem(content="b", session_id="s2"))
        stats = self.mem.stats()
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertIn("L2_SESSION", stats["by_level"])


class LocalMemoryVectorTest(unittest.TestCase):
    """Embeddings reorder candidates; they never invent them."""

    def setUp(self) -> None:
        self.root = _tmp()
        # Fake embedders are consulted live here. The shared probe cache keys
        # on ``(base_url, model)``, so it would happily hand one fake's verdict
        # to another -- and a test run has no business writing throwaway keys
        # into the real ``data/state/probes.json`` in the first place.
        patcher = mock.patch.dict(os.environ, {"ALAN_PROBE_CACHE": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_embeddings_are_written_by_the_background_worker(self) -> None:
        embedder = _FakeEmbedder()
        mem = LocalMemory(self.root, embedder=embedder)
        try:
            mem.remember(MemoryItem(content="the database password is rotated"))
            deadline = time.time() + 5.0
            while time.time() < deadline and mem.stats()["embedded"] == 0:
                time.sleep(0.05)
            self.assertEqual(mem.stats()["embedded"], 1)
            self.assertEqual(mem.stats()["vectors"], True)
        finally:
            mem.close()

    def test_a_write_never_waits_for_the_model(self) -> None:
        """The worker owns embedding cost, not the caller of remember()."""
        embedder = _FakeEmbedder(delay=1.5)
        mem = LocalMemory(self.root, embedder=embedder)
        try:
            started = time.perf_counter()
            mem.remember(MemoryItem(content="slow to embed"))
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.5, f"remember blocked for {elapsed:.3f}s")
        finally:
            mem.close()

    def test_an_unavailable_embedder_degrades_to_lexical(self) -> None:
        embedder = _FakeEmbedder(available=False)
        mem = LocalMemory(self.root, embedder=embedder)
        try:
            mem.remember(MemoryItem(content="the answer is forty two"))
            self.assertTrue(mem.search("what is the answer"),
                            "lexical recall must survive an unavailable model")
            self.assertEqual(mem.stats()["embedded"], 0)
            self.assertFalse(mem.stats()["vectors"])
        finally:
            mem.close()

    def test_a_broken_embedder_stops_being_asked(self) -> None:
        embedder = _FakeEmbedder(fail=True)
        mem = LocalMemory(self.root, embedder=embedder)
        try:
            # Enough writes to fill several batches: the worker gives up after
            # `_EMBED_FAILURE_LIMIT` consecutive failures, not after one bad
            # request, because a single network blip must not silence vectors
            # for the rest of the process.
            for i in range(30):
                mem.remember(MemoryItem(content=f"failure case {i}"))
            deadline = time.time() + 5.0
            while time.time() < deadline and not mem._vectors_dead:
                time.sleep(0.05)
            self.assertTrue(mem._vectors_dead,
                            "a broken embedder must not be retried forever")
            before = len(embedder.calls)
            mem.remember(MemoryItem(content="after the embedder died"))
            time.sleep(0.3)
            self.assertEqual(len(embedder.calls), before,
                             "no further embed attempts once declared dead")
        finally:
            mem.close()

    def test_a_cold_model_does_not_delay_the_answer(self) -> None:
        """The vector budget is what keeps a cold model off the first token."""
        embedder = _FakeEmbedder(delay=2.0)
        mem = LocalMemory(self.root, embedder=embedder, vector_budget_ms=80)
        try:
            mem.remember(MemoryItem(content="quick fact about latency"))
            started = time.perf_counter()
            hits = mem.search("latency fact")
            elapsed = time.perf_counter() - started
            self.assertTrue(hits, "lexical results must still come back")
            self.assertLess(elapsed, 0.6, f"search waited {elapsed:.3f}s on the model")
        finally:
            mem.close()

    def test_the_vector_signal_reaches_the_final_score(self) -> None:
        """A cosine match must actually change the number that orders results.

        Same content, same query, two stores: one with an embedder and one
        without. If vectors only reordered candidates that `relevance.gate`
        later re-sorted, the two scores would be identical and the embedder
        would be decoration.
        """
        texts = [
            "the quarterly numbers moved north that quarter",
            "a completely unrelated note about painting",
        ]
        plain = LocalMemory(_tmp(), embedder=None)
        vector = LocalMemory(_tmp(), embedder=_FakeEmbedder())
        try:
            for store in (plain, vector):
                for text in texts:
                    store.remember(MemoryItem(content=text))
            # Embeddings are written by the background worker, so the rows are
            # genuinely vector-less for a moment. Searching before it finishes
            # would assert on the lexical-only path and prove nothing.
            deadline = time.time() + 5.0
            while time.time() < deadline and vector.stats()["embedded"] < 2:
                time.sleep(0.05)
            self.assertEqual(vector.stats()["embedded"], 2)
            query = "the quarterly numbers that quarter"
            a = plain.search(query, top_k=2)
            b = vector.search(query, top_k=2)
            self.assertEqual([i.content for i in a], [i.content for i in b],
                             "vectors reorder, they do not invent results")
            self.assertGreater(b[0].score, a[0].score,
                               "the cosine weight must survive into the score")
        finally:
            plain.close()
            vector.close()
            shutil.rmtree(plain.root, ignore_errors=True)
            shutil.rmtree(vector.root, ignore_errors=True)

    def test_import_records_adopts_another_backend(self) -> None:
        mem = LocalMemory(self.root, embedder=None)
        try:
            adopted = mem.import_records([
                {"content": "migrated from cognee", "level": 4},
                {"content": "", "level": 4},          # blank: skipped
                {"content": "also migrated", "level": 2},
                {"level": "not-a-real-level"},        # corrupt: skipped
            ])
            self.assertEqual(adopted, 2)
            self.assertTrue(mem.search("migrated from"))
        finally:
            mem.close()


class DeriveKindTest(unittest.TestCase):
    def test_tool_episode(self) -> None:
        item = MemoryItem(content="ran", type=MemoryType.EPISODE,
                          source="tool:web_search")
        self.assertEqual(derive_kind(item), "tool")

    def test_mcp_source(self) -> None:
        item = MemoryItem(content="called", type=MemoryType.EPISODE,
                          source="mcp:github")
        self.assertEqual(derive_kind(item), "mcp")

    def test_preference_and_instruction(self) -> None:
        self.assertEqual(
            derive_kind(MemoryItem(content="x", type=MemoryType.PREFERENCE)),
            "preference",
        )
        self.assertEqual(
            derive_kind(MemoryItem(content="x", type=MemoryType.INSTRUCTION)),
            "instruction",
        )

    def test_explicit_kind_wins(self) -> None:
        item = MemoryItem(content="x", type=MemoryType.FACT,
                          metadata={"kind": "custom"})
        self.assertEqual(derive_kind(item), "custom")

    def test_plain_fact(self) -> None:
        self.assertEqual(derive_kind(MemoryItem(content="x")), "fact")


class ObserverTest(unittest.TestCase):
    """What Alan *does* becomes session-scoped memory."""

    def setUp(self) -> None:
        self.root = _tmp()
        self.mem = LocalMemory(self.root, embedder=None)
        self.bus = EventBus()
        self.observer = MemoryObserver(self.mem, session_id="s1")
        self.observer.attach(self.bus)

    def tearDown(self) -> None:
        self.observer.close()
        self.mem.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _tool(self, *, finished: bool = False, failed: bool = False) -> None:
        self.bus.emit(
            Events.TOOL_STARTED, trace_id="tr1", session_id="s1",
            tool="web_search_exa", arguments={"query": "quantum battery"},
        )
        if finished:
            self.bus.emit(
                Events.TOOL_FINISHED, trace_id="tr1", session_id="s1",
                tool="web_search_exa", duration_ms=1250.0, result="5 results",
            )
        if failed:
            self.bus.emit(
                Events.TOOL_FAILED, trace_id="tr1", session_id="s1",
                tool="web_search_exa", duration_ms=300.0,
                error="TimeoutError",
            )

    def test_a_finished_tool_is_recorded_with_its_arguments(self) -> None:
        self._tool(finished=True)
        hits = self.mem.search("quantum battery")
        self.assertEqual(len(hits), 1)
        self.assertIn("web_search_exa", hits[0].content)
        self.assertIn("quantum battery", hits[0].content)
        self.assertIn("5 results", hits[0].content)

    def test_a_tool_episode_is_session_scoped(self) -> None:
        self._tool(finished=True)
        item = self.mem.recent()[0]
        self.assertEqual(item.level, MemoryLevel.L2_SESSION)
        self.assertEqual(item.session_id, "s1")
        self.assertEqual(item.source, "tool:web_search_exa")
        self.assertEqual(item.metadata["ok"], True)

    def test_a_failure_is_recorded_and_weighted_heavier(self) -> None:
        self._tool(failed=True)
        item = self.mem.recent()[0]
        self.assertIn("failed: TimeoutError", item.content)
        self.assertEqual(item.metadata["ok"], False)
        self.assertGreater(item.importance, 0.5,
                           "a failure is more worth remembering than a success")

    def test_the_arguments_come_from_the_start_event(self) -> None:
        """`tool.finished` carries only the duration; the call needs both."""
        self._tool(finished=True)
        item = self.mem.recent()[0]
        self.assertEqual(item.metadata["arguments"],
                         {"query": "quantum battery"})

    def test_arguments_survive_an_unmatched_start(self) -> None:
        """A tool that hangs leaves a start with no finish; the TTL sweeps it."""
        self.bus.emit(Events.TOOL_STARTED, trace_id="tr1", tool="hang",
                      arguments={"x": 1})
        self.assertEqual(len(self.observer._pending), 1)
        with self.observer._lock:
            self.observer._pending[("tr1", "hang")] = (
                {"x": 1}, time.time() - 4000)
            self.observer._sweep_locked()
        self.assertEqual(len(self.observer._pending), 0)

    def test_an_unmatched_start_is_not_a_memory(self) -> None:
        self.bus.emit(Events.TOOL_STARTED, trace_id="tr1", tool="web_search",
                      arguments={"query": "x"})
        self.assertEqual(self.mem.stats()["total"], 0)

    def test_an_unknown_tool_failure_still_records(self) -> None:
        self.bus.emit(Events.TOOL_FAILED, trace_id="tr2", tool="nope",
                      error="unknown tool")
        self.assertEqual(len(self.mem.search("unknown tool nope")), 1)

    def test_an_agent_run_is_recorded(self) -> None:
        self.bus.emit(Events.AGENT_FINISHED, trace_id="tr3", agent="researcher",
                      session_id="s1")
        item = self.mem.recent()[0]
        self.assertEqual(item.metadata["kind"], "agent")
        self.assertIn("researcher", item.content)

    def test_the_default_agent_is_not_an_episode(self) -> None:
        self.bus.emit(Events.AGENT_FINISHED, trace_id="tr3", agent="alan")
        self.assertEqual(self.mem.stats()["total"], 0)

    def test_a_refused_action_is_recorded(self) -> None:
        self.bus.emit(Events.PERMISSION_DENIED, trace_id="tr4",
                      action="rm -rf /", reason="above the ceiling",
                      session_id="s1")
        item = self.mem.recent()[0]
        self.assertEqual(item.metadata["kind"], "permission")
        self.assertGreater(item.importance, 0.5)

    def test_an_mcp_call_is_labelled_as_one(self) -> None:
        self.bus.emit(Events.TOOL_STARTED, trace_id="tr5", tool="mcp_github_list",
                      arguments={})
        self.bus.emit(Events.TOOL_FINISHED, trace_id="tr5", tool="mcp_github_list",
                      duration_ms=40.0, result="3 issues")
        item = self.mem.recent()[0]
        self.assertEqual(item.metadata["kind"], "mcp")
        self.assertTrue(item.source.startswith("mcp:"))

    def test_a_registry_tag_marks_an_mcp_call(self) -> None:
        class _Spec:
            tags = ("mcp",)

        class _Registry:
            def spec(self, name):
                return _Spec()

        observer = MemoryObserver(self.mem, session_id="s1", registry=_Registry())
        bus = EventBus()
        observer.attach(bus)
        bus.emit(Events.TOOL_STARTED, trace_id="tr6", tool="exa_web_search",
                 arguments={})
        bus.emit(Events.TOOL_FINISHED, trace_id="tr6", tool="exa_web_search",
                 duration_ms=10.0, result="ok")
        item = self.mem.recent()[0]
        self.assertEqual(item.metadata["kind"], "mcp")
        observer.close()

    def test_arguments_are_redacted_and_truncated(self) -> None:
        self.bus.emit(Events.TOOL_STARTED, trace_id="tr7", tool="shell",
                      arguments={"cmd": "export OPENAI_API_KEY=sk-abcdefghijklmnop",
                                 "blob": "x" * 5000})
        self.bus.emit(Events.TOOL_FINISHED, trace_id="tr7", tool="shell",
                      duration_ms=5.0, result="ok")
        item = self.mem.recent()[0]
        self.assertNotIn("sk-abcdefghijklmnop", item.content)
        self.assertLess(len(item.content), 400,
                        "one tool call must not fill the store")

    def test_disabled_observer_records_nothing(self) -> None:
        observer = MemoryObserver(self.mem, session_id="s1", enabled=False)
        bus = EventBus()
        observer.attach(bus)
        bus.emit(Events.TOOL_FINISHED, trace_id="tr8", tool="web_search",
                 duration_ms=1.0, result="ok")
        self.assertEqual(self.mem.stats()["total"], 0)
        observer.close()

    def test_a_broken_memory_does_not_break_the_bus(self) -> None:
        """An observer raising must not take the turn down with it."""

        class _Exploding(MemoryProvider):
            name = "boom"

            def remember(self, item):
                raise RuntimeError("no space")

            def update(self, item_id, item):
                return False

            def forget(self, item_id=None, *, everything=False):
                return 0

            def retrieve(self, item_id):
                return None

            def search(self, query, *, top_k=5, levels=None,
                       min_confidence=0.0, session_id=None):
                return []

            def summarize(self, query="", *, top_k=20):
                return ""

            def promote_to_long_term(self, item):
                return item

        observer = MemoryObserver(_Exploding(), session_id="s1")
        bus = EventBus()
        observer.attach(bus)
        bus.emit(Events.TOOL_FINISHED, trace_id="tr9", tool="web_search",
                 duration_ms=1.0, result="ok")
        self.assertEqual(observer.dropped, 1)
        observer.close()

    def test_detach_stops_recording(self) -> None:
        detach = MemoryObserver(self.mem, session_id="s1")
        bus = EventBus()
        unsub = detach.attach(bus)
        bus.emit(Events.TOOL_FINISHED, trace_id="trA", tool="web_search",
                 duration_ms=1.0, result="ok")
        self.assertEqual(self.mem.stats()["total"], 1)
        unsub()
        bus.emit(Events.TOOL_FINISHED, trace_id="trB", tool="web_search",
                 duration_ms=1.0, result="ok")
        self.assertEqual(self.mem.stats()["total"], 1)
        detach.close()


class ObserverIntegrationTest(unittest.TestCase):
    """A tool call, end to end, is recallable by a later session."""

    def test_another_session_can_ask_what_was_done(self) -> None:
        root = _tmp()
        try:
            first = LocalMemory(root, embedder=None)
            bus = EventBus()
            observer = MemoryObserver(first, session_id="s1")
            observer.attach(bus)
            bus.emit(Events.TOOL_STARTED, trace_id="tr1", session_id="s1",
                     tool="read_file", arguments={"path": "D:\\Alan\\README.md"})
            bus.emit(Events.TOOL_FINISHED, trace_id="tr1", session_id="s1",
                     tool="read_file", duration_ms=12.0, result="300 lines")
            observer.close()
            first.close()

            second = LocalMemory(root, embedder=None)
            try:
                hits = second.search("what file did you read", top_k=3)
                self.assertTrue(hits, "the episode must survive the restart")
                self.assertTrue(any("README" in h.content for h in hits))
            finally:
                second.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
