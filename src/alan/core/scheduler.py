"""The always-on scheduler: Alan's work continues while nobody is typing.

What it is
----------
A single background loop that owns a persisted list of tasks
(``data/state/schedule.json``) and runs each one through a
:class:`TaskRunner` when it comes due. The runner is a seam: today it is only
:class:`LocalRunner`, which calls this process's orchestrator with
``session_id="sched-<task id>"``; a future remote/VPS runner swaps in without
touching scheduler logic ("local first, cloud later").

Why it is built this way -- three incidents from the Dots / Grok Bot audit
shaped every decision here:

* **One task at a time, on purpose.** Grok Bot's failure mode was a bot that
  re-applied itself in a loop nobody could stop. The scheduler runs tasks
  *sequentially* in its own thread, each in a worker joined with a timeout, and
  refuses to start a task whose previous worker is still alive. There is no
  configuration for "run N at once"; the bounded concurrency is structural.
* **Approvals stall, they do not fail silently.** A scheduled run that needs a
  human files an :class:`~alan.permissions.queue.Approval`, stops, and marks
  the task ``waiting_approval`` -- visible in ``/activity`` -- until somebody
  decides. Approving re-queues the task immediately; rejecting pauses it so it
  cannot nag or loop.
* **State survives a crash.** Every mutation is written with
  :func:`~alan.core.atomic.write_atomic` before the next one, so pulling the
  USB out mid-run loses at most the transition in flight, not the schedule.

Not built (deliberate, recorded in the worklog): *goal* tasks. "Run until X"
needs a completion predicate the request alone cannot supply; a recurring task
that the user pauses when satisfied covers today's need without inventing a
condition language.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from ..interfaces.types import new_id
from .atomic import write_atomic
from .events import EventBus, Events

#: Task statuses. Transitions are one-way except pending <-> running.
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_WAITING = "waiting_approval"
ST_PAUSED = "paused"
ST_DONE = "done"
ST_FAILED = "failed"

VALID_STATUSES = (ST_PENDING, ST_RUNNING, ST_WAITING, ST_PAUSED, ST_DONE, ST_FAILED)

#: How many run summaries a task keeps in the schedule file.
RUN_HISTORY = 10

#: Longest summary text stored for a run (full outputs live in the trace).
SUMMARY_CHARS = 600


def _now_local() -> datetime:
    """Local, timezone-aware, aware-of-DST clock. Scheduling is a local act."""
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_iso(text: str) -> Optional[datetime]:
    """Parse a stored timestamp; ``None`` if absent or unreadable."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def next_occurrence(at_time: str, after: datetime) -> Optional[datetime]:
    """Next datetime whose local ``HH:MM`` equals ``at_time``, after ``after``.

    Today if it is still ahead, otherwise tomorrow -- so "every day at 07:30"
    never fires at 07:31 because a run started late.
    """
    try:
        hour, minute = at_time.split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            return None
    except (ValueError, AttributeError):
        return None
    candidate = after.replace(hour=hour_i, minute=minute_i, second=0, microsecond=0)
    if candidate <= after:
        candidate = candidate + timedelta(days=1)
    return candidate


#: Unit words in the timing phrase, in seconds. "month" is a fixed 30 days:
#: guessing calendar months would make February behave differently from March
#: in a way nobody asked for.
_UNITS = {
    "second": 1.0, "seconds": 1.0,
    "minute": 60.0, "minutes": 60.0,
    "hour": 3600.0, "hours": 3600.0,
    "day": 86400.0, "days": 86400.0,
    "week": 604800.0, "weeks": 604800.0,
    "month": 2592000.0, "months": 2592000.0,
}
#: Named times of day -> the clock time they mean. Alan is a personal
#: assistant, not an airline timetable: morning is eight, night is nine.
_NAMED_TIMES = {
    "morning": "08:00", "noon": "12:00", "afternoon": "15:00",
    "evening": "18:00", "night": "21:00", "midnight": "00:00",
}
#: The message shown when a timing phrase cannot be parsed. Also appended to
#: the tool's error so the user sees it without opening the docs.
WHEN_HELP = (
    "I could not read that time. Try forms like: 'every 30 minutes', "
    "'every day at 07:30', 'every morning', 'at 18:45', "
    "'tomorrow at 09:00', 'in 2 hours', 'daily', 'weekly at 09:00'."
)


