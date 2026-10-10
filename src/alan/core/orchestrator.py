"""The Alan Orchestrator.

This is the one place a user request passes through, and the implementation of
brief section 8:

    request -> trace -> memory retrieval -> prior lessons -> intent/plan
    -> capability selection -> permission gate -> execution -> observation
    -> recovery -> memory decision -> response

Design rules that matter:

* **The gate is never skipped.** Every capability the plan wants is checked
  before it runs; a denial degrades the plan instead of aborting the request.
* **The model is chosen, not assumed.** All generation goes through the router,
  which may fail over between backends.
* **Memory is decided, not dumped.** Only what the run proposes *and* clears
  the importance bar is persisted, so model output cannot silently become
  permanent memory.
* **Nothing raises at the boundary.** Callers get an :class:`AgentResult` with
  ``success=False`` and a reason, because a chat turn must degrade rather than
  crash.
* **A failure is remembered.** What did not work, why, and what eventually did
  are recorded by :mod:`.lessons` and shown to the next attempt at the same
  task, so a second try starts where the first one stopped instead of walking
  the same ground again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..interfaces.agent_runtime import Agent, AgentContext, AgentRuntime
from ..interfaces.memory import MemoryProvider
from ..interfaces.model import UnavailableModel
from ..interfaces.types import (
    AgentResult,
    MemoryItem,
    MemoryLevel,
    MemoryType,
    PermissionRequest,
)
from ..models.router import ModelRouter, NoModelAvailable, RoutingRequest
from ..permissions.gate import PermissionGate
from ..tools.registry import ScopedTools, ToolRegistry
from .events import EventBus, Events, new_trace
from .lessons import LessonStore, render_hint
from .planner import HeuristicPlanner, Plan, Planner, Step

#: Steps whose absence degrades the answer rather than failing the request.
#: ``answer`` is in here because it is satisfied by the model itself: there is
#: no tool to be missing, so a missing one is not a reason to report failure.
_SOFT_STEPS = {
    "code",
    "review_diff",
    "analyze_repo",
    "summarize",
    "search_web",
    "answer",
    "run_tests",
}

#: Plan actions that name a capability, and the tool that provides it. Plan
#: actions name a *capability*; tools are named differently, and the mapping is
#: explicit so an unmapped action is a visible bug rather than a silent no-op.
#:
#: Module level and complete on purpose: another surface -- the streaming
#: assistant -- has to be able to ask "does this request need a tool?" before it
#: decides whether to stream or to plan, and it must not answer by consulting a
#: second, separately-maintained list.
_CAPABILITY_HANDLERS = {
    "search_web": "web.search",
    "open_target": "system.open",
    "analyze_repo": "code.analyze",
    "code": "code.edit",
    "run_tests": "code.test",
    "review_diff": "code.review",
    # Alan's coding role is delegation and reporting: no plan step writes code
    # through Alan's own model any more. `vibecoder_report` maps to a tool rather
    # than being a bare marker so the report is something the run actually performs
    # rather than something the model decides to add later -- and being mapped also
    # means a delegating request always counts as needing a capability, so the
    # streaming assistant escalates to the full pipeline instead of answering it
    # conversationally and never invoking Vibecoder.
    "vibecoder_delegate": "vibecoder.delegate",
    "vibecoder_inspect": "vibecoder.inspect",
    "vibecoder_report": "vibecoder.report",
    # Always-on operation. These are capabilities like any other: the gate
    # checks them, the registry executes them, and `requires_capability`
    # counts them -- so a schedule request escalates to the full pipeline
    # instead of being answered conversationally with a promise to remember.
    "schedule_add": "schedule.add",
    "schedule_list": "schedule.list",
    "schedule_remove": "schedule.remove",
    # ECC skills: loading one is a plan step, not a model tool-call (Alan's
    # model layer has none), so `skill.read` is reached only through this
    # mapping -- and, like every tool, through the gate in the registry.
    "load_skill": "skill.read",
    # The whole-device filesystem. The planner only emits these steps for a
    # request carrying a real path (plus content for a write), so the mapping
    # is what makes that request executable rather than a chat reply about it.
    "fs_read": "filesystem.read",
    "fs_list": "filesystem.list",
    "fs_write": "filesystem.write",
    "fs_delete": "filesystem.delete",
    # The second brain. Mapped like every other capability, so the gate checks
    # them and `requires_capability` counts them -- a question aimed at the
    # user's corpus escalates to the full pipeline instead of being answered
    # conversationally with a promise to go and look.
    "kb_search": "knowledge.search",
    "kb_ingest": "knowledge.ingest",
    "kb_list": "knowledge.list",
    "kb_forget": "knowledge.forget",
}

#: Which observations carry the authoritative record of a delegation, in the form
#: `_execute_step` writes them. Both names are listed because the plan step that
#: produced them differs: a change delegates, an inspection reads, and each would
#: otherwise silently miss the other.
_DELEGATE_MARKERS = ("[vibecoder_delegate] ", "[vibecoder_inspect] ")


@dataclass
class OrchestratorConfig:
    """Tunables for one orchestrator."""

    system_prompt: str = (
        "You are Alan, a helpful, concise AI assistant. Answer clearly and get to the point. If you do not know something, say so plainly."
    )
    top_k: int = 5
    max_context_chars: int = 2000
    memory_threshold: float = 0.6
    allow_autonomous_promotion: bool = False

    auto_recall: bool = True
    auto_remember: bool = True

    # Privacy default: anything touching memory stays on this machine.
    local_only: bool = True

    #: Remember failed attempts and warn the next attempt at the same task.
    #:
    #: Off by default so an orchestrator built by hand -- a test, an embedded
    #: copy -- does not quietly start writing a shared file, and switched on by
    #: :func:`alan.runtime.build_runtime`, which is the wiring with an operator
    #: behind it. Supplying a ``LessonStore`` directly also enables it: passing
    #: one is the caller saying "learn into this".
    learn: bool = False

    #: How many open lessons may reach one prompt. Hints are context, not the
    #: subject, and a request that matches five stale failures should not have
    #: five of them pushed at the model.
    lesson_top_k: int = 2

    #: How to ask a human to approve a capability that needs it. ``None`` means
    #: nobody can be asked, and anything needing approval then fails closed.
    #:
    #: This lives on the config rather than the gate because the gate is shared
    #: with every subagent, so an approval channel stored there would be inherited
    #: by agents that have no human present -- a subagent could raise its own
    #: approval rate by prompting on the operator's behalf. Only the surface that
    #: genuinely has a person in front of it sets this.
    confirmer: Callable[[PermissionRequest], bool] | None = None


class Orchestrator(AgentRuntime):
    """Runs a request end to end."""

    def __init__(
        self,
        router: ModelRouter,
        registry: ToolRegistry,
        *,
        memory: MemoryProvider | None = None,
        gate: PermissionGate | None = None,
        bus: EventBus | None = None,
        planner: Planner | None = None,
        config: OrchestratorConfig | None = None,
        agents: Sequence[Agent] = (),
        lessons: LessonStore | None = None,
    ) -> None:
        self.router = router
        self.registry = registry
        self.memory = memory
        self.gate = gate or registry.gate
        self.bus = bus or registry.bus
        # A router built before the bus cannot emit into the trace. Give it one
        # rather than letting half the events go missing.
        if getattr(router, "bus", None) is None:
            router.bus = self.bus
        self.planner = planner or HeuristicPlanner()
        self.config = config or OrchestratorConfig()
        # `is not None`, never `or`: an empty store is falsy, because `__len__`
        # counts its lessons -- and "nothing recorded yet" is exactly the state
        # a caller-supplied store starts in.
        self.lessons: LessonStore | None = (
            lessons if lessons is not None else (LessonStore() if self.config.learn else None)
        )
        self.agents = {a.name: a for a in agents}
        self.last_plan: Plan | None = None

    def available(self) -> list[str]:
        return ["orchestrator", *sorted(self.agents)]

    def delegate(
        self, ctx: AgentContext, agent: str, request: str, **kwargs: Any
    ) -> AgentResult:
        """Run a subagent. Delegation never widens permissions."""
        target = self.agents.get(agent)
        if target is None:
            return AgentResult(
                output=f"no such agent: {agent}",
                agent=agent,
                success=False,
                error="unknown agent",
            )
        child = AgentContext(
            request=request,
            agent=target.name,
            model=ctx.model,
            # Narrowed structurally, not just declared: the child sees a view of
            # the parent's invoker that cannot exceed the intersection of what
            # both agents are allowed, nor raise the ceiling. Passing the parent's
            # invoker straight through -- which this used to do -- left the
            # allow-list as something the child was trusted to remember.
            tools=ScopedTools(
                ctx.tools,
                target.tools & ctx.allowed_tools,
                min(target.ceiling, ctx.ceiling),
            ),
            memory=ctx.memory,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            allowed_tools=frozenset(target.tools & ctx.allowed_tools),
            ceiling=min(target.ceiling, ctx.ceiling),
            emit=ctx.emit,
        )
        self.bus.emit(
            Events.SUBAGENT_STARTED,
            trace_id=ctx.trace_id,
            agent=target.name,
            request=request[:120],
        )
        result = target.run(child)
        self.bus.emit(
            Events.SUBAGENT_FINISHED,
            trace_id=ctx.trace_id,
            agent=target.name,
            success=result.success,
        )
        return result

    def run(
        self,
        request: str,
        *,
        agent: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        remember: bool = True,
    ) -> AgentResult:
        trace = trace_id or new_trace()
        self.bus.emit(
            Events.REQUEST_STARTED,
            trace_id=trace,
            session_id=session_id,
            request=request[:200],
        )

        # 1. Retrieve memory before planning: the plan may not need it, but a
        #    planner that cannot see what is already known plans badly.
        recalled, recalled_count = self._recall(request, trace, session_id)

        # 1b. What already failed for a task like this one, read *before*
        #     planning: a hint that arrives with the observations is advice
        #     about work already done, and this is advice about work not to
        #     start. One lookup, used twice -- the planner gets it as context
        #     and the model gets it in the system prompt, because the heuristic
        #     planner discards context and the model-facing copy is the one
        #     that has to carry it.
        lesson_block = self.lesson_hint(request)

        # 2. Plan.
        plan = self.planner.plan(request, context=_join(recalled, lesson_block))
        self.last_plan = plan
        self.bus.emit(
            Events.PLAN_CREATED, trace_id=trace, session_id=session_id,
            intent=plan.intent, steps=plan.actions(), category=plan.category.value,
        )

        # What did not deliver during this run, as (stage, reason) pairs. It is
        # the input to the lesson, and it is collected here rather than recorded
        # where it happens so that one run produces exactly one record: a pass
        # that broke three steps must not be counted as three attempts.
        broken: list[tuple[str, str]] = []

        # 3. A named agent runs the whole request itself: it has its own
        #    tool list and its own ceiling, so routing it through the generic
        #    path would throw both away.
        if agent and agent in self.agents:
            return self._run_named_agent(
                agent, request, plan, recalled, trace, session_id
            )

        private = self._privacy_required(request, plan)
        routing = RoutingRequest(
            category=plan.category,
            complexity=plan.complexity,
            local_only=private and self.config.local_only,
        )

        try:
            model = self.router.route(routing)
        except NoModelAvailable as exc:
            # A private request with no local model must not silently fall
            # through to a cloud one. Say what happened and how to allow it.
            if private and self.config.local_only:
                return self._fail(
                    trace, session_id, request, plan,
                    f"model unavailable: {exc} This request touches your memory, so Alan will not send it to a cloud model. Start Ollama (scripts\\run.ps1) or set permissions.local_only = false to allow it deliberately.",
                )
            return self._fail(
                trace, session_id, request, plan, f"model unavailable: {exc}"
            )

        # The operator has already said local_only = false and this request is
        # private. It will go out. Record that it did, so the trace shows it.
        if private and not self.config.local_only and not model.local:
            self.bus.emit(
                Events.MODEL_STARTED,
                trace_id=trace,
                session_id=session_id,
                agent="orchestrator",
                model=model.describe(),
                note="private request sent to a remote model by operator setting",
            )

        # 4/5/6. Execute the plan. Capabilities that need a tool are invoked
        #        through the registry, which gates them; a denial becomes an
        #        observation and the run continues.
        observations: list[str] = []
        steps_run: list[str] = []
        for step in plan.steps:
            # Memory actions were already decided above; re-running them here
            # would recall twice and remember before the answer exists.
            if step.action in ("recall", "remember", "forget"):
                continue
            outcome = self._execute_step(
                step, request, model, trace, session_id, observations, plan, broken,
            )
            steps_run.append(step.action)
            if outcome is False:
                self.bus.emit(
                    Events.RECOVERY_APPLIED,
                    trace_id=trace,
                    step=step.action,
                    # "Degraded", not "skipped": a step that ran and came back
                    # empty (a delegation with outstanding work) did not get
                    # skipped, it failed to deliver, and that is the version an
                    # operator reading the trace needs.
                    action="capability degraded",
                )

        # 7. Answer.
        system = self._system_prompt(plan, recalled, observations, lesson_block)
        messages = [{"role": "user", "content": request}]
        try:
            reply, used = self.router.complete(
                messages,
                system=system,
                request=routing,
                trace_id=trace,
            )
        except NoModelAvailable as exc:
            return self._fail(trace, session_id, request, plan, str(exc))

        # 7b. A delegated coding task's report is a record, not a summary. The
        #     model is asked to restate it and drops the parts it finds
        #     inconvenient -- observed live: the file was confirmed changed, and
        #     the reply gave a DONE section with no NOT DONE section at all, even
        #     though three failed reads were sitting right there in the
        #     observation. Verifiable facts are therefore attached to the reply
        #     rather than left to a 1.5b model to rephrase, so "what is done and
        #     what is not" cannot be edited out of the answer.
        reply = self._attach_delegation_record(reply, plan, observations)

        # 7c. Exactly one lesson for this run: a step that did not deliver, or
        #     an attempt that worked. A reply produced while a step came back
        #     empty is not evidence the task is solved, so it is never credited
        #     as a success -- that is what keeps the lesson open for the next
        #     attempt instead of closing it on a half-answer.
        if broken:
            self._note_failure(
                request, plan, trace, stage=broken[0][0], cause=_reasons(broken)
            )
        else:
            self._note_success(request, plan, trace, used.name)

        # 8. Memory decision. After the reply: storing "I will look that up" is
        #    not a fact about the user.
        written = self._memory_decision(
            request, reply, plan, trace, session_id, remember,
        )

        result = AgentResult(
            output=reply,
            agent=used.name,
            success=True,
            steps=steps_run,
            artifacts={
                "plan": plan.to_dict(),
                "model": used.describe(),
                "recalled": recalled_count,
                "observations": observations,
                "trace_id": trace,
            },
            memory_writes=written,
        )
        self.bus.emit(
            Events.REQUEST_FINISHED,
            trace_id=trace,
            session_id=session_id,
            success=True,
            agent=used.name,
            steps=steps_run,
        )
        return result

    def requires_capability(self, request: str) -> bool:
        """Whether this request needs a tool, rather than only the model.

        Answers from the deterministic plan, so it is safe to call before
        committing to a code path. It is the reason the streaming assistant can
        skip planning for a conversation without also skipping tools for a
        command: "tell me about X" needs neither, "open X" needs one, and both
        arrive through the same entry point.
        """
        try:
            plan = self.planner.plan(request)
        except Exception:  # noqa: BLE001 - planning must not break a chat turn
            return False
        return any(step.action in _CAPABILITY_HANDLERS for step in plan.steps)

    def _recall(
        self, request: str, trace: str, session_id: str | None
    ) -> tuple[str, int]:
        """Return ``(context_block, hit_count)``.

        One search, not two. This used to call ``search`` for the count and
        then ``context_block``, which searched again -- two round trips per
        turn against cognee, on a store where a search is slow enough to be
        felt.
        """
        if self.memory is None or not self.config.top_k:
            return "", 0
        if not self.config.auto_recall:
            return "", 0
        try:
            candidates = self.memory.search(request, top_k=self.config.top_k)
        except Exception:
            return "", 0
        if not candidates:
            return "", 0

        # The backend proposes; this decides. A backend that returns passages
        # with no overlap with the question must not get to assert them.
        from ..memory.relevance import gate

        hits = gate(request, candidates)
        if not hits:
            self.bus.emit(
                Events.MEMORY_RETRIEVED,
                trace_id=trace,
                session_id=session_id,
                hits=0,
                discarded=len(candidates),
            )
            return "", 0
        self.bus.emit(
            Events.MEMORY_RETRIEVED,
            trace_id=trace,
            session_id=session_id,
            hits=len(hits),
            discarded=len(candidates) - len(hits),
        )
        return (
            self.memory.context_block(
                request,
                top_k=self.config.top_k,
                max_chars=self.config.max_context_chars,
                hits=hits,
            ),
            len(hits),
        )

    def lesson_hint(self, request: str, *, trace: str = "") -> str:
        """What earlier attempts at a task like this one got wrong.

        Public because the streaming assistant answers without planning, and it
        would otherwise be the one path that can never learn: a conversation
        that reaches for a missing tool should not have to fail twice to find
        out it is missing.

        A store that cannot be read returns an empty string. This is asked for
        on the hot path of every reply, and the price of forgetting a lesson
        for one turn is one wasted attempt -- not a failed conversation.
        """
        if self.lessons is None:
            return ""
        try:
            found = self.lessons.recall(request, top_k=self.config.lesson_top_k)
        except Exception:
            return ""
        if not found:
            return ""
        block = render_hint(found)
        if not block:
            return ""
        self.bus.emit(
            Events.LESSON_RECALLED,
            trace_id=trace or None,
            matches=len(found),
            task=found[0].task[:80],
        )
        return block

    def _note_failure(
        self, request: str, plan: Plan, trace: str, *, stage: str, cause: str
    ) -> None:
        """Record an attempt that did not come off. Never raises.

        The reason this is wrapped: a broken store must not be able to turn a
        degraded answer into a crashed one. Forgetting a lesson is the same
        outcome as never having learned it.
        """
        if self.lessons is None:
            return
        try:
            lesson = self.lessons.note_failure(
                request, stage=stage, cause=cause, actions=plan.actions()
            )
        except Exception:  # noqa: BLE001 - a lesson is never worth the answer
            return
        self.bus.emit(
            Events.LESSON_RECORDED,
            trace_id=trace,
            outcome="failed",
            stage=lesson.last_stage,
            task=lesson.task[:80],
            failures=lesson.failures,
            successes=lesson.successes,
        )

    def _note_success(
        self, request: str, plan: Plan, trace: str, model: str = ""
    ) -> None:
        """Credit an attempt at a task that has failed before.

        Returns quietly when the task has never failed: those are recorded
        nowhere, which is what keeps the store holding only the things worth
        remembering.
        """
        if self.lessons is None:
            return
        try:
            lesson = self.lessons.note_success(
                request, actions=plan.actions(), model=model
            )
        except Exception:  # noqa: BLE001 - same contract as above
            return
        if lesson is None:
            return
        self.bus.emit(
            Events.LESSON_RECORDED,
            trace_id=trace,
            outcome="recovered",
            task=lesson.task[:80],
            failures=lesson.failures,
            successes=lesson.successes,
            approach=lesson.winner,
        )

    def _execute_step(
        self,
        step: Step,
        request: str,
        model: Any,
        trace: str,
        session_id: str | None,
        observations: list[str],
        plan: Plan,
        broken: list[tuple[str, str]],
    ) -> bool:
        """Run one plan step that needs a capability. False = degraded.

        Anything that does not deliver is appended to ``broken`` as
        ``(stage, reason)``. The caller owns the recording: a step knows it
        failed, it does not know whether the run as a whole is a failure yet,
        and counting attempts where the step stands would inflate every lesson
        by the number of steps in the plan.
        """
        # Plan actions name a *capability*; tools are named differently. The
        # mapping is explicit so an unmapped action is a visible bug rather
        # than a silent no-op.
        handler = _CAPABILITY_HANDLERS.get(step.action)
        if handler is None:
            if step.action in _SOFT_STEPS:
                return True
            broken.append(
                (f"step:{step.action}", f"no tool provides the '{step.action}' capability")
            )
            return False

        spec = self.registry.spec(handler)
        if spec is None:
            observations.append(f"[{step.action}] capability unavailable")
            broken.append(
                (f"step:{step.action}", f"the '{handler}' tool is not registered")
            )
            return False

        arguments = dict(step.arguments) or {}
        arguments.setdefault("query", request)
        # `vibecoder.delegate` names its input `task`, not `query`. The plan
        # supplies it, but defaulting it here as well means a hand-written or
        # model-produced step that omits the wording still delegates the user's
        # actual request rather than an empty string. An empty task would produce
        # a clean-looking no-op report, which is the worst possible outcome.
        if handler in ("vibecoder.delegate", "vibecoder.inspect"):
            arguments.setdefault("task", request)

        # Only pass what the tool actually declares. Without this a plan step
        # can hand an executor a key it never asked for.
        arguments = _filter_arguments(spec, arguments)
        result = self.registry.invoke(
            handler,
            arguments,
            agent="orchestrator",
            session_id=session_id,
            trace_id=trace,
            # Only the main path carries an approval channel. Subagents reach
            # the same registry but never pass one, so an agent with no human to
            # ask fails closed on anything that needs approval.
            confirmer=self.config.confirmer,
        )
        if result.ok:
            # A tool that returns a rendered `report` is trying to be read by a
            # model, and a Python dict repr is the worst way to show it: the 1.5b
            # model reads `{...}` as JSON-ish noise and then describes what it
            # wishes had happened. Observed in a live run where the file had
            # demonstrably changed on disk and the reply still opened with
            # "Vibecoder claimed:". Prefer the rendered report when there is one;
            # structured output remains available to any other consumer.
            rendered = self._render(result.output)
            # A delegation with outstanding work ran, but it did not do what was
            # asked. Counting it as a broken step is what stops the run being
            # credited as a success: closing a lesson on a half-done task would
            # leave the next attempt *less* informed than this one.
            outstanding = _outstanding(result.output)
            observations.append(f"[{step.action}] {rendered}")
            if outstanding:
                broken.append(
                    (
                        f"step:{step.action}",
                        f"Vibecoder reported work outstanding: {outstanding}",
                    )
                )
                return False
            return True
        observations.append(f"[{step.action}] unavailable: {result.error}")
        broken.append((f"step:{step.action}", f"{result.error or 'the tool failed'}"))
        return False

    @staticmethod
    def _render(output: Any) -> str:
        """Text for the model's prompt: the rendered report, plus a clear verdict.

        The verdict line exists because of a specific failure. A live run had
        changed a file on disk, confirmed by mtime, and the reply still opened
        "Vibecoder claimed:" -- the model read a Python dict as noise and
        described what it wished had happened. A plain-English verdict at the top,
        written in the words the report itself will use, is much harder to
        get backwards than a structured field it has to infer from.
        """
        if not isinstance(output, dict):
            return str(output)

        report = output.get("report")
        if not isinstance(report, str) or not report.strip():
            return json.dumps(output, default=str)

        # The verdict is about files a delegation may have changed, so it is
        # keyed on a delegation's own fields rather than on "a report exists".
        # Every other report-shaped tool -- knowledge search, ingest, list --
        # returns `report` alone, and stamping "Vibecoder may have only
        # claimed" over a block of cited passages tells the model its own
        # citations are unverified (seen live: `alan run "search my notes"`
        # answered by describing the evidence as a claim it could not trust).
        verified = output.get("verified")
        not_done = output.get("not_done")
        if not isinstance(verified, list) and not isinstance(not_done, list):
            return report.strip()

        if verified:
            names = ", ".join(str(v.get("path")) for v in verified)
            verdict = (
                f"FILE CHANGES CONFIRMED ON DISK: {names}. "
                "State these as done, and say they are verified."
            )
        elif not_done:
            verdict = "NO FILE CHANGE WAS CONFIRMED. Do not report any as made."
        else:
            verdict = (
                "NO FILE CHANGE WAS CONFIRMED. Vibecoder may have only read "
                "or only claimed; do not report any change as made."
            )
        return f"{verdict}\n\n{report.strip()}"

    def _attach_delegation_record(
        self, reply: str, plan: Plan, observations: Sequence[str]
    ) -> str:
        """Append Vibecoder's own report to the reply when there is one.

        The model is shown the report and asked to restate it. It writes an
        answer shaped like the contract but thinner, and the parts it drops are
        always the unflattering ones -- in the first live run it produced a DONE
        section alone while three failed reads went unmentioned. Attaching the
        record means the user gets the real thing whether or not the model is
        good at summarising.

        Not attached when the reply already contains the report's first line,
        because then the model did restate it and appending would double it.
        """
        if not plan.intent.startswith("vibecoder_"):
            return reply

        record = ""
        for marker in _DELEGATE_MARKERS:
            for observation in observations:
                text = str(observation)
                if text.startswith(marker):
                    record = text[len(marker):].strip()
                    break
            if record:
                break
        if not record:
            return reply

        # The observation opens with a verdict line written *for the model* --
        # "State these as done, and say they are verified." Appending that
        # verbatim would put an instruction to the model into the answer the user
        # reads. The report proper follows it, and that is what gets attached.
        parts = record.split("\n", 1)
        body = parts[1].strip() if len(parts) > 1 else record
        if not body:
            return reply

        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        if first and first in reply:
            return reply

        return (
            f"{reply.strip()}\n\n"
            "--- What Vibecoder actually did ---\n"
            f"{body}"
        )

    def _system_prompt(
        self,
        plan: Plan,
        recalled: str,
        observations: list[str],
        lessons: str = "",
    ) -> str:
        parts = [self.config.system_prompt]
        # Before the findings, not among them: a lesson is an instruction about
        # how to treat the rest of this prompt, and advice placed after the
        # evidence it governs is advice the model has already finished acting on.
        if lessons:
            parts.append(lessons)
        if recalled:
            parts.append(recalled)
        if observations:
            parts.append("Findings from this request:\n" + "\n".join(observations))
        # Shown as context, never as a menu. The steps have already run by the
        # time this prompt is built, and nothing on the answer path can execute
        # a tool -- `router.complete` carries no tool plumbing at all -- so a
        # list headed "Available tools:" reads as an invitation. Seen live:
        # `alan run "index this folder"` finished by emitting a raw
        # `to=knowledge.ingest` call as its reply, leaving the user to read
        # tool syntax as prose. The same catalogue, headed as a capability that
        # is already spent, still tells the model what was in scope without
        # offering it a call to make.
        catalogue = self.registry.catalog(_allowed_for(plan))
        if catalogue:
            parts.append(
                "Capabilities in scope for this request (informational only: "
                "the steps already ran and no tool can be called from this "
                "message, so reply in plain prose):\n"
                + catalogue.removeprefix("Available tools:")
            )
        return "\n\n".join(p for p in parts if p)

    def _memory_decision(
        self,
        request: str,
        reply: str,
        plan: Plan,
        trace: str,
        session_id: str | None,
        remember: bool,
    ) -> list[MemoryItem]:
        """Decide what, if anything, becomes memory (brief section 2/10)."""
        if self.memory is None or not remember or not self.config.auto_remember:
            return []
        explicit = plan.intent == "memory" and "remember" in plan.actions()
        written: list[MemoryItem] = []

        candidates: list[tuple[str, float]] = []
        if explicit:
            candidates.append((request, 1.0))
        elif _is_durable_fact(request):
            candidates.append((request, self.config.memory_threshold))

        for content, importance in candidates:
            item = MemoryItem(
                content=content,
                level=MemoryLevel.L2_SESSION,
                type=MemoryType.FACT,
                source="user",
                importance=importance,
                session_id=session_id,
            )
            try:
                self.memory.remember(item)
            except Exception:
                # A failed write must not lose the answer. The run continues
                # without the memory; the user is not told a lie about it.
                continue
            written.append(item)
            self.bus.emit(
                Events.MEMORY_CREATED,
                trace_id=trace,
                session_id=session_id,
                memory_id=item.id,
                level=str(item.level),
            )
        return written

    def store_turn(
        self,
        request: str,
        reply: str,
        *,
        session_id: str | None = None,
        remember: bool = True,
    ) -> list[MemoryItem]:
        """Apply the memory decision to one already-completed turn.

        Public because the streaming chat path answers the model directly, for
        latency, and must not invent a second, laxer rule for what gets stored.
        One decision gate, two callers.
        """
        plan = self.planner.plan(request)
        return self._memory_decision(
            request, reply, plan, "", session_id, remember
        )

    def _privacy_required(self, request: str, plan: Plan) -> bool:
        """User facts and memory operations stay on this machine."""
        if plan.intent == "memory":
            return True
        return _is_durable_fact(request)

    def _run_named_agent(
        self,
        name: str,
        request: str,
        plan: Plan,
        recalled: str,
        trace: str,
        session_id: str | None,
    ) -> AgentResult:
        agent = self.agents[name]

        try:
            model = self.router.route(
                RoutingRequest(category=plan.category, complexity=plan.complexity)
            )
        except NoModelAvailable as exc:
            # An agent that only inspects state can still run offline. One that
            # needs to generate cannot, and saying so beats failing obscurely.
            if agent.needs_model:
                return self._fail(trace, session_id, request, plan, str(exc))
            model = UnavailableModel(
                str(exc).split(".")[0] or "no model available"
            )
        ctx = AgentContext(
            request=request,
            agent=name,
            model=model,
            tools=ScopedTools(self.registry, agent.tools, agent.ceiling),
            memory=self.memory,
            session_id=session_id,
            trace_id=trace,
            allowed_tools=frozenset(agent.tools),
            ceiling=agent.ceiling,
            emit=lambda n, **f: self.bus.emit(
                n, trace_id=trace, session_id=session_id, **f
            ),
        )
        self.bus.emit(
            Events.AGENT_STARTED, trace_id=trace, session_id=session_id, agent=name
        )
        self.bus.emit(
            Events.SUBAGENT_STARTED,
            trace_id=trace,
            session_id=session_id,
            agent=name,
            request=request[:120],
        )
        result = self.agents[name].run(ctx)
        # Exactly one record for this run: either the agent ran, or the model
        # it needed was unavailable and `_fail` has already said so. Recording
        # here as well would count a single request twice.
        if result.success:
            self._note_success(request, plan, trace, name)
        else:
            self._note_failure(
                request,
                plan,
                trace,
                stage=f"agent:{name}",
                cause=result.error or str(result.output)[:300] or "the agent failed",
            )
        self.bus.emit(
            Events.AGENT_FINISHED,
            trace_id=trace,
            session_id=session_id,
            agent=name,
            success=result.success,
        )
        self.bus.emit(
            Events.SUBAGENT_FINISHED,
            trace_id=trace,
            session_id=session_id,
            agent=name,
            success=result.success,
        )
        return result

    def _fail(
        self,
        trace: str,
        session_id: str | None,
        request: str,
        plan: Plan,
        reason: str,
    ) -> AgentResult:
        # One lesson per run, and the run-level reason is the one recorded: it
        # is the answer to "why did I get nothing", which is the thing the next
        # attempt has to avoid. Steps that degraded on the way there are already
        # named by the plan, and counting them separately would inflate a single
        # request into several attempts.
        self._note_failure(request, plan, trace, stage="run", cause=reason)
        self.bus.emit(
            Events.REQUEST_FINISHED,
            trace_id=trace,
            session_id=session_id,
            success=False,
            error=reason,
        )
        return AgentResult(
            output="",
            agent="orchestrator",
            success=False,
            error=reason,
            artifacts={"plan": plan.to_dict(), "trace_id": trace},
        )

    def trace(self, trace_id: str) -> list[dict]:
        return [e.to_dict() for e in self.bus.trace(trace_id)]


def _filter_arguments(spec: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep only the arguments a tool's input schema declares.

    A tool with no declared properties accepts nothing; that is safer than
    guessing, and it means an unknown key can never reach an executor.
    """
    properties = (getattr(spec, "input_schema", None) or {}).get("properties")
    if not isinstance(properties, dict) or not properties:
        return {}
    return {key: value for key, value in arguments.items() if key in properties}


