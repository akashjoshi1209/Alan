"""Schedule tools: how a conversation becomes background work.

Three capabilities, all talking to one :class:`~alan.core.scheduler.Scheduler`:

    schedule.add       parse a timing phrase, create the task
    schedule.list      show what is scheduled (also used by /activity)
    schedule.remove    cancel by id or by a phrase that matches the request

Two design rules, both learned from the audit:

* **The confirmation is in the reply.** ``schedule.add`` returns the parsed
  interpretation ("every day at 07:30 (local time)") together with the task
  id, so the user can see exactly what was understood and cancel it by id if
  it was wrong. A scheduler that silently misreads "every morning" as "every
  second" and only reveals it when the machine wakes at 3am is the bug.
* **Cancellation must be as easy as creation.** Every reply that contains a
  task id says how to remove it; ``schedule.remove`` accepts a natural phrase
  as well as an id, because people remember "the news one", not ``tsk_9f2a``.

The tools return plain strings, not dicts: they are read by a 1.5b model in
the observation block, and structured output renders as JSON noise there.
"""

from __future__ import annotations

from typing import Sequence

from ..core.scheduler import Scheduler, parse_when
from ..interfaces.tools import ToolProvider
from ..interfaces.types import RiskLevel, ToolSpec

#: How a task id may also be matched: by exact id, or by a case-insensitive
#: phrase contained in the task's request.
REMOVE_MATCH_NOTE = "cancel it with: /schedule remove <id>"


class ScheduleTools(ToolProvider):
    """The scheduler as the model sees it."""

    name = "schedule"

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler

    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def tools(self) -> Sequence[ToolSpec]:
        return (
            self._add(),
            self._list(),
            self._remove(),
        )

    # -- specs -----------------------------------------------------------

    def _add(self) -> ToolSpec:
        return ToolSpec(
            name="schedule.add",
            description=(
                "Create a background task that runs later or repeatedly. "
                "Reads like: 'every 30 minutes', 'every day at 07:30', "
                "'every morning', 'at 18:45', 'tomorrow at 09:00', "
                "'in 2 hours', 'daily', 'weekly at 09:00'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "what should run, as a plain request",
                    },
                    "when": {
                        "type": "string",
                        "description": "the timing phrase, e.g. 'every day at 07:30'",
                    },
                },
                "required": ["task", "when"],
            },
            output_schema={"type": "string"},
            risk=RiskLevel.LOW_RISK,
            permissions=("schedule.manage",),
            timeout=15.0,
            executor=self._add_task,
            tags=("schedule", "write"),
        )

    def _list(self) -> ToolSpec:
        return ToolSpec(
            name="schedule.list",
            description="List scheduled background tasks with status and next run.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            output_schema={"type": "string"},
            risk=RiskLevel.SAFE,
            permissions=("schedule.list",),
            timeout=15.0,
            executor=self._list_tasks,
            tags=("schedule", "read"),
        )

    def _remove(self) -> ToolSpec:
        return ToolSpec(
            name="schedule.remove",
            description=(
                "Cancel scheduled task(s). Pass an exact id (tsk_...), or a "
                "phrase from the task's request to cancel matching tasks."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "task id or phrase contained in the request",
                    },
                },
                "required": ["match"],
            },
            output_schema={"type": "string"},
            risk=RiskLevel.LOW_RISK,
            permissions=("schedule.manage",),
            timeout=15.0,
            executor=self._remove_task,
            tags=("schedule", "write"),
        )

    # -- executors -------------------------------------------------------

    def _add_task(self, task: str, when: str = "") -> str:
        spec = parse_when(when or "")
        created = self.scheduler.add(
            task,
            kind=spec.kind,
            every_seconds=spec.every_seconds,
            at_time=spec.at_time if spec.kind == "interval" else "",
            first_at=spec.first_at,
            created_by="tool",
        )
        return (
            f"scheduled task {created.id}: {created.request!r} "
            f"-- {spec.describe()}, next run {created.next_run_at}. "
            f"Say the id and {REMOVE_MATCH_NOTE} to cancel."
        )

    def _list_tasks(self) -> str:
        tasks = self.scheduler.store.list()
        if not tasks:
            return "no scheduled tasks"
        lines = self.scheduler.activity_lines()
        waiting = self.scheduler.waiting()
        if waiting:
            lines.append(
                f"{len(waiting)} task(s) need approval: /approve <id> or "
                f"/reject <id> (see /activity)"
            )
        return "\n".join(lines)

    def _remove_task(self, match: str) -> str:
        match = (match or "").strip()
        if not match:
            raise ValueError("say which task to cancel: an id or a phrase")
        tasks = self.scheduler.store.list()
        by_id = [t for t in tasks if t.id == match or match in t.id]
        if by_id:
            removed = [t for t in by_id if self.scheduler.remove(t.id)]
            return f"cancelled {len(removed)} task(s): " + ", ".join(
                t.id for t in removed
            )
        lowered = match.lower()
        hits = [t for t in tasks if lowered in t.request.lower()]
        if not hits:
            known = ", ".join(f"{t.id}={t.request[:40]!r}" for t in tasks[:8])
            return (
                f"no scheduled task matches {match!r}. "
                f"Known tasks: {known or 'none'}."
            )
        removed = [t for t in hits if self.scheduler.remove(t.id)]
        return f"cancelled {len(removed)} task(s): " + ", ".join(
            f"{t.id} ({t.request[:50]})" for t in removed
        )
