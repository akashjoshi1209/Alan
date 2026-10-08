"""The model interface and routing metadata.

Alan's existing ``llm.Provider`` already does the real work of talking to
OpenAI, Anthropic and Ollama. Rather than replace it, :class:`ModelProvider`
is the *routing* view of the same object: it adds the capability tags and
health signal the model router needs, and :func:`as_model_provider` adapts any
existing provider without touching it.

That keeps ``build_provider()``, the CLI and the session format working while
the router gains what it needs (brief sections 3 and 9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Iterator, Sequence

from .types import redact


class ModelCategory(str, Enum):
    """What a model is good at. The router picks by category first."""

    FAST = "fast"                # cheap, low latency
    REASONING = "reasoning"      # multi-step logic
    CODING = "coding"            # code editing / debugging
    VISION = "vision"            # images
    EMBEDDING = "embedding"      # vectorisation only
    GENERAL = "general"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class ModelProvider(ABC):
    """A chat backend plus the metadata the router needs to choose it."""

    name: str = "model"
    model: str = ""
    categories: frozenset[ModelCategory] = frozenset({ModelCategory.GENERAL})

    # -- capabilities --------------------------------------------------
    @property
    def local(self) -> bool:
        """True when the model runs on this machine (privacy-preserving)."""
        return False

    @abstractmethod
    def chat(self, messages: Sequence[dict], system: str | None = None) -> str:
        """Full reply for the given conversation."""

    def stream(
        self, messages: Sequence[dict], system: str | None = None
    ) -> Iterator[str]:
        """Incremental reply. Default falls back to :meth:`chat`."""
        yield self.chat(messages, system)

    # -- routing signals ----------------------------------------------
    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def supports(self, category: ModelCategory) -> bool:
        return category in self.categories

    def priority(self) -> int:
        """Lower wins when several models are eligible (0 = preferred)."""
        return 100

    def describe(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name

    def close(self) -> None:
        return None


class RoutedModel(ModelProvider):
    """Wraps a provider to redact secrets from anything that leaves the machine.

    Only applied to remote providers; a local model never needs it, but keeping
    the check in one place means a new remote backend cannot forget it.
    """

    def __init__(self, inner: ModelProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.categories = inner.categories

    @property
    def local(self) -> bool:
        return self._inner.local

    @property
    def inner(self) -> ModelProvider:
        return self._inner

    def chat(self, messages: Sequence[dict], system: str | None = None) -> str:
        cleaned = _redact_messages(messages, system)
        return self._inner.chat(cleaned[0], cleaned[1])

    def stream(
        self, messages: Sequence[dict], system: str | None = None
    ) -> Iterator[str]:
        cleaned = _redact_messages(messages, system)
        yield from self._inner.stream(cleaned[0], cleaned[1])

    def available(self) -> tuple[bool, str]:
        return self._inner.available()

    def priority(self) -> int:
        return self._inner.priority()

    def close(self) -> None:
        self._inner.close()


class UnavailableModel(ModelProvider):
    """Stands in when an agent needs a model and none is available.

    Used so an agent that only inspects state can still run offline. Calling it
    fails loudly with a useful message rather than pretending to be a model.
    """

    def __init__(self, reason: str = "no model backend is available") -> None:
        self.name = "unavailable"
        self.model = ""
        self.reason = reason

    @property
    def local(self) -> bool:
        return False

    def available(self) -> tuple[bool, str]:
        return False, self.reason

    def chat(self, messages: Sequence[dict], system: str | None = None) -> str:
        raise RuntimeError(
            f"{self.reason}. Start Ollama (scripts/run.ps1) or add a key in "
            "config/.env, then retry."
        )


def as_model_provider(provider: Any) -> ModelProvider:
    """Adapt an existing ``llm.Provider`` to :class:`ModelProvider`.

    This is the seam that lets the model router see Alan's working providers
    without rewriting them: ``build_provider()``, the CLI and saved sessions all
    keep working unchanged.
    """
    if isinstance(provider, ModelProvider):
        return provider

    inner = provider
    # An operator-stated category set wins over guessing from the model name.
    # Name-guessing alone cannot tell a coding specialist from a chat model:
    # "gpt-4o-mini" and "qwen2.5:1.5b" both infer as {fast, general}, so the
    # guess returned the same answer for a $0.15/1M model and a 1.5B one and
    # the category tier never separated anything.
    configured = getattr(inner, "categories", None)
    if isinstance(configured, frozenset) and configured:
        categories = configured
    else:
        categories = _infer_categories(getattr(inner, "model", ""))

    # Priority is whatever the provider reports, defaulting to the neutral 100
    # for a backend that has no opinion. Without this the adapter fell through
    # to ModelProvider.priority() and every model scored identically, which is
    # why a key in config/.env changed nothing: the tie-break existed but read
    # the same value for every provider.
    raw_priority = getattr(inner, "priority", None)
    try:
        priority_value = int(raw_priority() if callable(raw_priority) else raw_priority)
    except (TypeError, ValueError):
        priority_value = 100

    local = bool(getattr(inner, "name", "") == "ollama")

    class _Adapted(ModelProvider):
        _priority: int = 100

        def chat(self, messages: Sequence[dict], system: str | None = None) -> str:
            return inner.chat(messages, system)

        def stream(
            self, messages: Sequence[dict], system: str | None = None
        ) -> Iterator[str]:
            return inner.stream(messages, system)

        def available(self) -> tuple[bool, str]:
            return inner.available()

        def priority(self) -> int:
            return self._priority

        def close(self) -> None:
            inner.close()

    adapted = _Adapted()
    adapted.name = getattr(inner, "name", "provider")
    adapted.model = getattr(inner, "model", "")
    adapted.categories = categories
    adapted._priority = priority_value
    # Preserve locality for the privacy-sensitive routing rule.
    type(adapted).local = property(lambda self: local)  # type: ignore[assignment]
    return adapted


def _infer_categories(model: str) -> frozenset[ModelCategory]:
    """Cheap capability guess from a model id, until a backend reports better."""
    name = (model or "").lower()
    if not name:
        return frozenset({ModelCategory.GENERAL})
    if any(token in name for token in ("embed", "bge-", "gte-")):
        return frozenset({ModelCategory.EMBEDDING})
    cats = {ModelCategory.GENERAL, ModelCategory.FAST}
    if any(token in name for token in ("coder", "code", "starcoder", "deepseek-coder")):
        cats.add(ModelCategory.CODING)
    if any(token in name for token in ("reason", "r1", "qwq", "think", "o1", "o3")):
        cats.add(ModelCategory.REASONING)
    if any(token in name for token in ("vision", "vl", "-vl", "llava", "multimodal")):
        cats.add(ModelCategory.VISION)
    return frozenset(cats)


def _redact_messages(
    messages: Sequence[dict], system: str | None
) -> tuple[list[dict], str | None]:
    out: list[dict] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        # A model never needs a credential to answer; strip before egress.
        out.append({"role": role, "content": redact(str(content))})
    return out, redact(system) if system else None
