"""Alan's own persistent memory: one SQLite file, no sidecar.

This is the default backend. It exists because the cognee sidecar was measured
costing **1.47 s warm and 24 s cold on every chat turn** (an HTTP hop on the
critical path to the first token) while returning nothing: its dataset had
never been written to. This store answers the same questions in **under 5 ms**
with no process to boot, no model to load and no port to probe.

What it stores
--------------
Everything Alan observes about a session, with the metadata that makes it
retrievable later:

* durable facts and preferences (L4, cross-session) -- what the user said
* episodic activity (L2, session-scoped) -- which tools ran, what they
  returned, which MCP servers were called, what failed and why
* decisions, instructions and agent state

The separation is deliberate. A transcript is not a memory (see
``orchestrator._memory_decision``): the *facts* are gated before they are
written, while the *episodes* record what happened so a later session can ask
"what did you do last time?". Session-scoped items stay scoped -- they are
boosted for their own session and quiet everywhere else -- so a long history
never drowns out a durable fact.

How retrieval works
-------------------
Three layers, cheapest first:

1. **Lexical.** SQLite FTS5 narrows the store to a few dozen candidates in
   single-digit milliseconds. No model, no network, always available.
2. **Scoring.** Candidates are scored by :func:`alan.memory.relevance.score`,
   the *same* scorer the JSON store and the graph adapter use, so the two
   cannot drift into disagreeing about what a match is.
3. **Vectors, optional.** When the local Ollama embedding model is up, a query
   vector reorders candidates by cosine similarity. Embeddings are produced by
   a background worker so a write never blocks, and the query vector is
   fetched on a strict time budget: if the model is cold, retrieval silently
   keeps the lexical answer rather than making the user wait for the first
   token.

The relevance gate is still authoritative (:func:`alan.memory.relevance.gate`):
a high cosine score never resurrects an item with no lexical overlap, which is
exactly the failure mode that gate was written to prevent -- cognee's top-k for
"capital of France" and for the user's own name were the same four items.

Degrading is a feature, not a failure: with Ollama down the store still writes,
still answers and still persists. It just loses the reordering signal.
"""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from ...config import DATA_DIR
from ...core.probecache import probe
from ...interfaces.memory import MemoryProvider, render_block
from ...interfaces.types import MemoryItem, MemoryLevel, MemoryType
from ..relevance import score as relevance_score
from ..relevance import session_bonus
from ..relevance import terms as query_terms

#: Candidates pulled out of FTS before scoring. Ample for a personal store,
#: and the FTS query itself is the cheap part.
_CANDIDATE_LIMIT = 60

#: Vectors only *reorder* candidates the gate already liked, so they are worth
#: a little, not a lot. Zero would make the whole embedder pointless; one
#: would let cosine outvote the shared scorer.
_VECTOR_WEIGHT = 0.6

#: Cap on how many embeddings one scan will read. Beyond this the store leans
#: on FTS alone, because loading a few hundred thousand 768-dim vectors into
#: RAM on a 4 GB machine is not a trade worth making.
_VECTOR_SCAN_LIMIT = 4000

