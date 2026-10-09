"""Turn files on disk into store rows.

Ingestion is a diff, not a re-import: each file is hashed, and a file whose hash
matches the stored one is skipped. That keeps a re-run cheap and, more
importantly, keeps embeddings stable. Re-indexing a corpus nightly is what makes
a local RAG tool feel alive; re-importing the whole corpus every time is what
makes it feel broken the moment the embedding server is slow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .chunker import chunk_text
from .embeddings import Embedder
from .loaders import load, walk
from .store import KnowledgeStore


@dataclass
class IngestReport:
    """What one ingest run did, in numbers a human can read in one line."""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    embedded: int = 0
    errors: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)

    @property
    def files(self) -> int:
        return self.added + self.updated

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "files": self.files,
            "roots": list(self.roots),
            "errors": list(self.errors),
        }

    def describe(self) -> str:
        parts = [
            f"{self.files} file(s) indexed",
            f"{self.added} new",
            f"{self.updated} updated",
            f"{self.skipped} unchanged",
            f"{self.chunks} chunk(s)",
            f"{self.embedded} embedded",
        ]
        if self.failed:
            parts.append(f"{self.failed} skipped (unreadable)")
        return ", ".join(parts)


def ingest_paths(
    store: KnowledgeStore,
    paths: Sequence[str],
    *,
    embedder: Embedder | None = None,
    chunk_size: int = 1200,
    overlap: int = 200,
    max_bytes: int = 2_000_000,
    reindex: bool = False,
) -> IngestReport:
    """Index every readable file under ``paths`` into ``store``."""
    report = IngestReport()
    for raw in paths or []:
        if not raw:
            continue
        target = Path(str(raw)).expanduser()
        report.roots.append(str(target))
        if not target.exists():
            report.failed += 1
            report.errors.append(f"{target}: path does not exist")
            continue
        for file_path in walk(target):
            _ingest_file(
                store, file_path, embedder, report,
                chunk_size=chunk_size, overlap=overlap,
                max_bytes=max_bytes, reindex=reindex,
            )
    return report


def _ingest_file(
    store: KnowledgeStore,
    path: Path,
    embedder: Embedder | None,
    report: IngestReport,
    *,
    chunk_size: int,
    overlap: int,
    max_bytes: int,
    reindex: bool,
) -> None:
    try:
        doc = load(path, max_bytes=max_bytes)
    except (ValueError, OSError) as exc:
        report.failed += 1
        report.errors.append(f"{path}: {exc}")
        return

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        stat = path.stat()
    except OSError as exc:
        report.failed += 1
        report.errors.append(f"{path}: {exc}")
        return

    existing = store.document_for(str(path))
    if existing is not None and existing.content_hash == digest and not reindex:
        report.skipped += 1
        return

    doc_id, was_update = store.upsert_document(
        doc, content_hash=digest, size=len(raw), mtime=stat.st_mtime
    )
    chunks = chunk_text(doc.text, chunk_size=chunk_size, overlap=overlap)

    vectors = None
    if embedder is not None and chunks:
        try:
            vectors = embedder.embed([chunk.text for chunk in chunks])
        except Exception as exc:  # noqa: BLE001 - store lexically, never lose the file
            report.errors.append(f"{path}: embeddings failed ({exc}); stored lexically")
            vectors = None

    count = store.add_chunks(doc_id, chunks, vectors)
    report.chunks += count
    if vectors:
        report.embedded += sum(1 for vector in vectors if vector)

    if was_update:
        report.updated += 1
    else:
        report.added += 1
