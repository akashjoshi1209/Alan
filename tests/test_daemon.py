"""Tests for the always-on daemon skeleton (Phase 2, section 4.1).

Two of these tests spawn a second process on purpose. Windows byte-range locks
are held *per process*, so two `SingleInstance` objects in one process will
both succeed -- an in-process contention test would pass on Windows while
proving nothing, and would fail to catch the exact bug the lock exists for.
Where the claim is about two processes, the test uses two processes.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from alan.config import Settings, _apply_tables
from alan.core.daemon import (
    MAX_CONSECUTIVE_FAILURES,
    Daemon,
    SingleInstance,
    resolve_daemon_paths,
    stop_flag_for,
)
from alan.core.timeline import TimelineStore

SRC = str(Path(__file__).resolve().parents[1] / "src")

#: How long a wait-for-condition helper may poll before giving up.
WAIT = 8.0


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, name: str, **fields) -> None:
        self.events.append((name, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


def _wait_until(predicate, timeout: float = WAIT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class DaemonConfigTests(unittest.TestCase):
    """Settings for the daemon, including the part that keeps it off.

    ``enabled`` defaults to False and the test saying so is load-bearing:
    section 4.3's "OFF by default" is easiest to honour at the one place it
    cannot be forgotten, and a default that silently flips is exactly how an
    observer ships turned on.
    """

    def test_defaults_are_off(self) -> None:
        cfg = Settings().daemon
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.lock_path, "")
        self.assertEqual(cfg.store_path, "")
        self.assertEqual(cfg.tick_seconds, 30.0)
        self.assertEqual(cfg.retention_days, 14.0)

    def test_table_is_parsed_and_clamped(self) -> None:
        settings = Settings()
        _apply_tables(
            settings,
            {
                "daemon": {
                    "enabled": "true",
                    "tick_seconds": "0.01",  # clamped up: would spin the disk
                    "retention_days": "99999",  # clamped down: not forever
                    "store_path": "/tmp/tl.db",
                }
            },
        )
        self.assertTrue(settings.daemon.enabled)
        self.assertEqual(settings.daemon.tick_seconds, 1.0)
        self.assertEqual(settings.daemon.retention_days, 3650.0)
        self.assertEqual(settings.daemon.store_path, "/tmp/tl.db")

    def test_absent_table_leaves_the_defaults_alone(self) -> None:
        settings = Settings()
        _apply_tables(settings, {})
        self.assertFalse(settings.daemon.enabled)

    def test_non_boolean_is_rejected_visibly(self) -> None:
        with self.assertRaises(ValueError):
            _apply_tables(Settings(), {"daemon": {"enabled": "maybe"}})

    def test_default_paths_sit_under_the_data_dir(self) -> None:
        from alan.config import DATA_DIR

        lock, store = resolve_daemon_paths(Settings())
        self.assertEqual(lock, DATA_DIR / "daemon" / "alan-daemon.lock")
        self.assertEqual(store, DATA_DIR / "timeline" / "timeline.db")

    def test_configured_paths_win_over_the_defaults(self) -> None:
        settings = Settings()
        settings.daemon.lock_path = "/tmp/x.lock"
        settings.daemon.store_path = "/tmp/x.db"
        lock, store = resolve_daemon_paths(settings)
        # Compared as paths, not strings: str(Path("/tmp/x.lock")) renders
        # "\tmp\x.lock" on Windows, and a separator assertion would be a test
        # of the OS rather than of this function.
        self.assertEqual(lock, Path("/tmp/x.lock"))
        self.assertEqual(store, Path("/tmp/x.db"))

    def test_the_cli_and_the_daemon_agree_on_the_kill_switch_path(self) -> None:
        """One formula, asserted rather than assumed.

        `alan daemon stop` writes a marker and the running loop reads it. If
        the two ever disagreed, stop would appear to succeed while the daemon
        ran on -- a stop command that silently does nothing is worse than one
        that errors, because nothing prompts a second look.

        Closed in a `finally` rather than via addCleanup: cleanups run after
        the test, which on Windows means after the directory is gone, and an
        open SQLite handle then makes the removal fail in teardown -- where the
        real fault is hardest to read.
        """
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "alan-daemon.lock"
            store = TimelineStore(Path(tmp) / "tl.db")
            try:
                daemon = Daemon(timeline=store, lock_path=lock, tick_seconds=0.05)
                self.assertEqual(daemon.stop_flag, stop_flag_for(lock))
            finally:
                store.close()


class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="alan_lock_")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "alan-daemon.lock"

    def test_acquire_records_our_pid_and_holds(self) -> None:
        lock = SingleInstance(self.path)
        self.addCleanup(lock.release)
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.held)
        self.assertEqual(lock.holder_pid(), os.getpid())

    def test_acquire_is_idempotent_while_already_held(self) -> None:
        lock = SingleInstance(self.path)
        self.addCleanup(lock.release)
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquire(), "a second call must not deadlock")

    def test_release_then_reacquire_succeeds(self) -> None:
        first = SingleInstance(self.path)
        self.assertTrue(first.acquire())
        first.release()
        self.assertFalse(first.held)

        second = SingleInstance(self.path)
        self.addCleanup(second.release)
        self.assertTrue(second.acquire(), "a released lock must be reacquirable")

    def test_holder_pid_is_zero_when_the_file_was_never_written(self) -> None:
        self.assertEqual(SingleInstance(self.path).holder_pid(), 0)

    def test_a_live_holder_blocks_another_process_and_a_dead_one_does_not(self) -> None:
        """The property the whole design rests on.

        A live holder must be detected, and -- the part worth the subprocess --
        a *dead* holder must need no cleanup: the OS drops its lock, so the
        next daemon simply starts. That is why there is no stale-lock sweeper
        and nothing for a user to be told to delete after a crash.
        """
        child_src = (
            "import sys, time\n"
            f"sys.path.insert(0, {SRC!r})\n"
            "from pathlib import Path\n"
            "from alan.core.daemon import SingleInstance\n"
            "lock = SingleInstance(Path(sys.argv[1]))\n"
            "assert lock.acquire(), 'child failed to acquire'\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_src, str(self.path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            line = child.stdout.readline()
            self.assertEqual(line.strip(), "LOCKED")

            blocked = SingleInstance(self.path)
            self.assertFalse(
                blocked.acquire(), "a live holder in another process must block us"
            )
            self.assertEqual(blocked.holder_pid(), child.pid)
        finally:
            child.kill()
            child.wait(timeout=WAIT)

        # The holder is gone. No cleanup, no sweeper, no manual step.
        recovered = SingleInstance(self.path)
        self.addCleanup(recovered.release)
        self.assertTrue(
            recovered.acquire(), "a dead holder's lock must clear on its own"
        )
        self.assertEqual(recovered.holder_pid(), os.getpid())


class DaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="alan_daemon_")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.bus = FakeBus()

    def _daemon(self, **kwargs) -> Daemon:
        path = self.root / f"tl_{len(self.bus.events)}_{time.time_ns()}.db"
        store = TimelineStore(path)
        # Registered after the temp dir, so LIFO closes it *before* the dir is
        # removed. Leaving a WAL-mode connection open makes the file
        # undeletable on Windows, which fails the test in cleanup -- where the
        # real failure is hardest to see.
        self.addCleanup(store.close)
        kwargs.setdefault("tick_seconds", 0.05)
        kwargs.setdefault("bus", self.bus)
        return Daemon(
            timeline=store,
            lock_path=self.root / "alan-daemon.lock",
            **kwargs,
        )

    # -- tick -------------------------------------------------------------

    def test_tick_records_a_heartbeat(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)

        notes = daemon.tick()

        self.assertEqual(daemon.timeline.count(), 1)
        self.assertEqual(daemon.timeline.recent(limit=1)[0].kind, "daemon.heartbeat")
        self.assertTrue(notes, "a tick reports what it did")

    def test_ticks_are_counted(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        daemon.tick()
        daemon.tick()
        self.assertEqual(daemon.ticks, 2)

    # -- lifecycle --------------------------------------------------------

    def test_start_and_stop_release_the_lock(self) -> None:
        daemon = self._daemon()
        self.assertTrue(daemon.start())
        self.assertTrue(daemon.running)
        self.assertTrue(daemon.lock.held)
        self.assertTrue(daemon.started_at, "status must be able to say since when")

        self.assertTrue(daemon.stop(), "stopping a running daemon reports so")
        self.assertFalse(daemon.running)
        self.assertFalse(daemon.lock.held, "a stopped daemon must not hold the lock")

    def test_start_is_idempotent(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        self.assertTrue(daemon.start())
        self.assertFalse(daemon.start(), "a second start must not spawn a second loop")

    def test_stop_is_safe_when_never_started(self) -> None:
        daemon = self._daemon()
        self.assertFalse(daemon.stop())

    def test_the_loop_keeps_ticking_on_its_own(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        self.assertTrue(daemon.start())

        self.assertTrue(
            _wait_until(lambda: daemon.ticks >= 3),
            f"expected the loop to tick, saw {daemon.ticks}",
        )

    def test_status_reports_the_shape_of_the_run(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        status = daemon.status()
        for key in ("running", "ticks", "lock", "lock_held", "tick_seconds"):
            self.assertIn(key, status)
        self.assertFalse(status["running"])

    # -- the kill switch --------------------------------------------------

    def test_the_stop_flag_stops_a_running_daemon(self) -> None:
        """The kill switch is a file so it works from another process.

        Windows has no graceful process signal, so `alan daemon stop` writes
        this marker and the loop leaves on its own -- which is what releases
        the lock and flushes the store. A kill by pid would do none of that.
        """
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        self.assertTrue(daemon.start())
        self.assertTrue(_wait_until(lambda: daemon.ticks >= 1))

        daemon.stop_flag.write_text("now", encoding="utf-8")

        self.assertTrue(
            _wait_until(lambda: not daemon.running),
            "the loop must notice the flag and exit",
        )
        self.assertIn("daemon_kill_switch", self.bus.names())

    def test_a_stale_stop_flag_from_a_previous_life_is_cleared_on_start(self) -> None:
        """An unclean exit must not kill the daemon that replaces it."""
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        daemon.stop_flag.write_text("left over", encoding="utf-8")

        self.assertTrue(daemon.start())
        self.assertFalse(
            daemon.stop_flag.exists(), "the new run must not inherit the old flag"
        )
        self.assertTrue(daemon.running)

    def test_request_stop_writes_the_flag_for_another_process(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        daemon.request_stop()
        self.assertTrue(daemon.stop_flag.exists())

    # -- failure ----------------------------------------------------------

    def test_a_failing_tick_is_survived_and_counted(self) -> None:
        """One bad tick must not take the loop down.

        A daemon that dies on the first error is not always-on, and the error
        is the least useful time to lose the process.
        """
        daemon = self._daemon()
        self.addCleanup(daemon.stop)

        def boom(now=None):
            raise RuntimeError("sensor exploded")

        daemon.tick = boom  # type: ignore[method-assign]
        self.assertTrue(daemon.start())

        self.assertTrue(
            _wait_until(lambda: daemon.consecutive_failures >= 1),
            "the failure must be counted, not swallowed",
        )
        self.assertTrue(daemon.running, "one failure is survivable")
        self.assertIn("daemon_tick_failed", self.bus.names())

    def test_a_run_of_failures_stops_the_daemon_rather_than_spinning(self) -> None:
        """Structural failure stops the loop; it does not burn CPU forever.

        "Always-on" is not the same as "always retrying the same broken thing",
        and a daemon that logs the same error every 30 seconds forever is a
        worse failure than one that stops and says why.
        """
        daemon = self._daemon()
        self.addCleanup(daemon.stop)

        def boom(now=None):
            raise RuntimeError("structural")

        daemon.tick = boom  # type: ignore[method-assign]
        self.assertTrue(daemon.start())

        self.assertTrue(
            _wait_until(lambda: not daemon.running),
            "the daemon must stop itself after the failure budget",
        )
        self.assertGreaterEqual(daemon.consecutive_failures, MAX_CONSECUTIVE_FAILURES)
        self.assertIn("daemon_stopped_on_failures", self.bus.names())

    def test_a_success_resets_the_failure_run(self) -> None:
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        daemon.consecutive_failures = 3
        daemon.tick()
        self.assertEqual(daemon.consecutive_failures, 0)

    # -- the incident these two tests exist for ----------------------------

    def test_the_loop_thread_can_write_what_it_observes(self) -> None:
        """sqlite3 binds a connection to the thread that opened it.

        A store built on the main thread raised ProgrammingError the instant
        the loop thread ticked. Every tick failed, no row was ever written,
        and -- because tick() cleared the failure run before appending -- the
        circuit breaker never fired either. The result was a daemon that held
        its lock, reported "running", and did nothing, forever, with no error
        anywhere. Cross-thread use is the design, so it is tested as one.
        """
        daemon = self._daemon()
        self.addCleanup(daemon.stop)
        self.assertTrue(daemon.start())

        self.assertTrue(
            _wait_until(lambda: daemon.timeline.count() >= 2),
            f"the loop thread must be able to append; saw "
            f"{daemon.timeline.count()} row(s) and "
            f"{daemon.consecutive_failures} failure(s)",
        )
        self.assertEqual(
            daemon.consecutive_failures,
            0,
            "a thread that cannot reach storage is not a recovered failure",
        )

    def test_broken_storage_stops_the_daemon_instead_of_spinning(self) -> None:
        """The failure the original ordering hid.

        `tick()` reset its counter before doing any work, so a tick that
        always failed alternated 0 -> 1 -> 0 and never reached the budget.
        The breaker existed and was unreachable, which is worse than not
        having one: storage stays broken while the daemon keeps claiming to
        run. Ordering the reset after the work is what makes the budget real.
        """
        daemon = self._daemon()
        self.addCleanup(daemon.stop)

        def broken(event) -> int:
            raise sqlite3.OperationalError("disk full")

        daemon.timeline.append = broken  # type: ignore[method-assign]
        self.assertTrue(daemon.start())

        self.assertTrue(
            _wait_until(lambda: not daemon.running),
            "a daemon that cannot write must stop itself, not spin on the lock",
        )
        self.assertIn("daemon_stopped_on_failures", self.bus.names())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
