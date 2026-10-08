"""mem0 as an Alan :class:`MemoryProvider`.

The second backend behind ADR-002. cognee is the default because it is proven and
keyless; mem0 is the semantic-memory specialist.

Three things shape this adapter:

* **Lazy import.** mem0 pulls qdrant-client, sqlalchemy, protobuf and posthog.
  None of that is imported unless mem0 is actually selected, so the main
  environment stays lean and the test suite never touches it.
* **Local LLM.** mem0 needs an extraction model. It is pointed at the bundled
  Ollama server, so Alan remains keyless and offline.
* **Telemetry off.** mem0 ships posthog as a hard dependency. ``MEM0_TELEMETRY``
  is forced off before the import, because brief section 16 forbids unsolicited
  telemetry in a portable product.

mem0 returns ``{"results": [...]}`` with its own memory records. Those are
mapped onto :class:`MemoryItem` here so the rest of Alan never sees a mem0 type.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from ...config import DATA_DIR, MemorySettings
from ...interfaces.memory import MemoryProvider, MemoryUnavailable
from ...interfaces.types import MemoryItem, MemoryLevel, MemoryType, Privacy, utcnow


class Mem0Provider(MemoryProvider):
    """Semantic long-term memory via mem0 (optional dependency)."""

    name = "mem0"

    def __init__(self, settings: MemorySettings, *, model: str = "") -> None:
        self.settings = settings
        self.model = model or settings.mem0_model or "qwen2.5:1.5b"
        self.store_path = settings.mem0_store_path or str(DATA_DIR / "mem0")
        self.user_id = settings.dataset or "alan"
        self._memory: Any = None
        self._load_error = ""

    # -- construction --------------------------------------------------
    def _build(self) -> Any:
        """Import and configure mem0. Raises MemoryUnavailable if impossible."""
        if self._memory is not None:
            return self._memory
        if self._load_error:
            raise MemoryUnavailable(self._load_error)

        # Must be set before the import: mem0 reads it at module import time.
        os.environ["MEM0_TELEMETRY"] = "False"
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        try:
            from mem0 import Memory  # type: ignore[import-not-found]
        except ImportError as exc:
            self._load_error = (
                "mem0 is not installed in this environment "
                "(pip install mem0ai -- see docs/integrations/dependencies.md)"
            )
            raise MemoryUnavailable(self._load_error) from exc

        config = {
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": self.model,
                    "temperature": 0.1,
                    "ollama_base_url": self.settings.mem0_base_url,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": self.settings.mem0_embedder},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": self.store_path,
                    "collection_name": self.user_id,
                    "embedding_model_dims": 384,
                },
            },
        }
        try:
            self._memory = Memory.from_config(config)
        except Exception as exc:
            self._load_error = f"mem0 failed to initialise: {exc}"
            raise MemoryUnavailable(self._load_error) from exc
        return self._memory

    def available(self) -> tuple[bool, str]:
        if self._load_error:
            return False, self._load_error
        try:
            import importlib.util

            if importlib.util.find_spec("mem0") is None:
                return False, "mem0 is not installed in this environment"
        except (ImportError, ValueError):
            return False, "mem0 is not installed in this environment"
        return True, f"ready (llm={self.model} via {self.settings.mem0_base_url})"

    # -- mapping -------------------------------------------------------
    def _to_item(self, record: dict[str, Any]) -> MemoryItem:
        metadata = dict(record.get("metadata") or {})
        level = metadata.get("level")
        try:
            level_enum = MemoryLevel(int(level)) if level is not None else MemoryLevel.L4_USER
        except (TypeError, ValueError):
            level_enum = MemoryLevel.L4_USER
        return MemoryItem(
            content=str(record.get("memory") or record.get("text") or ""),
            level=level_enum,
            type=MemoryType.FACT,
            source=f"mem0:{record.get('user_id', self.user_id)}",
            id=str(record.get("id") or ""),
            importance=float(metadata.get("importance", 0.7)),
            privacy=Privacy.INTERNAL,
            metadata=metadata,
        )

    # -- MemoryProvider ------------------------------------------------
    def remember(self, item: MemoryItem) -> MemoryItem:
        memory = self._build()
        try:
            result = memory.add(
                [{"role": "user", "content": item.content}],
                user_id=self.user_id,
                metadata={
                    "level": int(item.level),
                    "type": item.type.value,
                    "importance": item.importance,
                    "privacy": item.privacy.value,
                },
            )
        except Exception as exc:
            raise MemoryUnavailable(f"mem0 write failed: {exc}") from exc
        if isinstance(result, dict):
            for record in result.get("results", []) or []:
                if isinstance(record, dict):
                    item.id = item.id or str(record.get("id", ""))
        item.timestamp = utcnow()
        return item

    def update(self, item_id: str, item: MemoryItem) -> bool:
        memory = self._build()
        try:
            memory.update(memory_id=item_id, data=item.content)
        except Exception:
            # No update path: remove then re-add so the text is current.
            self.forget(item_id)
            self.remember(item)
        return True

    def forget(self, item_id: str | None = None, *, everything: bool = False) -> int:
        memory = self._build()
        try:
            if everything:
                memory.delete_all(user_id=self.user_id)
                return -1  # mem0 does not report a count
            if item_id is None:
                return 0
            memory.delete(memory_id=item_id)
            return 1
        except Exception as exc:
            raise MemoryUnavailable(f"mem0 delete failed: {exc}") from exc

    def retrieve(self, item_id: str) -> MemoryItem | None:
        try:
            memory = self._build()
            record = memory.get(memory_id=item_id)
        except MemoryUnavailable:
            raise
        except Exception:
            return None
        return self._to_item(record) if isinstance(record, dict) else None

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        levels: Sequence[MemoryLevel] | None = None,
        min_confidence: float = 0.0,
    ) -> list[MemoryItem]:
        memory = self._build()
        try:
            result = memory.search(query=query, user_id=self.user_id, limit=top_k * 2)
        except Exception as exc:
            raise MemoryUnavailable(f"mem0 search failed: {exc}") from exc
        records = result.get("results", result) if isinstance(result, dict) else result
        wanted = set(levels) if levels else None
        hits: list[MemoryItem] = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            item = self._to_item(record)
            if not item.content or item.is_expired():
                continue
            if item.confidence < min_confidence:
                continue
            if wanted is not None and item.level not in wanted:
                continue
            item.touched()
            hits.append(item)
            if len(hits) >= top_k:
                break
        return hits

    def summarize(self, query: str = "", *, top_k: int = 20) -> str:
        hits = self.search(query, top_k=top_k) if query else []
        if not hits:
            return ""
        return "\n".join(f"- {item.content}" for item in hits)

    def promote_to_long_term(self, item: MemoryItem) -> MemoryItem:
        item.level = MemoryLevel.L4_USER
        item.importance = max(item.importance, 0.7)
        return self.remember(item)

    def stats(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {
            "backend": self.name,
            "model": self.model,
            "store": self.store_path,
            "reachable": ok,
            "reason": reason,
        }