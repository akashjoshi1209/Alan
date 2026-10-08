"""A dependency-free, file-backed :class:`MemoryProvider`.

This is Alan's default when the cognee sidecar is unavailable, and the store the
test suite uses. It implements the full contract, honours the L0-L5 hierarchy,
and needs no model, no embeddings and no network.

Search is lexical (token overlap + phrase bonus). That is deliberately weaker
than the cognee sidecar's semantic recall, and the orchestrator treats this
store as a fallback rather than a competitor: it is what keeps Alan useful when
the sidecar is down.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Sequence

from ...config import DATA_DIR
from ...core.atomic import write_atomic
from ...interfaces.memory import MemoryProvider
from ...interfaces.types import MemoryItem, MemoryLevel, MemoryType
from ..relevance import score as relevance_score

_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how i in is it me my of on or
    that the this to was were what when where which who will with you your""".split()
)


class JsonMemory(MemoryProvider):
    """JSONL-backed memory, one file per level under ``data/memory``."""

    name = "json"

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DATA_DIR / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[MemoryLevel, list[MemoryItem]] = {}

    # -- storage -------------------------------------------------------
    def _path(self, level: MemoryLevel) -> Path:
        return self.root / f"L{int(level)}.jsonl"

    def _load(self, level: MemoryLevel) -> list[MemoryItem]:
        with self._lock:
            if level in self._cache:
                return self._cache[level]
        items: list[MemoryItem] = []
        path = self._path(level)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(MemoryItem.from_dict(json.loads(line)))
                except (ValueError, KeyError):
                    continue  # skip a corrupt line rather than losing the store
        with self._lock:
            self._cache[level] = items
        return items

    def _flush(self, level: MemoryLevel) -> None:
        """Serialise one level to disk.

        Held under the lock for the whole read-modify-write, and written through
        a unique temp file, so concurrent writers cannot corrupt or lose an
        update (see alan.core.atomic).
        """
        with self._lock:
            items = self._cache.get(level, [])
            payload = "\n".join(
                json.dumps(item.to_dict(), ensure_ascii=False) for item in items
            )
            write_atomic(self._path(level), payload + ("\n" if payload else ""))

    def _levels_to_search(self, levels: Sequence[MemoryLevel] | None) -> list[MemoryLevel]:
        if not levels:
            return list(MemoryLevel)
        return list(levels)

    # -- MemoryProvider ------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".writable"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, f"ready ({self.root})"
        except OSError as exc:
            return False, f"cannot write to {self.root}: {exc}"

    def remember(self, item: MemoryItem) -> MemoryItem:
        with self._lock:
            items = self._load(item.level)
            items.append(item)
            self._cache[item.level] = items
            self._flush(item.level)
        return item

    def update(self, item_id: str, item: MemoryItem) -> bool:
        for level in MemoryLevel:
            with self._lock:
                items = self._load(level)
                for index, existing in enumerate(items):
                    if existing.id == item_id:
                        items[index] = item
                        self._cache[level] = items
                        self._flush(level)
                        return True
        return False

    def forget(self, item_id: str | None = None, *, everything: bool = False) -> int:
        removed = 0
        with self._lock:
            for level in list(MemoryLevel):
                items = self._load(level)
                if not items:
                    continue
                if everything:
                    removed += len(items)
                    self._cache[level] = []
                    self._flush(level)
                    continue
                if item_id is None:
                    continue
                keep = [i for i in items if i.id != item_id]
                removed += len(items) - len(keep)
                if len(keep) != len(items):
                    self._cache[level] = keep
                    self._flush(level)
        return removed

    def retrieve(self, item_id: str) -> MemoryItem | None:
        for level in MemoryLevel:
            for item in self._load(level):
                if item.id == item_id:
                    item.touched()
                    return item
        return None

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        levels: Sequence[MemoryLevel] | None = None,
        min_confidence: float = 0.0,
    ) -> list[MemoryItem]:
        terms = _terms(query)
        if not terms:
            return []
        scored: list[tuple[float, MemoryItem]] = []
        for level in self._levels_to_search(levels):
            for item in self._load(level):
                if item.is_expired() or item.confidence < min_confidence:
                    continue
                score = _score(terms, query, item)
                if score > 0:
                    item.touched()
                    scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _score, item in scored[:top_k]]

    def summarize(self, query: str = "", *, top_k: int = 20) -> str:
        items = self.search(query, top_k=top_k) if query else [
            item for level in MemoryLevel for item in self._load(level)
        ][:top_k]
        if not items:
            return ""
        by_level: dict[MemoryLevel, list[str]] = {}
        for item in items:
            by_level.setdefault(item.level, []).append(item.content.strip())
        lines = []
        for level in sorted(by_level):
            lines.append(f"[{level}] " + "; ".join(by_level[level][:5]))
        return "\n".join(lines)

    def promote_to_long_term(self, item: MemoryItem) -> MemoryItem:
        """Move to L4 (user) if not already durable, then persist there."""
        with self._lock:
            source = self._load(item.level)
            self._cache[item.level] = [i for i in source if i.id != item.id]
            self._flush(item.level)
        item.level = MemoryLevel.L4_USER
        if item.type is MemoryType.EPISODE:
            item.type = MemoryType.FACT
        return self.remember(item)

    def stats(self) -> dict[str, int]:
        return {str(level): len(self._load(level)) for level in MemoryLevel}


def _terms(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS}


def _score(terms: set[str], query: str, item: MemoryItem) -> float:
    """Relevance for one item. Zero means "not a match at all".

    Thin alias for :func:`alan.memory.relevance.score`, so the JSON store and
    the graph store cannot drift into disagreeing about what a match is.
    """
    return relevance_score(terms, query, item)
