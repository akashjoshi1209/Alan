"""The tool interface Alan owns.

A tool is anything with a name, a schema, a risk level and an executor. The
registry (see :mod:`alan.tools.registry`) is the only thing allowed to call an
executor, and it always goes through the permission gate first.

Backends such as browser-use expose many concrete operations; Alan wraps each
one as a separate :class:`ToolSpec` so that permissions, timeouts and audit
records are per-operation rather than per-library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Sequence

from .types import PermissionRequest, ToolResult, ToolSpec


class ToolProvider(ABC):
    """A source of tools that Alan can register."""

    name: str = "provider"

    @abstractmethod
    def tools(self) -> Sequence[ToolSpec]:
        """Every tool this provider offers, with full metadata."""

    def available(self) -> tuple[bool, str]:
        """Return ``(is_available, reason)`` without raising."""
        return True, "ready"

    def close(self) -> None:
        """Release any subprocess or browser session held open."""
        return None


class ToolInvoker(ABC):
    """The single sanctioned path from a tool name to an executor.

    Implemented by :class:`alan.tools.registry.ToolRegistry`. Keeping it an
    interface means an agent depends on the capability, never on the registry
    class (brief section 7).
    """

    @abstractmethod
    def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        agent: str = "alan",
        session_id: str | None = None,
        trace_id: str | None = None,
        confirm: bool = False,
        confirmer: Callable[[PermissionRequest], bool] | None = None,
    ) -> ToolResult:
        """Run a registered tool through the permission gate.

        Implementations must not raise for ordinary failures: an unknown tool, a
        denied permission and a timeout all come back as a ``ToolResult`` with
        ``ok=False`` so an agent can recover instead of crashing.

        ``confirmer`` is supplied per call rather than held on the gate, because
        the gate is shared: every subagent invokes through the same registry, so
        an approval channel attached to the gate would be inherited by agents
        that have no human to ask. A caller passes one only when it is genuinely
        able to put the question to a person; with none, a capability that needs
        approval fails closed rather than proceeding unasked.
        """

    @abstractmethod
    def specs(self) -> Sequence[ToolSpec]:
        """Registered tool specs, for prompt construction and audit."""
