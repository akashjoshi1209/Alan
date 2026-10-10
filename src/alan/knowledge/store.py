"""The knowledge store: one SQLite file, lexical + vector search, no server.

Design notes, because the choices here are load-bearing:

* **SQLite, not a vector database.** Alan targets one machine and one user. A
  single file already has FTS5 (fast keyword search) and room for the vectors;
  adding Qdrant or Chroma would mean a service to run, secure and back up for
  no retrieval quality a personal corpus can use.
* **Hybrid by construction.** Lexical hits come from FTS5 with bm25 ranking;
  vector hits come from a cosine scan over the stored embeddings. The two lists
  are fused in :mod:`alan.knowledge.retrieve` with reciprocal-rank fusion, which
  needs no calibration between two ranking systems that are not comparable.
* **Vectors are optional.** With no embedding model, ``embedding`` is NULL and
  everything still works, just lexically.
* **Citations are structural.** Every chunk stores its source path and line
  span, so an answer can always say where it read something.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .chunker import TextChunk
from .loaders import LoadedDoc

# numpy, imported on first use rather than at module import. It is ~200ms of
# every Alan start, paid for a module that most commands -- `version`,
# `doctor`, a chat prompt before the first brain lookup -- never call. The
# degrade path is unchanged: lexical search works without it, and a missing
# or broken numpy still just means "no vectors" (see _numpy's callers).
_NP: Any = None
_NP_TRIED = False


def _numpy() -> Any:
    """The numpy module, or ``None`` when it is absent -- resolved once."""
    global _NP, _NP_TRIED
    if not _NP_TRIED:
        _NP_TRIED = True
        try:
            import numpy

            _NP = numpy
        except Exception:  # noqa: BLE001 - degrade to lexical-only
            _NP = None
    return _NP

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end INTEGER NOT NULL DEFAULT 0,
    line_start INTEGER NOT NULL DEFAULT 1,
    line_end INTEGER NOT NULL DEFAULT 1,
    embedding BLOB,
    dims INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class DocumentRecord:
    """A stored document, without its chunks."""

    id: int
    source: str
    kind: str
    title: str
    content_hash: str
    size: int
    mtime: float


class KnowledgeStore:
    """A single-file, thread-safe-enough knowledge base.

    The connection is shared and guarded by a re-entrant lock: Alan's tool
    registry runs each tool call on its own thread, so two searches can overlap,
    and SQLite's default ``check_same_thread`` would refuse that outright.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    # -- documents -----------------------------------------------------
    def document_for(self, source: str) -> DocumentRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE source = ?", (source,)
            ).fetchone()
        if row is None:
            return None
        return DocumentRecord(
            id=int(row["id"]),
            source=str(row["source"]),
            kind=str(row["kind"]),
            title=str(row["title"]),
            content_hash=str(row["content_hash"]),
            size=int(row["size"]),
            mtime=float(row["mtime"]),
        )

    def upsert_document(
        self, doc: LoadedDoc, *, content_hash: str, size: int, mtime: float
    ) -> tuple[int, bool]:
        """Insert or replace a document row. Returns ``(doc_id, was_update)``.

        Replacement deletes the old chunks first, so a shorter new version
        cannot leave orphaned chunks whose citations point past the end of the
        file.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM documents WHERE source = ?", (doc.path,)
            ).fetchone()
            if row is not None:
                doc_id = int(row["id"])
                self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                self._conn.execute(
                    "UPDATE documents SET kind=?, title=?, content_hash=?, size=?, "
                    "mtime=?, updated_at=datetime('now') WHERE id=?",
                    (doc.kind, doc.title, content_hash, size, mtime, doc_id),
                )
                self._conn.commit()
                return doc_id, True
            cursor = self._conn.execute(
                "INSERT INTO documents(source, kind, title, content_hash, size, mtime) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc.path, doc.kind, doc.title, content_hash, size, mtime),
            )
            self._conn.commit()
            return int(cursor.lastrowid), False

    def add_chunks(
        self,
        doc_id: int,
        chunks: Sequence[TextChunk],
        vectors: Sequence[Sequence[float]] | None = None,
    ) -> int:
        """Store ``chunks`` for a document, with optional per-chunk vectors."""
        rows = []
        for index, chunk in enumerate(chunks):
            vector = vectors[index] if vectors and index < len(vectors) else None
            blob, dims = _pack_vector(vector)
            rows.append(
                (
                    doc_id,
                    index,
                    chunk.text,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.line_start,
                    chunk.line_end,
                    blob,
                    dims,
                )
            )
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO chunks(doc_id, ordinal, text, char_start, char_end, "
                "line_start, line_end, embedding, dims) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    # -- search --------------------------------------------------------
    def search_lexical(self, query: str, limit: int = 20) -> list[tuple[int, int, float]]:
        """bm25-ranked full-text hits as ``(chunk_id, doc_id, rank)``, best first.

        Terms are tried with AND first ("pricing plan cost" prefers a passage
        holding all three) and OR second. The fallback matters because a single
        inflected word the corpus lacks -- "cost" where the notes say "costs" --
        makes an all-AND query match nothing at all, so a good passage is lost to
        one word. bm25 still ranks the OR result by how much it matches, so the
        fallback widens recall without turning into noise.
        """
        sql = (
            "SELECT c.id AS id, c.doc_id AS doc_id, bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        for operator in ("AND", "OR"):
            match = fts_query(query, operator)
            if not match:
                return []
            try:
                with self._lock:
                    rows = self._conn.execute(sql, (match, limit)).fetchall()
            except sqlite3.OperationalError:
                # A query the FTS grammar cannot parse returns nothing rather
                # than killing the run; the vector side may still answer.
                return []
            if rows:
                return [
                    (int(r["id"]), int(r["doc_id"]), float(r["rank"])) for r in rows
                ]
        return []

    def load_vectors(self) -> tuple[list[int], "list | None", "object | None"]:
        """``(ids, doc_ids, matrix)`` for every stored vector, or ``([], None, None)``."""
        np = _numpy()
        if np is None:
            return [], None, None
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, doc_id, embedding, dims FROM chunks "
                "WHERE embedding IS NOT NULL"
            ).fetchall()
        ids: list[int] = []
        doc_ids: list[int] = []
        vectors = []
        for row in rows:
            blob = row["embedding"]
            dims = int(row["dims"] or 0)
            if not blob or dims <= 0:
                continue
            array = np.frombuffer(blob, dtype=np.float32)
            if array.shape[0] != dims:
                continue
            ids.append(int(row["id"]))
            doc_ids.append(int(row["doc_id"]))
            vectors.append(array)
        if not vectors:
            return [], None, None
        return ids, doc_ids, np.vstack(vectors)

    def chunks_by_ids(self, ids: Sequence[int]) -> dict[int, dict]:
        """Full chunk + document rows for the given chunk ids."""
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        sql = (
            "SELECT c.id AS id, c.doc_id AS doc_id, c.text AS text, "
            "c.line_start AS line_start, c.line_end AS line_end, c.ordinal AS ordinal, "
            "d.source AS source, d.kind AS kind, d.title AS title "
            "FROM chunks c JOIN documents d ON d.id = c.doc_id "
            f"WHERE c.id IN ({placeholders})"
        )
        with self._lock:
            rows = self._conn.execute(sql, list(ids)).fetchall()
        return {int(r["id"]): dict(r) for r in rows}

    # -- inventory -----------------------------------------------------
    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.id, d.source, d.kind, d.title, d.size, d.mtime, "
                "d.updated_at, "
                "(SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id) AS chunks "
                "FROM documents d ORDER BY d.source"
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            documents = int(
                self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            )
            chunks = int(
                self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            )
            embedded = int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM chunks WHERE embedding IS NOT NULL"
                ).fetchone()["n"]
            )
        return {
            "documents": documents,
            "chunks": chunks,
            "embedded": embedded,
            "path": str(self.path),
            "schema_version": SCHEMA_VERSION,
        }

    # -- mutation ------------------------------------------------------
    def forget(self, *, source: str | None = None, everything: bool = False) -> int:
        """Erase whole documents. Returns the number removed; 0 if nothing matched.

        ``source`` matches either one exact file or every document under a
        directory -- the same shape ``ingest`` accepts -- so forgetting a folder
        forgets exactly what indexing that folder created, and a path with LIKE
        wildcards in its name is still matched literally.
        """
        with self._lock:
            if everything:
                self._conn.execute("DELETE FROM chunks")
                cursor = self._conn.execute("DELETE FROM documents")
            elif source:
                clauses = ["source = ?"]
                params: list[object] = [source]
                for sep in ("\\", "/"):
                    stripped = source.rstrip(sep)
                    if stripped:
                        clauses.append("source LIKE ? ESCAPE '\\'")
                        params.append(_escape_like(stripped + sep) + "%")
                where = " OR ".join(clauses)
                self._conn.execute(
                    "DELETE FROM chunks WHERE doc_id IN "
                    f"(SELECT id FROM documents WHERE {where})",
                    params,
                )
                cursor = self._conn.execute(
                    f"DELETE FROM documents WHERE {where}", params
                )
            else:
                return 0
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:  # noqa: BLE001 - closing twice must not raise
            pass


def _escape_like(text: str) -> str:
    """Escape one segment of a LIKE pattern so path characters match literally."""
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def fts_query(text: str, operator: str = "AND") -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Terms are quoted (so punctuation cannot be read as an FTS operator) and
    joined by ``operator``. Duplicates are dropped so a repeated word does not
    double its weight in a query that is meant to be about presence, not counts.
    """
    tokens = _TOKEN_RE.findall(text or "")
    unique: list[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    joiner = f" {operator.upper()} "
    return joiner.join(f'"{token}"' for token in unique)


def _pack_vector(vector: Sequence[float] | None) -> tuple[bytes | None, int]:
    np = _numpy()
    if not vector or np is None:
        return None, 0
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.shape[0] == 0:
        return None, 0
    return array.tobytes(), int(array.shape[0])
