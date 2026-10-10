"""Memory backends, all behind one :class:`~alan.interfaces.memory.MemoryProvider`.

Selection follows the configured ``[memory] backend`` and always degrades
instead of failing:

    auto   -> the local SQLite store, else the dependency-free JSON store
    local  -> SQLite on disk, with optional local embeddings
    cognee -> the cognee sidecar, else the fallback if one is configured
    mem0   -> mem0, else the fallback
    json   -> the dependency-free local store
    none   -> no memory

The fallback matters: Alan must stay useful when a backend is broken, which is
a normal occurrence, not an exception.

Why ``local`` is the default
----------------------------
Cognee was measured costing **1.47 s warm and 24 s cold per chat turn** -- an
HTTP round trip sitting in front of the first token -- while returning nothing,
because its dataset had never been written to (``updatedAt: null``) and its
semantic ranking was non-discriminating enough that a lexical gate had to be
layered on top. It also cost an 817 MB sidecar, a 1-2 minute cold boot, and
enough RAM to push this 4 GB machine into pagefile thrash.

``LocalMemory`` answers the same questions in single-digit milliseconds from
one SQLite file, with no process to boot and nothing to probe. cognee stays
available behind ``backend = "cognee"`` for anyone who wants the graph; it is
simply no longer paid for on every message.
"""

from __future__ import annotations

import os
from pathlib import Path

from ...config import MemorySettings
from ...interfaces.memory import MemoryProvider, MemoryUnavailable
from .cognee_provider import CogneeProvider
from .json_store import JsonMemory

# Safe to import at module load: mem0_provider only imports mem0 lazily, inside
# _build(), so the heavy dependency stays unloaded until it is actually selected.
from .local_store import LocalMemory
from .mem0_provider import Mem0Provider

__all__ = [
    "BACKENDS",
    "CogneeProvider",
    "JsonMemory",
    "LocalMemory",
    "MemoryProvider",
    "MemoryUnavailable",
    "Mem0Provider",
    "build_memory_provider",
    "describe_backends",
]


def _make_mem0(settings: MemorySettings, model: str = "") -> MemoryProvider:
    # Imported lazily so the heavy optional dependency is never required.
    from .mem0_provider import Mem0Provider

    return Mem0Provider(settings, model=model)


#: The default. "auto" resolves to ``local`` then ``json``, so a fresh install
#: has a working, persistent, fast memory with nothing to install.
DEFAULT_BACKEND = "local"

BACKENDS = ("auto", "local", "cognee", "mem0", "json", "none")


def build_memory_provider(
    settings: MemorySettings,
    *,
    model: str = "",
    ollama_base_url: str = "",
    background_writes: bool = False,
    index_root=None,
) -> tuple[MemoryProvider | None, str]:
    """Return ``(provider, note)``. ``note`` explains any fallback, for the CLI.

    ``provider`` is ``None`` only when memory is disabled or the configured
    backend and its fallback are both unavailable.
    """
    if not settings.enabled:
        return None, "memory disabled in settings"
    # The documented kill switch. It used to stop a sidecar being started; now
    # that memory is a file, it disables the backend instead -- same meaning to
    # the user ("start Alan without memory"), one less process either way.
    if os.environ.get("ALAN_NO_MEMORY") == "1":
        return None, "memory disabled by ALAN_NO_MEMORY"

    backend = (settings.backend or "auto").lower()
    fallback = (settings.fallback or "").lower()
    tried: list[str] = []

    def _local() -> MemoryProvider:
        return LocalMemory(
            _local_root(settings, index_root),
            embedder=_memory_embedder(settings, ollama_base_url),
            vector_budget_ms=settings.local_vector_budget_ms,
        )

    def _cognee() -> MemoryProvider:
        return CogneeProvider(
            settings,
            index_root=index_root,
            background_writes=background_writes,
        )

    def _json() -> MemoryProvider:
        return JsonMemory(index_root)

    order: list[tuple[str, object]] = []
    if backend == "auto":
        # local first because it is fast, persistent and needs no process;
        # json is a safety net, not a competitor.
        order = [(DEFAULT_BACKEND, _local), ("json", _json)]
    elif backend == "local":
        order = [("local", _local)]
    elif backend == "cognee":
        order = [("cognee", _cognee)]
    elif backend == "mem0":
        order = [("mem0", lambda: _make_mem0(settings, model))]
    elif backend == "json":
        order = [("json", _json)]
    elif backend == "none":
        return None, "backend set to none"
    else:
        return None, f"unknown memory backend {backend!r} (expected one of {BACKENDS})"

    primary = order[0][0]
    primary_reason = ""
    for name, factory in order:
        tried.append(name)
        try:
            provider = factory()  # type: ignore[operator]
            ok, reason = provider.available()
        except MemoryUnavailable as exc:
            ok, reason = False, str(exc)
        except Exception as exc:  # a broken backend must not stop startup
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        if ok:
            if name == primary:
                return provider, reason
            return provider, f"{primary} unavailable ({primary_reason}); using {name}"
        if name == primary:
            primary_reason = reason

    # Primary failed: try the configured fallback exactly once.
    if fallback and fallback != "none" and fallback != primary:
        builders = {
            "json": _json,
            "local": _local,
            "cognee": _cognee,
            "mem0": lambda: _make_mem0(settings, model),
        }
        build = builders.get(fallback)
        if build is not None:
            tried.append(fallback)
            try:
                provider = build()  # type: ignore[operator]
                ok, reason = provider.available()
                if ok:
                    # Keep the primary's reason. Dropping it leaves the reader
                    # knowing memory fell back but not why, and "cognee was
                    # never running" is exactly the fact that explains semantic
                    # recall quietly behaving like keyword lookup.
                    return provider, (
                        f"using {fallback} fallback ({reason}); "
                        f"{primary} was unavailable: {primary_reason}"
                    )
                last = reason
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            return None, f"no usable memory backend; tried {', '.join(tried)} ({last})"

    return None, f"no usable memory backend; tried {', '.join(tried)}"


def _local_root(settings: MemorySettings, index_root=None):
    """Where the SQLite file lives, honouring an injected test root first."""
    if index_root is not None:
        return Path(index_root)
    if settings.local_db:
        path = Path(settings.local_db)
        return path.parent if path.suffix else path
    return None  # LocalMemory falls back to DATA_DIR/memory


def _memory_embedder(settings: MemorySettings, ollama_base_url: str = ""):
    """The embedder for memory, or ``None``.

    Shares the knowledge store's model by default so the two never ask Ollama
    to load two different embedding models on a machine with 4 GB of RAM.
    """
    if not settings.local_embed:
        return None
    from ...knowledge.embeddings import build_embedder

    model = settings.local_embed_model
    return build_embedder(model, ollama_base_url or "http://127.0.0.1:11435")


def describe_backends() -> list[dict[str, str]]:
    """One row per backend for ``alan memory backends``."""
    rows: list[dict[str, str]] = []
    for name in ("local", "cognee", "mem0", "json"):
        try:
            provider = (
                _make_mem0(MemorySettings())
                if name == "mem0"
                else CogneeProvider(MemorySettings())
                if name == "cognee"
                else JsonMemory()
                if name == "json"
                else LocalMemory()
            )
            ok, reason = provider.available()
            if name == "local":
                provider.close()
        except Exception as exc:
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "backend": name,
                "status": "ready" if ok else "unavailable",
                "detail": reason,
                "note": "default" if name == DEFAULT_BACKEND else "fallback"
                if name == "json"
                else "optional",
            }
        )
    return rows
