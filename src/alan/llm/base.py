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
        connect_timeout: float = 30.0,
        **_: object,
    ) -> None:
        if model:
            self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        #: Ceiling on the TCP + TLS handshake. Deliberately wider than the
        #: SDKs' 5.0s default: that number assumes a clean link, and any TLS
        #: inspecting proxy or antivirus web filter pushes the handshake well
        #: past it, which then gets reported as a provider failure. The read
        #: timeout is left at the SDK default so a stuck generation still ends.
        self.connect_timeout = float(connect_timeout or 30.0)
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


#: Phrases the SDKs and their HTTP layer use for a handshake that never
#: finished. Matched case-insensitively against the whole exception chain,
#: because the signal is one level down: ``openai.APITimeoutError`` only says
#: "Request timed out.", while the ``httpcore`` ConnectTimeout that caused it
#: says which phase actually failed.
_CONNECT_TIMEOUT_MARKERS = (
    "handshake operation timed out",
    "connecttimeout",
    "connect timeout",
    "all connection attempts failed",
)


def _is_connect_timeout(exc: BaseException) -> bool:
    """Did the request fail before it reached the API, during connect?

    Walks ``__cause__``/``__context__`` rather than inspecting ``exc`` alone:
    the SDK wraps the HTTP layer's error, and the wrapper deliberately loses
    the distinction between "never connected" and "connected, then stalled",
    which is the whole difference between a local network problem and a slow
    model. Markers are matched against the type name as well as the text, since
    several of these carry a terse message and a descriptive class.
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        text = f"{type(node).__name__} {node}".lower()
        if any(marker in text for marker in _CONNECT_TIMEOUT_MARKERS):
            return True
        node = node.__cause__ or node.__context__
    return False


def connect_hint(exc: BaseException, connect_timeout: float) -> str:
    """Explain a connect-phase failure, or return ``""`` when it was not one.

    Appended to the provider's own message rather than replacing it, so the
    underlying SDK text -- which is what gets searched for -- stays intact.

    Without this, a connect timeout reads as ``groq stream failed: Request
    timed out.``, which sends the operator to the provider's dashboard to
    investigate a service that was never reached and was answering correctly
    all along. That exact misdirection cost an hour of tracing on this
    machine, where an antivirus web filter was adding ~10.5s to every TLS
    handshake against the SDK's 5.0s default.
    """
    if not _is_connect_timeout(exc):
        return ""
    return (
        f" (the TLS handshake did not finish within {connect_timeout:g}s, so "
        f"the request never reached the provider -- this points at a local "
        f"network filter, proxy or antivirus web-protection module delaying "
        f"HTTPS, not at the API. Raise connect_timeout under [provider] in "
        f"config/settings.toml, or exclude this application from HTTPS "
        f"inspection.)"
    )
