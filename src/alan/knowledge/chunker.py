"""Split a document into retrievable chunks.

Chunks are windows over the original text, snapped backwards to a paragraph or
line break so a sentence is not cut in half, with a configurable overlap so a
fact that straddles a boundary is still wholly present in one chunk. Every chunk
keeps its character span, which is what lets a result cite ``path:line`` -- a
citation the user can actually go and look at.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Smallest window worth searching. Below this, overlap dominates the content
#: and every chunk of a long document matches every query.
_MIN_CHUNK = 200


@dataclass
class TextChunk:
    """One window of a document, with the span that locates it in the source."""

    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1200,
    overlap: int = 200,
) -> list[TextChunk]:
    """Return the chunks of ``text`` in document order."""
    text = text or ""
    if not text.strip():
        return []

    chunk_size = max(_MIN_CHUNK, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size // 2))

    chunks: list[TextChunk] = []
    total = len(text)
    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        if end < total:
            # Prefer a paragraph break, accept a line break, within the last
            # 40% of the window. Snapping earlier would throw away most of the
            # window on text with frequent blank lines.
            floor = start + (chunk_size * 3 // 5)
            brk = text.rfind("\n\n", floor, end)
            if brk == -1:
                brk = text.rfind("\n", floor, end)
            if brk > start:
                end = brk + (2 if text[brk : brk + 2] == "\n\n" else 1)

        window = text[start:end]
        if window.strip():
            line_start = text.count("\n", 0, start) + 1
            line_end = text.count("\n", 0, end) + 1
            chunks.append(
                TextChunk(
                    text=window,
                    char_start=start,
                    char_end=end,
                    line_start=line_start,
                    line_end=line_end,
                )
            )

        if end >= total:
            break
        following = end - overlap
        # Guarantee forward progress even when overlap is large relative to the
        # snapped window, or the loop would spin forever on one chunk.
        start = following if following > start else start + 1

    return chunks
