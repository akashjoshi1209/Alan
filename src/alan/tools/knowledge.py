"""Knowledge-base tools, exposed to the planner and the permission gate.

Each operation is a separate :class:`ToolSpec` so the gate can treat them
differently: searching the user's own corpus is ``SAFE`` and needs no
confirmation, while erasing it is ``HIGH_RISK`` and goes through the same
confirmation path as any other destructive action. The store and embedder are
owned by the runtime and closed with it.

The search tool returns structured hits *and* a rendered, citable block, so the
orchestrator can put a checkable answer in front of the model rather than a
sentence the model has to be trusted to attribute.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..interfaces.tools import ToolProvider
from ..interfaces.types import RiskLevel, ToolSpec
from ..knowledge import KnowledgeStore, search
from ..knowledge.embeddings import Embedder, NullEmbedder
from ..knowledge.ingest import ingest_paths


class KnowledgeTools(ToolProvider):
    """The four knowledge operations: search, ingest, list, forget."""

    name = "knowledge"

    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder | None = None,
        *,
        top_k: int = 8,
        chunk_size: int = 1200,
        overlap: int = 200,
        max_file_mb: float = 2.0,
    ) -> None:
        self.store = store
        self.embedder = embedder or NullEmbedder()
        self.top_k = top_k
        self.chunk_size = chunk_size
        self.overlap = overlap
        self._max_bytes = int(max_file_mb * 1_000_000)

    # -- provider surface ----------------------------------------------
    def available(self) -> tuple[bool, str]:
        ok, reason = self.embedder.available()
        stats = self.store.stats()
        mode = "hybrid" if ok else f"lexical only ({reason})"
        return True, (
            f"{stats['documents']} doc(s), {stats['chunks']} chunk(s); {mode}"
        )

    def tools(self) -> Sequence[ToolSpec]:
        return (self._search_tool(), self._ingest_tool(), self._list_tool(),
                self._forget_tool())

    def close(self) -> None:
        self.store.close()

    # -- specs ---------------------------------------------------------
    def _search_tool(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge.search",
            description=(
                "Search the user's own notes, documents and code for a query and "
                "return the matching passages with a file:line citation for each."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": self.top_k},
                },
                "required": ["query"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.SAFE,
            permissions=("knowledge.search",),
            timeout=60.0,
            executor=self._search,
            tags=("knowledge", "read", "local"),
        )

    def _ingest_tool(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge.ingest",
            description=(
                "Index files or directories into the second brain so they can be "
                "searched later. Unchanged files are skipped."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "reindex": {"type": "boolean", "default": False},
                },
                "required": ["paths"],
            },
            output_schema={"type": "object"},
            risk=RiskLevel.LOW_RISK,
            permissions=("knowledge.ingest",),
            timeout=300.0,
            executor=self._ingest,
            tags=("knowledge", "write", "index"),
        )

    def _list_tool(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge.list",
            description="List everything indexed in the second brain.",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "array"},
            risk=RiskLevel.SAFE,
            permissions=("knowledge.list",),
            timeout=30.0,
            executor=self._list,
            tags=("knowledge", "read"),
        )

    def _forget_tool(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge.forget",
            description=(
                "Remove documents from the second brain, by source path or in full. "
                "This is destructive and cannot be undone."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "everything": {"type": "boolean", "default": False},
                },
            },
            output_schema={"type": "object"},
            risk=RiskLevel.HIGH_RISK,
            permissions=("knowledge.forget",),
            timeout=30.0,
            executor=self._forget,
            tags=("knowledge", "write", "destructive"),
        )

    # -- executors -----------------------------------------------------
    def _search(self, query: str, top_k: int | None = None) -> dict[str, Any]:
        limit = int(top_k) if top_k else self.top_k
        hits = search(self.store, query, embedder=self.embedder, top_k=limit)
        passages = [hit.to_dict() for hit in hits]
        return {
            "query": query,
            "count": len(passages),
            "hits": passages,
            "report": _render_hits(query, passages),
        }

    def _ingest(self, paths: Sequence[str], reindex: bool = False) -> dict[str, Any]:
        report = ingest_paths(
            self.store,
            list(paths or ()),
            embedder=self.embedder,
            chunk_size=self.chunk_size,
            overlap=self.overlap,
            max_bytes=self._max_bytes,
            reindex=bool(reindex),
        )
        return {"report": report.describe(), **report.to_dict()}

    def _list(self) -> list[dict]:
        return self.store.list_documents()

    def _forget(self, source: str | None = None, everything: bool = False) -> dict[str, Any]:
        removed = self.store.forget(source=source, everything=bool(everything))
        stats = self.store.stats()
        target = "everything" if everything else (source or "")
        return {
            "removed": removed,
            "target": target,
            "report": (
                f"Removed {removed} document(s) from the second brain "
                f"({stats['documents']} remain)."
            ),
        }


def _render_hits(query: str, passages: list[dict]) -> str:
    """A citable block for the model: quoted text, then where it came from.

    Citations are printed as ``[n] path:line`` and inlined after each passage,
    because a model asked to summarise numbered sources is far likelier to keep
    the numbers than to reproduce a path it was told to remember.
    """
    if not passages:
        return f"No indexed passages matched {query!r}."
    lines = [f"Retrieved {len(passages)} passage(s) for {query!r}:"]
    for index, passage in enumerate(passages, start=1):
        text = " ".join(str(passage.get("text", "")).split())
        if len(text) > 700:
            text = text[:700] + "..."
        lines.append(
            f"\n[{index}] {passage.get('citation', '?')}\n{text}"
        )
    return "\n".join(lines)
