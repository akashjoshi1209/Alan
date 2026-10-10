"""The relevance gate: what may be asserted as memory.

A memory backend decides *which candidates to consider*. This module decides
*which of those may reach the prompt as something Alan knows*. Those are
different jobs, and conflating them is how a graph store ends up answering
"what is the capital of France?" with a note about a browser sidecar port.

The rule, which is the whole point of the module: **zero lexical overlap scores
zero.** An item that shares no meaningful term with the question is not a
weak match, it is not a match, and rendering it under "Relevant memory" is worse
than rendering nothing -- it looks like knowledge Alan has when it does not.
That is the failure mode this gate exists to prevent.

Why lexical overlap, when cognee ranks semantically
---------------------------------------------------
Semantic recall is kept as the *candidate generator*: cognee decides what is
worth looking at, and it is good at finding paraphrases. The gate then applies
to those candidates. Measured on this machine, cognee's top-k for a query about
France and a query about the user's own name were the *same four items*, so
semantic ranking alone was not discriminating at all and the gate was the only
thing standing between the dataset and the prompt.

Why bonuses cannot create a match
---------------------------------
Level, importance and confidence bonuses are added *after* a real overlap is
required, and never to a zero score. If they could raise a zero, the cheapest
high-priority note in the store would outrank an exact match and get asserted.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from ..interfaces.memory import MemoryItem

#: Alphanumeric runs only, so punctuation and casing cannot hide a match.
_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how i in is it me my of 
on or
that the this to was were what when where which who will with you 
your""".split()
)


def terms(text: str) -> set[str]:
    """Meaningful lowercased terms, stopwords and single characters removed."""
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS}


def score(query_terms: set[str], query: str, item: MemoryItem) -> float:
    """Relevance for one item. Zero means "not a match at all"."""
    content = item.content.lower()
    item_terms = {t for t in _TOKEN.findall(content) if t not in _STOPWORDS}
    if not item_terms:
        return 0.0
    overlap = len(query_terms & item_terms) / max(1, len(query_terms))
    phrase = bool(query and query.strip().lower() in content)
    if overlap == 0.0 and not phrase:
        return 0.0
    value = overlap
    if phrase:
        value += 0.5
    # Tighter scopes are more relevant when the content is otherwise equal.
    value += (5 - int(item.level)) * 0.02
    value += item.importance * 0.1
    value += item.confidence * 0.05
    return value


def gate(
    query: str, items: Iterable[MemoryItem]
) -> list[MemoryItem]:
    """Filter to items that actually match ``query``, best first.

    Each survivor has its ``score`` set, so callers and tests can see *why* an
    item was kept rather than having to trust the backend's ordering.

    A query with no meaningful terms (``"what is it"``, or empty) cannot be
    judged, and is returned untouched: refusing to filter is honest, whereas
    silently returning nothing would make a working memory look broken.
    """
    query_terms = terms(query)
    candidates = list(items)
    if not query_terms:
        return candidates
    scored: list[tuple[float, MemoryItem]] = []
    for item in candidates:
        value = score(query_terms, query, item)
        if value <= 0.0:
            continue
        item.score = value
        scored.append((value, item))
    # Ties keep the backend's own ordering, which is the only signal left for
    # two items that overlap equally.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _value, item in scored]


def gate_all(
    query: str, per_query: Sequence[Sequence[MemoryItem]]
) -> list[MemoryItem]:
    """Gate several result lists at once, preserving order between them."""
    kept: list[MemoryItem] = []
    for group in per_query:
        kept.extend(gate(query, group))
    return kept


#: How much a same-session item is preferred. Enough that a tool this
#: conversation ran outranks an older near-duplicate of the same tool, and
#: small enough that a durable fact from last week still wins on lexical fit.
SESSION_BOOST = 0.35


def session_bonus(session_id: str | None, item: MemoryItem) -> float:
    """The score added to an item recorded during ``session_id``.

    Shared so every backend agrees about what "from this session" is worth.
    A provider that scores numerically (the SQLite store) adds this to its
    total; one that relies on :func:`gate` for ordering calls
    :func:`rank_sessions` instead.
    """
    if session_id and item.session_id == session_id:
        return SESSION_BOOST
    return 0.0


def rank_sessions(
    session_id: str | None, items: Iterable[MemoryItem]
) -> list[MemoryItem]:
    """Stable-partition: this session's items first, everything else after.

    For backends whose ordering comes entirely from :func:`gate`, where the
    per-item scores are not available to blend against. Stable, so items
    within each group keep the relevance order they already had.
    """
    items = list(items)
    if not session_id:
        return items
    own = [i for i in items if i.session_id == session_id]
    if not own:
        return items
    other = [i for i in items if i.session_id != session_id]
    return own + other


__all__ = [
    "SESSION_BOOST",
    "gate",
    "gate_all",
    "rank_sessions",
    "score",
    "session_bonus",
    "terms",
]
