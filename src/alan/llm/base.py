"""Provider abstraction for Alan's language backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Iterator, Sequence

Message = dict  # {"role": "user"|"assistant"|"system", "content": str}


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request."""


class Provider(ABC):
    """A minimal chat-completion backend.

    Carries the two routing signals that only the operator can know: how much
    this backend is preferred (``priority``) and what it is actually good at
    (``categories``). Both default to "no opinion", so a provider constructed
    without them still ranks -- it just never wins on a tie it did not earn.
    """

    name: str = "provider"
    model: str = ""

    def __init__(
        self,
        model: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        priority: int = 100,
        categories: object = None,
        **_: object,
    ) -> None:
        if model:
            self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        #: Lower wins (0 = preferred). 100 is deliberately neutral: it is the
        #: value every provider reports when config says nothing, so an unset
        #: priority cannot be mistaken for a strong one.
        self.priority_value = int(priority)
        #: Operator-stated capabilities. Left unset, the adapter infers them
        #: from the model name, which is a guess and is treated as one.
        if categories:
            self.categories = frozenset(categories)

    @abstractmethod
    def chat(self, messages: Sequence[Message], system: str | None = None) -> str:
        """Return the assistant's full reply for the given conversation."""

    def priority(self) -> int:
        """Lower wins when several models are eligible (0 = preferred)."""
        return self.priority_value

    def stream(
        self, messages: Sequence[Message], system: str | None = None
    ) -> Iterator[str]:
        """Yield reply text incrementally. Default: fall back to chat()."""
        yield self.chat(messages, system)

    def available(self) -> tuple[bool, str]:
        """Return (is_available, reason)."""
        return True, "ready"

    def describe(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name

    def close(self) -> None:
        """Release any network client held by the provider."""
        return None


def _merge_system(
    messages: Sequence[Message], system: str | None
) -> list[Message]:
    """Return a copy of messages with the system prompt first (if any)."""
    merged: list[Message] = []
    if system:
        merged.append({"role": "system", "content": system})
    merged.extend(dict(m) for m in messages)
    return merged


def as_text(chunks: Iterable[str]) -> str:
    return "".join(chunks)
