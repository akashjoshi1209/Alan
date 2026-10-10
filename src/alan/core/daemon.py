"""The always-on daemon (Phase 2, roadmap section 4.1).

A long-running process that owns a tick loop and writes what it observes into
:class:`~alan.core.timeline.TimelineStore`. This slice ships the skeleton --
single-instance locking, a loop that survives anything, a kill switch, and a
circuit breaker -- and deliberately senses *nothing*. Activity sensing and
screen understanding are section 4.2 and 4.3, both gated on controls this
module now has a place to hang from: a per-app allow list, a pause, and a
visible state. Shipping capture before those controls exist would make the
controls retrofits, and a retrofit is exactly how "OFF by default" ends up
shipped as ON.

Three decisions worth stating, because each is easy to get wrong:

**Single-instance is an OS lock, not a lock file's existence.** Checking
whether a file exists and then creating it is a race that loses to a second
process starting at the same moment. Holding an advisory lock instead is
correct for a reason worth spelling out: the OS releases it when the holder
dies, so a crash cannot leave a permanent lock that needs manual cleanup. The
pid written into the file is diagnostic only -- it answers "who has it?" for
the error message, and it is never consulted to decide whether the lock is
free.

**The kill switch is a file, not a signal.** Windows has no graceful process
signal, and terminating by pid is not a shutdown -- it is a cut that skips
every ``finally``. A stop marker the loop checks each tick stops the daemon
from inside, so the lock is released and the store is flushed.

**Failure stops the daemon rather than spinning it.** A tick that raises is
recorded and the loop continues; a run of consecutive failures means
something structural is wrong, and a daemon that burns CPU logging the same
error forever is worse than one that stops and says why.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .timeline import TimelineEvent, TimelineStore

if os.name == "nt":  # pragma: no cover - exercised on the platform that has it
    import msvcrt
else:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]
    import fcntl

#: Consecutive tick failures before the daemon stops itself.
MAX_CONSECUTIVE_FAILURES = 5

#: Foreground supervision restarts a dead tick loop at most this many times.
#: Below the point where "restart" becomes "flail": five deaths in one run is
#: a bug in the daemon, not a transient.
MAX_RESTARTS = 5


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stop_flag_for(lock_path: Path | str) -> Path:
    """The kill-switch marker for a lock path.

    One formula, used by the daemon and by `alan daemon stop`. Duplicating it
    in the CLI is how a stop command silently writes a flag nobody reads.
    """
    return Path(str(lock_path) + ".stop")


def resolve_daemon_paths(settings) -> tuple[Path, Path]:
    """``(lock_path, store_path)`` from settings, with defaults filled in."""
    from ..config import DATA_DIR

    lock = (
        Path(settings.daemon.lock_path)
        if settings.daemon.lock_path
        else DATA_DIR / "daemon" / "alan-daemon.lock"
    )
    store = (
        Path(settings.daemon.store_path)
        if settings.daemon.store_path
        else DATA_DIR / "timeline" / "timeline.db"
    )
    return lock, store


def build_daemon(settings) -> "Daemon":
    """Construct a daemon from settings, resolving the default paths.

    Kept out of :class:`Daemon` so the class takes plain paths and stays
    testable without a `Settings` at all -- which is also why the import is
    lazy: `core` must not depend on config merely to be importable.
    """
    lock, store = resolve_daemon_paths(settings)
    return Daemon(
        timeline=TimelineStore(store),
        lock_path=lock,
        tick_seconds=settings.daemon.tick_seconds,
        retention_days=settings.daemon.retention_days,
    )


class SingleInstance:
    """An exclusive, crash-safe, cross-process lock on one path.

    ``acquire()`` returns ``False`` only when a *live* process holds the lock.
    That is the whole advantage of an OS advisory lock over a pid file: a dead
    holder's lock is already gone, so there is no stale state to detect, no
    age to guess at, and no cleanup step for the user to be told to run.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # The pid lives beside the lock, never inside it. A Windows
        # byte-range lock blocks *other handles* from reading the locked
        # bytes, so a pid written into the lock file is unreadable by exactly
        # the caller that wants it -- and the resulting PermissionError was
        # being swallowed into "unknown holder", which is how a daemon ends up
        # saying only "failed to start" at the moment that matters most.
        self.pid_path = Path(str(self.path) + ".pid")
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def holder_pid(self) -> int:
        """The pid of whoever recorded itself here, or 0. Diagnostic only.

        Never consulted to decide whether the lock is free -- only to answer
        "who has it?" for an error message. A stale pid is therefore harmless:
        the lock is the truth, and a dead holder's is already gone.
        """
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return 0

    def acquire(self) -> bool:
        """Take the lock. ``False`` means another live daemon holds it."""
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a+b" creates without truncating. Truncation matters: on Windows a
        # byte-range lock covers a region of the file, and shrinking the file
        # out from under it is how a lock silently stops guarding anything.
        handle = open(self.path, "a+b")
        try:
            self._lock(handle)
        except OSError:
            handle.close()
            return False
        # We hold it, so record who does -- in the separate pid file, written
        # after the lock so only the holder ever writes it.
        try:
            self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError:
            pass
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._unlock(handle)
        except OSError:
            pass
        finally:
            handle.close()
        # Tidy after a clean release. A crash leaves it behind, which is the
        # point of not depending on this file for correctness.
        try:
            self.pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _lock(handle) -> None:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle) -> None:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Daemon:
    """Owns the lock, the loop thread, and every state transition.

    Concurrency, stated once: one tick loop on one daemon thread. ``tick()`` is
    public so tests can drive it synchronously and inspect what it recorded;
    nothing in the loop path assumes a thread.
    """

    def __init__(
        self,
        *,
        timeline: TimelineStore,
        lock_path: Path | str,
        tick_seconds: float = 30.0,
        retention_days: float = 14.0,
        bus=None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.timeline = timeline
        self.tick_seconds = max(0.1, float(tick_seconds))
        self.retention_days = max(0.0, float(retention_days))
        self.bus = bus
        self._now = now_fn
        self.lock = SingleInstance(lock_path)
        self.stop_flag = stop_flag_for(self.lock.path)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._started_at = ""
        self.consecutive_failures = 0
        self.ticks = 0

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def started_at(self) -> str:
        return self._started_at

    def start(self) -> bool:
        """Acquire the singleton lock and start the loop. Idempotent.

        Returns ``False`` when another live daemon holds the lock, and says who
        it is, because "failed to start" with no reason is the least debuggable
        message a daemon can produce.
        """
        with self._lock:
            if self.running:
                return False
            if not self.lock.acquire():
                holder = self.lock.holder_pid()
                detail = f" (held by pid {holder})" if holder else ""
                self._note(f"another daemon is already running{detail}")
                return False
            # Clear a stop left behind by an unclean exit last time: a marker
            # from a previous life must not kill the new one on its first tick.
            self.stop_flag.unlink(missing_ok=True)
            # Retention runs once per start rather than per tick. Pruning is a
            # daily concern, and paying for it every 30 seconds would be waste
            # measured on the one device this whole feature is meant to respect.
            if self.retention_days > 0:
                purged = self.timeline.purge_older_than(self.retention_days)
                if purged:
                    self._emit(
                        "timeline_purged", rows=purged, days=self.retention_days
                    )
            self._stop.clear()
            self.consecutive_failures = 0
            self._started_at = _now_iso()
            thread = threading.Thread(
                target=self._loop, name="alan-daemon", daemon=True
            )
            self._thread = thread
            thread.start()
        self._emit("daemon_started", started_at=self._started_at)
        return True

    def stop(self, timeout: float = 10.0) -> bool:
        """Signal the loop to stop and wait for it to exit.

        Returns whether the daemon was running. The lock is released by the
        holder closing its handle, which is why stopping from *inside* the
        process -- rather than by killing it -- is the only shutdown that
        cannot leave a lock file owned by nobody.
        """
        self.request_stop()
        thread = self._thread
        was_running = thread is not None and thread.is_alive()
        self._stop.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
        self.lock.release()
        self.stop_flag.unlink(missing_ok=True)
        if was_running:
            self._emit("daemon_stopped", ticks=self.ticks)
        return was_running

    def request_stop(self) -> None:
        """Ask a running daemon to stop. Safe to call from another process."""
        try:
            self.stop_flag.parent.mkdir(parents=True, exist_ok=True)
            self.stop_flag.write_text(_now_iso(), encoding="utf-8")
        except OSError:
            pass
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.stop_flag.exists():
                self._emit("daemon_kill_switch")
                self.request_stop()
                break
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                self.consecutive_failures += 1
                self._emit("daemon_tick_failed", error=str(exc))
                if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # Structural, not transient: stop and say so rather than
                    # spin. The caller's next `alan daemon status` reads why.
                    self._emit(
                        "daemon_stopped_on_failures",
                        failures=self.consecutive_failures,
                    )
                    self.request_stop()
                    break
            self._stop.wait(self.tick_seconds)

    # -- the tick ---------------------------------------------------------

    def tick(self, now: Optional[datetime] = None) -> list[str]:
        """One pass. Returns notes for a caller; raises only if it must.

        There is nothing to sense yet, so a tick records the daemon's own
        heartbeat. That is not filler: it is what makes `status` honest about
        whether the loop is alive, and it is the row the first real sensor
        will sit next to.
        """
        stamp = now or (self._now() if self._now else None)
        notes: list[str] = []
        self.ticks += 1
        self.timeline.append(
            TimelineEvent(kind="daemon.heartbeat", ts=stamp.isoformat() if stamp else "")
        )
        # Cleared only *after* the work succeeds. Resetting it first would
        # make a tick that always fails look like one that always recovers:
        # the counter would read 0, then 1, then 0 again, never reaching the
        # budget, and a daemon whose storage is broken would spin on the lock
        # forever while reporting nothing wrong.
        self.consecutive_failures = 0
        notes.append(f"tick {self.ticks}")
        return notes

    # -- output -----------------------------------------------------------

    def status(self) -> dict:
        return {
            "running": self.running,
            "started_at": self._started_at,
            "ticks": self.ticks,
            "consecutive_failures": self.consecutive_failures,
            "lock": str(self.lock.path),
            "lock_held": self.lock.held,
            "tick_seconds": self.tick_seconds,
        }

    def _emit(self, name: str, **fields) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(name, **fields)
        except Exception:  # noqa: BLE001 - telemetry must never break the loop
            pass

    def _note(self, text: str) -> None:
        self._emit("daemon_note", note=text)