@dataclass
class WhenSpec:
    """A parsed timing phrase: when a task first runs and how often after."""

    kind: str                      # "once" | "interval"
    every_seconds: float = 0.0     # interval spacing
    #: "HH:MM" local anchor. Meaningful only for a daily cadence
    #: (``every_seconds == 86400``); ``parse_when`` never sets it otherwise,
    #: because an anchor on a non-daily period would silently fire daily.
    at_time: str = ""
    first_at: Optional[datetime] = None
    phrase: str = ""               # what was parsed, for the confirmation

    def describe(self) -> str:
        if self.kind == "interval":
            if self.at_time:
                return f"every day at {self.at_time} (local time)"
            hours = self.every_seconds / 3600.0
            if self.every_seconds < 3600:
                return f"every {self.every_seconds / 60.0:g} minutes"
            if hours < 24 and self.every_seconds % 86400 != 0:
                return f"every {hours:g} hours"
            days = self.every_seconds / 86400.0
            if days == 1:
                return "every day"
            if days == 7:
                return "every week"
            return f"every {days:g} days"
        if self.first_at is None:
            return "once, at the next tick"
        return f"once at {self.first_at.strftime('%Y-%m-%d %H:%M')}"


#: The clock inside a timing phrase: "07:30", "at 18:45", "at 7am". Two
#: alternatives rather than one with optional pieces, because a bare "at 7" in
#: prose ("at 7 things") is not a time, while "at 7:30" and "at 7am" always are.
_CLOCK = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<p>am|pm)?"
    r"|\bat\s+(?P<bh>\d{1,2})\s*(?P<bp>am|pm)\b",
    re.I,
)


