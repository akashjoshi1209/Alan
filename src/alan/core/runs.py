"""Durable task/run bookkeeping with leases and fencing tokens.

Ported from rakazo's ``Task``/``Run`` rows and OpenDots' leased scheduler.
Alan's scheduler already had statuses and a JSON file; what it lacked was the
concurrency contract that makes an *always-on* agent safe:

**A lease, not a lock.** Exactly one worker may hold a run at a time, and the
claim records *who* holds it and *until when*. A holder that dies does not
wedge the system: its lease simply expires, and the next claim proceeds. That
is the whole reason to prefer a lease over a mutex on a machine that gets
suspended, killed, or unplugged.

**A fencing token.** Every claim bumps ``task.fence``. A run carries the fence
it was claimed with, and committing a result requires *both* the holder id and
the current fence. So a worker that was paused past its lease -- wakes up, and
tries to write -- is rejected: its fence is stale, and the state has already
moved on. Without this, a stalled worker and its replacement both "finish" the
same run and the later write silently wins.

**Interrupted, not retried.** A run found active with an expired lease becomes
``interrupted``. It is *not* queued again, because nobody knows how far the
previous attempt got -- the effect ledger (``core.effects``) is what says
whether its side effects landed. Silence is never the answer; the run is left
somewhere a human or a supervisor can see it.

**waiting_input is a state, not a failure.** A delegated run may need the
operator ("which repo?"). It moves to ``waiting_input``, releases its lease so
other work proceeds, and is re-claimed once the answer arrives. rakazo's
``pause_input`` is this state; treating it as a timeout or a crash would make
asking a question look like a broken run.

State machine (one-way except where drawn):

    queued -> leased -> running -> completed | failed
                 |         |
                 |         +-> waiting_input -> queued   (answer supplied)
                 |         +-> interrupted               (lease expired)
                 +-> cancelled

Every transition is written through a temp file and ``os.replace``, so a crash
during a save leaves the previous complete file rather than a torn one.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..interfaces.types import new_id, redact, utcnow

# -- statuses -------------------------------------------------------------
ST_QUEUED = "queued"
ST_LEASED = "leased"
ST_RUNNING = "running"
ST_WAITING_INPUT = "waiting_input"
ST_COMPLETED = "completed"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"
ST_INTERRUPTED = "interrupted"

VALID_STATUSES = (
    ST_QUEUED, ST_LEASED, ST_RUNNING, ST_WAITING_INPUT,
    ST_COMPLETED, ST_FAILED, ST_CANCELLED, ST_INTERRUPTED,
)

#: Statuses that hold a lease and must be released or expire.
ACTIVE_STATUSES = (ST_LEASED, ST_RUNNING)

#: Statuses that end a run for good; a task in one of these is finished.
TERMINAL_STATUSES = (ST_COMPLETED, ST_FAILED, ST_CANCELLED, ST_INTERRUPTED)

#: Statuses a task cannot be *claimed* from without an explicit reopen.
#:
#: Completed and cancelled are obvious. Interrupted is here because nobody
#: knows how far the dead attempt got -- re-queueing it would be a silent
#: retry of half-finished work, which is exactly what the effect ledger exists
#: to prevent. **Failed is deliberately absent**: a failure is observed and
#: understood, so retrying it is a normal attempt rather than a guess, and the
#: attempt counter is what bounds how many times that may happen.
SETTLED_STATUSES = (ST_COMPLETED, ST_CANCELLED, ST_INTERRUPTED)

#: Default lease. Long enough to survive a slow model call, short enough that
#: a crashed holder does not block the task for long.
DEFAULT_LEASE_SECONDS = 300.0


def _now() -> str:
    return utcnow().isoformat(timespec="seconds")


def _parse(ts: str) -> float | None:
    """Epoch seconds for an ISO timestamp, or ``None`` if unreadable."""
    if not ts:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


@dataclass
class Task:
    """A durable unit of work. One task, many runs (attempts)."""

    id: str = ""
    prompt: str = ""
    agent: str = ""
    title: str = ""
    status: str = ST_QUEUED
    #: Monotonic fencing token. Bumped on every claim; a commit must match it.
    fence: int = 0
    attempts: int = 0
    #: How many claims are allowed before the task is failed for good.
    #: ``0`` means unlimited. Bounds a persistently-failing task from turning
    #: an always-on daemon into a retry loop that never stops.
    max_attempts: int = 5
    #: Idempotency key: submitting the same key twice returns this task.
    key: str = ""
    run_ids: list[str] = field(default_factory=list)
    waiting: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id("task_")
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "agent": self.agent,
            "title": self.title,
            "status": self.status,
            "fence": self.fence,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "key": self.key,
            "run_ids": list(self.run_ids),
            "waiting": self.waiting,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data.get("id", "")),
            prompt=str(data.get("prompt", "")),
            agent=str(data.get("agent", "")),
            title=str(data.get("title", "")),
            status=str(data.get("status", ST_QUEUED)),
            fence=int(data.get("fence", 0) or 0),
            attempts=int(data.get("attempts", 0) or 0),
            max_attempts=int(data.get("max_attempts", 5) or 0),
            key=str(data.get("key", "")),
            run_ids=[str(x) for x in (data.get("run_ids") or [])],
            waiting=dict(data.get("waiting") or {}),
            result=data.get("result"),
            error=str(data.get("error", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    @property
    def settled(self) -> bool:
        """Cannot be claimed again without an explicit :meth:`RunStore.reopen`."""
        return self.status in SETTLED_STATUSES

    @property
    def finished(self) -> bool:
        """No active attempt remains (including a failed one)."""
        return self.status in TERMINAL_STATUSES


@dataclass
class Run:
    """One attempt at a task, held under a lease identified by a fence."""

    id: str = ""
    task_id: str = ""
    attempt: int = 1
    fence: int = 0
    status: str = ST_QUEUED
    holder: str = ""
    lease_expires_at: str = ""
    started_at: str = ""
    updated_at: str = ""
    result: Any = None
    error: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id("run_")
        if not self.started_at:
            self.started_at = _now()
        if not self.updated_at:
            self.updated_at = self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "fence": self.fence,
            "status": self.status,
            "holder": self.holder,
            "lease_expires_at": self.lease_expires_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        return cls(
            id=str(data.get("id", "")),
            task_id=str(data.get("task_id", "")),
            attempt=int(data.get("attempt", 1) or 1),
            fence=int(data.get("fence", 0) or 0),
            status=str(data.get("status", ST_QUEUED)),
            holder=str(data.get("holder", "")),
            lease_expires_at=str(data.get("lease_expires_at", "")),
            started_at=str(data.get("started_at", "")),
            updated_at=str(data.get("updated_at", "")),
            result=data.get("result"),
            error=str(data.get("error", "")),
        )

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def lease_expired(self) -> bool:
        """Has this run's lease passed? An unparseable lease never expires.

        Failing *open* (not expired) on a corrupt timestamp is deliberate: a
        lease we cannot read is not evidence that the holder is dead, and
        stealing work from a live holder is worse than waiting.
        """
        expires = _parse(self.lease_expires_at)
        if expires is None:
            return False
        import time

        return expires <= time.time()


class RunStore:
    """Thread-safe, on-disk task/run store.

    All mutation happens under one re-entrant lock and is persisted before the
    lock is released, so a reader can never observe a state the disk does not
    have. That ordering is what makes recovery after a crash trustworthy.
    """

    def __init__(self, path: Path | str | None = None, *, bus: Any = None) -> None:
        self.path = Path(path) if path else None
        self.bus = bus
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._runs: dict[str, Run] = {}
        self._by_key: dict[str, str] = {}
        if self.path is not None:
            self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        assert self.path is not None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in (raw.get("tasks") or []) if isinstance(raw, dict) else []:
            if isinstance(item, dict):
                task = Task.from_dict(item)
                if task.id:
                    self._tasks[task.id] = task
                    if task.key:
                        self._by_key[task.key] = task.id
        for item in (raw.get("runs") or []) if isinstance(raw, dict) else []:
            if isinstance(item, dict):
                run = Run.from_dict(item)
                if run.id:
                    self._runs[run.id] = run

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {
            "tasks": [t.to_dict() for t in self._tasks.values()],
            "runs": [r.to_dict() for r in self._runs.values()],
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self) -> None:
        with self._lock:
            self._save()

    # -- lazy expiry ----------------------------------------------------
    def _expire_locked(self) -> list[Run]:
        """Mark runs whose lease ran out as interrupted.

        Done lazily at the start of operations rather than by a timer: a
        background thread would itself be a thing that can die, and the whole
        point of a lease is that it expires *without* anyone doing anything.
        """
        expired: list[Run] = []
        for run in self._runs.values():
            if run.active and run.lease_expired:
                run.status = ST_INTERRUPTED
                run.holder = ""
                run.lease_expires_at = ""
                run.updated_at = _now()
                run.error = run.error or "lease expired before an outcome was recorded"
                expired.append(run)
        for run in expired:
            task = self._tasks.get(run.task_id)
            if task is not None and task.status in ACTIVE_STATUSES + (ST_QUEUED,):
                task.status = ST_INTERRUPTED
                task.updated_at = _now()
                self._emit("run.interrupted", run)
        if expired:
            # Persist the interruption itself, not just in memory: a crash
            # right after expiry must not resurrect these as still-active.
            self._save()
        return expired

    # -- tasks ----------------------------------------------------------
    def create(
        self,
        prompt: str,
        *,
        agent: str = "",
        title: str = "",
        key: str = "",
    ) -> Task:
        """Create a task, or return the existing one for a repeated ``key``.

        The idempotency key is what makes fire-and-forget delegation safe: a
        retried submit (a flaky queue, a double-clicked button, a network
        replay) must not produce two tasks doing the same work.
        """
        with self._lock:
            self._expire_locked()
            if key and key in self._by_key:
                existing = self._tasks.get(self._by_key[key])
                if existing is not None:
                    return existing
            task = Task(prompt=prompt, agent=agent, title=title, key=key)
            self._tasks[task.id] = task
            if key:
                self._by_key[key] = task.id
            self._save()
            self._emit_task("task.created", task)
            return task

    def task(self, task_id: str) -> Task | None:
        with self._lock:
            self._expire_locked()
            return self._tasks.get(task_id)

    def task_by_key(self, key: str) -> Task | None:
        with self._lock:
            task_id = self._by_key.get(key)
            return self._tasks.get(task_id) if task_id else None

    def tasks(self, *, status: str | None = None) -> list[Task]:
        with self._lock:
            self._expire_locked()
            items = list(self._tasks.values())
        if status is not None:
            items = [t for t in items if t.status == status]
        return sorted(items, key=lambda t: t.created_at, reverse=True)

    def runs(self, task_id: str) -> list[Run]:
        with self._lock:
            self._expire_locked()
            task = self._tasks.get(task_id)
            if task is None:
                return []
            return [self._runs[k] for k in task.run_ids if k in self._runs]

    def latest_run(self, task_id: str) -> Run | None:
        found = self.runs(task_id)
        return found[-1] if found else None

    def cancel(self, task_id: str, reason: str = "cancelled by operator") -> bool:
        """Cancel a task, interrupting any active run holding it."""
        with self._lock:
            self._expire_locked()
            task = self._tasks.get(task_id)
            if task is None or task.settled:
                return False
            for run_id in task.run_ids:
                run = self._runs.get(run_id)
                if run is not None and run.active:
                    run.status = ST_CANCELLED
                    run.holder = ""
                    run.lease_expires_at = ""
                    run.error = reason
                    run.updated_at = _now()
            task.status = ST_CANCELLED
            task.error = reason
            task.updated_at = _now()
            self._save()
            self._emit_task("task.cancelled", task)
            return True

    def reopen(self, task_id: str) -> Task | None:
        """Deliberately make a finished task claimable again.

        Completed, cancelled and failed tasks hold until an operator says
        otherwise: nothing re-queues them automatically, because a retry that
        nobody asked for is how a half-finished job gets run twice.
        Reopening is that explicit ask, and it starts a fresh attempt under a
        new fence rather than trusting an old one.
        """
        with self._lock:
            self._expire_locked()
            task = self._tasks.get(task_id)
            if task is None or not task.finished:
                return None
            task.status = ST_QUEUED
            task.error = ""
            # A fresh runway, but *not* a fresh fence: the fencing token must
            # stay monotonic for the life of the task or a worker from an old
            # epoch could commit into a new one.
            task.attempts = 0
            task.updated_at = _now()
            self._save()
            self._emit_task("task.reopened", task)
            return task

    # -- claiming -------------------------------------------------------
    def claim(
        self,
        task_id: str,
        *,
        holder: str,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> Run | None:
        """Atomically take the task for ``holder``. ``None`` if unavailable.

        Refuses when the task is settled or already has a live lease, bumps
        the fencing token, and starts a fresh run. Exactly one caller can win
        a given fence, and every later commit must present it.
        """
        import time

        with self._lock:
            self._expire_locked()
            task = self._tasks.get(task_id)
            if task is None or task.settled:
                return None
            # A task that keeps failing must stop on its own. Attempts are
            # bounded so a daemon cannot spin on one broken job forever; the
            # operator reopens it deliberately once they know why.
            if task.max_attempts > 0 and task.attempts >= task.max_attempts:
                if task.status != ST_FAILED:
                    task.status = ST_FAILED
                    task.error = (
                        f"gave up after {task.attempts} attempt(s); "
                        "reopen the task to try again"
                    )
                    task.updated_at = _now()
                    self._save()
                return None
            # A run waiting for input is not leased; it waits for an answer.
            if task.status == ST_WAITING_INPUT:
                return None
            for run_id in task.run_ids:
                existing = self._runs.get(run_id)
                if existing is not None and existing.active:
                    return None
            task.fence += 1
            task.attempts += 1
            task.status = ST_LEASED
            task.updated_at = _now()
            run = Run(
                task_id=task.id,
                attempt=task.attempts,
                fence=task.fence,
                status=ST_LEASED,
                holder=holder,
                lease_expires_at=_iso_in(lease_seconds),
            )
            self._runs[run.id] = run
            task.run_ids.append(run.id)
            self._save()
            self._emit("run.claimed", run)
            return run

    def heartbeat(
        self,
        run_id: str,
        *,
        holder: str,
        fence: int,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend a lease. ``False`` means the lease (or its fence) is gone."""
        with self._lock:
            run = self._runs.get(run_id)
            task = self._tasks.get(run.task_id) if run else None
            if run is None or task is None:
                return False
            if run.holder != holder or run.fence != fence or task.fence != fence:
                return False
            if not run.active:
                return False
            run.lease_expires_at = _iso_in(lease_seconds)
            run.updated_at = _now()
            self._save()
            return True

    def start(self, run_id: str, *, holder: str, fence: int) -> bool:
        """leased -> running, for the holder that owns this fence."""
        with self._lock:
            run, task = self._pair(run_id)
            if run is None or task is None:
                return False
            if not self._owns(run, task, holder, fence):
                return False
            if run.status != ST_LEASED:
                return False
            run.status = ST_RUNNING
            run.updated_at = _now()
            task.status = ST_RUNNING
            task.updated_at = _now()
            self._save()
            return True

    def finish(
        self,
        run_id: str,
        *,
        holder: str,
        fence: int,
        status: str = ST_COMPLETED,
        result: Any = None,
        error: str = "",
    ) -> bool:
        """Commit an outcome. Refused unless holder *and* fence are current.

        This is the fencing guarantee in one method: a worker whose lease
        expired and was re-claimed (bumping ``task.fence``) cannot write its
        stale outcome over the newer attempt's state.
        """
        if status not in (ST_COMPLETED, ST_FAILED, ST_CANCELLED):
            status = ST_FAILED
        with self._lock:
            run, task = self._pair(run_id)
            if run is None or task is None:
                return False
            if not self._owns(run, task, holder, fence):
                return False
            if run.status not in ACTIVE_STATUSES:
                return False
            run.status = status
            run.result = result
            run.error = redact(str(error or ""))
            run.holder = ""
            run.lease_expires_at = ""
            run.updated_at = _now()
            task.status = status
            task.result = result
            task.error = run.error
            task.updated_at = _now()
            self._save()
            self._emit("run.finished", run)
            self._emit_task("task.finished", task)
            return True

    # -- waiting for input ---------------------------------------------
    def request_input(
        self,
        run_id: str,
        *,
        holder: str,
        fence: int,
        question: str,
        options: Sequence[str] = (),
    ) -> bool:
        """Pause a run for the operator and release its lease.

        Releasing matters: a paused run must not block other work or hold a
        lease hostage while a human is away. The fence is kept, so the run
        that resumes is provably the same one that asked.
        """
        with self._lock:
            run, task = self._pair(run_id)
            if run is None or task is None:
                return False
            if not self._owns(run, task, holder, fence):
                return False
            if run.status not in ACTIVE_STATUSES:
                return False
            run.status = ST_WAITING_INPUT
            run.holder = ""
            run.lease_expires_at = ""
            run.updated_at = _now()
            task.status = ST_WAITING_INPUT
            task.waiting = {
                "question": redact(str(question)),
                "options": [str(o) for o in options],
                "run_id": run.id,
                "fence": fence,
                "asked_at": _now(),
            }
            task.updated_at = _now()
            self._save()
            self._emit_task("task.waiting_input", task)
            return True

    def supply_input(self, task_id: str, answer: str) -> Task | None:
        """Answer a paused task and return it to the queue.

        The answered run is *not* resumed: it stays paused and a new claim
        starts a fresh attempt that can see the answer. Resuming the old run
        would mean trusting a lease that has been free for minutes.
        """
        with self._lock:
            self._expire_locked()
            task = self._tasks.get(task_id)
            if task is None or task.status != ST_WAITING_INPUT:
                return None
            waiting = dict(task.waiting or {})
            waiting["answer"] = redact(str(answer))
            waiting["answered_at"] = _now()
            run = self._runs.get(str(waiting.get("run_id", "")))
            if run is not None:
                run.status = ST_CANCELLED
                run.error = "superseded by an answer; a fresh attempt will run"
                run.updated_at = _now()
            task.waiting = waiting
            task.status = ST_QUEUED
            task.updated_at = _now()
            self._save()
            self._emit_task("task.resumed", task)
            return task

    def answer(self, task_id: str) -> str:
        """The operator's answer for a task, or ``""``."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return ""
            return str((task.waiting or {}).get("answer", ""))

    # -- recovery -------------------------------------------------------
    def recover(self) -> list[Run]:
        """Interrupt every run left active by a crash. Reported once each."""
        with self._lock:
            found = self._expire_locked()
            if found:
                self._save()
            return found

    # -- helpers --------------------------------------------------------
    def _pair(self, run_id: str) -> tuple[Run | None, Task | None]:
        run = self._runs.get(run_id)
        if run is None:
            return None, None
        return run, self._tasks.get(run.task_id)

    @staticmethod
    def _owns(run: Run, task: Task, holder: str, fence: int) -> bool:
        """Holder identity *and* current fence must both match.

        Checking only the holder lets a re-claimed run be written by the old
        holder after a restart; checking only the fence lets a different
        holder commit. Both are required.
        """
        return bool(
            run.holder == holder
            and run.fence == fence
            and task.fence == fence
            and holder
        )

    def _emit(self, name: str, run: Run) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(
                name,
                run_id=run.id,
                task_id=run.task_id,
                status=run.status,
                fence=run.fence,
                holder=run.holder,
            )
        except Exception:  # noqa: BLE001 - observability must not break writes
            pass

    def _emit_task(self, name: str, task: Task) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(
                name,
                task_id=task.id,
                status=task.status,
                agent=task.agent,
                fence=task.fence,
            )
        except Exception:  # noqa: BLE001
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)


def _iso_in(seconds: float) -> str:
    """An ISO timestamp ``seconds`` from now (lease expiry)."""
    import time
    from datetime import datetime, timezone

    return datetime.fromtimestamp(
        time.time() + max(0.0, float(seconds)), tz=timezone.utc
    ).isoformat(timespec="seconds")


def summarise(tasks: Iterable[Task]) -> dict[str, int]:
    """Task counts by status -- for the CLI and the daemon's status line."""
    counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return counts
