"""Alan's conversational assistant.

This is the interactive surface: stateful chat, streaming, session files. It
deliberately owns *no* behaviour of its own. Planning, memory, permissions, tool
execution, model routing and the memory decision all belong to the orchestrator
(:mod:`alan.core.orchestrator`), which is assembled once by
:func:`alan.runtime.build_runtime`.

Two paths, one rule:

``ask``     runs the full pipeline -- plan, recall, tools, agents, trace.
``stream``  calls the routed model directly, because token-by-token output is
            worth more than a plan, then applies the *same* memory decision via
            :meth:`Orchestrator.store_turn`. A request that needs a capability is
            escalated to the full pipeline anyway: a tool-less path cannot "open
            youtube" no matter how well the model answers.

The previous version of this file remembered every turn unconditionally. That is
what the memory decision gate exists to prevent: a transcript is not a memory.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import SESSION_DIR, Settings, load_settings
from .core.events import Events
from .interfaces.model import as_model_provider
from .interfaces.types import MemoryItem, PermissionRequest
from .llm import Message, Provider, build_provider
from .memory.providers import build_memory_provider
from .runtime import AlanRuntime, build_runtime


class Alan:
    """A stateful chat assistant bound to one orchestrator."""

    def __init__(
        self,
        settings: Settings | None = None,
        provider: Provider | None = None,
        *,
        runtime: AlanRuntime | None = None,
        session_id: str | None = None,
        confirmer: Callable[[PermissionRequest], bool] | None = None,
    ) -> None:
        """``confirmer`` is how a person approves a capability needing approval.

        Only a surface that can genuinely put the question to someone should pass
        one -- the interactive CLI does, scripts and tests do not. With none, such
        a capability fails closed and says which setting would change that, which
        is the safe direction: an unattended run must not stop to ask, and must
        not proceed unasked either.

        Ignored when ``runtime`` is supplied, since that runtime's orchestrator is
        already configured.
        """
        self.settings = settings or load_settings()
        self.session_id = session_id or time.strftime("session-%Y%m%d-%H%M%S")
        self._owns_runtime = runtime is None

        if runtime is None:
            runtime = build_runtime(self.settings, confirmer=confirmer)
            if provider is not None:
                _swap_in_provider(runtime, provider)
        self.runtime = runtime

        self.system_prompt = self.settings.system_prompt
        self.history: list[Message] = []
        self.memory = runtime.memory
        self._memory_queue: list[MemoryItem] = []
        self._write_lock = threading.Lock()

    # -- convenience accessors -----------------------------------------
    @property
    def provider(self) -> Provider | Any:
        """The model provider currently in use, for descriptions and streaming."""
        routed = self.runtime.router.providers
        return routed[0] if routed else None

    def describe(self) -> str:
        routed = self.runtime.router.providers
        return routed[0].describe() if routed else "no model backend available"

    # -- always-on operation ---------------------------------------------
    # Thin passthroughs: the behaviour lives on the runtime (approval queue,
    # scheduler), and Alan keeps a single surface for the chat loop to talk to.
    @property
    def scheduler(self):  # pragma: no cover - convenience alias
        return self.runtime.scheduler

    def start_scheduler(self) -> bool:
        """Start the background task loop if ``[scheduler] enabled``. A chat
        session owns this: nothing below the chat loop starts it, so scripts
        and one-shot commands never launch background threads."""
        return self.runtime.start_scheduler()

    def stop_scheduler(self) -> None:
        self.runtime.stop_scheduler()

    def start_telegram(self) -> bool:
        """Start the phone approval bridge if ``[telegram]`` is configured.
        Also chat-owned, for the same reason as the scheduler."""
        return self.runtime.start_telegram()

    def stop_telegram(self) -> None:
        self.runtime.stop_telegram()

    @property
    def telegram(self):  # pragma: no cover - convenience alias
        return self.runtime.telegram

    def activity(self, *, include_history: bool = False) -> str:
        return self.runtime.activity_text(include_history=include_history)

    def decide(self, approval_id: str, approve: bool) -> str:
        return self.runtime.decide_approval(approval_id, approve)

    # -- memory ---------------------------------------------------------
    def _store_memory(self, items: list[MemoryItem]) -> None:
        """Record what was written; writes themselves already happened.

        The orchestrator's decision gate ran synchronously, so this is bookkeeping
        for ``pending_memories`` and ``flush_memory``, not a second write path.
        """
        if not items:
            return
        with self._write_lock:
            self._memory_queue.extend(items)

    def pending_memories(self) -> int:
        """Memory items written this session."""
        with self._write_lock:
            return len(self._memory_queue)

    def flush_memory(self, timeout: float = 5.0) -> None:
        """Present for API compatibility; writes are already complete.

        An earlier version queued writes on a background thread because the
        cognee sidecar takes ~15 s per write. That queue is gone: blocking a chat
        turn on a graph build was worse than a visible pause, and the decision
        gate means far fewer writes happen anyway.
        """
        return None

    # -- conversation ---------------------------------------------------
    def reset(self) -> None:
        self.history.clear()

    def _with_user(self, text: str) -> list[Message]:
        return [*self.history, {"role": "user", "content": text}]

    def _remember(self, user: str, assistant: str) -> None:
        self.history.append({"role": "user", "content": user})
        self.history.append({"role": "assistant", "content": assistant})

    def ask(self, text: str, remember: bool = True) -> str:
        """Run one turn through the full pipeline."""
        result = self.runtime.orchestrator.run(
            text, session_id=self.session_id, remember=remember
        )
        reply = result.output if result.success else f"[{result.error}]"
        self._remember(text, reply)
        self._store_memory(result.memory_writes)
        return reply

    def stream(self, text: str, remember: bool = True) -> Iterator[str]:
        """Stream a reply directly from the routed model.

        Planning and tools are skipped on purpose: the value of streaming is the
        first token, and a plan costs seconds. Memory retrieval still happens,
        and the memory decision is still the orchestrator's.

        The exception is a request that needs a capability. Skipping the plan
        there does not merely cost quality, it changes what Alan *is*: told to
        "open youtube", a tool-less path cannot open anything, so the model
        answers the only way it can and says it cannot help. That is a correct
        model response to a correct request, which is what made the bug so
        hard to see from the output. So the deterministic plan is consulted
        first -- regex only, no model call, microseconds -- and a request that
        needs a tool is handed to the full pipeline.

        Cost of the check: a conversation pays one regex pass. Benefit: a
        command is never silently answered with small talk.
        """
        orchestrator = self.runtime.orchestrator
        if orchestrator.requires_capability(text):
            yield self.ask(text, remember=remember)
            return

        routed = self.runtime.router.providers
        if not routed:
            yield "[no model backend available; run scripts\\run.ps1 or add a key]"
            return

        model = routed[0]
        context = ""
        if self.memory is not None and self.settings.memory.auto_recall:
            try:
                context = self.memory.context_block(
                    text, max_chars=self.settings.memory.max_context_tokens * 4
                )
            except Exception:
                context = ""

        system = self.system_prompt
        # Before the recalled facts, for the same reason as in the orchestrator:
        # a lesson governs how the rest of the prompt should be read, and advice
        # placed after what it governs arrives too late. Costs one memoised file
        # read, and returns "" when nothing has failed that looks like this.
        lessons = orchestrator.lesson_hint(text)
        if lessons:
            system = f"{system}\n\n{lessons}"
        if context:
            system = f"{system}\n\n{context}"

        parts: list[str] = []
        for chunk in _stream(model, self._with_user(text), system):
            parts.append(chunk)
            yield chunk

        reply = "".join(parts)
        self._remember(text, reply)
        self._store_memory(
            self.runtime.orchestrator.store_turn(
                text, reply, session_id=self.session_id, remember=remember
            )
        )

    # -- persistence -----------------------------------------------------
    def save_session(self, name: str | None = None) -> Path:
        name = name or f"{self.session_id}"
        path = SESSION_DIR / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "provider": self.describe(),
            "system": self.system_prompt,
            "messages": self.history,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def load_session(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.history = list(data.get("messages", []))
        self.session_id = data.get("session_id", self.session_id)
        if data.get("system"):
            self.system_prompt = data["system"]

    def close(self) -> None:
        if self._owns_runtime:
            self.runtime.close()

    def __enter__(self) -> "Alan":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Alan provider={self.describe()} turns={len(self.history) // 2}>"


def _stream(model: Any, messages: list[Message], system: str) -> Iterator[str]:
    """Stream from whichever kind of provider the router returned."""
    stream = getattr(model, "stream", None)
    if callable(stream):
        yield from stream(messages, system)
        return
    # RoutedModel and UnavailableModel expose chat() only.
    yield model.chat(messages, system)


def _swap_in_provider(runtime: AlanRuntime, provider: Provider) -> None:
    """Replace the router's backends with an explicitly supplied provider."""
    adapted = as_model_provider(provider)
    adapted._bus = runtime.bus  # type: ignore[attr-defined]
    runtime.router.providers = [adapted]
    runtime.orchestrator.router = runtime.router
    runtime.model_note = f"injected {provider.describe()}"


def quick_ask(text: str, settings: Settings | None = None) -> str:
    """One-shot helper used by tests."""
    settings = settings or load_settings()
    memory, _note = build_memory_provider(settings.memory, model=settings.ollama.model)
    with Alan(settings=settings, runtime=build_runtime(
        settings, memory=memory, register_tools=False
    )) as assistant:
        return assistant.ask(text, remember=False)


__all__ = ["Alan", "quick_ask", "Events", "build_provider"]