def parse_when(text: str, *, now: Optional[datetime] = None) -> WhenSpec:
    """Parse a natural timing phrase into a :class:`WhenSpec`.

    Raises ``ValueError`` with :data:`WHEN_HELP` when the phrase is unknown --
    a scheduled task whose time was misread is worse than no task, because the
    user believes a thing will happen that will not.

    Word order never matters: the function searches for each construct anywhere
    in the text, so "at 07:30 daily", "daily at 07:30" and "07:30 daily" all
    parse alike. The planner hands over whatever timing fragments it found,
    space-joined (see ``_schedule_parts``).

    Precedence, from most to least specific: a delay ("in 2 hours"), a named
    time of day ("every morning"), a cadence word with a unit ("every 30
    minutes", "once a day"), a recurrence word ("daily", "weekly at 09:00"),
    and finally a bare clock time, which means *once* -- because a request that
    carries no recurrence word is a one-shot, and guessing "daily" from a bare
    time would fire every morning forever.
    """
    if now is None:
        now = _now_local()
    raw = (text or "").strip()
    if not raw:
        raise ValueError(WHEN_HELP)
    lowered = raw.lower()

    def _err() -> ValueError:
        return ValueError(f"{WHEN_HELP} (got {raw!r})")

    clock = _CLOCK.search(lowered)

    def _read_clock() -> tuple[int, int]:
        """(hour24, minute) from the found clock, or raise. ``clock`` is set."""
        if clock is None:  # pragma: no cover - guarded by each caller
            raise _err()
        if clock.group("h") is not None:
            hour_i = int(clock.group("h"))
            minute_i = int(clock.group("m") or 0)
            ampm = (clock.group("p") or "").lower()
        else:
            hour_i = int(clock.group("bh"))
            minute_i = 0
            ampm = (clock.group("bp") or "").lower()
        if ampm == "pm" and hour_i < 12:
            hour_i += 12
        elif ampm == "am" and hour_i == 12:
            hour_i = 0
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            raise _err()
        return hour_i, minute_i

    def _at() -> str:
        hour_i, minute_i = _read_clock()
        return f"{hour_i:02d}:{minute_i:02d}"

    # -- 1. a delay: "in 2 hours" ------------------------------------------
    in_match = re.search(
        r"\bin\s+(\d+)\s*(seconds?|minutes?|hours?|days?|weeks?)\b", lowered
    )
    if in_match:
        seconds = _UNITS.get(in_match.group(2))
        if not seconds:
            raise _err()
        delay = float(in_match.group(1)) * seconds
        if delay <= 0:
            raise _err()
        return WhenSpec(kind="once", first_at=now + timedelta(seconds=delay),
                        phrase=raw)

    # -- 2. named times of day: "every morning" -----------------------------
    named = re.search(
        r"\b(?:every|each)\s+(morning|noon|afternoon|evening|night|midnight)\b",
        lowered,
    )
    if named:
        return WhenSpec(kind="interval", every_seconds=86400.0,
                        at_time=_NAMED_TIMES[named.group(1)], phrase=raw)

    # -- 3. "once a day" / "twice a week" -----------------------------------
    oa = re.search(
        r"\b(once|twice)\s+a\s+(second|minute|hour|day|week|month)\b", lowered
    )
    if oa:
        mult = 1.0 if oa.group(1) == "once" else 2.0
        unit = oa.group(2)
        # The clock anchor only for a true daily cadence: "twice a day at
        # 07:30" must space runs 12h apart, not re-anchor to 07:30 each time.
        if unit == "day" and mult == 1.0 and clock is not None:
            return WhenSpec(kind="interval", every_seconds=86400.0,
                            at_time=_at(), phrase=raw)
        return WhenSpec(kind="interval", every_seconds=mult * _UNITS[unit],
                        phrase=raw)

    # -- 4. "every 30 minutes" / "every day at 07:30" / "every 2 weeks" -----
    every_match = re.search(
        r"\b(?:every|each)\s+(\d+)?\s*"
        r"(seconds?|minutes?|hours?|days?|weeks?|months?)\b",
        lowered,
    )
    if every_match:
        count = float(every_match.group(1) or 1)
        unit = every_match.group(2)
        if count <= 0 or unit not in _UNITS:
            raise _err()
        every = count * _UNITS[unit]
        if unit in ("day", "days") and count == 1 and clock is not None:
            return WhenSpec(kind="interval", every_seconds=86400.0,
                            at_time=_at(), phrase=raw)
        if unit in ("week", "weeks") and count == 1 and clock is not None:
            # Weekly keeps the clock as its *first* run only; subsequent runs
            # space from the previous one. A weekday field would be needed to
            # pin it ("every Tuesday"), and inventing one is worse than this.
            return WhenSpec(kind="interval", every_seconds=604800.0,
                            first_at=next_occurrence(_at(), now), phrase=raw)
        return WhenSpec(kind="interval", every_seconds=every,
                        first_at=now + timedelta(seconds=every), phrase=raw)

    # -- 5. "daily" / "weekly at 09:00" -------------------------------------
    dm = re.search(r"\b(daily|weekly)\b", lowered)
    if dm:
        daily = dm.group(1) == "daily"
        every = 86400.0 if daily else 604800.0
        if clock is not None:
            if daily:
                return WhenSpec(kind="interval", every_seconds=every,
                                at_time=_at(), phrase=raw)
            return WhenSpec(kind="interval", every_seconds=every,
                            first_at=next_occurrence(_at(), now), phrase=raw)
        return WhenSpec(kind="interval", every_seconds=every, phrase=raw)

    # -- 6. a bare clock: once ---------------------------------------------
    if clock is not None:
        hour_i, minute_i = _read_clock()
        at_time = f"{hour_i:02d}:{minute_i:02d}"
        day_word = re.search(r"\b(tomorrow|today)\b", lowered)
        if (
            day_word is not None
            and day_word.group(1) == "today"
            and (hour_i, minute_i) <= (now.hour, now.minute)
        ):
            raise ValueError(f"That time has already passed today (got {raw!r})")
        first = next_occurrence(at_time, now)
        if first is None:  # pragma: no cover - range-checked in _read_clock
            raise _err()
        if (
            day_word is not None
            and day_word.group(1) == "tomorrow"
            and first.date() == now.date()
        ):
            first = first + timedelta(days=1)
        return WhenSpec(kind="once", first_at=first, at_time=at_time,
                        phrase=raw)

    raise _err()



