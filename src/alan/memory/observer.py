"""Turns what Alan *does* into memory it can recall later.

Persistent memory that only stores sentences the user chose to remember is a
notebook, not a memory. The other half is recording what happened: which tools
ran, which MCP servers answered, what they returned, what failed and why. That
is what lets a fresh session answer "what did you do last time?", and the
current one answer "which tool did we just call?"

The mechanism is the shared :class:`~alan.core.events.EventBus`. Nothing here
reaches into the tool registry or the orchestrator -- the observer subscribes
to the same events every other component sees, which is the whole point of
having a bus (ADR-001). A tool call already emits ``tool.started`` and
``tool.finished``; this module is what makes those events *durable*.

Two deliberate limits
---------------------
* **Episodes, not transcripts.** Every item is written at ``L2_SESSION``, so
  it is scoped to the conversation that produced it. Durable facts (L4) are
  still decided by the orchestrator's gate -- a greeting is not a memory, and
  ``tests/test_memory.py::AssistantMemoryTest`` pins that this observer must
  not undo that.
* **Redacted and bounded.** The bus redacts credential-shaped strings on emit,
  and the argument/result previews are truncated before they are stored. What
  Alan remembers about you is on your disk; it should not be a megabyte per
  tool call, and it should never be an API key.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..core.events import Event, Events
from ..interfaces.memory import MemoryProvider
from ..interfaces.types import MemoryItem, MemoryLevel, MemoryType, redact

#: How long a ``tool.started`` waits to be matched by its ``tool.finished``.
#: A tool that hangs past its own timeout leaves an unmatched start behind;
#: without a TTL that entry would leak for the life of the process.
_PENDING_TTL_SECONDS = 600.0

#: Upper bound on unmatched starts held at once. Belt and braces against a
#: caller that emits starts and never finishes.
_MAX_PENDING = 200


class MemoryObserver:
    """Subscribes to the bus and records activity as session-scoped memory.

    Construction does not attach to anything: :meth:`attach` returns the
    unsubscribe callable, so a caller that builds a runtime and immediately
    closes it never leaks a subscription.
    """

    name = "memory-observer"

    def __init__(
        self,
        memory: MemoryProvider | None,
        *,
        session_id: str | None = None,
        registry: Any | None = None,
        enabled: bool = True,
    ) -> None:
        self.memory = memory
        self.session_id = session_id
        self.registry = registry
        self.enabled = enabled
        self._lock = threading.RLock()
        #: (trace_id, tool) -> (arguments, timestamp)
        self._pending: dict[tuple[str | None, str], tuple[dict[str, Any], float]] = {}
        self.recorded = 0
        self.dropped = 0

    # -- lifecycle -----------------------------------------------------
    def attach(self, bus: Any) -> Callable[[], None]:
        """Subscribe to the events this observer cares about."""
        patterns = (
            Events.TOOL_STARTED,
            Events.TOOL_FINISHED,
            Events.TOOL_FAILED,
            Events.AGENT_FINISHED,
            Events.PERMISSION_DENIED,
        )
        unsubscribers = [bus.subscribe(pattern, self.on_event) for pattern in patterns]
        return lambda: [unsub() for unsub in unsubscribers]  # type: ignore[misc]

    def close(self) -> None:
        with self._lock:
            self._pending.clear()

    # -- event handling ------------------------------------------------
    def on_event(self, event: Event) -> None:
        """Record one event. Never raises: an observer must not break the flow
        it observes, and the bus already guarantees that, but memory failing
        here would silently lose history."""
        if not self.enabled or self.memory is None:
            return
        try:
            item = self._to_item(event)
        except Exception:  # noqa: BLE001 - a malformed event is not an error
            return
        if item is None:
            return
        try:
            self.memory.remember(item)
        except Exception:  # noqa: BLE001 - memory is an enhancement
            self.dropped += 1
            return
        self.recorded += 1

    def _to_item(self, event: Event) -> MemoryItem | None:
        if event.name == Events.TOOL_STARTED:
            self._remember_start(event)
            return None
        if event.name in (Events.TOOL_FINISHED, Events.TOOL_FAILED):
            return self._tool_episode(event)
        if event.name == Events.AGENT_FINISHED:
            return self._agent_episode(event)
        if event.name == Events.PERMISSION_DENIED:
            return self._denial_episode(event)
        return None

    # -- pairing started with finished ---------------------------------
    def _key(self, event: Event) -> tuple[str | None, str]:
        return (event.trace_id, str(event.fields.get("tool", "")))

    def _remember_start(self, event: Event) -> None:
        """Hold the arguments until the matching finish arrives.

        ``tool.started`` is the only event carrying arguments; ``tool.finished``
        is the only one carrying the duration. Recording the call therefore
        means joining the two across a whole tool execution, which may be
        milliseconds or may be a minute.
        """
        arguments = event.fields.get("arguments")
        key = self._key(event)
        if not key[1]:
            return
        with self._lock:
            self._sweep_locked()
            if len(self._pending) >= _MAX_PENDING:
                self.pending_trimmed = getattr(self, "pending_trimmed", 0) + 1
                return
            self._pending[key] = (dict(arguments or {}), time.time())

    def _take_start(self, event: Event) -> dict[str, Any]:
        key = self._key(event)
        with self._lock:
            entry = self._pending.pop(key, None)
        if entry is None:
            return {}
        arguments, _stored = entry
        return arguments

    def _sweep_locked(self) -> None:
        cutoff = time.time() - _PENDING_TTL_SECONDS
        for key in [k for k, (_a, ts) in self._pending.items() if ts < cutoff]:
            del self._pending[key]

    # -- episode builders ----------------------------------------------
    def _tool_episode(self, event: Event) -> MemoryItem | None:
        tool = str(event.fields.get("tool", "")).strip()
        if not tool:
            return None
        arguments = self._take_start(event)
        failed = event.name == Events.TOOL_FAILED
        error = str(event.fields.get("error", "") or "")
        result = str(event.fields.get("result", "") or "")
        duration = event.duration_ms
        ok = not failed

        subject = _describe_arguments(arguments)
        if failed:
            content = f"{tool}({subject}) failed: {error}" if subject else (
                f"{tool} failed: {error}"
            )
        else:
            tail = f" -> {result}" if result else ""
            timing = f" in {_seconds(duration)}" if duration else ""
            content = f"Ran {tool}({subject}){tail}{timing}" if subject else (
                f"Ran {tool}{tail}{timing}"
            )
        if not content.strip():
            return None

        spec = self._spec(tool)
        mcp = _looks_like_mcp(tool, spec)
        importance = 0.45
        if failed:
            # A failure is more worth remembering than a success: it is the
            # thing that saves a repeat of the same dead end.
            importance = 0.7
        if spec is not None and getattr(spec, "risk", None) is not None:
            try:
                if str(getattr(spec, "risk")) not in ("RiskLevel.SAFE", "SAFE"):
                    importance = min(1.0, importance + 0.1)
            except Exception:  # noqa: BLE001 - cosmetic only
                pass
        return MemoryItem(
            content=content,
            level=MemoryLevel.L2_SESSION,
            type=MemoryType.EPISODE,
            source=("mcp:" if mcp else "tool:") + tool,
            importance=importance,
            confidence=1.0,
            session_id=event.session_id or self.session_id,
            metadata={
                "kind": "mcp" if mcp else "tool",
                "tool": tool,
                "agent": event.agent,
                "ok": ok,
                "duration_ms": duration,
                "arguments": arguments,
                "result": result,
                "error": error,
                "trace_id": event.trace_id,
            },
        )

    def _agent_episode(self, event: Event) -> MemoryItem | None:
        """Which sub-agent handled a request, and whether it succeeded."""
        agent = str(event.agent or "").strip()
        if not agent or agent in ("alan", ""):
            return None
        error = str(event.fields.get("error", "") or "")
        ok = not error
        content = (
            f"Delegated to agent {agent}: {error}" if error
            else f"Delegated to agent {agent}"
        )
        return MemoryItem(
            content=content,
            level=MemoryLevel.L2_SESSION,
            type=MemoryType.AGENT_STATE,
            source=f"agent:{agent}",
            importance=0.4,
            confidence=1.0,
            session_id=event.session_id or self.session_id,
            metadata={
                "kind": "agent",
                "agent": agent,
                "ok": ok,
                "error": error,
                "trace_id": event.trace_id,
            },
        )

    def _denial_episode(self, event: Event) -> MemoryItem | None:
        """A refused action. Worth remembering: it is a boundary the user set."""
        action = str(
            event.fields.get("action") or event.fields.get("tool") or ""
        ).strip()
        reason = str(event.fields.get("reason", "") or "")
        content = f"Refused {action}" + (f": {reason}" if reason else "")
        if action or reason:
            return MemoryItem(
                content=content,
                level=MemoryLevel.L2_SESSION,
                type=MemoryType.INSTRUCTION,
                source="permission",
                importance=0.8,
                confidence=1.0,
                session_id=event.session_id or self.session_id,
                metadata={
                    "kind": "permission",
                    "action": action,
                    "reason": reason,
                    "trace_id": event.trace_id,
                },
            )
        return None

    def _spec(self, tool: str) -> Any:
        if self.registry is None:
            return None
        try:
            return self.registry.spec(tool)
        except Exception:  # noqa: BLE001 - introspection only
            return None


def _seconds(duration_ms: Any) -> str:
    try:
        value = float(duration_ms)
    except (TypeError, ValueError):
        return ""
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value:.0f}ms"


def _describe_arguments(arguments: dict[str, Any], limit: int = 120) -> str:
    """The one or two arguments that identify a call, for the searchable text.

    Only scalar arguments, and only the first few: the point is that a later
    search for "quantum battery" finds the search that was run, not that the
    whole request is replayable from memory.
    """
    if not arguments:
        return ""
    parts: list[str] = []
    for key, value in arguments.items():
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        text = " ".join(str(value).split())
        if not text:
            continue
        parts.append(f"{key}={redact(text)}")
        if len(parts) >= 3:
            break
    joined = ", ".join(parts)
    if len(joined) > limit:
        return joined[: limit - 1] + "…"
    return joined


def _looks_like_mcp(tool: str, spec: Any) -> bool:
    """Does this tool come from an MCP server?

    Alan reaches MCP through the ``mcporter`` CLI, so an MCP-backed tool is an
    ordinary registered tool whose name or tags name the server. Detecting it
    here keeps the record honest -- "called the github server" reads very
    differently from "ran a tool" -- without the observer having to know which
    servers are configured.
    """
    lowered = tool.lower()
    if "mcp" in lowered or lowered.startswith("mcporter"):
        return True
    tags = tuple(getattr(spec, "tags", ()) or ())
    return any(str(tag).lower().startswith("mcp") for tag in tags)
