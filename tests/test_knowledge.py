"""Tests for the second brain: store, loaders, chunker, ingest and retrieval.

The engine is the part of the knowledge feature that must be right without a
network, a model or a running Ollama, so these tests never touch one. The
embedding path is exercised through a deterministic bag-of-words fake whose
cosine geometry is known, which is what lets the semantic-floor behaviour be
asserted rather than hoped for.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alan.knowledge import (
    KnowledgeStore,
    NullEmbedder,
    build_embedder,
    chunk_text,
    ingest_paths,
    load,
    search,
)
from alan.knowledge.embeddings import OllamaEmbedder, _has_model
from alan.knowledge.loaders import detect_kind, is_indexable, walk
from alan.knowledge.retrieve import _SEMANTIC_HIGH, _is_relevant
from alan.knowledge.store import fts_query


class FakeEmbedder:
    """Bag-of-words vectors over synonym groups, so cosine is predictable.

    "cost" and "tier" land in the same dimension, which is what lets a test
    prove that a passage with no shared *word* can still be found by meaning.
    """

    name = "fake"
    GROUPS = (
        {"pricing", "price", "cost", "costs", "plan", "tier", "tiers"},
        {"churn", "retention", "cancel", "cancellation"},
        {"onboarding", "signup", "activation"},
    )

    def available(self) -> tuple[bool, str]:
        return True, "fake embedder"

    def embed(self, texts):
        out = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * len(self.GROUPS)
            for index, words in enumerate(self.GROUPS):
                vector[index] = float(sum(lowered.count(word) for word in words))
            out.append(vector)
        return out


class NoisyEmbedder:
    """Reproduces the cosine profile nomic-embed-text actually gives a corpus.

    ``FakeEmbedder`` is orthogonal, so unrelated text scores 0.0 -- which is
    exactly what a cosine floor is good at rejecting, and therefore cannot
    express the bug the relevance gate exists for. Real embeddings score
    *unrelated* English 0.38-0.54, well clear of any floor that would still
    let a genuine match through, so a floor alone cannot be the gate.

    Here unmarked text -- a query -- is the reference direction, and its
    cosine to a passage is simply that passage's level:

    ``quiet``   ~0.38   unrelated; clears the floor, misses the high bar
    ``signal``  ~0.60   a genuine match, clear of the high bar
    ``spike``   ~0.72   an obvious match

    Those are the bands measured against nomic-embed-text: unrelated text
    0.379-0.466 on 8 documents and up to 0.541 at 500, genuine matches from
    0.567. Which of them is admitted then follows from the *lexical* evidence,
    and these levels sit either side of the bar that decides.
    """

    name = "noisy"
    LEVELS = {"quiet": 0.38, "signal": 0.60, "spike": 0.72}

    def available(self) -> tuple[bool, str]:
        return True, "noisy embedder"

    def embed(self, texts):
        out = []
        for text in texts:
            level = next(
                (value for marker, value in self.LEVELS.items() if marker in text),
                1.0,  # unmarked text is a query: the reference direction
            )
            out.append([level, math.sqrt(1.0 - level * level)])
        return out


class TempWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kb_test_")
        self.root = Path(self._tmp.name)
        self._stores: list[KnowledgeStore] = []

    def tearDown(self) -> None:
        # Close every store first: Windows refuses to delete a directory while a
        # SQLite handle (or its -wal) is still open, and the failure would read
        # as the feature's fault rather than the test's.
        for store in self._stores:
            store.close()
        self._tmp.cleanup()

    def write(self, name: str, text: str, *, sub: str = "") -> Path:
        path = self.root / sub / name if sub else self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def store(self, name: str = "kb.db") -> KnowledgeStore:
        store = KnowledgeStore(self.root / name)
        self._stores.append(store)
        return store


class ChunkerTests(unittest.TestCase):
    def test_offsets_are_exact(self) -> None:
        text = "line one\nline two\n\n" + ("x" * 500) + "\n\nline three\n"
        chunks = chunk_text(text, chunk_size=200, overlap=40)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(text[chunk.char_start : chunk.char_end], chunk.text)

    def test_overlap_repeats_context(self) -> None:
        text = "".join(f"word{i} " for i in range(200))
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        self.assertTrue(
            any(
                chunks[i].char_start < chunks[i - 1].char_end
                for i in range(1, len(chunks))
            )
        )

    def test_empty_and_whitespace_produce_nothing(self) -> None:
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n  "), [])

    def test_line_numbers_track_the_source(self) -> None:
        chunks = chunk_text("a\nb\nc\nd\ne\n", chunk_size=200)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].line_start, 1)
        self.assertEqual(chunks[0].line_end, 6)


class LoaderTests(unittest.TestCase):
    def test_detect_kind(self) -> None:
        self.assertEqual(detect_kind(Path("notes.md")), "markdown")
        self.assertEqual(detect_kind(Path("main.py")), "code")
        self.assertEqual(detect_kind(Path("readme.txt")), "text")
        self.assertIsNone(detect_kind(Path("photo.png")))

    def test_secrets_and_dotfiles_are_never_indexed(self) -> None:
        self.assertFalse(is_indexable(Path(".env")))
        self.assertFalse(is_indexable(Path("id_rsa")))
        self.assertTrue(is_indexable(Path("notes.md")))

    def test_walk_prunes_vendor_and_hidden_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "keep.md").write_text("keep", encoding="utf-8")
            for junk in (".git", "node_modules", "__pycache__"):
                (base / junk).mkdir()
                (base / junk / "junk.md").write_text("junk", encoding="utf-8")
            found = {path.name for path in walk(base)}
            self.assertEqual(found, {"keep.md"})

    def test_binary_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "blob.txt"
            binary.write_bytes(b"\x00\x01\x02\x03")
            with self.assertRaises(ValueError):
                load(binary, max_bytes=10_000)

    def test_size_cap_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "big.txt"
            big.write_text("x" * 5000, encoding="utf-8")
            with self.assertRaises(ValueError):
                load(big, max_bytes=1000)

    def test_cp1252_decodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latin.txt"
            path.write_bytes("caf\u00e9".encode("cp1252"))
            doc = load(path, max_bytes=10_000)
            self.assertIn("caf\u00e9", doc.text)

    def test_markdown_title_is_first_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.md"
            path.write_text("# Pricing decisions\n\nbody\n", encoding="utf-8")
            doc = load(path, max_bytes=10_000)
            self.assertEqual(doc.title, "Pricing decisions")


class StoreTests(TempWorkspace):
    def _seed(self, store: KnowledgeStore) -> None:
        notes = self.write("notes.md", "# Pricing\n\nWe chose the Pro tier at $29.\n")
        code = self.write("math.py", "def add(a, b):\n    return a + b\n")
        ingest_paths(store, [str(notes), str(code)], embedder=NullEmbedder())

    def test_ingest_then_lexical_search_cites_source(self) -> None:
        store = self.store()
        self._seed(store)
        hits = search(store, "pricing tier", embedder=NullEmbedder())
        self.assertTrue(hits)
        self.assertTrue(any("notes.md" in hit.citation() for hit in hits))
        self.assertIn(":", hits[0].citation())

    def test_and_then_or_fallback_finds_inflected_terms(self) -> None:
        store = self.store()
        self._seed(store)
        # "cost" is absent but "costs" would still be a word worth matching;
        # the OR fallback is what keeps one missing word from emptying results.
        hits = store.search_lexical("pricing tier nonexistentword")
        self.assertTrue(hits)

    def test_reingest_skips_unchanged_and_updates_changed(self) -> None:
        store = self.store()
        path = self.write("notes.txt", "alpha beta gamma\n")
        first = ingest_paths(store, [str(path)], embedder=NullEmbedder())
        self.assertEqual((first.added, first.updated, first.skipped), (1, 0, 0))
        second = ingest_paths(store, [str(path)], embedder=NullEmbedder())
        self.assertEqual((second.added, second.updated, second.skipped), (0, 0, 1))
        path.write_text("alpha beta gamma delta\n", encoding="utf-8")
        third = ingest_paths(store, [str(path)], embedder=NullEmbedder())
        self.assertEqual((third.added, third.updated, third.skipped), (0, 1, 0))

    def test_forget_source_and_everything(self) -> None:
        store = self.store()
        self._seed(store)
        self.assertEqual(store.stats()["documents"], 2)
        removed = store.forget(source=str(self.root / "notes.md"))
        self.assertEqual(removed, 1)
        self.assertEqual(store.stats()["documents"], 1)
        store.forget(everything=True)
        self.assertEqual(store.stats()["documents"], 0)
        self.assertEqual(store.stats()["chunks"], 0)

    def test_forget_directory_prefix_removes_entire_folder(self) -> None:
        store = self.store()
        self._seed(store)  # notes.md + math.py under self.root
        removed = store.forget(source=str(self.root))
        self.assertEqual(removed, 2)
        self.assertEqual(store.stats()["documents"], 0)

    def test_forget_no_match_returns_zero(self) -> None:
        store = self.store()
        self._seed(store)
        self.assertEqual(store.forget(source=str(self.root / "absent.md")), 0)
        self.assertEqual(store.stats()["documents"], 2)

    def test_forget_matches_like_special_chars_literally(self) -> None:
        store = self.store()
        path = self.write("notes_%1.md", "# Escaping\n\n50% of plans change.\n")
        ingest_paths(store, [str(path)], embedder=NullEmbedder())
        self.assertEqual(store.forget(source=str(path)), 1)
        self.assertEqual(store.forget(source=str(path)), 0)  # nothing left

    def test_reopening_persists_documents(self) -> None:
        path = self.root / "kb.db"
        store = KnowledgeStore(path)
        self._stores.append(store)
        self._seed(store)
        store.close()
        reopened = KnowledgeStore(path)
        self._stores.append(reopened)
        self.assertEqual(reopened.stats()["documents"], 2)
        reopened.close()

    def test_missing_path_is_reported_not_raised(self) -> None:
        store = self.store()
        report = ingest_paths(store, [str(self.root / "nope")], embedder=NullEmbedder())
        self.assertEqual(report.failed, 1)
        self.assertTrue(report.errors)

    def test_fts_query_is_safe_and_deduplicated(self) -> None:
        self.assertEqual(fts_query("a a b"), '"a" AND "b"')
        self.assertEqual(fts_query("!@#$"), "")
        self.assertEqual(fts_query("x y", "OR"), '"x" OR "y"')


class RetrievalTests(TempWorkspace):
    def test_semantic_only_hit_surfaces_via_embedder(self) -> None:
        store = self.store()
        # No shared word with the query "cost": only the embedding links them.
        path = self.write("plan.md", "# Tiers\n\nThe Pro tier costs twenty-nine dollars.\n")
        ingest_paths(store, [str(path)], embedder=FakeEmbedder())

        lexical = store.search_lexical("cost")
        self.assertFalse(lexical, "test premise: 'cost' must not match lexically")

        hits = search(store, "cost", embedder=FakeEmbedder(), top_k=3)
        self.assertTrue(hits)
        self.assertIsNotNone(hits[0].semantic_rank)

    def test_unrelated_semantic_match_is_gated_out(self) -> None:
        store = self.store()
        path = self.write("onboarding.md", "# Onboarding\n\nActivation happens at signup.\n")
        ingest_paths(store, [str(path)], embedder=FakeEmbedder())
        hits = search(store, "cost", embedder=FakeEmbedder(), top_k=3)
        self.assertEqual(hits, [])

    def test_empty_query_returns_nothing(self) -> None:
        store = self.store()
        self.assertEqual(search(store, "   ", embedder=NullEmbedder()), [])

    def test_a_nonsense_query_returns_nothing_not_the_whole_store(self) -> None:
        """The gate's reason to exist.

        No word of the query is in the store, so no passage may stand in as
        the answer on cosine alone -- least of all one scoring 0.38, which
        clears a floor comfortably and is exactly what a floor-only gate used
        to let through.
        """
        store = self.store()
        for index in range(4):
            path = self.write(
                f"quiet{index}.md",
                f"# Quiet {index}\n\nThe quiet level here, note {index}.\n",
            )
            ingest_paths(store, [str(path)], embedder=NoisyEmbedder())

        self.assertFalse(
            store.search_lexical("zzz nonexistent qqq"),
            "test premise: the query must match no word at all",
        )
        self.assertEqual(search(store, "zzz nonexistent qqq", embedder=NoisyEmbedder()), [])

    def test_a_genuine_match_still_surfaces_without_a_shared_word(self) -> None:
        """Recall is the other half of the gate, and must not be the casualty."""
        store = self.store()
        for index in range(3):
            path = self.write(
                f"quiet{index}.md",
                f"# Quiet {index}\n\nThe quiet level here, note {index}.\n",
            )
            ingest_paths(store, [str(path)], embedder=NoisyEmbedder())
        match = self.write(
            "signal.md", "# Signal\n\nThe signal level applies to this note.\n"
        )
        ingest_paths(store, [str(match)], embedder=NoisyEmbedder())

        hits = search(store, "zzz nonexistent qqq", embedder=NoisyEmbedder())
        self.assertEqual(
            [Path(hit.source).name for hit in hits],
            ["signal.md"],
            "a genuine match must clear the high bar while 0.38 noise is turned away",
        )

    def test_a_word_match_opens_the_floor_to_corroborating_passages(self) -> None:
        """With a word match in hand, a 0.38 neighbour is useful colour, not a risk.

        The query demonstrably lands in the store, so a passage that is
        merely not-unlike it adds context rather than masquerading as an
        answer. Only the floor applies, and 0.38 clears it.
        """
        store = self.store()
        lexical = self.write(
            "lexical.md", "# Lexical\n\nThe webhook fired twice at noon today.\n"
        )
        colour = self.write("colour.md", "# Colour\n\nThe quiet level here.\n")
        ingest_paths(store, [str(lexical), str(colour)], embedder=NoisyEmbedder())

        hits = search(store, "webhook", embedder=NoisyEmbedder())
        sources = [Path(hit.source).name for hit in hits]
        self.assertIn("lexical.md", sources)
        self.assertIn(
            "colour.md",
            sources,
            "0.38 clears the floor, and a word match is what makes the floor the bar",
        )

    def test_an_rrf_tie_is_broken_by_cosine_not_by_filename(self) -> None:
        """Two passages can fuse to an identical RRF score; the filename must not decide.

        RRF is magnitude-blind by design -- it keeps a ranker's rank and
        discards its scores. That costs something when a lexical match is
        worthless but still a rank: measured against the real store, the query
        "how much does the paid plan cost" matched launch.md on the stopword
        "the" for a bm25 of ~1e-06, fused both passages to 0.0325, and
        alphabetical order then put launch.md above the actual answer. The
        second ranker already holds the evidence the tie needs, so it breaks it.
        """
        store = self.store()
        # Both passages share "webhook" and "fired" with the query, so both
        # take the lexical path and fuse to an identical score. Only the
        # marker word differs, and it is what sets each passage's cosine.
        noise = self.write(
            "aaa_noise.md", "# Aaa\n\nThe quiet webhook fired at noon today.\n"
        )
        answer = self.write(
            "zzz_answer.md", "# Zzz\n\nThe signal webhook fired at noon today.\n"
        )
        ingest_paths(store, [str(noise), str(answer)], embedder=NoisyEmbedder())

        # Both passages share "webhook" with the query, so both take the
        # lexical path; the cosine decides between them. Without the fix this
        # returned aaa_noise.md first, on alphabetical order.
        hits = search(store, "webhook fired", embedder=NoisyEmbedder())
        self.assertEqual(
            [Path(hit.source).name for hit in hits][0],
            "zzz_answer.md",
            "an RRF tie must be broken by the cosine, not by the filename",
        )
        self.assertEqual(hits[0].score, hits[1].score, "test premise: a genuine tie")
        self.assertGreater(
            hits[0].cosine,
            hits[1].cosine,
            "test premise: the winner is the passage the embedder preferred",
        )

class EmbedderTests(unittest.TestCase):
    def test_null_embedder_is_unavailable_but_safe(self) -> None:
        embedder = NullEmbedder()
        ok, _ = embedder.available()
        self.assertFalse(ok)
        self.assertEqual(embedder.embed(["a", "b"]), [[], []])

    def test_build_embedder_honours_disable(self) -> None:
        self.assertIsInstance(build_embedder("", ""), NullEmbedder)
        self.assertIsInstance(
            build_embedder("nomic-embed-text", "http://x", enabled=False),
            NullEmbedder,
        )

    def test_has_model_matches_untagged_and_tagged(self) -> None:
        self.assertTrue(_has_model(["nomic-embed-text:latest"], "nomic-embed-text"))
        self.assertTrue(_has_model(["nomic-embed-text"], "nomic-embed-text"))
        self.assertFalse(_has_model(["qwen2.5:1.5b"], "nomic-embed-text"))

    def test_unreachable_server_reports_rather_than_raises(self) -> None:
        embedder = OllamaEmbedder(base_url="http://127.0.0.1:1", timeout=0.2)
        with mock.patch(
            "alan.knowledge.embeddings.urllib.request.urlopen",
            side_effect=OSError("no server"),
        ):
            ok, reason = embedder.available()
        self.assertFalse(ok)
        self.assertIn("unreachable", reason.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