@dataclass
class ScheduledTask:
    """One unit of scheduled work. Plain data; behaviour lives in Scheduler."""

    id: str
    request: str
    kind: str = "once"            # "once" | "interval"
    every_seconds: float = 0.0    # interval spacing
    at_time: str = ""             # "HH:MM" local anchor (interval, daily-style)
    next_run_at: str = ""         # ISO local-aware
    created_at: str = ""
    created_by: str = "chat"      # chat | cli | tool | import
    status: str = ST_PENDING
    run_count: int = 0
    max_runs: int = 0             # 0 = unlimited (only meaningful for interval)
    last_run_at: str = ""
    blocked_by: str = ""          # approval id while waiting_approval
    runs: List[Dict[str, Any]] = field(default_factory=list)

    # -- derived ----------------------------------------------------------

    @property
    def is_interval(self) -> bool:
        return self.kind == "interval"

    def next_run_dt(self) -> Optional[datetime]:
        return parse_iso(self.next_run_at)

    def due(self, now: datetime) -> bool:
        nxt = self.next_run_dt()
        return self.status == ST_PENDING and nxt is not None and nxt <= now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request,
            "kind": self.kind,
            "every_seconds": self.every_seconds,
            "at_time": self.at_time,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "status": self.status,
            "run_count": self.run_count,
            "max_runs": self.max_runs,
            "last_run_at": self.last_run_at,
            "blocked_by": self.blocked_by,
            "runs": list(self.runs),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ScheduledTask":
        task = cls(
            id=str(raw.get("id") or new_id("tsk_")),
            request=str(raw.get("request", "")),
            kind=str(raw.get("kind", "once")) if raw.get("kind") in ("once", "interval") else "once",
            every_seconds=float(raw.get("every_seconds", 0.0) or 0.0),
            at_time=str(raw.get("at_time", "")),
            next_run_at=str(raw.get("next_run_at", "")),
            created_at=str(raw.get("created_at", "")),
            created_by=str(raw.get("created_by", "import")),
            status=str(raw.get("status", ST_PENDING)) if raw.get("status") in VALID_STATUSES else ST_PENDING,
            run_count=int(raw.get("run_count", 0) or 0),
            max_runs=int(raw.get("max_runs", 0) or 0),
            last_run_at=str(raw.get("last_run_at", "")),
            blocked_by=str(raw.get("blocked_by", "")),
            runs=list(raw.get("runs", []) or []),
        )
        if not task.request:
            raise ValueError("scheduled task has no request")
        # A task that was mid-run when the process died resumes as pending --
        # running is a live status and never survives a restart.
        if task.status == ST_RUNNING:
            task.status = ST_PENDING
        if task.status == ST_PENDING and task.next_run_dt() is None:
            task.next_run_at = _iso(_now_local())
        return task


