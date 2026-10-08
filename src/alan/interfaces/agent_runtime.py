"""The agent contract and the context an agent is allowed to touch.

An agent is a bounded worker with an explicitly declared tool allow-list. It
never receives the full registry, the permission policy or a raw provider: it
receives an :class:`AgentContext`, which is the *only* way to reach a capability.

That is the mechanism behind brief section 13 ("subagents must have limited
tool access") and section 7 (no coupling to implementation APIs).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

from .memory import MemoryProvider
from .model import ModelProvider
from .tools import ToolInvoker
from .types import AgentResult, RiskLevel


@dataclass
class AgentContext:
    """Everything an agent may use, and nothing more."""

    request: str
    agent: str
    model: ModelProvider
    tools: ToolInvoker
    memory: MemoryProvider | None = None
    session_id: str | None = None
    trace_id: str | None = None
    allowed_tools: frozenset[str] = frozenset()
    ceiling: RiskLevel = RiskLevel.LOW_RISK
    emit: Any = None            # callable(event_name, **fields)
    scratch: dict[str, Any] = field(default_factory=dict)

    def tool_allowed(self, name: str) -> bool:
        """An empty allow-list means "no tools", never "all tools"."""
        return name in self.allowed_tools

    def remember(self, content: str, **kwargs: Any) -> None:
        """Queue a memory write for later review.

        Deliberately does *not* write: an agent proposes, the orchestrator
        decides. This is the mechanism that stops arbitrary model output from
        silently becoming permanent memory (brief section 2).
        """
        if self.memory is None:
            return
        self.scratch.setdefault("memory_proposals", []).append(
            {"content": content, "agent": self.agent, **kwargs}
        )


class Agent(ABC):
    """Base class for every Alan agent."""

    name: str = "agent"
    description: str = ""
    tools: frozenset[str] = frozenset()
    ceiling: RiskLevel = RiskLevel.LOW_RISK
    system_prompt: str = ""
    #: False for agents that only inspect state or call tools (the Planner, the
    #: System agent). Those must keep working with no model backend at all, so
    #: the orchestrator does not demand one for them.
    needs_model: bool = True

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult:
        """Do the work and return a result. Never raise for expected failure."""

    def capabilities(self) -> Sequence[str]:
        """Short capability tags, used by the orchestrator's router."""
        return ()


class AgentRuntime(ABC):
    """Owns agent construction, delegation and lifecycle."""

    @abstractmethod
    def run(self, request: str, *, agent: str | None = None, **kwargs: Any) -> AgentResult:
        """Run a request end to end, selecting an agent if none is named."""

    @abstractmethod
    def delegate(
        self, ctx: AgentContext, agent: str, request: str, **kwargs: Any
    ) -> AgentResult:
        """Run a subagent from within another agent.

        The subagent gets its own, narrower allow-list; delegation never widens
        permissions.
        """

    @abstractmethod
    def available(self) -> list[str]:
        """Names of the agents this runtime can run."""
