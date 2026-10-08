"""The relevance gate, and the turn it makes faster.

Two behaviours are pinned here, because both were measured going wrong:

* **Wrong answers.** cognee's recall returned the same four passages for
  "what is the capital of France?" and for "my name is Sam", and they were
  rendered into the prompt under the heading "Relevant memory". Alan was
  confidently answering with a note about a browser sidecar port.
* **Slow turns.** ``_recall`` searched, counted, and then asked
  ``context_block`` to search again -- two graph round trips per question.

The rule under both: zero lexical overlap scores zero, and a count reported in a
trace must be a count of what actually reached the prompt.
"""

from __future__ import annotations

import unittest

from alan.interfaces.types import MemoryItem, MemoryLevel
from alan.memory.relevance import gate, score, terms


def _item(content: str, **kw) -> MemoryItem:
    kw.setdefault("level", MemoryLevel.L4_USER)
    return MemoryItem(content=content, **kw)


class TermsTest(unittest.TestCase):
    def test_drops_stopwords_and_keeps_meaningful_words(self):
        self.assertEqual(terms("What is the capital of France?"), {"capital", "france"})

    def test_case_and_punctuation_are_irrelevant(self):
        self.assertEqual(terms("Berlin, BERLIN!"), {"berlin"})

    def test_an_all_stopword_query_has_no_terms(self):
        self.assertEqual(terms("what is it"), set())
        self.assertEqual(terms(""), set())


class ScoreTest(unittest.TestCase):
    def test_zero_overlap_is_zero(self):
        item = _item("Alan's browser sidecar listens on port 8022")
        self.assertEqual(score({"capital", "france"}, "capital of France", item), 0.0)

    def test_overlap_scores_above_zero(self):
        item = _item("Paris is the capital of France")
        self.assertGreater(score({"capital", "france"}, "capital of France", item), 0.0)

    def test_an_exact_phrase_is_a_match_even_with_no_term_overlap(self):
        # Punctuation and stopwords can leave the phrase as the only signal.
        item = _item("the answer is, Paris.")
        self.assertGreater(score(terms("Paris."), "Paris.", item), 0.0)

    def test_empty_content_is_never_a_match(self):
        self.assertEqual(score({"anything"}, "anything", _item("")), 0.0)

    def test_bonuses_cannot_rescue_a_non_match(self):
        """Level and importance must not manufacture relevance.

        This is the failure that makes the gate pointless: if a high-priority
        note can score above zero with no overlap in it, the store decides what
        Alan claims to know.
        """
        shouty = _item(
            "totally unrelated note about gardening",
            level=MemoryLevel.L0_CONTEXT,
            importance=1.0,
            confidence=1.0,
        )
        self.assertEqual(score({"berlin"}, "where does the user live", shouty), 0.0)

    def test_tighter_scope_wins_when_content_is_equal(self):
        """L0 is the tightest scope, so it gets the larger bonus.

        This pins the direction the code already documents ("tighter scopes are
        more relevant when the content is otherwise equal"): a *lower* level
        number is a tighter scope. Whether immediate working context should
        really outrank durable user facts for a question about the user is a
        defensible design question, not a bug, so the test records what is
        shipped rather than quietly redefining it.
        """
        tight = _item("the deploy key lives in the vault", level=MemoryLevel.L0_CONTEXT)
        loose = _item("the deploy key lives in the vault", level=MemoryLevel.L5_ARCHIVE)
        t = terms("deploy key")
        self.assertGreater(score(t, "deploy key", tight), score(t, "deploy key", loose))


class GateTest(unittest.TestCase):
    def test_drops_items_with_no_overlap(self):
        kept = gate("what is the capital of France", [
            _item("Paris is the capital of France"),
            _item("Alan's browser sidecar listens on port 8022"),
        ])
        self.assertEqual([i.content for i in kept], ["Paris is the capital of France"])

    def test_ranks_by_score_and_sets_it(self):
        weak = _item("france")
        strong = _item("Paris is the capital of france and the largest city")
        kept = gate("what is the capital of france", [weak, strong])
        self.assertEqual(kept[0], strong, "the better match must come first")
        self.assertGreater(strong.score, weak.score)
        self.assertTrue(all(i.score > 0 for i in kept))

    def test_empty_query_does_not_filter(self):
        """Refusing to judge is honest; returning nothing would look broken."""
        items = [_item("anything at all"), _item("something else")]
        self.assertEqual(gate("what is it", items), items)
        self.assertEqual(gate("", items), items)

    def test_everything_can_be_discarded(self):
        self.assertEqual(gate("capital of france", [_item("a note about ports")]), [])


