"""The always-on scheduler and approval queue (Phase 1).

Behaviour pinned here, not implementation:

* ``parse_when`` reads the phrases people actually type, in any word order,
  and a phrase it cannot read is an error -- never a guess that fires at the
  wrong time forever;
* the scheduler runs due work one task at a time, records each outcome, and
  a task that stalls at a permission gate becomes ``waiting_approval`` instead
  of failing silently;
* approving re-queues the waiting task the runner would otherwise have to
  nag about; rejecting pauses it (no retry loop);
* run outcomes and the task list survive a restart through the atomically
  written store, and a corrupt file cannot brick the assistant.

Times are injected (``now_fn`` / ``now=``), so no test depends on a clock.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alan.core.events import EventBus, Events
from alan.core.scheduler import (
    RUN_HISTORY,
    Scheduler,
    ScheduleStore,
    ScheduledTask,
    LocalRunner,
    RunOutcome,
    TaskRunner,
    WhenSpec,
    next_occurrence,
    parse_when,
    WHEN_HELP,
)
from alan.interfaces.types import PermissionRequest, RiskLevel
from alan.permissions.queue import ApprovalQueue

NOW = datetime(2026, 10, 8, 10, 0).astimezone()
NOON = datetime(2026, 10, 8, 12, 0).astimezone()


def _store(tmp: Path) -> ScheduleStore:
    return ScheduleStore(tmp / "schedule.json")


def _scheduler(
    store: ScheduleStore,
    runner: TaskRunner,
    *,
    approvals=None,
    tick_seconds: float = 0.05,
    task_timeout: float = 5.0,
    now=None,
) -> Scheduler:
    return Scheduler(
        store,
        runner,
        approvals=approvals,
        tick_seconds=tick_seconds,
        task_timeout=task_timeout,
        now_fn=lambda: now or NOW,
    )


def _ok_runner(text: str = "done") -> LocalRunner:
    return LocalRunner(lambda req, sid: text)


class TestParseWhen(unittest.TestCase):
    def test_every_n_minutes(self) -> None:
        spec = parse_when("every 30 minutes", now=NOW)
        self.assertEqual(spec.kind, "interval")
        self.assertEqual(spec.every_seconds, 1800.0)
        self.assertEqual(spec.at_time, "")
        self.assertEqual(spec.first_at, NOW + timedelta(minutes=30))

    def test_every_day_anchored_is_a_recurring_clock(self) -> None:
        spec = parse_when("every day at 07:30", now=NOW)
        self.assertEqual(spec.kind, "interval")
        self.assertEqual(spec.at_time, "07:30")
        self.assertEqual(spec.every_seconds, 86400.0)

    def test_named_times_of_day(self) -> None:
        for phrase, clock in [("every morning", "08:00"), ("every night", "21:00")]:
            spec = parse_when(phrase, now=NOW)
            self.assertEqual(spec.kind, "interval", phrase)
            self.assertEqual(spec.at_time, clock, phrase)

    def test_bare_clock_is_a_one_shot(self) -> None:
        spec = parse_when("at 18:45", now=NOW)
        self.assertEqual(spec.kind, "once")
        self.assertEqual(spec.first_at, datetime(2026, 10, 8, 18, 45).astimezone())

    def test_at_7am_is_am_pm_smart(self) -> None:
        spec = parse_when("at 7am", now=NOW)  # 07:00 already passed today
        self.assertEqual(spec.kind, "once")
        self.assertEqual(spec.first_at.date(), datetime(2026, 10, 9).date())
        self.assertEqual(spec.first_at.hour, 7)

    def test_tomorrow_at_advances_exactly_one_day(self) -> None:
        spec = parse_when("tomorrow at 09:00", now=NOW)
        self.assertEqual(spec.first_at, datetime(2026, 10, 9, 9, 0).astimezone())

    def test_in_delay(self) -> None:
        spec = parse_when("in 2 hours", now=NOW)
        self.assertEqual(spec.kind, "once")
        self.assertEqual(spec.first_at, NOW + timedelta(hours=2))

    def test_daily_and_weekly(self) -> None:
        self.assertEqual(parse_when("daily", now=NOW).every_seconds, 86400.0)
        self.assertEqual(parse_when("weekly", now=NOW).every_seconds, 604800.0)

    def test_daily_anchor_is_order_independent(self) -> None:
        a = parse_when("at 07:30 daily", now=NOW)
        b = parse_when("daily at 07:30", now=NOW)
        self.assertEqual(a.at_time, "07:30")
        self.assertEqual(a.kind, b.kind)
        self.assertEqual(a.at_time, b.at_time)
        self.assertEqual(a.every_seconds, b.every_seconds)

    def test_weekly_keeps_clock_as_first_run_only(self) -> None:
        spec = parse_when("weekly at 09:00", now=NOW)
        self.assertEqual(spec.kind, "interval")
        self.assertEqual(spec.every_seconds, 604800.0)
        self.assertEqual(spec.first_at.hour, 9)
        # at_time must never be set on a non-daily cadence: the scheduler
        # treats at_time as a daily re-anchor, and a weekly task that runs
        # daily is the kind of bug this function exists to prevent.
        self.assertEqual(spec.at_time, "")

    def test_today_at_in_the_past_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "passed today"):
            parse_when("today at 09:00", now=NOW)

    def test_unparseable_phrase_is_an_error_not_a_guess(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_when("nonsense phrase", now=NOW)
        self.assertIn("Try forms like", str(ctx.exception))
        with self.assertRaises(ValueError):
            parse_when("", now=NOW)

    def test_twice_a_day_spaces_dont_reanchor(self) -> None:
        spec = parse_when("twice a day at 07:30", now=NOW)
        self.assertEqual(spec.kind, "interval")
        self.assertEqual(spec.every_seconds, 172800.0)
        self.assertEqual(spec.at_time, "")


class TestNextOccurrence(unittest.TestCase):
    def test_future_today(self) -> None:
        self.assertEqual(
            next_occurrence("10:30", NOW), datetime(2026, 10, 8, 10, 30).astimezone()
        )

    def test_passed_rolls_to_tomorrow(self) -> None:
        self.assertEqual(
            next_occurrence("09:00", NOW), datetime(2026, 10, 9, 9, 0).astimezone()
        )

    def test_bad_time_is_none(self) -> None:
        self.assertIsNone(next_occurrence("25:99", NOW))
        self.assertIsNone(next_occurrence("oops", NOW))


class TestScheduleStore(unittest.TestCase):
    def test_round_trip_through_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            task = ScheduledTask(id="tsk_a", request="check the news")
            store.add(task)
            again = ScheduleStore(Path(tmp) / "schedule.json")
            self.assertEqual(again.get("tsk_a").request, "check the news")
            self.assertEqual(again.list()[0].id, "tsk_a")

    def test_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.add(ScheduledTask(id="tsk_a", request="x"))
            self.assertTrue(store.remove("tsk_a"))
            self.assertFalse(store.remove("tsk_a"))
            self.assertEqual(store.list(), [])

    def test_duplicate_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.add(ScheduledTask(id="tsk_a", request="x"))
            with self.assertRaises(ValueError):
                store.add(ScheduledTask(id="tsk_a", request="y"))

    def test_running_status_never_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            store = ScheduleStore(path)
            store.add(ScheduledTask(id="tsk_a", request="x", status="running"))
            again = ScheduleStore(path)
            self.assertEqual(again.get("tsk_a").status, "pending")

    def test_corrupt_file_is_preserved_and_does_not_brick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            path.write_text("{not json", encoding="utf-8")
            store = ScheduleStore(path)
            self.assertEqual(store.list(), [])
            self.assertTrue(store.load_error)
            # The corrupt original survived for a human to inspect.
            survivors = [p for p in Path(tmp).glob("schedule.json.corrupt-*")]
            self.assertEqual(len(survivors), 1)

    def test_bad_row_does_not_delete_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            path.write_text(
                '{"tasks": [{"id": "ok", "request": "fine"}, '
                '{"id": "bad", "request": "x", "every_seconds": "abc"}, '
                '{"id": "ok2", "request": "also fine"}]}',
                encoding="utf-8",
            )
            store = ScheduleStore(path)
            # "bad" fails to convert its spacing; both neighbours survive.
            self.assertEqual([t.id for t in store.list()], ["ok", "ok2"])


class TestSchedulerAuthoring(unittest.TestCase):
    def test_add_requires_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            with self.assertRaises(ValueError):
                sched.add("   ")
            with self.assertRaises(ValueError):
                sched.add("", kind="interval")

    def test_interval_requires_a_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            with self.assertRaises(ValueError):
                sched.add("x", kind="interval")
            with self.assertRaises(ValueError):
                sched.add("x", kind="interval", every_seconds=-1)

    def test_add_persists_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            sched = _scheduler(ScheduleStore(path), _ok_runner())
            task = sched.add("water the plants")
            self.assertEqual(ScheduleStore(path).get(task.id).request, "water the plants")

    def test_add_with_bad_time_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            with self.assertRaises(ValueError):
                sched.add("x", kind="interval", at_time="25:99")


class TestSchedulerExecution(unittest.TestCase):
    def test_once_task_runs_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            sched = _scheduler(ScheduleStore(path), _ok_runner("ok: checked"))
            task = sched.add("check the news")
            ran = sched.tick(now=NOON)
            self.assertEqual(ran, [task.id])
            state = ScheduleStore(path).get(task.id)
            self.assertEqual(state.status, "done")
            self.assertEqual(state.run_count, 1)
            self.assertTrue(state.runs[-1]["ok"])

    def test_interval_task_repeats_and_moves_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            task = sched.add("poll", kind="interval", every_seconds=60.0)
            sched.tick(now=NOON)
            state = sched.store.get(task.id)
            self.assertEqual(state.status, "pending")
            self.assertEqual(state.run_count, 1)
            self.assertEqual(state.next_run_dt(), NOON + timedelta(minutes=1))

    def test_failed_interval_reschedules_but_marks_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), LocalRunner(lambda r, s: (_ for _ in ()).throw(RuntimeError("boom"))))
            task = sched.add("poll", kind="interval", every_seconds=60.0)
            sched.tick(now=NOON)
            state = sched.store.get(task.id)
            self.assertEqual(state.status, "pending")
            self.assertFalse(state.runs[-1]["ok"])
            self.assertIn("boom", state.runs[-1]["error"])

    def test_failed_once_task_is_failed_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), LocalRunner(lambda r, s: (_ for _ in ()).throw(RuntimeError("boom"))))
            task = sched.add("check")
            sched.tick(now=NOON)
            self.assertEqual(sched.store.get(task.id).status, "failed")

    def test_pause_resume_run_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            task = sched.add("check")
            sched.pause(task.id)
            self.assertEqual(sched.store.get(task.id).status, "paused")
            # a paused task does not run on its own
            self.assertEqual(sched.tick(now=NOON), [])
            sched.resume(task.id)
            self.assertEqual(sched.store.get(task.id).status, "pending")
            sched.run_now(task.id)
            self.assertEqual(sched.tick(now=NOON), [task.id])

    def test_runs_history_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _scheduler(_store(Path(tmp)), _ok_runner())
            task = sched.add("poll", kind="interval", every_seconds=1.0)
            now = NOW
            for _ in range(RUN_HISTORY + 5):
                now = now + timedelta(seconds=1)
                sched.tick(now=now)
            self.assertLessEqual(len(sched.store.get(task.id).runs), RUN_HISTORY)

    def test_timed_out_worker_is_recorded_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            slow = LocalRunner(lambda r, s: time.sleep(1.0) or "never seen")
            sched = _scheduler(_store(Path(tmp)), slow, task_timeout=0.2)
            task = sched.add("slow")
            sched.tick(now=NOON)
            self.assertEqual(sched.store.get(task.id).status, "failed")
            self.assertIn("timed out", sched.store.get(task.id).runs[-1]["error"])


class TestApprovalInteraction(unittest.TestCase):
    def test_run_stopping_at_the_gate_becomes_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            approvals = ApprovalQueue()

            def gate_stopper(request: str, session_id: str) -> str:
                # The real flow: the gate's routed confirmer files an approval
                # and returns False; the run records that it stopped at the gate.
                approvals.confirmer(
                    PermissionRequest(
                        "filesystem.delete", RiskLevel.HIGH_RISK, session_id=session_id
                    ),
                    task_id=session_id[len("sched-"):],
                )
                return "stopped at the gate"

            sched = _scheduler(
                _store(Path(tmp)), LocalRunner(gate_stopper), approvals=approvals
            )
            approvals.set_on_decide(sched.on_approval_decided)
            task = sched.add("clean temp")
            sched.tick(now=NOON)
            state = sched.store.get(task.id)
            self.assertEqual(state.status, "waiting_approval")
            self.assertTrue(state.blocked_by)

    def test_approving_requeues_the_waiting_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            approvals = ApprovalQueue()
            sched = _scheduler(
                _store(Path(tmp)), _ok_runner(), approvals=approvals
            )
            approvals.set_on_decide(sched.on_approval_decided)
            task = sched.add("clean temp")
            # a waiting task, exactly as the gate run above leaves it
            approval = approvals.submit(
                PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK),
                source="scheduler",
                task_id=task.id,
            )
            state = sched.store.get(task.id)
            state.status = "waiting_approval"
            state.blocked_by = approval.id
            sched.store.persist()

            approvals.decide(approval.id, True)
            state = sched.store.get(task.id)
            self.assertEqual(state.status, "pending")
            self.assertEqual(state.blocked_by, "")
            self.assertTrue(state.due(NOON), "approving re-queues immediately")

    def test_rejecting_pauses_and_cannot_auto_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            approvals = ApprovalQueue()
            sched = _scheduler(
                _store(Path(tmp)), _ok_runner(), approvals=approvals
            )
            approvals.set_on_decide(sched.on_approval_decided)
            task = sched.add("clean temp")
            approval = approvals.submit(
                PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK),
                source="scheduler",
                task_id=task.id,
            )
            state = sched.store.get(task.id)
            state.status = "waiting_approval"
            state.blocked_by = approval.id
            sched.store.persist()
            # A rejection is also not cached: re-running is an explicit operator
            # action, which is what stops "no" from becoming a nag loop.
            approvals.decide(approval.id, False)
            self.assertEqual(sched.store.get(task.id).status, "paused")
            self.assertFalse(approvals.confirmer(
                PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK),
                task_id="tsk_other",
            ))

    def test_decide_notifies_via_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            approvals = ApprovalQueue()
            calls = []
            approvals.set_on_decide(lambda a: calls.append((a.id, a.status)))
            approval = approvals.submit(
                PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK)
            )
            approvals.decide(approval.id, True)
            self.assertEqual(calls, [(approval.id, "approved")])


class TestLocalRunner(unittest.TestCase):
    def test_clips_long_outputs(self) -> None:
        runner = LocalRunner(lambda r, s: "x" * 1000, summary_chars=50)
        outcome = runner.run(SimpleNamespace(id="tsk_x", request="r"))
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.summary), 50)

    def test_accepts_agent_result_shapes(self) -> None:
        class AgentLike:
            success = False
            output = ""
            error = "nope"

        runner = LocalRunner(lambda r, s: AgentLike())
        outcome = runner.run(SimpleNamespace(id="tsk_x", request="r"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "nope")

    def test_a_runner_exception_is_an_outcome_not_a_crash(self) -> None:
        def explode(request, session_id):
            raise RuntimeError("run exploded")

        outcome = LocalRunner(explode).run(SimpleNamespace(id="tsk_x", request="r"))
        self.assertFalse(outcome.ok)
        self.assertIn("exploded", outcome.error)


class TestApprovalQueue(unittest.TestCase):
    def _request(self, task_id: str = "") -> PermissionRequest:
        return PermissionRequest(
            "filesystem.delete", RiskLevel.HIGH_RISK,
            arguments={"path": "D:/tmp/x"}, session_id=f"sched-{task_id or 'x'}",
        )

    def test_fingerprint_is_stable_across_runs(self) -> None:
        queue = ApprovalQueue()
        self.assertFalse(queue.confirmer(self._request("a")))
        first = queue.pending()[0]
        queue.decide(first.id, True)
        # Same action, same arguments -> the cached approval answers it.
        self.assertTrue(queue.confirmer(self._request("a")))

    def test_different_arguments_are_not_the_same_approval(self) -> None:
        queue = ApprovalQueue()
        queue.confirmer(self._request("a"))
        queue.decide(queue.pending()[0].id, True)
        other = PermissionRequest(
            "filesystem.delete", RiskLevel.HIGH_RISK,
            arguments={"path": "D:/elsewhere"}, session_id="sched-a",
        )
        self.assertFalse(queue.confirmer(other))

    def test_same_pending_action_is_deduplicated(self) -> None:
        queue = ApprovalQueue()
        queue.confirmer(self._request("a"))
        again = queue.confirmer(self._request("a"))
        self.assertEqual(len(queue.pending()), 1)
        self.assertFalse(again)

    def test_decide_by_task_targets_the_newest(self) -> None:
        queue = ApprovalQueue()
        queue.confirmer(self._request("a"), task_id="tsk_a")
        queue.confirmer(self._request("b"), task_id="tsk_b")
        result = queue.decide_by_task("tsk_a", True)
        self.assertEqual(result.status, "approved")

    def test_rejections_leave_no_cache(self) -> None:
        queue = ApprovalQueue()
        face = queue.confirmer(self._request("a"))
        self.assertFalse(face)
        queue.decide(queue.pending()[0].id, False)
        self.assertFalse(queue.confirmer(self._request("a")))
        self.assertEqual(len(queue.history()), 1)


class TestSchedulerEvents(unittest.TestCase):
    def test_loop_and_run_events_are_emitted(self) -> None:
        bus = EventBus()
        seen = []

        def watch(event) -> None:
            seen.append(event.name)

        for pattern in (
            Events.SCHEDULER_STARTED,
            Events.TASK_SCHEDULED,
            Events.TASK_STARTED,
            Events.TASK_FINISHED,
            Events.TASK_FAILED,
            Events.TASK_WAITING,
            Events.TASK_PAUSED,
            Events.TASK_RESUMED,
            Events.APPROVAL_SUBMITTED,
            Events.APPROVAL_DECIDED,
        ):
            bus.subscribe(pattern, watch)

        with tempfile.TemporaryDirectory() as tmp:
            approvals = ApprovalQueue()
            sched = Scheduler(
                _store(Path(tmp)), _ok_runner(), bus=bus, approvals=approvals,
                tick_seconds=0.02, now_fn=lambda: NOW,
            )
            approvals.set_on_decide(sched.on_approval_decided)
            task = sched.add("check")
            sched.start()
            time.sleep(0.15)
            sched.stop()
        self.assertIn(Events.SCHEDULER_STARTED, seen)
        self.assertIn(Events.TASK_SCHEDULED, seen)
        self.assertIn(Events.TASK_STARTED, seen)
        self.assertIn(Events.TASK_FINISHED, seen)


if __name__ == "__main__":
    unittest.main()