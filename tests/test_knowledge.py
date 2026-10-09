"""Tests for the second brain: store, loaders, chunker, ingest and retrieval.

The engine is the part of the knowledge feature that must be right without a
network, a model or a running Ollama, so these tests never touch one. The
embedding path is exercised through a deterministic bag-of-words fake whose
cosine geometry is known, which is what lets the semantic-floor behaviour be
asserted rather than hoped for.
"""

from __future__ import annotations

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
