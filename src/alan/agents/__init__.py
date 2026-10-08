"""Alan's agents: bounded workers with least-privilege tool access.

The orchestrator owns these; an agent never constructs another agent and never
reaches a capability that is not in its own allow-list (brief section 13).
"""

from __future__ import annotations

from .standard import (
    BrowserAgent,
    CodingAgent,
    MemoryAgent,
    PlannerAgent,
    ResearchAgent,
    ReviewerAgent,
    SystemAgent,
    standard_agents,
)

__all__ = [
    "BrowserAgent",
    "CodingAgent",
    "MemoryAgent",
    "PlannerAgent",
    "ResearchAgent",
    "ReviewerAgent",
    "SystemAgent",
    "standard_agents",
]