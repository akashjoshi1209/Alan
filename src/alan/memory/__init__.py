"""Alan's persistent memory.

Two backends sit behind one Alan-owned :class:`MemoryProvider` interface:

* **cognee** (default) — the local sidecar started by
  ``scripts/cognee-serve.ps1``, exposing cognee's REST API in keyless local
  mode: GLiNER for entity extraction and fastembed for embeddings, with no
  cloud LLM in the loop.
* **mem0** (optional) — semantic long-term memory over a local vector store.
* **json** — a dependency-free local store used as the fallback so Alan keeps
  working when the sidecar is down.

:func:`build_memory_provider` picks one from configuration. The raw cognee HTTP
client stays exported for the existing CLI and assistant paths.
"""

from ..interfaces.memory import MemoryUnavailable
from .cognee_client import CogneeMemory
from .providers import (
    BACKENDS,
    CogneeProvider,
    JsonMemory,
    build_memory_provider,
    describe_backends,
)

__all__ = [
    "BACKENDS",
    "CogneeMemory",
    "CogneeProvider",
    "JsonMemory",
    "MemoryUnavailable",
    "build_memory_provider",
    "describe_backends",
]