class ContextBlockIsGatedTest(unittest.TestCase):
    """`context_block` gates whatever a backend returns, whatever the backend."""

    def _provider(self, hits):
        from alan.interfaces.memory import MemoryProvider

        class _P(MemoryProvider):
            name = "stub"

            def __init__(self, items):
                self._items = list(items)

            def available(self): return True, "stub"
            def remember(self, item, **kw): return item
            def forget(self, item_id=None, *, everything=False): return 0
            def update(self, item_id, item): return True
            def retrieve(self, item_id): return None
            def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
                return list(self._items)
            def summarize(self, query="", *, top_k=20): return ""
            def promote_to_long_term(self, item): return item

        return _P(hits)

    def test_irrelevant_backend_output_never_reaches_the_prompt(self):
        block = self._provider([
            _item("cognee latency probe 1: Alan's browser sidecar listens on port 8022"),
        ]).context_block("what is the capital of France?")
        self.assertEqual(block, "", "irrelevant memory must not be presented as relevant")

    def test_a_real_match_is_rendered(self):
        block = self._provider([
            _item("Paris is the capital of France"),
        ]).context_block("what is the capital of France?")
        self.assertIn("Paris", block)

    def test_hits_argument_avoids_a_second_search(self):
        """A caller that already searched must not pay for another round trip."""
        calls = {"n": 0}

        class _Counting(self._provider([]).__class__):
            def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
                calls["n"] += 1
                return [_item("Paris is the capital of France")]

        provider = _Counting([])
        provider.context_block("capital of France", hits=[_item("Paris is the capital of France")])
        self.assertEqual(calls["n"], 0, "hits were supplied; the backend must not be called")

        provider.context_block("capital of France")
        self.assertEqual(calls["n"], 1, "without hits, exactly one search")


class _Registry:
    """The minimum surface the orchestrator reads off its registry."""

    def __init__(self, memory=None):
        from alan.core.events import EventBus

        self.gate = None
        self.bus = EventBus()
        self.memory = memory


class _AllowedGate:
    """A gate that allows everything, so recall can be tested on its own."""

    ceiling = None

    def check(self, *a, **kw):
        return True, "allowed"

    def __getattr__(self, _name):
        return lambda *a, **kw: (True, "allowed")


class OrchestratorRecallTest(unittest.TestCase):
    """The orchestrator searches once, and reports what it actually used."""

    def test_the_backend_is_searched_exactly_once_per_recall(self):
        calls = {"n": 0}
        hits = [_item("Paris is the capital of France")]

        from alan.core.orchestrator import Orchestrator, OrchestratorConfig
        from alan.models.router import ModelRouter

        class _M:
            name = "stub"
            def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
                calls["n"] += 1
                return list(hits)
            def context_block(self, query, *, top_k=5, max_chars=2000, hits=None):
                return "Paris is the capital of France"

        memory = _M()
        orch = Orchestrator(
            ModelRouter(), _Registry(memory), memory=memory,
            gate=_AllowedGate(), config=OrchestratorConfig(top_k=5),
        )
        orch._recall("what is the capital of France", "tr", None)
        self.assertEqual(
            calls["n"], 1,
            "one question must cost one graph round trip, not two",
        )

    def _orchestrator(self, hits):
        from alan.core.orchestrator import Orchestrator, OrchestratorConfig
        from alan.models.router import ModelRouter

        class _M:
            name = "stub"
            def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
                return list(hits)
            def context_block(self, query, *, top_k=5, max_chars=2000, hits=None):
                from alan.memory.relevance import gate
                return "\n".join(i.content for i in gate(query, list(hits)))

        memory = _M()
        return Orchestrator(
            ModelRouter(), _Registry(memory), memory=memory,
            gate=_AllowedGate(), config=OrchestratorConfig(top_k=5),
        )

    def test_reported_hits_excludes_what_the_gate_discarded(self):
        orch = self._orchestrator([
            _item("Paris is the capital of France"),
            _item("cognee latency probe: browser sidecar on port 8022"),
        ])
        from alan.core.events import EventBus
        bus = EventBus()
        orch.bus = bus
        block, count = orch._recall("what is the capital of France", "tr", None)
        self.assertEqual(count, 1, "only the item that reached the prompt may be counted")
        self.assertIn("Paris", block)
        self.assertNotIn("sidecar", block)

    def test_no_relevant_memory_reports_zero_and_no_block(self):
        orch = self._orchestrator([_item("a note about gardening")])
        block, count = orch._recall("what is the capital of France", "tr", None)
        self.assertEqual((block, count), ("", 0))


if __name__ == "__main__":
    unittest.main()
