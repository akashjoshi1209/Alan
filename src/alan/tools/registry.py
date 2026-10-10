"""The central tool registry — the only path from a name to an executor.

Brief section 11. Every capability Alan exposes is registered here with its
schema, risk and timeout, and :meth:`ToolRegistry.invoke` is the single place
that consults the permission gate before running anything.

Failure is a value, not an exception: an unknown tool, a denial and a timeout
all return ``ToolResult(ok=False, ...)`` so an agent can recover (brief
section 15) instead of the process dying.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Sequence

from ..core.events import EventBus, Events
from ..interfaces.tools import ToolInvoker, ToolProvider
from ..interfaces.types import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
    ToolResult,
    ToolSpec,
)
from ..permissions.gate import PermissionGate

#: How many abandoned threads *one* tool may accumulate before that tool stops
#: being started. Python cannot interrupt a running thread, so a tool that never
#: returns leaves a thread behind permanently; without a ceiling here a
#: repeatedly-hung tool would leak a thread per call until the process died.
#:
#: The ceiling is per tool, not global, and that distinction is the whole point.
#: A global ceiling lets one broken tool disable every other one -- the same
#: single point of failure this mechanism exists to remove, just moved up a
#: layer. Quarantining the offender keeps ``web.open`` usable while ``shell.
#: execute`` is wedged, which is the only recovery worth having.
_MAX_HUNG = 4


class ScopedTools(ToolInvoker):
    """A view of the registry that can only ever be narrower.

    An agent's tool allow-list and risk ceiling used to be things it kept by
    convention: the context handed it the *whole* registry and relied on each
    agent to call :meth:`AgentContext.tool_allowed` before invoking. That is one
    forgotten check -- or one new agent written by someone in a hurry -- away
    from any tool in the system being reachable, and it contradicted this
    package's own documentation, which states an agent never receives the full
    registry.

    Enforcing it here instead makes it structural rather than a convention:

    * the allow-list is checked before the registry is consulted at all;
    * the ceiling is checked against the spec's own risk, so a tool too risky
      for this agent is refused *here* rather than surfacing later as a gate
      denial that reads like bad luck;
    * ``confirm`` and ``confirmer`` are dropped rather than forwarded. Approval
      is the operator's to give. An agent able to approve its own calls could
      raise its own permission simply by being asked to;
    * registration is refused outright, because a scope that could add tools
      would be wider than the one it exists to narrow.

    Nests safely: passing a :class:`ScopedTools` as the registry narrows again,
    so delegation composes.
    """

    def __init__(
        self,
        registry: Any,
        allowed: frozenset[str] | set[str] | None,
        ceiling: RiskLevel,
    ) -> None:
        self._registry = registry
        # An empty allow-list means "no tools". Treating it as "all tools" would
        # turn a typo into full access, which is the one reading of this that
        # must never happen.
        self._allowed = frozenset(allowed or ())
        self._ceiling = ceiling

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        agent: str = "alan",
        session_id: str | None = None,
        trace_id: str | None = None,
        confirm: bool = False,  # noqa: ARG002 - deliberately not forwarded
        confirmer: Callable[[PermissionRequest], bool] | None = None,  # noqa: ARG002
    ) -> ToolResult:
        if name not in self._allowed:
            return ToolResult(
                ok=False, tool=name,
                error=(
                    f"{name} is not in this agent's allow-list; it may use: "
                    f"{', '.join(sorted(self._allowed)) or '(no tools)'}"
                ),
            )
        spec = self._registry.spec(name)
        if spec is not None and spec.risk > self._ceiling:
            return ToolResult(
                ok=False, tool=name,
                error=(
                    f"{name} is {spec.risk.name.lower()} risk, above this "
                    f"agent's {self._ceiling.name.lower()} ceiling"
                ),
            )
        return self._registry.invoke(
            name, arguments, agent=agent, session_id=session_id,
            trace_id=trace_id, confirm=False, confirmer=None,
        )

    # -- read-only surface ---------------------------------------------
    def spec(self, name: str) -> Any:
        return self._registry.spec(name) if name in self._allowed else None

    def specs(self) -> Sequence[Any]:
        return tuple(s for s in self._registry.specs() if s.name in self._allowed)

    def names(self) -> list[str]:
        return sorted(self._allowed)

    def catalog(self, allowed: Sequence[str] | None = None) -> str:
        wanted = self._allowed if allowed is None else self._allowed & set(allowed)
        return self._registry.catalog(sorted(wanted))

    def close(self) -> None:
        """Nothing to release; the registry is not ours to close."""

    # -- refused outright ----------------------------------------------
    def _refuse(self, what: str) -> None:
        raise PermissionError(
            f"a scoped tool view cannot {what}: it exists to narrow access, "
            f"and that would widen it"
        )

    def register(self, spec: Any) -> Any:
        self._refuse("register a tool")

    def register_tool(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse("register a tool")

    def register_provider(self, provider: Any) -> Any:
        self._refuse("register a provider")

    def unregister(self, name: str) -> Any:
        self._refuse("unregister a tool")


def _preview(output: Any, limit: int = 200) -> str:
    """A short, redacted description of what a tool returned.

    Carried on the ``tool.finished`` event so the memory observer can record
    *what happened*, not just that a tool ran. Bounded and coerced to text on
    purpose: an event field must never hold a megabyte of tool output, and the
    bus redacts strings but cannot redact an object.
    """
    try:
        if isinstance(output, str):
            text = output
        elif isinstance(output, dict):
            text = ", ".join(f"{k}={v}" for k, v in list(output.items())[:4])
        else:
            text = str(output)
    except Exception:  # noqa: BLE001 - a hostile __str__ must not break a turn
        text = type(output).__name__
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


class ToolRegistry(ToolInvoker):
    """Holds tool specs and executes them behind the permission gate."""

    def __init__(
        self,
        gate: PermissionGate,
        *,
        bus: EventBus | None = None,
        max_workers: int = 4,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._lock = threading.RLock()
        self.gate = gate
        self.bus = bus or EventBus()
        # Small pool: Alan's tools are I/O-bound but must not fan out wildly.
        self._live = threading.Semaphore(max_workers)
        #: Threads still running a call we already gave up on, per tool name.
        #: See :meth:`invoke` for why abandoning a call cannot be free.
        self._hung: dict[str, set[threading.Thread]] = {}

    # -- registration --------------------------------------------------
    def register(self, spec: ToolSpec) -> ToolSpec:
        """Add or replace a tool. Replacing is allowed and audited by callers."""
        if not spec.name or "." not in spec.name:
            raise ValueError(f"tool name must be namespaced, got {spec.name!r}")
        if spec.executor is None:
            raise ValueError(f"tool {spec.name!r} has no executor")
        with self._lock:
            self._specs[spec.name] = spec
        return spec

    def register_tool(
        self,
        name: str,
        description: str,
        executor: Callable[..., Any],
        *,
        risk: RiskLevel = RiskLevel.SAFE,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
        permissions: Sequence[str] = (),
        tags: Sequence[str] = (),
    ) -> ToolSpec:
        """Convenience wrapper around :meth:`register`."""
        return self.register(
            ToolSpec(
                name=name,
                description=description,
                executor=executor,
                risk=risk,
                input_schema=input_schema or {},
                output_schema=output_schema or {},
                timeout=timeout,
                permissions=tuple(permissions),
                tags=tuple(tags),
            )
        )

    def register_provider(self, provider: ToolProvider) -> int:
        """Register every tool a provider offers. Returns how many were added."""
        added = 0
        for spec in provider.tools():
            self.register(spec)
            added += 1
        return added

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._specs.pop(name, None) is not None

    def specs(self) -> Sequence[ToolSpec]:
        with self._lock:
            return tuple(self._specs.values())

    def spec(self, name: str) -> ToolSpec | None:
        with self._lock:
            return self._specs.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._specs)

    @staticmethod
    def _approval_needed(
        name: str, risk: RiskLevel, request: PermissionRequest | None,
    ) -> str:
        """Say what is blocked and what the operator can do about it.

        The old wording -- "retry with confirm=True" -- described a flag no
        caller in the tree ever set, so a blocked capability looked like a bug
        rather than a decision. Whoever reads this is usually watching a tool
        not run and wondering why, so the message names the capability, the
        risk, and the one setting that changes it.
        """
        what = f"{name} (risk={risk.name.lower()}"
        if request is not None:
            what += f", capability {request.capability}"
        return (
            f"{what}) needs your approval before it can run, and there is no "
            f"one here to ask, so it was not run. "
            "Set permissions.auto_confirm = true in settings.toml to approve "
            "this class of action without asking, or lower the request."
        )

    # -- invocation ----------------------------------------------------
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
        args = dict(arguments or {})
        spec = self.spec(name)

        # One place to emit from, so every tool.* event carries the same
        # correlation fields. A trace where tool.* lacks the session id but
        # permission.* has it is not a usable trace.
        def emit(name_: str, **fields: Any) -> None:
            self.bus.emit(
                name_, trace_id=trace_id, session_id=session_id,
                agent=fields.pop("agent", agent), **fields,
            )

        if spec is None:
            emit(Events.TOOL_FAILED, tool=name, error="unknown tool")
            return ToolResult(ok=False, tool=name, error=f"unknown tool: {name}")

        # The gate. Nothing runs before this returns ALLOWED.
        #
        # Every capability the spec *declares* is checked, not the tool's own
        # name. Those differ for exactly the tools whose policy most needs to
        # apply: `system.open` declares `app.launch`, `web.open` declares
        # `browser.open`, `web.search` declares `browser.search`. Matching on the
        # name found no such policy, fell through to a default derived from the
        # spec's own risk, and so silently ignored every rule the operator had
        # written for those capabilities -- including the one that decides they
        # need approval at all.
        #
        # Tools that declare no permissions are still matched on their name, so
        # nothing about them changes.
        capabilities = spec.permissions or (name,)

        denied = False
        needs_confirmation = False
        awaiting: PermissionRequest | None = None
        first: PermissionRequest | None = None
        for capability in capabilities:
            candidate = PermissionRequest(
                capability=capability,
                risk=spec.risk,
                agent=agent,
                session_id=session_id,
                trace_id=trace_id,
                reason=spec.description,
                arguments=args,
            )
            if first is None:
                first = candidate
            decision = self.gate.check(candidate, emit=emit)
            if decision is PermissionDecision.DENIED:
                # A denial anywhere refuses the whole call. It is the strictest
                # answer and must not be overridable by an approval elsewhere.
                denied = True
                break
            if decision is PermissionDecision.CONFIRMATION_REQUIRED:
                # Sticky: a later ALLOWED must not erase the outstanding request.
                needs_confirmation = True
                awaiting = candidate

        if denied:
            emit(Events.TOOL_FAILED, tool=name, error="permission denied")
            return ToolResult(
                ok=False, tool=name,
                error=f"permission denied for {name} (risk={spec.risk})",
            )
        if needs_confirmation and not confirm:
            approved = False
            if confirmer is not None and awaiting is not None:
                try:
                    approved = bool(confirmer(awaiting))
                except Exception:  # noqa: BLE001 - a broken prompt is a refusal
                    approved = False
            if not approved:
                emit(Events.TOOL_FAILED, tool=name, error="confirmation required")
                return ToolResult(
                    ok=False, tool=name,
                    error=self._approval_needed(name, spec.risk, awaiting),
                )

        emit(
            Events.TOOL_STARTED, tool=name,
            arguments=(first or PermissionRequest(name, spec.risk)).redacted()["arguments"],
        )
        started = time.perf_counter()

        # Quarantine this tool only, before taking a slot, so the answer is
        # immediate rather than a queue behind threads that will never finish.
        # Every other tool is unaffected -- see the note on _MAX_HUNG.
        with self._lock:
            stranded = len(self._hung.get(name, ()))
        if stranded >= _MAX_HUNG:
            emit(Events.TOOL_FAILED, tool=name, error="tool quarantined")
            return ToolResult(
                ok=False, tool=name,
                error=(
                    f"{name} has left {stranded} calls still running after "
                    f"timing out, so it was not started again. Restart Alan to "
                    f"clear them. Other tools are unaffected. (This is a stuck "
                    f"tool, not a permission problem.)"
                ),
            )

        # One thread per call, on purpose. A shared worker pool means a tool that
        # ignores its timeout permanently consumes one of a fixed number of
        # workers, and enough of those turns the whole registry into a queue:
        # every later tool times out waiting for a thread that is never going to
        # free up. Python cannot interrupt a thread, so a hung call must be able
        # to hurt only itself. The semaphore still bounds how many run at once.
        self._live.acquire()
        done = threading.Event()
        outcome: list[tuple[bool, Any]] = []

        def runner() -> None:
            me = threading.current_thread()
            try:
                outcome.append((True, spec.executor(**args)))
            except BaseException as exc:  # noqa: BLE001 - relayed to the caller
                outcome.append((False, exc))
            finally:
                # Only if we are still counted as stranded: a call that finished
                # normally must not decrement a counter it was never added to.
                with self._lock:
                    group = self._hung.get(name)
                    if group is not None and me in group:
                        group.discard(me)
                        if not group:
                            del self._hung[name]
                done.set()

        worker = threading.Thread(
            target=runner, name=f"alan-tool:{name}", daemon=True
        )
        try:
            worker.start()
            finished = done.wait(spec.timeout)
            if not finished:
                # Abandoned, not killed. Record it so the ceiling above can see
                # the accumulation, and report honestly: the caller is told the
                # tool timed out, never that it was stopped.
                with self._lock:
                    if not done.is_set():
                        self._hung.setdefault(name, set()).add(worker)
                duration = (time.perf_counter() - started) * 1000
                emit(
                    Events.TOOL_FAILED, tool=name, duration_ms=duration,
                    error="timeout",
                )
                return ToolResult(
                    ok=False, tool=name, duration_ms=duration,
                    error=f"tool {name} timed out after {spec.timeout}s",
                )
            succeeded, value = outcome[0]
            if not succeeded:
                raise value
            output = value
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            emit(
                Events.TOOL_FAILED, tool=name, duration_ms=duration,
                error=type(exc).__name__,
            )
            return ToolResult(
                ok=False, tool=name, duration_ms=duration,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            # Released by the *caller* on every path, including the timeout that
            # walks away from the thread. A worker releasing its own slot could
            # double-release, and a hung thread releasing it would hand out a
            # slot that is still occupied.
            self._live.release()

        duration = (time.perf_counter() - started) * 1000
        emit(Events.TOOL_FINISHED, tool=name, duration_ms=duration,
             result=_preview(output))
        return ToolResult(ok=True, output=output, tool=name, duration_ms=duration)

    # -- prompt rendering ----------------------------------------------
    def catalog(self, allowed: Sequence[str] | None = None) -> str:
        """Render the tool catalog for a system prompt.

        An empty/absent ``allowed`` list renders nothing, so an agent can never
        be handed the full catalog by accident.
        """
        if not allowed:
            return ""
        wanted = set(allowed)
        rows = [
            f"- {spec.name}: {spec.description}"
            for spec in self.specs()
            if spec.name in wanted
        ]
        return "Available tools:\n" + "\n".join(rows) if rows else ""

    def close(self) -> None:
        """Release the registry. Safe to call more than once.

        There is deliberately nothing to shut down. Tool calls run on their own
        daemon threads so a hung one cannot take the others down with it, and a
        daemon thread is ended by the process rather than by us -- there is no
        safe way to interrupt one, and pretending otherwise is what made an
        earlier version of this unable to exit.
        """
