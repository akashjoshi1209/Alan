"""Shared value types for Alan's provider interfaces.

These are the vocabulary every interface in :mod:`alan.interfaces` speaks. They
are intentionally dependency-free so that adapters (cognee, mem0, browser-use,
DSH-style plugins) can be written, tested and swapped without pulling in any
external library.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Callable


def utcnow() -> datetime:
    """Timezone-aware UTC now. Every timestamp in Alan uses this."""
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    """Short unique id, optionally prefixed (``mem_3f9a...``)."""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


class RiskLevel(IntEnum):
    """How dangerous a capability is, ordered so the gate can compare.

    Brief section 12. The gate denies anything above the active ceiling; no
    agent may raise its own ceiling.
    """

    SAFE = 0            # reading a local file
    LOW_RISK = 1        # creating a normal file
    MEDIUM_RISK = 2     # installing packages
    HIGH_RISK = 3       # deleting files, outbound communication
    PRIVILEGED = 4      # arbitrary privileged commands

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.name.lower()


class PermissionDecision(str, Enum):
    """Outcome of a permission check."""

    ALLOWED = "allowed"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    NOT_APPLICABLE = "not_applicable"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class MemoryLevel(IntEnum):
    """The L0-L5 memory hierarchy (brief section 10).

    Ordered so that retrieval can prefer the tightest scope that answers a
    question, and so promotion walks strictly upward.
    """

    L0_CONTEXT = 0     # the current context window itself
    L1_WORKING = 1     # scratch state for the active task
    L2_SESSION = 2     # durable for the current session
    L3_PROJECT = 3     # durable for the project/workspace
    L4_USER = 4        # long-term user facts and preferences
    L5_ARCHIVE = 5     # archived / episodic history

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.name


class Privacy(str, Enum):
    """How sensitive a memory item is. Never logged or sent to a remote model."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    SECRET = "secret"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class MemoryType(str, Enum):
    """What kind of thing a memory item holds."""

    FACT = "fact"
    PREFERENCE = "preference"
    INSTRUCTION = "instruction"
    DECISION = "decision"
    SUMMARY = "summary"
    EPISODE = "episode"
    SKILL = "skill"
    AGENT_STATE = "agent_state"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


@dataclass
class MemoryItem:
    """One retrievable memory, with the metadata brief section 10 requires.

    ``content`` is the only field a provider must fill. Everything else exists
    so retrieval can rank, scope, expire and redact.
    """

    content: str
    level: MemoryLevel = MemoryLevel.L2_SESSION
    type: MemoryType = MemoryType.FACT
    source: str = "user"
    id: str = field(default_factory=lambda: new_id("mem_"))
    timestamp: datetime = field(default_factory=utcnow)
    confidence: float = 1.0
    importance: float = 0.5
    privacy: Privacy = Privacy.INTERNAL
    last_accessed: datetime | None = None
    expires: datetime | None = None
    session_id: str | None = None
    project: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = _clamp(self.confidence)
        self.importance = _clamp(self.importance)

    def touched(self) -> None:
        """Mark as accessed (used by retention and decay)."""
        self.last_accessed = utcnow()

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires is None:
            return False
        return (now or utcnow()) >= self.expires

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "level": int(self.level),
            "type": self.type.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "importance": self.importance,
            "privacy": self.privacy.value,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "expires": self.expires.isoformat() if self.expires else None,
            "session_id": self.session_id,
            "project": self.project,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MemoryItem":
        def _dt(value: Any) -> datetime | None:
            if not value:
                return None
            return datetime.fromisoformat(str(value))

        return cls(
            content=str(raw.get("content", "")),
            level=MemoryLevel(int(raw.get("level", MemoryLevel.L2_SESSION))),
            type=MemoryType(str(raw.get("type", MemoryType.FACT))),
            source=str(raw.get("source", "user")),
            id=str(raw.get("id") or new_id("mem_")),
            timestamp=_dt(raw.get("timestamp")) or utcnow(),
            confidence=float(raw.get("confidence", 1.0)),
            importance=float(raw.get("importance", 0.5)),
            privacy=Privacy(str(raw.get("privacy", Privacy.INTERNAL))),
            last_accessed=_dt(raw.get("last_accessed")),
            expires=_dt(raw.get("expires")),
            session_id=raw.get("session_id"),
            project=raw.get("project"),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass
class ToolSpec:
    """Declarative description of a registered tool (brief section 11)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.SAFE
    permissions: tuple[str, ...] = ()
    timeout: float = 60.0
    logging_policy: str = "standard"
    executor: Callable[..., Any] | None = None
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risk": str(self.risk),
            "permissions": list(self.permissions),
            "timeout": self.timeout,
            "logging_policy": self.logging_policy,
            "tags": list(self.tags),
        }


@dataclass
class ToolResult:
    """Uniform result envelope so callers never parse provider-specific shapes."""

    ok: bool
    output: Any = None
    error: str | None = None
    tool: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


@dataclass
class AgentResult:
    """What an agent returns to the orchestrator."""

    output: str
    agent: str = ""
    success: bool = True
    error: str | None = None
    steps: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    memory_writes: list[MemoryItem] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.success


@dataclass
class PermissionRequest:
    """A capability request submitted to the permission gate (brief section 14)."""

    capability: str
    risk: RiskLevel
    agent: str = "alan"
    session_id: str | None = None
    trace_id: str | None = None
    reason: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """Loggable view with secret-looking arguments masked."""
        return {
            "capability": self.capability,
            "risk": str(self.risk),
            "agent": self.agent,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "reason": self.reason,
            "arguments": {
                k: ("***" if _looks_secret(k) else v) for k, v in self.arguments.items()
            },
        }


_SECRET_HINTS = (
    "key", "token", "secret", "password", "passwd", "credential", "auth",
    "cookie", "session_key", "api_key",
)


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


_SECRET_ENV_NAMES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY", "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "GH_TOKEN", "HF_TOKEN",
    "AZURE_OPENAI_API_KEY", "GOOGLE_API_KEY", "MEM0_API_KEY", "PINECONE_API_KEY",
    # Provider keys, named because every one of these can appear in a log line:
    # a provider's own error message is the usual way one gets out, and a
    # provider's *name* appearing in a traceback is exactly when its key is
    # nearby. Covered by a test against config.API_KEY_ENV so adding a provider
    # without listing its key here fails the suite.
    "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
    "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
)


def redact(text: str) -> str:
    """Mask anything that looks like a credential in free text.

    Applied to every log line and every memory item before it can reach a
    remote model. Deliberately conservative: a false positive costs a little
    context, a false negative leaks a secret.
    """
    import re

    out = text
    for name in _SECRET_ENV_NAMES:
        out = re.sub(
            rf"({re.escape(name)}\s*[=:]\s*)\S+", r"\1***", out, flags=re.IGNORECASE
        )
    # key-shaped tokens (sk-..., ghp_..., AKIA...)
    out = re.sub(
        r"\b(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}"
        r"|gsk_[A-Za-z0-9]{16,}"      # groq
        r"|sk-or-v1-[A-Za-z0-9]{16,}"  # openrouter
        r"|xai-[A-Za-z0-9]{16,})"      # xai
        r"\b",
        "***",
        out,
    )
    return out


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low
