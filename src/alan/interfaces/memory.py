"""The memory interface Alan owns.

Every memory backend (the cognee sidecar, mem0, the local JSON store) implements
:class:`MemoryProvider`. Nothing outside ``alan.memory.providers`` may import a
backend library, so the backend stays replaceable (ADR-002).

The seven operations are the brief's contract. Note that *nothing* here decides
what is worth remembering: that judgement belongs to the Memory Agent and the
orchestrator. A provider stores and retrieves; it does not promote.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .types import MemoryItem, MemoryLevel


class MemoryUnavailable(RuntimeError):
    """Raised when a memory backend cannot serve a request.

    Callers treat this as "memory is off", never as a fatal error: a chat turn
    must still succeed when the store is down.
    """


class MemoryProvider(ABC):
    """Persistent, retrievable memory for Alan."""

    name: str = "memory"

    # -- lifecycle -----------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """Return ``(is_available, reason)`` without raising."""
        return True, "ready"

    def close(self) -> None:
        """Release any client or subprocess held by the provider."""
        return None

    # -- write ---------------------------------------------------------
    @abstractmethod
    def remember(self, item: MemoryItem) -> MemoryItem:
        """Store ``item`` and return it as persisted (with its final id)."""

    @abstractmethod
    def update(self, item_id: str, item: MemoryItem) -> bool:
        """Replace an existing item. False when ``item_id`` is unknown."""

    @abstractmethod
    def forget(self, item_id: str | None = None, *, everything: bool = False) -> int:
        """Delete one item, or the whole store when ``everything``.

        Returns the number of items removed.
        """

    # -- read ----------------------------------------------------------
    @abstractmethod
    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        levels: Sequence[MemoryLevel] | None = None,
        min_confidence: float = 0.0,
        session_id: str | None = None,
    ) -> list[MemoryItem]:
        """Semantic search, best match first.

        ``session_id`` is a *ranking hint*, never a filter. Items recorded
        during that session are boosted, so "what tool did we just run?" is
        answered from this conversation; items from other sessions still
        compete on their own merits, because a durable fact about the user is
        just as true now as it was last week. Hard-filtering would make
        persistent memory forget the moment a new chat opened.
        """

    @abstractmethod
    def retrieve(self, item_id: str) -> MemoryItem | None:
        """Fetch one item by id, or ``None``."""

    # -- derived -------------------------------------------------------
    @abstractmethod
    def summarize(self, query: str = "", *, top_k: int = 20) -> str:
        """Condense matching items into a short briefing for a prompt."""

    @abstractmethod
    def promote_to_long_term(self, item: MemoryItem) -> MemoryItem:
        """Move an item to durable scope (L4 user / L5 archive).

        Promotion is an explicit decision by the orchestrator, never a side
        effect of writing.
        """

    # -- context helper ------------------------------------------------
    def context_block(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_chars: int = 2000,
        hits: Sequence[MemoryItem] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Search and render as a prompt block. Empty string when no hits.

        Returning ``""`` rather than raising is deliberate: the caller can always
        inject the block, and a dead backend degrades to "no memory" instead of
        breaking the turn.

        ``hits`` lets a caller that has *already* searched pass those results
        in. Without it this method searches again, which doubled the cost of
        every turn against a graph store -- two round trips per question.

        Whatever the backend returns is put through the relevance gate before
        being rendered. A semantic backend is good at choosing candidates and
        was measured returning the same items for unrelated questions, so the
        gate -- not the backend's ordering -- decides what is asserted as
        memory. See :mod:`alan.memory.relevance`.
        """
        from ..memory.relevance import gate

        if hits is None:
            try:
                hits = self.search(query, top_k=top_k, session_id=session_id)
            except TypeError:
                # A provider that predates session-aware search. Losing the
                # ranking hint must never lose the recall itself.
                try:
                    hits = self.search(query, top_k=top_k)
                except Exception:
                    return ""
            except MemoryUnavailable:
                return ""
            except Exception:
                return ""
        return render_block(gate(query, hits), max_chars=max_chars)


def render_block(items: Sequence[MemoryItem], *, max_chars: int = 2000) -> str:
    """Render recalled items as a compact, labelled prompt block."""
    if not items:
        return ""
    lines: list[str] = []
    used = 0
    for item in items:
        text = item.content.strip()
        if not text:
            continue
        line = f"- ({item.level}) {text}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return "Relevant memory:\n" + "\n".join(lines)