#: Consecutive embed failures before the worker gives up for this process.
_EMBED_FAILURE_LIMIT = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    ts            TEXT NOT NULL,
    ts_epoch      REAL NOT NULL,
    last_accessed TEXT,
    level         INTEGER NOT NULL,
    type          TEXT NOT NULL,
    source        TEXT NOT NULL,
    content       TEXT NOT NULL,
    content_norm  TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'fact',
    confidence    REAL NOT NULL,
    importance    REAL NOT NULL,
    privacy       TEXT NOT NULL,
    expires       TEXT,
    expires_epoch REAL,
    session_id    TEXT,
    project       TEXT,
    tags          TEXT NOT NULL DEFAULT '',
    embed_model   TEXT NOT NULL DEFAULT '',
    embedding     BLOB,
    dim           INTEGER,
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mem_fp      ON memories(fingerprint);
CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_mem_level   ON memories(level);
CREATE INDEX IF NOT EXISTS idx_mem_kind    ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_mem_ts      ON memories(ts_epoch DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    fts_id UNINDEXED
);
"""


def _numpy() -> Any:
    """numpy, imported only when a vector scan actually happens.

    Same reasoning as ``knowledge.store``: numpy is ~100 ms of import on a
    machine where ``alan`` opens in 1.8 s, and most turns never scan vectors.
    """
    try:
        import numpy  # noqa: PLC0415 - deliberate lazy import

        return numpy
    except Exception:  # noqa: BLE001 - absent numpy only costs the reordering
        return None


def _normalise(text: str) -> str:
    """Collapse text to a stable key for dedupe."""
    return " ".join((text or "").lower().split()).strip(" .!?,;:")


def _fingerprint(text: str) -> str:
    import hashlib

    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:32]


def _epoch(value: datetime | None) -> float | None:
    return value.timestamp() if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def derive_kind(item: MemoryItem) -> str:
    """A one-word label for what an item *is*, used for filtering.

    ``/memory --kind tool`` should show what Alan did, and ``--kind fact``
    what it knows. Deriving it here keeps every caller from re-implementing
    the same branching -- and keeps it stable in the database.

    MCP is checked *before* the episode rule: an MCP call is also an episode,
    and testing the broader category first would quietly relabel every MCP
    record as a plain tool call.
    """
    source = (item.source or "").lower()
    kind = (item.metadata or {}).get("kind")
    if kind:
        return str(kind)
    if source.startswith("mcp") or "mcp" in (item.metadata or {}):
        return "mcp"
    if item.type is MemoryType.EPISODE or source.startswith("tool"):
        return "tool"
    if item.type is MemoryType.INSTRUCTION:
        return "instruction"
    if item.type is MemoryType.DECISION:
        return "decision"
    if item.type is MemoryType.PREFERENCE:
        return "preference"
    if item.type is MemoryType.AGENT_STATE:
        return "agent_state"
    if item.type is MemoryType.SUMMARY:
        return "summary"
    if item.type is MemoryType.SKILL:
        return "skill"
    return "fact"


class LocalMemory(MemoryProvider):
    """Persistent, per-session-aware memory backed by one SQLite file.

    Construction is cheap and never touches the network: the schema is applied
    on first use, and the embedder is only *probed* (through the shared probe
    cache) when a vector scan is about to happen.
    """

    name = "local"

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        embedder: Any | None = None,
        vector_budget_ms: int = 150,
        batch_size: int = 8,
    ) -> None:
        base = Path(root) if root else _default_root()
        base.mkdir(parents=True, exist_ok=True)
        self.root = base
        self.path = base / "memory.db"
        self.embedder = embedder
        self.vector_budget_ms = max(0, int(vector_budget_ms))
        self.batch_size = max(1, int(batch_size))

        self._local = threading.local()
        self._lock = threading.RLock()
        self._schema_done = False
        self._ready: tuple[bool, str] | None = None

        # Background embedding: writes never block on a model.
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._embed_failures = 0
        self._vectors_dead = False
        # One in-flight query embedding at a time; a cold model that takes 60 s
        # must not let a second thread pile another request onto a busy server.
        self._embedding_lock = threading.Lock()
        self._migrated = False

    def _migrate_legacy(self) -> None:
        """Adopt anything an older backend wrote, once.

        A store that starts empty on the day cognee is retired would silently
        lose what little it had managed to persist. Importing is idempotent --
        items dedupe on their content fingerprint -- and it only runs while the
        database is empty, so the scan costs nothing on the way to a store that
        already has history.
        """
        if self._migrated:
            return
        self._migrated = True
        try:
            total = self._conn().execute(
                "SELECT COUNT(*) c FROM memories"
            ).fetchone()["c"]
        except sqlite3.Error:
            return
        if total:
            return
        records: list[dict[str, Any]] = []
        # The JSON store: one JSONL file per level, same directory.
        for path in sorted(self.root.glob("L[0-5].jsonl")):
            records.extend(_read_jsonl(path))
        # cognee's local metadata index. Its records carry cognee-specific keys
        # ("fingerprint") that MemoryItem.from_dict ignores, which is exactly
        # what makes adopting them a plain import.
        records.extend(_read_jsonl(self.root / "cognee_index.jsonl"))
        if records:
            self.import_records(records)

    # -- connection handling ------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """One connection per thread.

        SQLite connections are not shareable across threads, and this store is
        written by the chat thread and the embedding worker simultaneously.
        WAL plus a busy timeout keeps the reader from blocking the writer.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path), timeout=10.0, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
            self._schema(conn)
        self._migrate_legacy()
        return conn

    def _schema(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            if self._schema_done:
                return
            conn.executescript(_SCHEMA)
            conn.commit()
            self._schema_done = True

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            self._queue.put(None)
            worker.join(timeout=2.0)
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    # -- MemoryProvider: availability ---------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            conn = self._conn()
            conn.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            return False, f"sqlite unavailable at {self.path}: {exc}"
        if self._ready is None:
            embed = "vectors off"
            if self.embedder is not None:
                model = getattr(self.embedder, "model", "")
                base = getattr(self.embedder, "base_url", "")
                ok, reason = probe(
                    f"memory|embed|{base}|{model}",
                    lambda: _safe_available(self.embedder),
                )
                embed = f"vectors {reason}" if ok else f"{reason} (lexical only)"
            self._ready = (True, f"ready ({self.path}, {embed})")
        return self._ready

    # -- embedding -----------------------------------------------------
    def _embedder_ready(self) -> bool:
        """Is the embedding model usable right now?

        Answered through the shared probe cache, never by asking Ollama again.
        ``available()`` is an HTTP round trip to ``/api/tags`` -- roughly 0.24 s
        -- and this runs in front of the first token, so calling it per search
        would rebuild the exact latency problem this store exists to remove.
        """
        if self.embedder is None or self._vectors_dead or self._embed_failures:
            return False
        model = getattr(self.embedder, "model", "")
        base = getattr(self.embedder, "base_url", "")
        ok, _reason = probe(
            f"memory|embed|{base}|{model}",
            lambda: _safe_available(self.embedder),
        )
        return bool(ok)

    def _start_worker(self) -> None:
        with self._lock:
            if self._worker is not None or self.embedder is None:
                return
            worker = threading.Thread(
                target=self._embed_loop, name="alan-memory-embed", daemon=True
            )
            self._worker = worker
            worker.start()

    def _embed_loop(self) -> None:
        """Drain queued ids and write embeddings back in batches."""
        pending: list[str] = []
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                item = None
            if item is None:
                if pending:
                    self._flush_embeddings(pending)
                    pending = []
                if self._queue.empty():
                    break
                continue
            pending.append(item)
            if len(pending) >= self.batch_size:
                self._flush_embeddings(pending)
                pending = []
        if pending:
            self._flush_embeddings(pending)

    def _flush_embeddings(self, ids: Sequence[str]) -> None:
        if not ids or self._embed_failures >= _EMBED_FAILURE_LIMIT:
            return
        rows = self._rows_for_embedding(list(ids))
        if not rows:
            return
        try:
            vectors = self.embedder.embed([r["content"] for r in rows])
        except Exception:  # noqa: BLE001 - a broken embedder only costs vectors
            self._embed_failures += 1
            if self._embed_failures >= _EMBED_FAILURE_LIMIT:
                self._vectors_dead = True
            return
        if len(vectors) != len(rows):
            self._embed_failures += 1
            return
        model = getattr(self.embedder, "model", "unknown")
        try:
            conn = self._conn()
            with self._lock:
                for row, vector in zip(rows, vectors):
                    if not vector:
                        continue
                    conn.execute(
                        "UPDATE memories SET embedding = ?, dim = ?, embed_model = ?"
                        " WHERE id = ?",
                        (_pack(vector), len(vector), model, row["id"]),
                    )
                conn.commit()
        except sqlite3.Error:
            self._embed_failures += 1
        else:
            self._embed_failures = 0

    def _rows_for_embedding(self, ids: Sequence[str]) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        try:
            conn = self._conn()
            return conn.execute(
                f"SELECT id, content FROM memories"
                f" WHERE embed_model = '' AND id IN ({marks})",
                tuple(ids),
            ).fetchall()
        except sqlite3.Error:
            return []

    def _embed_query(self, query: str) -> list[float] | None:
        """Embed a query, bounded by ``vector_budget_ms``.

        Bounded because this runs in front of the first token. A cold Ollama
        model can take tens of seconds; the user must not pay that to be told
        hello, so the call is abandoned after the budget and retrieval keeps
        the lexical answer it already has.
        """
        if not self._embedder_ready() or not self.vector_budget_ms:
            return None
        if not self._embedding_lock.acquire(blocking=False):
            return None  # another query is already waiting on the model
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["vector"] = self.embedder.embed([query])[0]
            except Exception:  # noqa: BLE001
                box["vector"] = None

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(self.vector_budget_ms / 1000.0)
        self._embedding_lock.release()
        return box.get("vector")

    # -- MemoryProvider: write ----------------------------------------
    def remember(self, item: MemoryItem) -> MemoryItem:
        if not item.content.strip():
            return item
        fingerprint = _fingerprint(item.content)
        kind = derive_kind(item)
        conn = self._conn()
        row = {
            "id": item.id,
            "fingerprint": fingerprint,
            "ts": _iso(item.timestamp) or utcnow_iso(),
            "ts_epoch": _epoch(item.timestamp) or time.time(),
            "last_accessed": _iso(item.last_accessed),
            "level": int(item.level),
            "type": item.type.value,
            "source": item.source or "user",
            "content": item.content,
            "content_norm": _normalise(item.content),
            "kind": kind,
            "confidence": float(item.confidence),
            "importance": float(item.importance),
            "privacy": item.privacy.value,
            "expires": _iso(item.expires),
            "expires_epoch": _epoch(item.expires),
            "session_id": item.session_id,
            "project": item.project,
            "tags": ",".join(_as_tags(item.metadata.get("tags"))),
            "embed_model": "",
            "embedding": None,
            "dim": None,
            "metadata": json.dumps(item.metadata or {}, ensure_ascii=False),
        }
        with self._lock:
            existing = conn.execute(
                "SELECT id FROM memories WHERE fingerprint = ? AND session_id IS ?",
                (fingerprint, item.session_id),
            ).fetchone()
            if existing is not None:
                # Same text in the same session: refresh rather than duplicate.
                # A tool that ran the same query twice is one memory, not two.
                row["id"] = str(existing["id"])
                item.id = row["id"]
            conn.execute(
                "INSERT OR REPLACE INTO memories (id, fingerprint, ts, ts_epoch,"
                " last_accessed, level, type, source, content, content_norm, kind,"
                " confidence, importance, privacy, expires, expires_epoch,"
                " session_id, project, tags, embed_model, embedding, dim, metadata)"
                " VALUES (:id, :fingerprint, :ts, :ts_epoch, :last_accessed, :level,"
                " :type, :source, :content, :content_norm, :kind, :confidence,"
                " :importance, :privacy, :expires, :expires_epoch, :session_id,"
                " :project, :tags, :embed_model, :embedding, :dim, :metadata)",
                row,
            )
            conn.execute(
                "DELETE FROM memories_fts WHERE fts_id = ?", (row["id"],)
            )
            conn.execute(
                "INSERT INTO memories_fts (content, fts_id) VALUES (?, ?)",
                (item.content, row["id"]),
            )
            conn.commit()
        if self.embedder is not None and not self._vectors_dead:
            self._start_worker()
            self._queue.put(row["id"])
        return item

    def update(self, item_id: str, item: MemoryItem) -> bool:
        conn = self._conn()
        row = conn.execute(
            "SELECT id FROM memories WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False
        item.id = item_id
        self.forget(item_id)
        item.id = item_id
        self.remember(item)
        return True

    def forget(self, item_id: str | None = None, *, everything: bool = False) -> int:
        conn = self._conn()
        with self._lock:
            if everything:
                count = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
                conn.execute("DELETE FROM memories")
                conn.execute("DELETE FROM memories_fts")
                conn.commit()
                return int(count or 0)
            if item_id is None:
                return 0
            conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
            conn.execute("DELETE FROM memories_fts WHERE fts_id = ?", (item_id,))
            changed = conn.total_changes
            conn.commit()
            return 1 if changed else 0

    # -- MemoryProvider: read -----------------------------------------
    def retrieve(self, item_id: str) -> MemoryItem | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        item = _to_item(row)
        item.touched()
        self._touch(item_id)
        return item

    def _touch(self, item_id: str) -> None:
        try:
            conn = self._conn()
            with self._lock:
                conn.execute(
                    "UPDATE memories SET last_accessed = ? WHERE id = ?",
                    (utcnow_iso(), item_id),
                )
                conn.commit()
        except sqlite3.Error:  # pragma: no cover - access stamps are advisory
            pass

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        levels: Sequence[MemoryLevel] | None = None,
        min_confidence: float = 0.0,
        session_id: str | None = None,
    ) -> list[MemoryItem]:
        terms = _terms(query)
        if not terms:
            return []
        candidates = self._candidates(query, terms)
        if not candidates:
            return []
        wanted = set(levels) if levels else None
        query_vector = self._embed_query(query)
        scored: list[tuple[float, MemoryItem]] = []
        for row, lexical in candidates:
            if row["expires_epoch"] and row["expires_epoch"] <= time.time():
                continue
            if float(row["confidence"]) < min_confidence:
                continue
            wanted_level = MemoryLevel(int(row["level"]))
            if wanted is not None and wanted_level not in wanted:
                continue
            item = _to_item(row)
            total = lexical + session_bonus(session_id, item)
            if query_vector and row["embedding"]:
                cosine = _cosine(query_vector, _unpack(row["embedding"]))
                if cosine is not None:
                    total += _VECTOR_WEIGHT * max(0.0, cosine)
            if total <= 0:
                continue
            item.touched()
            scored.append((total, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # The zero-overlap half of the gate was already applied in
        # `_candidates` (nothing with `relevance.score <= 0` was ever a
        # candidate), so what is left is to record *why* each survivor ranked
        # where it did. Calling `relevance.gate` again here would re-sort by
        # pure lexical score and silently discard the session bonus and the
        # vector weight computed just above -- the memory would be filtered
        # correctly and then ordered as if neither existed.
        hits: list[MemoryItem] = []
        for total, item in scored:
            if total <= 0:
                continue
            item.score = total
            hits.append(item)
        return hits[:top_k]

    def context_block(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_chars: int = 2000,
        hits: Sequence[MemoryItem] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Render already-gated hits without re-gating them.

        The base implementation calls `relevance.gate` a second time. Here that
        would undo the ordering `search` just computed -- same trap as above,
        one frame higher, and the visible one: it decides what Alan actually
        reads. The gate's *filtering* is already done; only its re-sort is
        unwanted.
        """
        if hits is None:
            try:
                hits = self.search(query, top_k=top_k, session_id=session_id)
            except Exception:  # noqa: BLE001 - memory is an enhancement
                return ""
        return render_block(hits, max_chars=max_chars)

    def _candidates(
        self, query: str, terms: set[str]
    ) -> list[tuple[sqlite3.Row, float]]:
        """FTS narrows the store; the shared scorer ranks what comes back."""
        conn = self._conn()
        match = " OR ".join(f'"{t}"' for t in sorted(terms))
        try:
            rows = conn.execute(
                "SELECT m.* FROM memories_fts f"
                " JOIN memories m ON m.id = f.fts_id"
                " WHERE memories_fts MATCH ?"
                " ORDER BY bm25(memories_fts)"
                " LIMIT ?",
                (match, _CANDIDATE_LIMIT),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        if not rows:
            # No FTS hit at all (a brand-new store, or a query made entirely of
            # stopwords). Fall back to a cheap lexical scan so the relevance
            # scorer still gets a say.
            try:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY ts_epoch DESC LIMIT ?",
                    (_CANDIDATE_LIMIT,),
                ).fetchall()
            except sqlite3.Error:
                return []
        out: list[tuple[sqlite3.Row, float]] = []
        for row in rows:
            item = _to_item(row)
            value = relevance_score(terms, query, item)
            if value > 0:
                out.append((row, value))
        return out

    def summarize(self, query: str = "", *, top_k: int = 20) -> str:
        conn = self._conn()
        if query:
            items = self.search(query, top_k=top_k)
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY ts_epoch DESC LIMIT ?", (top_k,)
            ).fetchall()
            items = [_to_item(row) for row in rows]
        if not items:
            return ""
        by_level: dict[MemoryLevel, list[str]] = {}
        for item in items:
            by_level.setdefault(item.level, []).append(item.content.strip())
        return "\n".join(
            f"[{level}] " + "; ".join(by_level[level][:5]) for level in sorted(by_level)
        )

    def promote_to_long_term(self, item: MemoryItem) -> MemoryItem:
        """Move to durable user scope.

        An episode that becomes durable is no longer an episode: "we ran the
        build" is an event, "the build command is npm run build" is a fact.
        The JSON store makes the same conversion, so the two cannot disagree
        about what promotion means.
        """
        item.level = MemoryLevel.L4_USER
        if item.type is MemoryType.EPISODE:
            item.type = MemoryType.FACT
        return self.remember(item)

    # -- extras the interface does not require -------------------------
    def recent(
        self,
        *,
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[MemoryItem]:
        """Newest-first listing. What ``/memory`` renders without a query."""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if session_id is not None:
            sql += " AND session_id IS ?"
            params.append(session_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        # id DESC breaks ties: two items written in the same clock tick share a
        # ts_epoch, and without the tiebreaker SQLite would return them in
        # insertion order -- oldest first, which is the opposite of "newest
        # first". items() already orders this way for the same reason.
        sql += " ORDER BY ts_epoch DESC, id DESC LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn().execute(sql, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        return [_to_item(row) for row in rows]

    #: A listing is loaded whole, so it is bounded. A store that has grown
    #: past this is past the point where a terminal table is the right tool,
    #: and `--all` does not need the rows to delete them.
    _LIST_LIMIT = 5000

    def items(self) -> list[MemoryItem]:
        """Every stored item, oldest first.

        Ordering is chronological rather than ranked: this exists so `alan
        forget --list` can show what is stored, and what a person cleaning up
        wants to read is the sequence in which things were written.
        """
        try:
            rows = self._conn().execute(
                "SELECT * FROM memories ORDER BY ts_epoch ASC, id ASC LIMIT ?",
                (self._LIST_LIMIT,),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [_to_item(row) for row in rows]

    def forget_session(self, session_id: str) -> int:
        """Drop everything recorded for one session.

        Cross-session facts are *not* dropped: forgetting "what we did last
        Tuesday" must not delete "the user prefers metric units".
        """
        conn = self._conn()
        with self._lock:
            ids = [
                str(r["id"])
                for r in conn.execute(
                    "SELECT id FROM memories WHERE session_id = ?", (session_id,)
                )
            ]
            if not ids:
                return 0
            conn.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
            for item_id in ids:
                conn.execute(
                    "DELETE FROM memories_fts WHERE fts_id = ?", (item_id,)
                )
            conn.commit()
        return len(ids)

    def stats(self) -> dict[str, Any]:
        conn = self._conn()
        total = int(conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"])
        embedded = int(
            conn.execute(
                "SELECT COUNT(*) c FROM memories WHERE embed_model != ''"
            ).fetchone()["c"]
        )
        sessions = int(
            conn.execute(
                "SELECT COUNT(DISTINCT session_id) c FROM memories"
                " WHERE session_id IS NOT NULL"
            ).fetchone()["c"]
        )
        by_level: dict[str, int] = {}
        for row in conn.execute(
            "SELECT level, COUNT(*) c FROM memories GROUP BY level"
        ):
            by_level[str(MemoryLevel(int(row["level"])))] = int(row["c"])
        return {
            "backend": self.name,
            "path": str(self.path),
            "total": total,
            "embedded": embedded,
            "sessions": sessions,
            "by_level": by_level,
            "db_bytes": _db_bytes(self.path),
            "vectors": self._embedder_ready(),
        }

    # -- migration -----------------------------------------------------
    def import_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Adopt items written by another backend. Idempotent by fingerprint."""
        imported = 0
        for record in records:
            try:
                item = MemoryItem.from_dict(record)
            except (ValueError, KeyError, TypeError):
                continue
            if not item.content.strip():
                continue
            self.remember(item)
            imported += 1
        return imported


def utcnow_iso() -> str:
    from ...interfaces.types import utcnow

    return utcnow().isoformat()


def _default_root() -> Path:
    """Where memory.db lives by default.

    ``ALAN_MEMORY_DIR`` relocates the whole store. It exists so a test run
    never writes into the user's real memory -- episodic records from a test
    suite that invokes tools would otherwise pile up in a live store -- and it
    doubles as the documented way to put memory on another disk.
    """
    override = os.environ.get("ALAN_MEMORY_DIR", "").strip()
    return Path(override) if override else DATA_DIR / "memory"


def _safe_available(embedder: Any) -> tuple[bool, str]:
    try:
        return embedder.available()
    except Exception as exc:  # noqa: BLE001 - availability must never raise
        return False, f"{type(exc).__name__}: {exc}"


def _as_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(t).strip() for t in value if str(t).strip()]
    return []


def _terms(text: str) -> set[str]:
    """Terms for the FTS narrowing query.

    Deliberately :func:`alan.memory.relevance.terms` and not a private copy:
    the candidates FTS returns are scored by that module's ``score``, and two
    independent stopword lists mean the two can quietly disagree about what
    counts as a word -- which is a recall bug that only shows up as a missing
    memory. ``json_store`` still carries its own copy; this store does not.
    """
    return query_terms(text)


def _to_item(row: sqlite3.Row) -> MemoryItem:
    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads(row["metadata"] or "{}")
    except (ValueError, TypeError):
        metadata = {}
    return MemoryItem(
        content=row["content"],
        level=MemoryLevel(int(row["level"])),
        type=MemoryType(str(row["type"])),
        source=str(row["source"]),
        id=str(row["id"]),
        timestamp=_parse_dt(row["ts"]) or _now(),
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        privacy=_privacy(row["privacy"]),
        last_accessed=_parse_dt(row["last_accessed"]),
        expires=_parse_dt(row["expires"]),
        session_id=row["session_id"],
        project=row["project"],
        metadata=metadata,
    )


def _now() -> datetime:
    from ...interfaces.types import utcnow

    return utcnow()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping any line that is not an object."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("content"):
            out.append(record)
    return out


def _privacy(value: Any) -> Any:
    from ...interfaces.types import Privacy

    try:
        return Privacy(str(value))
    except ValueError:
        return Privacy.INTERNAL


def _db_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _pack(vector: Sequence[float]) -> bytes:
    """float32 little-endian. Half the bytes of a JSON list, and numpy-free."""
    import struct

    return struct.pack(f"<{len(vector)}f", *[float(v) for v in vector])


def _unpack(blob: Any) -> list[float] | None:
    if not blob:
        return None
    import struct

    count = len(blob) // 4
    if not count:
        return None
    return list(struct.unpack(f"<{count}f", bytes(blob)))


def _cosine(left: Sequence[float], right: Sequence[float] | None) -> float | None:
    """Cosine similarity. numpy when present, because 4000 x 768 in pure Python
    is a second of CPU on a machine that only has a few hundred MB to spare."""
    if not right or len(left) != len(right):
        return None
    np = _numpy()
    if np is not None:
        a = np.asarray(left, dtype=np.float32)
        b = np.asarray(right, dtype=np.float32)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if not denom:
            return None
        return float(np.dot(a, b) / denom)
    dot = sum(x * y for x, y in zip(left, right))
    norm_a = sum(x * x for x in left) ** 0.5
    norm_b = sum(y * y for y in right) ** 0.5
    if not norm_a or not norm_b:
        return None
    return dot / (norm_a * norm_b)