def _join(*parts: str) -> str:
    """Non-empty blocks, in order, separated by blank lines."""
    return "\n\n".join(p for p in parts if p)


def _reasons(broken: Sequence[tuple[str, str]]) -> str:
    """The distinct reasons a run degraded, as one line.

    Distinct because one missing tool is reported by every step that needed
    it, and a lesson that lists the same reason four times reads as four
    separate problems rather than one obstacle met four times.
    """
    return "; ".join(dict.fromkeys(cause for _, cause in broken if cause))[:400]


def _outstanding(output: Any) -> str:
    """A delegation report's ``not_done`` list as one line, or ``""``.

    An empty result means the tool itself says nothing was left undone. That is
    a claim made by a successful run, not an absence of one, which is why this
    reads the list instead of guessing from the report's text.
    """
    if not isinstance(output, dict):
        return ""
    items = output.get("not_done")
    if not isinstance(items, list) or not items:
        return ""
    return "; ".join(str(item) for item in items[:3])[:300]


def _allowed_for(plan: Plan) -> list[str]:
    return {
        "search_web": ["web.search"],
        "coding": ["code.analyze", "code.edit", "code.test", "code.review"],
        # Delegation plans see the Vibecoder tools so the model can describe the
        # division of labour rather than claim to have written the code itself.
        "vibecoder_delegate": ["vibecoder.delegate", "vibecoder.report"],
        "vibecoder_inspect": ["vibecoder.inspect", "vibecoder.report"],
        "schedule": ["schedule.add", "schedule.list", "schedule.remove"],
        "schedule_list": ["schedule.list", "schedule.remove"],
        "schedule_remove": ["schedule.remove", "schedule.list"],
        # A skill plan sees the loader plus the two discovery tools: the entry
        # is usually already named, but a near-miss needs search to correct it.
        "use_skill": ["skill.read", "skill.search", "skill.list"],
        # A filesystem plan runs exactly the one operation it was built for:
        # exposing all four would invite a step to drift from its intent.
        "filesystem": ["filesystem.read", "filesystem.list",
                       "filesystem.write", "filesystem.delete"],
        # A second-brain plan sees only the operation it was built for: the
        # search and ingest catalogs stay narrow so a step cannot silently
        # become a destructive forget or an unrelated index.
        "knowledge_search": ["knowledge.search"],
        "knowledge_ingest": ["knowledge.ingest"],
    }.get(plan.intent, [])


def _is_durable_fact(text: str) -> bool:
    """True for statements that stay true tomorrow.

    Deliberately narrow: it matches explicit user statements about themselves
    or their preferences, not questions, not transient task chatter.
    """
    lowered = (text or "").lower().strip()
    if not lowered or "?" in lowered:
        return False
    markers = (
        "i prefer",
        "i like",
        "i always",
        "i never",
        "my name is",
        "i work",
        "i use",
        "call me",
        "remember that",
        "keep in mind",
        "from now on",
        "my timezone",
        "i am a",
        "i'm a",
    )
    return lowered.startswith(markers) or any(m in lowered for m in markers)