class ScheduleStore:
    """Thread-safe, crash-safe persistence for tasks.

    A corrupt file is *preserved* (renamed beside the original) and the store
    starts empty rather than bricking every Alan command with a traceback:
    losing tomorrow's schedule is bad, losing the assistant is worse, and the
    corrupt original is still on disk for a human to look at.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._tasks: Dict[str, ScheduledTask] = {}
        self._order: List[str] = []
        self.load_error = ""
        self._load()

    # -- loading/saving ---------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.load_error = f"schedule store unreadable ({exc}); started empty"
            try:
                backup = self.path.with_name(
                    f"{self.path.name}.corrupt-{int(time.time())}"
                )
                self.path.replace(backup)
            except OSError:
                pass
            return
        entries = raw.get("tasks", []) if isinstance(raw, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                task = ScheduledTask.from_dict(entry)
            except (ValueError, TypeError):
                continue  # one bad row must not delete the rest
            self._tasks[task.id] = task
            self._order.append(task.id)

    def save(self) -> bool:
        """Persist atomically. Caller must hold the lock (or accept the race)."""
        payload = {
            "version": 1,
            "tasks": [self._tasks[i].to_dict() for i in self._order if i in self._tasks],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return write_atomic(self.path, text)

    # -- accessors --------------------------------------------------------

    def add(self, task: ScheduledTask) -> ScheduledTask:
        with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"duplicate task id {task.id}")
            self._tasks[task.id] = task
            self._order.append(task.id)
            self.save()
        return task

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> List[ScheduledTask]:
        with self._lock:
            return [self._tasks[i] for i in self._order if i in self._tasks]

    def remove(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            self._order.remove(task_id)
            self.save()
            return True

    def persist(self) -> bool:
        with self._lock:
            return self.save()


# -- runners ---------------------------------------------------------------


@dataclass
class RunOutcome:
    """What a runner reports back for one execution of a task."""

    ok: bool
    summary: str = ""
    error: str = ""


class TaskRunner(Protocol):
    """Where a task executes. Local today, remote later; the shape is fixed."""

    def run(self, task: ScheduledTask) -> RunOutcome:
        ...


class LocalRunner:
    """Runs tasks in this process through ``run_fn``.

    ``run_fn(request, session_id)`` is supplied by the runtime and is expected
    to return an :class:`~alan.interfaces.types.AgentResult` (the orchestrator
    contract), though a plain string is accepted for simple stubs.
    """

    def __init__(
        self,
        run_fn: Callable[[str, str], Any],
        *,
        summary_chars: int = SUMMARY_CHARS,
    ) -> None:
        self._run_fn = run_fn
        self._summary_chars = summary_chars

    def run(self, task: ScheduledTask) -> RunOutcome:
        session_id = f"sched-{task.id}"
        try:
            result = self._run_fn(task.request, session_id)
        except Exception as exc:  # a runner never raises into the scheduler
            return RunOutcome(ok=False, summary="", error=f"{type(exc).__name__}: {exc}")

        if isinstance(result, RunOutcome):
            return result
        if isinstance(result, str):
            return RunOutcome(ok=True, summary=self._clip(result))
        success = bool(getattr(result, "success", True))
        output = str(getattr(result, "output", "") or "")
        error = str(getattr(result, "error", "") or "")
        summary = output if output else (error if not success else "(no output)")
        return RunOutcome(
            ok=success,
            summary=self._clip(summary),
            error=self._clip(error),
        )

    def _clip(self, text: str) -> str:
        text = text.strip()
        if len(text) <= self._summary_chars:
            return text
        return text[: self._summary_chars - 1] + "\u2026"


# -- the scheduler ---------------------------------------------------------


class Scheduler:
    """Owns the task list, the loop thread, and every state transition.

    Concurrency model, stated once: **one task runs at a time, inside one
    worker thread joined with ``task_timeout``**. The scheduler thread never
    runs task code itself (so a hung model call cannot freeze the loop's
    bookkeeping), never starts task B while task A's worker is alive, and
    ``stop()`` prevents any new task from starting. A worker that outlives its
    timeout is left to finish in the background; its outcome is discarded
    because the scheduler already recorded the timeout. Python cannot kill
    threads, and pretending otherwise would be the real bug.
    """

    def __init__(
        self,
        store: ScheduleStore,
        runner: TaskRunner,
        *,
        bus: Optional[EventBus] = None,
        approvals: Any = None,  # Optional[ApprovalQueue]; Any avoids a cycle
        tick_seconds: float = 5.0,
        task_timeout: float = 600.0,
        now_fn: Callable[[], datetime] = _now_local,
    ) -> None:
        self.store = store
        self.runner = runner
        self.bus = bus
        self.approvals = approvals
        self.tick_seconds = max(0.2, float(tick_seconds))
        # 0.1 rather than 1.0: a sub-second ceiling is useless in production,
        # but tests use tiny timeouts to exercise the abandoned-worker path
        # quickly, and a minimum that hides a code path is a misplaced guard.
        self.task_timeout = max(0.1, float(task_timeout))
        self._now = now_fn
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._alive: Dict[str, threading.Thread] = {}
        self.last_tick_at = ""

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> bool:
        """Start the loop thread. Idempotent."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            thread = threading.Thread(
                target=self._loop, name="alan-scheduler", daemon=True
            )
            self._thread = thread
            thread.start()
        self._emit(Events.SCHEDULER_STARTED, tasks=len(self.store.list()))
        return True

    def stop(self, timeout: float = 10.0) -> None:
        """Stop launching new work and wait for the loop to exit.

        A worker already inside a task keeps running until its own timeout --
        documented behaviour, not a leak: interrupting a half-finished task
        would leave files half-written with no record of which half.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
        self._emit(Events.SCHEDULER_STOPPED)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # the loop must survive anything
                self._emit(Events.SCHEDULER_ERROR, error=str(exc))
            self._stop.wait(self.tick_seconds)

    # -- authoring --------------------------------------------------------

    def add(
        self,
        request: str,
        *,
        kind: str = "once",
        every_seconds: float = 0.0,
        at_time: str = "",
        first_at: Optional[datetime] = None,
        max_runs: int = 0,
        created_by: str = "chat",
    ) -> ScheduledTask:
        """Create and persist a task. Raises ``ValueError`` on bad input."""
        request = (request or "").strip()
        if not request:
            raise ValueError("a scheduled task needs a request")
        if kind not in ("once", "interval"):
            raise ValueError(f"unknown task kind {kind!r}; expected 'once' or 'interval'")
        now = self._now()

        if kind == "interval":
            if at_time:
                # An anchor means "daily at this clock time". It is only
                # honoured for a daily cadence -- a caller that pairs an anchor
                # with other spacing gets the spacing, not a task that claims
                # to run weekly but fires every day off the clock.
                if every_seconds and abs(float(every_seconds) - 86400.0) > 1.0:
                    at_time = ""
                else:
                    nxt = next_occurrence(at_time, now)
                    if nxt is None:
                        raise ValueError(f"bad time {at_time!r}; expected HH:MM")
                    every_seconds = every_seconds or 86400.0
            if not at_time:
                if every_seconds <= 0:
                    raise ValueError("an interval task needs every_seconds or at_time")
                nxt = first_at or (now + timedelta(seconds=every_seconds))
        else:
            if at_time and first_at is None:
                first_at = next_occurrence(at_time, now)
                if first_at is None:
                    raise ValueError(f"bad time {at_time!r}; expected HH:MM")
            nxt = first_at or now

        task = ScheduledTask(
            id=new_id("tsk_"),
            request=request,
            kind=kind,
            every_seconds=float(every_seconds or 0.0),
            at_time=at_time,
            next_run_at=_iso(nxt),
            created_at=_iso(now),
            created_by=created_by,
            status=ST_PENDING,
            max_runs=int(max_runs or 0),
        )
        self.store.add(task)
        self._emit(
            Events.TASK_SCHEDULED,
            task_id=task.id,
            kind=task.kind,
            next_run_at=task.next_run_at,
            request=task.request,
        )
        return task

    def remove(self, task_id: str) -> bool:
        removed = self.store.remove(task_id)
        if removed:
            self._emit(Events.TASK_REMOVED, task_id=task_id)
        return removed

    def pause(self, task_id: str) -> Optional[ScheduledTask]:
        return self._set_status(task_id, ST_PAUSED, Events.TASK_PAUSED)

    def resume(self, task_id: str) -> Optional[ScheduledTask]:
        task = self.store.get(task_id)
        if task is None:
            return None
        with self._lock:
            if task.status not in (ST_PAUSED, ST_FAILED, ST_WAITING, ST_DONE):
                return task
            task.status = ST_PENDING
            task.blocked_by = ""
            if task.next_run_dt() is None or task.next_run_dt() <= self._now():
                task.next_run_at = _iso(self._now())
            self.store.persist()
        self._emit(Events.TASK_RESUMED, task_id=task_id)
        return task

    def run_now(self, task_id: str) -> Optional[ScheduledTask]:
        """Queue a task for the next tick, whatever its status (except running)."""
        task = self.store.get(task_id)
        if task is None:
            return None
        with self._lock:
            if task.status == ST_RUNNING:
                return task
            task.status = ST_PENDING
            task.blocked_by = ""
            task.next_run_at = _iso(self._now())
            self.store.persist()
        self._emit(Events.TASK_RESUMED, task_id=task_id, reason="run_now")
        return task

    def _set_status(self, task_id: str, status: str, event: str) -> Optional[ScheduledTask]:
        task = self.store.get(task_id)
        if task is None:
            return None
        with self._lock:
            if task.status == ST_RUNNING:
                return task
            task.status = status
            self.store.persist()
        self._emit(event, task_id=task_id, status=status)
        return task

    # -- approvals --------------------------------------------------------

    def on_approval_decided(self, approval: Any) -> None:
        """Called by the ApprovalQueue after every decision.

        Approved  -> the waiting task re-queues to run immediately.
        Rejected  -> the task pauses; re-running is an explicit operator act,
                     which is what keeps "no" from becoming a retry loop.
        """
        task_id = getattr(approval, "task_id", "")
        if not task_id:
            return
        task = self.store.get(task_id)
        if task is None or task.status != ST_WAITING:
            return
        with self._lock:
            if getattr(approval, "status", "") == "approved":
                task.status = ST_PENDING
                task.blocked_by = ""
                task.next_run_at = _iso(self._now())
                event = Events.TASK_RESUMED
            else:
                task.status = ST_PAUSED
                task.blocked_by = getattr(approval, "id", "")
                event = Events.TASK_PAUSED
            self.store.persist()
        self._emit(event, task_id=task_id, approval_id=getattr(approval, "id", ""))

    # -- ticking ----------------------------------------------------------

    def tick(self, now: Optional[datetime] = None) -> List[str]:
        """Run every due task, one at a time. Returns the ids that ran."""
        now = now or self._now()
        self.last_tick_at = _iso(now)
        self._prune_workers()
        ran: List[str] = []
        for task in self.store.list():
            if self._stop.is_set():
                break
            if not task.due(now):
                continue
            thread = self._alive.get(task.id)
            if thread is not None and thread.is_alive():
                continue  # previous worker still going; never overlap a task
            self._execute(task, now)
            ran.append(task.id)
        return ran

    def _prune_workers(self) -> None:
        with self._lock:
            dead = [tid for tid, t in self._alive.items() if not t.is_alive()]
            for tid in dead:
                del self._alive[tid]

    def _execute(self, task: ScheduledTask, now: datetime) -> None:
        with self._lock:
            task.status = ST_RUNNING
            task.last_run_at = _iso(now)
            self.store.persist()
        self._emit(Events.TASK_STARTED, task_id=task.id, request=task.request)

        box: Dict[str, Any] = {}

        def worker() -> None:
            try:
                box["outcome"] = self.runner.run(task)
            except Exception as exc:  # runner contract says it cannot, but...
                box["outcome"] = RunOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(
            target=worker, name=f"alan-task-{task.id}", daemon=True
        )
        with self._lock:
            self._alive[task.id] = thread
        thread.start()
        thread.join(timeout=self.task_timeout)

        if "outcome" in box:
            outcome: RunOutcome = box["outcome"]
        else:
            outcome = RunOutcome(
                ok=False,
                error=f"timed out after {self.task_timeout:.0f}s (worker abandoned)",
            )
        self._record(task, outcome, now)

    def _record(self, task: ScheduledTask, outcome: RunOutcome, now: datetime) -> None:
        """Apply one outcome to task state. The single write path per run."""
        approvals_pending: Sequence[Any] = ()
        if self.approvals is not None:
            try:
                approvals_pending = self.approvals.pending_for(task.id)
            except Exception:  # pragma: no cover - defensive
                approvals_pending = ()

        with self._lock:
            entry = {
                "at": _iso(now),
                "ok": bool(outcome.ok),
                "summary": outcome.summary or outcome.error,
                "error": outcome.error,
            }
            task.runs.append(entry)
            del task.runs[:-RUN_HISTORY]

            if approvals_pending:
                # The run stopped at a permission gate. That is neither a
                # completion nor a crash -- it is a question waiting for a human.
                task.status = ST_WAITING
                task.blocked_by = approvals_pending[0].id
                event = Events.TASK_WAITING
            elif outcome.ok:
                task.run_count += 1
                if task.is_interval and not (
                    task.max_runs and task.run_count >= task.max_runs
                ):
                    task.status = ST_PENDING
                    task.next_run_at = _iso(self._compute_next(task, now))
                    event = Events.TASK_FINISHED
                else:
                    task.status = ST_DONE
                    event = Events.TASK_FINISHED
            else:
                if task.is_interval:
                    task.status = ST_PENDING
                    task.next_run_at = _iso(self._compute_next(task, now))
                else:
                    task.status = ST_FAILED
                event = Events.TASK_FAILED
            self.store.persist()

        self._emit(
            event,
            task_id=task.id,
            ok=bool(outcome.ok),
            status=task.status,
            summary=(outcome.summary or outcome.error)[:200],
            error=outcome.error[:200],
        )

    def _compute_next(self, task: ScheduledTask, after: datetime) -> datetime:
        if task.at_time:
            nxt = next_occurrence(task.at_time, after)
            if nxt is not None:
                return nxt
        every = task.every_seconds if task.every_seconds > 0 else 86400.0
        return after + timedelta(seconds=every)

    # -- views ------------------------------------------------------------

    def due_soon(self, now: Optional[datetime] = None) -> List[ScheduledTask]:
        now = now or self._now()
        return [t for t in self.store.list() if t.due(now)]

    def waiting(self) -> List[ScheduledTask]:
        return [t for t in self.store.list() if t.status == ST_WAITING]

    def activity_lines(self) -> List[str]:
        """Human-readable snapshot for ``/activity`` and ``alan activity``."""
        lines: List[str] = []
        tasks = self.store.list()
        if not tasks:
            lines.append("no scheduled tasks")
        for task in tasks:
            nxt = task.next_run_at or "-"
            lines.append(
                f"[{task.status:>16}] {task.id} {nxt}  {task.request[:70]}"
            )
        if self.store.load_error:
            lines.append(f"warning: {self.store.load_error}")
        return lines

    # -- helpers ----------------------------------------------------------

    def _emit(self, name: str, **fields: Any) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(name, **fields)
        except Exception:  # pragma: no cover - events never break scheduling
            pass
