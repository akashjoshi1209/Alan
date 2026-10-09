"""Hybrid retrieval over the knowledge store.

Two rankers run and neither is trusted alone: full-text search is precise about
words but blind to meaning, and embeddings are the reverse. Their results are
fused with reciprocal-rank fusion (RRF) and every hit carries the citation that
makes it checkable.

An RRF *relevance gate* stops a weak semantic match from being reported as an
answer. If nothing matched lexically, a vector hit still has to clear a cosine
floor -- otherwise "what did I decide about pricing" returns whichever paragraph
happens to be least unlike the question, which reads as a confident non-answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .embeddings import Embedder
from .store import KnowledgeStore

#: RRF dampening. 60 is the value from the original RRF paper and is
#: unremarkable here precisely because it is not tuned to this corpus.
_RRF_K = 60
#: A vector-only neighbour below this cosine similarity is not reported.
_SEMANTIC_FLOOR = 0.30


@dataclass
class Hit:
    """One retrieved passage, with everything needed to cite it."""

    chunk_id: int
    doc_id: int
    source: str
    kind: str
    title: str
    text: str
    line_start: int
    line_end: int
    score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    cosine: float | None = None

    def citation(self) -> str:
        return f"{self.source}:{self.line_start}"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "citation": self.citation(),
            "score": round(self.score, 6),
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "cosine": round(self.cosine, 4) if self.cosine is not None else None,
        }


def search(
    store: KnowledgeStore,
    query: str,
    *,
    embedder: Embedder | None = None,
    top_k: int = 8,
    semantic: bool = True,
) -> list[Hit]:
    """Return the best passages for ``query``, best first, with citations."""
    query = (query or "").strip()
    if not query:
        return []

    pool = max(top_k * 4, 16)
    fused: dict[int, dict] = {}

    for rank, (chunk_id, _doc_id, _rank) in enumerate(store.search_lexical(query, limit=pool)):
        entry = fused.setdefault(chunk_id, {"rrf": 0.0})
        entry["rrf"] += 1.0 / (_RRF_K + rank + 1)
        entry["lex"] = rank

    if semantic and embedder is not None:
        for rank, (chunk_id, cosine) in enumerate(_semantic_hits(store, embedder, query, pool)):
            if chunk_id in fused or cosine >= _SEMANTIC_FLOOR:
                entry = fused.setdefault(chunk_id, {"rrf": 0.0})
                entry["rrf"] += 1.0 / (_RRF_K + rank + 1)
                entry["sem"] = rank
                entry["cos"] = cosine

    if not fused:
        return []

    records = store.chunks_by_ids(list(fused.keys()))
    hits: list[Hit] = []
    for chunk_id, meta in fused.items():
        record = records.get(chunk_id)
        if record is None:
            continue
        hits.append(
            Hit(
                chunk_id=chunk_id,
                doc_id=int(record["doc_id"]),
                source=str(record["source"]),
                kind=str(record["kind"]),
                title=str(record["title"]),
                text=str(record["text"]),
                line_start=int(record["line_start"]),
                line_end=int(record["line_end"]),
                score=float(meta["rrf"]),
                lexical_rank=meta.get("lex"),
                semantic_rank=meta.get("sem"),
                cosine=meta.get("cos"),
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.source, hit.line_start))
    return hits[: max(1, int(top_k))]


def _semantic_hits(
    store: KnowledgeStore, embedder: Embedder, query: str, pool: int
) -> list[tuple[int, float]]:
    """``(chunk_id, cosine)`` best-first, or ``[]`` when vectors are unavailable."""
    try:
        vectors = embedder.embed([query])
    except Exception:  # noqa: BLE001 - a dead embedder means lexical-only, not a failure
        return []
    if not vectors or not vectors[0]:
        return []

    ids, _doc_ids, matrix = store.load_vectors()
    if matrix is None or not ids or matrix.size == 0:
        return []

    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return []

    query_vector = np.asarray(vectors[0], dtype=np.float32)
    if query_vector.ndim != 1 or query_vector.shape[0] != matrix.shape[1]:
        return []

    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    normalised = matrix / norms
    query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-9)
    similarities = normalised @ query_norm
    order = np.argsort(-similarities)[:pool]
    return [(ids[int(index)], float(similarities[int(index)])) for index in order]
