"""Memory backends, all behind one :class:`~alan.interfaces.memory.MemoryProvider`.

Selection follows the configured ``[memory] backend`` and always degrades
instead of failing:

    auto   -> cognee if reachable, else the local JSON store
    cognee -> cognee, else the fallback if one is configured
    mem0   -> mem0, else the fallback
    json   -> the dependency-free local store
    none   -> no memory

The fallback matters: Alan must stay useful when the sidecar is down, which on a
portable USB stick is a normal occurrence, not an exception.
"""

from __future__ import annotations

from ...config import MemorySettings
from ...interfaces.memory import MemoryProvider, MemoryUnavailable
from .cognee_provider import CogneeProvider
from .json_store import JsonMemory

# Safe to import at module load: mem0_provider only imports mem0 lazily, inside
# _build(), so the heavy dependency stays unloaded until it is actually selected.
from .mem0_provider import Mem0Provider

__all__ = [
    "BACKENDS",
    "CogneeProvider",
    "JsonMemory",
    "Mem0Provider",
    "MemoryProvider",
    "MemoryUnavailable",
    "build_memory_provider",
    "describe_backends",
]


def _make_mem0(settings: MemorySettings, model: str = "") -> MemoryProvider:
    # Imported lazily so the heavy optional dependency is never required.
    from .mem0_provider import Mem0Provider

    return Mem0Provider(settings, model=model)


BACKENDS = ("auto", "cognee", "mem0", "json", "none")


def build_memory_provider(
    settings: MemorySettings,
    *,
    model: str = "",
    background_writes: bool = False,
    index_root=None,
) -> tuple[MemoryProvider | None, str]:
    """Return ``(provider, note)``. ``note`` explains any fallback, for the CLI.

    ``provider`` is ``None`` only when memory is disabled or the configured
    backend and its fallback are both unavailable.
    """
    if not settings.enabled:
        return None, "memory disabled in settings"

    backend = (settings.backend or "auto").lower()
    fallback = (settings.fallback or "").lower()
    tried: list[str] = []

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
        # cognee first because it is the proven, richest backend; json is a
        # safety net, not a competitor.
        order = [("cognee", _cognee), ("json", _json)]
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


def describe_backends() -> list[dict[str, str]]:
    """One row per backend for ``alan memory backends``."""
    rows: list[dict[str, str]] = []
    for name in ("cognee", "mem0", "json"):
        try:
            provider = (
                _make_mem0(MemorySettings())
                if name == "mem0"
                else CogneeProvider(MemorySettings())
                if name == "cognee"
                else JsonMemory()
            )
            ok, reason = provider.available()
        except Exception as exc:
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "backend": name,
                "status": "ready" if ok else "unavailable",
                "detail": reason,
                "note": "default" if name == "cognee" else "fallback"
                if name == "json"
                else "optional",
            }
        )
    return rows