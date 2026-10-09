"""Alan's second brain: a local, citable knowledge base.

The pieces are deliberately small and independent:

* :mod:`~alan.knowledge.store` -- one SQLite file; FTS5 plus optional vectors.
* :mod:`~alan.knowledge.embeddings` -- local vectors via the Ollama Alan
  already runs; optional, and its absence degrades cleanly.
* :mod:`~alan.knowledge.loaders` -- which files are read, and which are refused.
* :mod:`~alan.knowledge.chunker` -- line-spanning windows that keep citations.
* :mod:`~alan.knowledge.ingest` -- hash-diffed indexing of files and trees.
* :mod:`~alan.knowledge.retrieve` -- hybrid RRF search with citations.

Everything here runs on the local machine. Retrieval never calls the network;
only synthesis over passages that retrieval already chose may use a cloud model.
"""

from .chunker import TextChunk, chunk_text
from .embeddings import Embedder, NullEmbedder, OllamaEmbedder, build_embedder
from .ingest import IngestReport, ingest_paths
from .loaders import LoadedDoc, load
from .retrieve import Hit, search
from .store import KnowledgeStore

__all__ = [
    "Embedder",
    "Hit",
    "IngestReport",
    "KnowledgeStore",
    "LoadedDoc",
    "NullEmbedder",
    "OllamaEmbedder",
    "TextChunk",
    "build_embedder",
    "chunk_text",
    "ingest_paths",
    "load",
    "search",
]
