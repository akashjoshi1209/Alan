"""cognee as an Alan :class:`MemoryProvider`.

Wraps the existing, proven :class:`~alan.memory.cognee_client.CogneeMemory` HTTP
client rather than reimplementing it, so the verified sidecar behaviour keeps
working unchanged.

## The metadata problem, and how it is solved

cognee retrieves *passages of text*. Alan's contract is
:class:`~alan.interfaces.types.MemoryItem`, which carries id, level, type,
confidence, importance, privacy and expiry. cognee has nowhere to put that.

So this adapter keeps a small **local index** — one JSONL record per remembered
passage — and attaches metadata on the way out:

* ``remember``  writes to cognee *and* records the item's metadata locally.
* ``search``    asks cognee for passages, then joins them to the index by
  normalised content. A passage with no index entry is treated as consolidated
  long-term knowledge (``L4_USER``), which is what the sidecar's dataset holds.

## Honest limitations

* cognee has no per-item update. :meth:`update` re-writes the passage and
  updates the index; the old text lingers in the graph until a dataset forget.
* cognee has no per-item delete either. :meth:`forget` tombstones the passage
  in a local tombstone set and drops it from the index; ``search`` then filters
  tombstones out, so Alan stops surfacing it. The graph copy is only purged by
  forgetting the dataset. This is recorded in the ADR rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Sequence

from ...config import DATA_DIR, MemorySettings
from ...core.atomic import write_atomic
from ...interfaces.memory import MemoryProvider, MemoryUnavailable
from ...interfaces.types import MemoryItem, MemoryLevel, MemoryType, Privacy, utcnow
from ..cognee_client import CogneeMemory


def _normalise(text: str) -> str:
    """Collapse text to a stable key for index joins and tombstones."""
    return " ".join((text or "").lower().split()).strip(" .!?,;:")


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:32]


class CogneeProvider(MemoryProvider):
    """Alan's default memory backend (ADR-002)."""

    name = "cognee"

    def __init__(
        self,
        settings: MemorySettings,
        *,
        index_root: Path | str | None = None,
        background_writes: bool = False,
    ) -> None:
        self.settings = settings
        self.client = CogneeMemory(settings)
        root = Path(index_root) if index_root else DATA_DIR / "memory"
        root.mkdir(parents=True, exist_ok=True)
        self._index_path = root / "cognee_index.jsonl"
        self._tombstone_path = root / "cognee_tombstones.json"
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}
        self._tombstones: set[str] = set()
        # cognee only builds the retrieval graph on a foreground write; a
        # background write returns before the graph exists. Default to correct
        # behaviour; the assistant's write queue opts into backgrounding.
        self.background_writes = background_writes
        self._load_index()

    # -- local metadata index ------------------------------------------
    def _load_index(self) -> None:
        with self._lock:
            self._index.clear()
            if self._index_path.is_file():
                for line in self._index_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    self._index[str(record.get("fingerprint"))] = record
            self._tombstones = set()
            if self._tombstone_path.is_file():
                try:
                    self._tombstones = set(
                        json.loads(self._tombstone_path.read_text(encoding="utf-8"))
                    )
                except ValueError:
                    self._tombstones = set()

    def _write_index(self) -> None:
        with self._lock:
            payload = "\n".join(
                json.dumps(record, ensure_ascii=False) for record in self._index.values()
            )
        write_atomic(self._index_path, payload + ("\n" if payload else ""))

    def _write_tombstones(self) -> None:
        with self._lock:
            payload = json.dumps(sorted(self._tombstones), indent=0)
        write_atomic(self._tombstone_path, payload)

    def _item_for(self, passage: str) -> MemoryItem:
        """Join a cognee passage to its recorded metadata, or synthesise it."""
        record = self._index.get(_fingerprint(passage))
        if record is None:
            # Never indexed: consolidated knowledge the sidecar built itself.
            return MemoryItem(
                content=passage.strip(),
                level=MemoryLevel.L4_USER,
                type=MemoryType.FACT,
                source="cognee",
                id=f"mem_cg_{_fingerprint(passage)}",
                confidence=0.8,
                importance=0.5,
            )
        return MemoryItem.from_dict({**record, "content": passage.strip()})

    # -- MemoryProvider ------------------------------------------------
    def available(self) -> tuple[bool, str]:
        health = self.client.health()
        if health is None:
            return False, f"cognee sidecar not reachable at {self.client.base_url}"
        return True, f"ready ({self.client.base_url}, dataset={self.dataset})"

    @property
    def dataset(self) -> str:
        return self.client.dataset

    def remember(self, item: MemoryItem) -> MemoryItem:
        if not item.content.strip():
            return item
        if self.client.health() is None:
            raise MemoryUnavailable(
                f"cognee sidecar not reachable at {self.client.base_url}"
            )
        ok = self.client.remember(
            item.content, background=self.background_writes
        )
        if not ok:
            raise MemoryUnavailable("cognee rejected the write")
        record = item.to_dict()
        record["fingerprint"] = _fingerprint(item.content)
        with self._lock:
            self._index[record["fingerprint"]] = record
            self._tombstones.discard(record["fingerprint"])
        self._write_index()
        return item

    def update(self, item_id: str, item: MemoryItem) -> bool:
        with self._lock:
            current = next(
                (r for r in self._index.values() if r.get("id") == item_id), None
            )
        if current is None:
            return False
        # cognee cannot update in place: re-write the passage, then move the
        # metadata. The superseded text stays in the graph until a forget.
        self.remember(item)
        with self._lock:
            self._index.pop(str(current.get("fingerprint")), None)
        self._write_index()
        return True

    def forget(self, item_id: str | None = None, *, everything: bool = False) -> int:
        if everything:
            removed = len(self._index)
            with self._lock:
                self._index.clear()
                self._tombstones.clear()
            self._write_index()
            self._write_tombstones()
            self.client.forget(everything=True)
            return removed
        if item_id is None:
            return 0

        with self._lock:
            match = next(
                (r for r in self._index.values() if r.get("id") == item_id), None
            )
            if match is None:
                return 0
            fingerprint = str(match.get("fingerprint"))
            self._index.pop(fingerprint, None)
            self._tombstones.add(fingerprint)
        self._write_index()
        self._write_tombstones()
        return 1

    def retrieve(self, item_id: str) -> MemoryItem | None:
        with self._lock:
            record = next(
                (r for r in self._index.values() if r.get("id") == item_id), None
            )
        if record is None:
            return None
        item = MemoryItem.from_dict(record)
        item.touched()
        return item

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        levels: Sequence[MemoryLevel] | None = None,
        min_confidence: float = 0.0,
        session_id: str | None = None,
    ) -> list[MemoryItem]:
        # Ask for extra candidates: tombstones and level filters are applied
        # after retrieval, so requesting exactly top_k can under-fill.
        passages = self.client.recall(query, top_k=max(top_k * 3, top_k + 5))
        if not passages:
            return []
        wanted = set(levels) if levels else None
        hits: list[MemoryItem] = []
        for passage in passages:
            if _fingerprint(passage) in self._tombstones:
                continue
            item = self._item_for(passage)
            if item.is_expired() or item.confidence < min_confidence:
                continue
            if wanted is not None and item.level not in wanted:
                continue
            item.touched()
            hits.append(item)
        # cognee's recall does not discriminate on a small dataset: the same
        # passages came back for unrelated questions. Gating here keeps `search`
        # honest for every caller, not only the ones that build a prompt.
        from ..relevance import gate, rank_sessions

        return rank_sessions(session_id, gate(query, hits))[:top_k]

    def summarize(self, query: str = "", *, top_k: int = 20) -> str:
        items = self.search(query, top_k=top_k) if query else []
        if not items:
            return ""
        by_level: dict[MemoryLevel, list[str]] = {}
        for item in items:
            by_level.setdefault(item.level, []).append(item.content)
        return "\n".join(
            f"[{level}] " + "; ".join(by_level[level][:5]) for level in sorted(by_level)
        )

    def promote_to_long_term(self, item: MemoryItem) -> MemoryItem:
        """Move an item to durable user scope.

        Privacy is only ever tightened here, never loosened: promoting a
        ``SECRET`` to ``L4_USER`` must not silently downgrade its sensitivity.
        """
        item.level = MemoryLevel.L4_USER
        if item.privacy is Privacy.PUBLIC:
            item.privacy = Privacy.INTERNAL
        item.importance = max(item.importance, 0.7)
        item.timestamp = item.timestamp or utcnow()
        return self.remember(item)

    def close(self) -> None:
        return None

    # -- introspection -------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            indexed = len(self._index)
            tombstoned = len(self._tombstones)
        ok, reason = self.available()
        return {
            "backend": self.name,
            "dataset": self.dataset,
            "indexed": indexed,
            "tombstoned": tombstoned,
            "reachable": ok,
            "reason": reason,
        }