"""Alan-owned interfaces.

Every external system is reached through one of these contracts (brief
section 7). Adapters live in :mod:`alan.integrations` and
:mod:`alan.memory.providers`; nothing else may import a backend library.

    MemoryProvider      <- cognee, mem0, local JSON store
    ModelProvider       <- llm.Provider (openai / anthropic / ollama)
    ToolProvider        <- registry sources
    ToolInvoker         <- ToolRegistry (the only path to an executor)
    Agent / AgentRuntime<- orchestrator, planner, subagents
    ExecutionProvider   <- Shell/Python/Filesystem/Process executors
    BrowserProvider     <- browser-use sidecar
    CodingProvider      <- Alan's coding agent
"""

from __future__ import annotations

from .agent_runtime import Agent, AgentContext, AgentRuntime
from .capabilities import (
    BrowserProvider,
    BrowserResult,
    CodingProvider,
    CommandResult,
    DiffSummary,
    ExecutionProvider,
    RepoAnalysis,
)
from .memory import MemoryProvider, MemoryUnavailable, render_block
from .model import (
    ModelCategory,
    ModelProvider,
    RoutedModel,
    UnavailableModel,
    as_model_provider,
)
from .tools import ToolInvoker, ToolProvider
from .types import (
    AgentResult,
    MemoryItem,
    MemoryLevel,
    MemoryType,
    PermissionDecision,
    PermissionRequest,
    Privacy,
    RiskLevel,
    ToolResult,
    ToolSpec,
    new_id,
    redact,
    utcnow,
)

__all__ = [
    "Agent",
    "AgentContext",
    "AgentResult",
    "AgentRuntime",
    "BrowserProvider",
    "BrowserResult",
    "CodingProvider",
    "CommandResult",
    "DiffSummary",
    "ExecutionProvider",
    "MemoryItem",
    "MemoryLevel",
    "MemoryProvider",
    "MemoryType",
    "MemoryUnavailable",
    "ModelCategory",
    "ModelProvider",
    "PermissionDecision",
    "PermissionRequest",
    "Privacy",
    "RepoAnalysis",
    "RiskLevel",
    "RoutedModel",
    "ToolInvoker",
    "ToolProvider",
    "ToolResult",
    "ToolSpec",
    "UnavailableModel",
    "as_model_provider",
    "new_id",
    "redact",
    "render_block",
    "utcnow",
]
