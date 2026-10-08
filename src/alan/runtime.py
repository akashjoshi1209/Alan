"""Composition root.

One function builds the whole system from a :class:`~alan.config.Settings`, in
dependency order, and hands back a handle that owns every resource:

    settings -> event bus -> permission gate -> memory -> execution
             -> tools -> model router -> agents -> orchestrator
             -> approval queue -> scheduler

Everything that needs the system (CLI, assistant, tests, demos) builds it here,
so there is exactly one place where Alan's wiring is decided and no module has
to reach around another's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents.standard import standard_agents
from .config import DATA_DIR, HOME, LOG_DIR, PROVIDER_NAMES, Settings, load_settings
from .core.events import EventBus, JsonlTrace
from .core.orchestrator import Orchestrator, OrchestratorConfig
from .core.planner import HeuristicPlanner
from .core.scheduler import LocalRunner, ScheduleStore, Scheduler
from .interfaces.capabilities import BrowserProvider
from .interfaces.memory import MemoryProvider
from .interfaces.model import ModelProvider
from .interfaces.model import as_model_provider
from .interfaces.types import PermissionRequest, RiskLevel, ToolSpec
from .llm import _construct, known_providers
from .llm.base import ProviderError
from .memory.providers import build_memory_provider
from .models.router import ModelRouter
from .permissions.gate import PermissionGate, Policy, default_gate
from .permissions.queue import ApprovalQueue
from .permissions.rules import parse_rules
from .telegram import TelegramBridge
from .tools.browser import SidecarBrowser
from .tools.coding import LocalCoding
from .tools.execution import LocalExecution
from .tools.internet import Internet
from .tools.registry import ToolRegistry
from .tools.schedule import ScheduleTools
from .tools.standard import StandardTools
from .tools.vibecoder import VibecoderTools

_RISK_NAMES = {level.name: level for level in RiskLevel}


@dataclass
class AlanRuntime:
    """Everything Alan needs, plus a correct shutdown."""

    settings: Settings
    bus: EventBus
    gate: PermissionGate
    registry: ToolRegistry
    router: ModelRouter
    orchestrator: Orchestrator
    memory: MemoryProvider | None = None
    execution: LocalExecution | None = None
    coding: LocalCoding | None = None
    browser: BrowserProvider | None = None
    #: Zero-config web backends (Exa + Jina Reader) behind the web.* tools.
    internet: Internet | None = None
    #: Unattended permission requests, waiting for a human decision.
    approvals: ApprovalQueue | None = None
    #: Background task loop. ``None`` when ``[scheduler] enabled = false``.
    scheduler: Scheduler | None = None
    #: Long-poll approval bridge to the operator's phone. Built when
    #: ``[telegram]`` is configured; started only by a chat session.
    telegram: TelegramBridge | None = None
    memory_note: str = ""
    model_note: str = ""
    browser_note: str = ""
    internet_note: str = ""
    trace_path: Path | None = None
    _closeables: list[Any] = field(default_factory=list)

    # -- introspection -------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        return list(self.registry.specs())

    def trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.bus.trace(trace_id)]

    def status(self) -> dict[str, Any]:
        """A single dict describing the whole system, for ``alan doctor``."""
        models = [
            {"provider": p.name, "model": p.model, "local": p.local,
             "available": p.available()[0]}
            for p in self.router.providers
        ]
        return {
            "home": str(self.settings.name),
            "models": models,
            "model_note": self.model_note,
            "memory": (
                {"backend": self.memory.name, "note": self.memory_note}
                if self.memory
                else {"backend": None, "note": self.memory_note}
            ),
            "tools": [spec.name for spec in self.registry.specs()],
            "browser": (
                {"provider": self.browser.name, "note": self.browser_note}
                if self.browser
                else {"provider": None, "note": self.browser_note}
            ),
            "internet": (
                {"provider": "internet", "note": self.internet_note}
                if self.internet
                else {"provider": None, "note": self.internet_note}
            ),
            "telegram": (
                {
                    "enabled": True,
                    "running": self.telegram.started,
                    "note": self.telegram.status()["note"],
                    "chat_id": self.telegram.chat_id,
                }
                if self.telegram
                else {
                    "enabled": False,
                    "running": False,
                    "note": "not configured (set bot_token and chat_id in settings.toml)",
                    "chat_id": "",
                }
            ),
            "agents": self.orchestrator.available(),
            "risk_ceiling": str(self.gate.ceiling),
            "scheduler": self.scheduler_state(),
            "pending_approvals": (
                len(self.approvals.pending()) if self.approvals else 0
            ),
            "events": len(self.bus.history()),
            "trace_file": str(self.trace_path) if self.trace_path else None,
        }

    # -- always-on operation ---------------------------------------------
    def scheduler_state(self) -> dict[str, Any]:
        """Scheduler status for ``alan doctor`` and ``/activity``."""
        if self.scheduler is None:
            return {"enabled": False, "running": False, "note": "disabled in settings"}
        tasks = self.scheduler.store.list()
        return {
            "enabled": True,
            "running": self.scheduler.running,
            "tasks": len(tasks),
            "waiting": len(self.scheduler.waiting()),
            "due": len(self.scheduler.due_soon()),
            "note": self.scheduler.store.load_error,
        }

    def start_scheduler(self) -> bool:
        """Start the background loop if enabled. Idempotent; False if disabled."""
        if self.scheduler is None:
            return False
        return self.scheduler.start()

    def stop_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()

    def start_telegram(self) -> bool:
        """Start the phone bridge if configured. Idempotent; False if none.

        Like the scheduler, the long-poll thread is owned by a chat session and
        is never started by a build or a one-shot command.
        """
        if self.telegram is None:
            return False
        return self.telegram.start()

    def stop_telegram(self) -> None:
        if self.telegram is not None:
            self.telegram.stop()

    def activity_text(self, *, include_history: bool = False) -> str:
        """The ``/activity`` view: tasks, approvals awaiting a decision, and
        what recently completed."""
        lines: list[str] = []

        lines.append("-- scheduled tasks --")
        if self.scheduler is None:
            lines.append("scheduler disabled ([scheduler] enabled = false)")
        else:
            lines.extend(self.scheduler.activity_lines())

        pending = self.approvals.pending() if self.approvals else []
        lines.append("-- awaiting approval --")
        if not pending:
            lines.append("nothing waiting")
        else:
            lines.extend(a.summary() for a in pending)
            lines.append("decide with: /approve <id> or /reject <id>")

        if include_history and self.scheduler is not None:
            recent: list[str] = []
            for task in reversed(self.scheduler.store.list()):
                for run in reversed(task.runs):
                    mark = "ok" if run.get("ok") else "FAILED"
                    recent.append(
                        f"[{mark}] {task.id} {run.get('at', '')} "
                        f"{str(run.get('summary', ''))[:80]}"
                    )
                    if len(recent) >= 10:
                        break
                if len(recent) >= 10:
                    break
            lines.append("-- recent runs --")
            lines.extend(recent or ["none yet"])

        waiting = self.scheduler.waiting() if self.scheduler else []
        if waiting:
            lines.append(
                f"note: {len(waiting)} task(s) stopped at a permission gate and "
                "will not continue until approved."
            )
        return "\n".join(lines)

    def decide_approval(self, approval_id: str, approve: bool) -> str:
        """Approve/reject by id (or a unique suffix). Returns a reply string."""
        if self.approvals is None:
            return "approvals are unavailable"
        approval = self.approvals.get(approval_id)
        if approval is None:
            # Accept a unique trailing fragment -- ids are shown truncated in
            # activity output, and demanding a copy-paste of tsk_/apr_ ids
            # defeats the purpose of a quick phone reply.
            matches = [
                a for a in self.approvals.pending()
                if a.id.endswith(approval_id)
            ]
            if len(matches) == 1:
                approval = matches[0]
            elif len(matches) > 1:
                return f"{approval_id} is ambiguous: " + ", ".join(
                    a.id for a in matches
                )
        if approval is None:
            return f"no pending approval {approval_id!r} (see /activity)"
        decided = self.approvals.decide(approval.id, approve)
        if decided is None:
            return f"{approval.id} was already decided"
        verb = "approved" if approve else "rejected"
        if approve:
            return (
                f"{verb} {approval.id} ({approval.capability}); "
                f"task {approval.task_id or '-'} will run again."
            )
        return (
            f"{verb} {approval.id} ({approval.capability}); "
            f"task {approval.task_id or '-'} paused -- /schedule resume "
            f"{approval.task_id} to restart it."
        )

    def close(self) -> None:
        # The scheduler first: no new work may start while tools are being
        # torn down underneath it.
        self.stop_scheduler()
        for closeable in reversed(self._closeables):
            try:
                closeable.close()
            except Exception:
                continue
        self.registry.close()


def build_gate(settings: Settings) -> PermissionGate:
    """The gate from configuration: ceiling, denials, and the operator's rules.

    A malformed rule raises here (via ``parse_rules``) rather than being
    skipped -- the same philosophy as a typo in ``deny``: configuration that
    silently does nothing is worse than configuration that refuses to load.
    """
    rules = (
        parse_rules(settings.permissions.rules) if settings.permissions.rules else None
    )
    gate = default_gate(
        _RISK_NAMES.get(settings.permissions.ceiling.upper(),
                        RiskLevel.MEDIUM_RISK),
        rules,
    )
    gate.auto_confirm = settings.permissions.auto_confirm
    for capability in settings.permissions.deny:
        gate.set_policy(Policy(capability=capability, allow=False,
                               reason="denied in settings.toml"))
    return gate


def build_router(settings: Settings) -> tuple[ModelRouter, str]:
    """Add every *available* backend so the router can fail over between them.

    Unlike :func:`llm.build_provider`, this does not stop at the first success:
    a local model that is up and a cloud key that is present are both useful
    fallbacks for each other.
    """
    router = ModelRouter()
    requested = (settings.provider or "auto").strip().lower()
    # One list, from config. This line used to carry its own copy of the
    # provider names, so a provider added to ``llm._NAMES`` was still unknown
    # here and never reached the router -- the failure mode looks exactly like
    # "my API key does nothing".
    if requested == "auto":
        names: tuple[str, ...] = PROVIDER_NAMES
    elif requested in PROVIDER_NAMES:
        names = (requested,)
    else:
        return router, f"unknown provider {requested!r}; no models registered"

    problems: list[str] = []
    for name in names:
        try:
            provider = _construct(name, settings)
        except ProviderError as exc:
            problems.append(f"{name}: {exc}")
            continue
        ok, reason = provider.available()
        if not ok:
            provider.close()
            problems.append(f"{name}: {reason}")
            continue
        router.add(as_model_provider(provider))

    if not router.providers:
        return router, "no model backend available: " + "; ".join(problems)
    return router, f"{len(router.providers)} backend(s): " + ", ".join(
        provider.describe() for provider in router.providers
    )


#: How to bring a memory backend back up. Only sidecars are listed, because only
#: sidecars have a start command -- mem0 is a package, not a process, so the
#: honest advice for it is to inspect rather than to run something.
_START_COMMAND = {
    "cognee": "scripts\\cognee-serve.ps1",
}


def _route_confirmer(
    user_confirmer: Callable[[PermissionRequest], bool] | None,
    approvals: ApprovalQueue,
) -> Callable[[PermissionRequest], bool]:
    """One approval channel that serves both interactive and scheduled runs.

    The orchestrator has a single confirmer slot, but the two kinds of session
    need different behaviour:

    * ``sched-<task id>`` sessions have no one at a console, so a question
      becomes an :class:`Approval` the user answers later from ``/activity``
      (or Telegram, once the bridge lands). The queue returns False now and
      remembers an approval for the task's next run.
    * every other session goes to the interactive confirmer, or fails closed
      when there is none -- exactly the behaviour of passing ``None``, so
      scripts and tests are unaffected.
    """
    def route(request: PermissionRequest) -> bool:
        session = request.session_id or ""
        if session.startswith("sched-"):
            return approvals.confirmer(request, task_id=session[len("sched-"):])
        if user_confirmer is not None:
            return user_confirmer(request)
        return False

    return route


def build_runtime(
    settings: Settings | None = None,
    *,
    memory: MemoryProvider | None = None,
    attach_trace: bool = False,
    trace_name: str = "events.jsonl",
    register_tools: bool = True,
    confirmer: Callable[[PermissionRequest], bool] | None = None,
) -> AlanRuntime:
    """Assemble Alan. Never raises for a missing optional dependency.

    ``confirmer`` is the approval channel for capabilities that need a human to
    agree. Pass one only from a surface that can actually put the question to
    someone -- the interactive CLI. Left as ``None``, which is the default and
    the right answer for scripts, tests and anything unattended, a capability
    that needs approval fails closed instead of running unasked. Scheduled
    sessions are the exception: they route to the approval queue so an
    unattended run stops at the gate and waits for a decision instead of
    silently failing or silently proceeding.

    The scheduler is *built* here (so ``/activity`` and ``alan schedule`` work
    in one-shot commands) but never *started* here -- ``build_runtime`` must
    not spawn a background thread for ``alan ask`` or ``alan doctor``. Chat
    starts it via :meth:`AlanRuntime.start_scheduler`.
    """
    settings = settings or load_settings()

    bus = EventBus()
    trace_path: Path | None = None
    if attach_trace:
        trace = JsonlTrace(LOG_DIR / trace_name)
        trace.attach(bus)
        trace_path = trace.path

    gate = build_gate(settings)
    approvals = ApprovalQueue(bus=bus)
    router, model_note = build_router(settings)
    # Let routed models emit their own model.* events on the shared bus.
    for provider in router.providers:
        provider._bus = bus  # type: ignore[attr-defined]

    if memory is None:
        memory, memory_note = build_memory_provider(
            settings.memory, model=settings.ollama.model
        )
        # A silent fallback is worse than a slow one: cognee down means semantic
        # recall quietly becomes keyword lookup, and the user has no way to tell
        # from Alan's answers that anything changed. So compare what was asked
        # for against what is actually in use -- structural, not string-matching
        # a message that may be reworded later -- and name the command to fix it.
        requested = (settings.memory.backend or "auto").lower()
        # In `auto` mode cognee is the first choice, so cognee being the active
        # backend is success, not a fallback. Getting this wrong would nag a
        # correctly-configured install to start a sidecar it is already using.
        expected = "cognee" if requested == "auto" else requested
        if memory is not None and memory.name != expected:
            remedy = _START_COMMAND.get(expected)
            memory_note += f" - start it with {remedy}" if remedy else (
                f" - check `alan backends` for {expected}"
            )
    else:
        memory_note = f"injected {memory.name}"

    # Even when execution is enabled it is jailed to Alan's own folder, so a
    # mistake cannot rewrite the host outside the product.
    execution = LocalExecution(root=HOME) if settings.tools.execution else None
    coding = LocalCoding(execution) if settings.tools.coding else None

    # The browser is a sidecar, so constructing the provider costs nothing and
    # starts no browser. Whether it is actually usable is reported, not
    # assumed: an enabled-but-not-running sidecar is a configuration mistake
    # worth surfacing at `alan doctor`, not a silent no-op at first use.
    browser: BrowserProvider | None = None
    browser_note = "disabled"
    if settings.tools.browser:
        browser = SidecarBrowser(settings.browser.base_url, timeout=settings.browser.timeout)
        ok, reason = browser.available()
        # The provider reports *what* is wrong; the command to fix it belongs
        # here, because this is the layer that writes for a human. Naming the
        # port without the script leaves the reader to go and find it.
        browser_note = reason if ok else (
            f"{reason} - start it with scripts\\browser-serve.ps1"
        )

    # The internet backends are zero-config: `available()` only asks whether
    # mcporter and curl exist, so probing costs nothing and starts nothing.
    internet: Internet | None = None
    internet_note = "disabled"
    if settings.tools.internet:
        internet = Internet()
        ok, reason = internet.available()
        internet_note = reason if ok else f"unavailable ({reason})"

    registry = ToolRegistry(gate, bus=bus)
    closeables: list[Any] = [router]

    if register_tools:
        registry.register_provider(
            StandardTools(
                settings,
                execution=execution,
                memory=memory,
                browser=browser,
                internet=internet,
            )
        )
        if coding is not None:
            _register_coding_tools(registry, coding)
        vibecoder_note = _register_vibecoder_tools(registry, settings)
        closeables.append(registry)

    orchestrator = Orchestrator(
        router,
        registry,
        memory=memory,
        gate=gate,
        bus=bus,
        planner=HeuristicPlanner(),
        config=OrchestratorConfig(
            system_prompt=settings.system_prompt,
            top_k=settings.memory.top_k,
            auto_recall=settings.memory.auto_recall,
            auto_remember=settings.memory.auto_remember,
            local_only=settings.permissions.local_only,
            confirmer=_route_confirmer(confirmer, approvals),
            # The one wiring with an operator behind it, so this is where
            # remembering failures switches on: a hand-built orchestrator keeps
            # the default (off) and writes nothing to shared state.
            learn=True,
        ),
        agents=standard_agents(coding),
    )

    # The approval queue decides, the scheduler reacts: approving re-queues the
    # waiting task, rejecting pauses it (see Scheduler.on_approval_decided).
    scheduler: Scheduler | None = None
    if settings.scheduler.enabled:
        store = ScheduleStore(DATA_DIR / "state" / "schedule.json")
        runner = LocalRunner(
            lambda req, session_id: orchestrator.run(req, session_id=session_id)
        )
        scheduler = Scheduler(
            store,
            runner,
            bus=bus,
            approvals=approvals,
            tick_seconds=settings.scheduler.tick_seconds,
            task_timeout=settings.scheduler.task_timeout,
        )
        approvals.set_on_decide(scheduler.on_approval_decided)
        if register_tools:
            registry.register_provider(ScheduleTools(scheduler))

    runtime = AlanRuntime(
        settings=settings,
        bus=bus,
        gate=gate,
        registry=registry,
        router=router,
        orchestrator=orchestrator,
        memory=memory,
        execution=execution,
        coding=coding,
        browser=browser,
        internet=internet,
        approvals=approvals,
        scheduler=scheduler,
        memory_note=memory_note,
        model_note=model_note,
        browser_note=browser_note,
        internet_note=internet_note,
        trace_path=trace_path,
    )
    runtime._closeables = closeables
    if memory is not None:
        runtime._closeables.append(memory)

    # The Telegram bridge is built (subscribing to the bus) but never started
    # here: start_telegram belongs to chat, exactly like the scheduler. It is
    # wired after the runtime exists so its `decide` can be the same call the
    # chat loop's /approve makes -- one decision path for both surfaces.
    if (
        settings.telegram.enabled
        and settings.telegram.bot_token
        and settings.telegram.chat_id
    ):
        bridge = TelegramBridge(
            settings.telegram.bot_token,
            settings.telegram.chat_id,
            decide=runtime.decide_approval,
            activity=lambda: runtime.activity_text(include_history=True),
            bus=bus,
            long_poll=settings.telegram.long_poll,
            retry_seconds=settings.telegram.retry_seconds,
            notify_tasks=settings.telegram.notify_tasks,
        )
        runtime.telegram = bridge
        runtime._closeables.append(bridge)
    return runtime


def _register_vibecoder_tools(registry: ToolRegistry, settings: Settings) -> str:
    """Register the Vibecoder delegation tools, or explain why they are absent.

    Registered unconditionally when the binary is found. The tool declares
    ``MEDIUM_RISK`` and requires both ``app.launch`` and ``filesystem.read``, so
    the existing permission gate decides whether a given run may use it -- the
    point of registering it is that the decision is visible and auditable rather
    than hard-wired here.

    Returns a one-line note for the status line. An absent Vibecoder is not an
    error: Alan is still useful for chat and inspection, it just cannot code. The
    note says so plainly rather than leaving the tool silently missing.
    """
    tools = VibecoderTools(settings)
    ok, reason = tools.available()
    if not ok:
        return f"vibecoder: not available ({reason}) - Alan cannot code on its own"
    registry.register_provider(tools)
    return f"vibecoder: {reason}"


def _register_coding_tools(registry: ToolRegistry, coding: LocalCoding) -> None:
    """Expose the coding provider as four separate, separately-gated tools."""

    def _repo(repo: str = "") -> str:
        return repo or "."

    registry.register_tool(
        "code.analyze",
        "Inspect a repository: language, layout, entry points, test command.",
        lambda repo="", query=None: coding.analyze(_repo(repo)).__dict__,
        input_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "default": "."},
                # Accepted so the audit trail shows what the user asked for;
                # this tool works from the repository, not from the wording.
                "query": {"type": "string", "description": "ignored by this tool"},
            },
        },
        risk=RiskLevel.SAFE,
        tags=("code", "read"),
    )
    registry.register_tool(
        "code.edit",
        "Write file contents into a repository and return the unified diff.",
        lambda edits, repo="", query=None: coding.edit(_repo(repo), edits).__dict__,
        input_schema={
            "type": "object",
            "properties": {
                "edits": {
                    "type": "object",
                    "description": "relative path -> new content",
                    "additionalProperties": {"type": "string"},
                },
                "repo": {"type": "string", "default": "."},
                "query": {"type": "string", "description": "ignored by this tool"},
            },
            "required": ["edits"],
        },
        risk=RiskLevel.MEDIUM_RISK,
        tags=("code", "write"),
    )
    registry.register_tool(
        "code.test",
        "Run a repository's test suite.",
        lambda repo="", command="", query=None: coding.run_tests(
            _repo(repo), command=command
        ).__dict__,
        input_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "default": "."},
                "command": {"type": "string", "default": ""},
                "query": {"type": "string", "description": "ignored by this tool"},
            },
        },
        risk=RiskLevel.MEDIUM_RISK,
        timeout=660.0,
        tags=("code", "test"),
    )
    registry.register_tool(
        "code.review",
        "Review a change set for risk and security concerns.",
        lambda files_changed=None, additions=0, deletions=0, patch="",
        files_content=None, query=None: coding.review(
            _summary(files_changed, additions, deletions, patch, files_content)
        ),
        input_schema={
            "type": "object",
            "properties": {
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "additions": {"type": "integer", "default": 0},
                "deletions": {"type": "integer", "default": 0},
                "patch": {"type": "string"},
                "files_content": {"type": "object"},
                "query": {"type": "string", "description": "ignored by this tool"},
            },
        },
        risk=RiskLevel.SAFE,
        tags=("code", "review"),
    )


def _summary(
    files_changed: list[str] | None,
    additions: int,
    deletions: int,
    patch: str,
    files_content: dict[str, str] | None,
):
    from .interfaces.capabilities import DiffSummary

    return DiffSummary(
        files_changed=list(files_changed or []),
        additions=additions,
        deletions=deletions,
        patch=patch,
        files_content=dict(files_content or {}),
    )


__all__ = [
    "AlanRuntime",
    "build_gate",
    "build_router",
    "build_runtime",
    "known_providers",
]