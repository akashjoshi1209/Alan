"""Hybrid retrieval over the knowledge store.

Two rankers run and neither is trusted alone: full-text search is precise about
words but blind to meaning, and embeddings are the reverse. Their results are
fused with reciprocal-rank fusion (RRF) and every hit carries the citation that
makes it checkable.

An RRF *relevance gate* stops a weak semantic match from being reported as an
answer. If nothing matched lexically, a vector hit still has to prove it is a
match rather than merely the least-unlike paragraph in the store -- otherwise
"zzz nonexistent qqq" returns every passage the store holds, which reads as a
confident non-answer. See ``_is_relevant``: the bar depends on whether any
word matched, because that is what decides how much evidence the passage is
being asked to carry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .embeddings import Embedder
from .store import KnowledgeStore

#: RRF dampening. 60 is the value from the original RRF paper and is
#: unremarkable here precisely because it is not tuned to this corpus.
_RRF_K = 60

#: The two bars a vector-only hit must clear. Which one applies is decided by
#: ``_is_relevant`` from a structural fact -- did *any* word match? -- not by a
#: statistic of the corpus.
#:
#: Below this, nothing is a match. It is deliberately low -- nomic-embed-text
#: scores *unrelated* English at 0.38-0.54 -- so on its own it admits noise,
#: and it is not what does the rejecting. It is the bar for a hit that is
#: corroborating colour on top of a word match that already happened.
_SEMANTIC_FLOOR = 0.30
#: The bar for a hit that is the *only* evidence for the query: lexical search
#: found no word, so this passage alone will be read as the answer.
_SEMANTIC_HIGH = 0.55


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
        # The bar for a vector-only hit depends on whether any word matched,
        # which is known only after the lexical pass above.
        corroborating = bool(fused)
        for rank, (chunk_id, cosine) in enumerate(_semantic_hits(store, embedder, query, pool)):
            # A chunk lexical search already found vouches for itself and only
            # takes its semantic RRF contribution here.
            if chunk_id in fused or _is_relevant(cosine, corroborating):
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

    hits.sort(key=lambda hit: (-hit.score, -_cosine_key(hit.cosine), hit.source, hit.line_start))
    return hits[: max(1, int(top_k))]


def _cosine_key(cosine: float | None) -> float:
    """Ordering weight for a hit's cosine, for breaking RRF ties.

    RRF is magnitude-blind by design: it keeps a ranker's *rank* and throws
    away its scores, which is what makes it robust across rankers. The cost
    shows up when two hits fuse to an identical score. Lexical search
    returned bm25 ~0 for a match on a stopword like "the" -- evidence the
    match is worthless -- but the rank is still a rank, so RRF weights it
    the same as a content-word match and the tie lands on the filename
    instead. Measured on the two-document store: "how much does the paid
    plan cost" fused both passages to 0.0325 and alphabetical order put
    launch.md (cosine 0.38, matched only on "the") above pricing.md
    (cosine 0.595, the actual answer).

    The other ranker already computed the evidence the tie needs, so use it:
    of two equally-ranked hits, prefer the one the embedder also liked. A hit
    with no cosine at all -- no embedder, or lexical-only -- sorts last,
    since nothing vouches for it either way.
    """
    return -1.0 if cosine is None else cosine


def _is_relevant(cosine: float, corroborating: bool) -> bool:
    """Whether a *vector-only* hit is a match, or just the least-unlike paragraph.

    The question is not "how high is this cosine" but "how much evidence does
    this passage have to carry". A hit that already shares a word with the
    query is corroborating colour on top of a lexical match that happened for
    reasons of its own, so it only has to clear the floor. A hit with *no*
    shared word is the sole evidence for the query, and will be read as the
    answer, so it has to clear the much higher bar.

    ``corroborating`` is whether any word of the query matched any passage at
    all. It is a fact about the query, not a statistic of the corpus, which is
    what makes this stable as the store grows.

    The bars themselves, measured on nomic-embed-text across corpora of 100 to
    1000 passages drawn from mixed vocabulary:

        unrelated   cosine up to 0.541
        related     cosine from 0.567

    Those bands *overlap*, so no threshold separates them reliably: the gap is
    0.026 wide and sits inside measurement noise. A cosine test alone is
    therefore not the fix -- it was tried, and at 500 documents unrelated text
    reached 0.541, above the 0.55 bar's margin of safety. What the bar buys is
    not accuracy but *cost asymmetry*. Retrieval feeds an LLM that answers in
    prose, so a false positive becomes a confident non-answer the user cannot
    see through, while a false negative is an empty result that invites a
    rephrase. ``_SEMANTIC_HIGH`` is set where it errs toward the cheap failure.

    Measured again for the record: unrelated text scored 0.379-0.466 on 8
    documents and up to 0.541 at 500-1000; genuine matches started at 0.567.
    """
    return cosine >= (_SEMANTIC_FLOOR if corroborating else _SEMANTIC_HIGH)


def _semantic_hits(
    store: KnowledgeStore, embedder: Embedder, query: str, pool: int
) -> list[tuple[int, float]]:
    """``(chunk_id, cosine)`` best-first, or ``[]`` when vectors are unavailable.

    No relevance gate lives here: a candidate's admission depends on whether
    lexical search found anything, which is the caller's to know.
    """
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
