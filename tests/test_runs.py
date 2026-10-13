"""Task/run bookkeeping: leases, fencing tokens, waiting-input, recovery.

Ported from rakazo's Task/Run rows. The properties under test are the ones
that make an always-on agent safe:

* only one holder may claim a task, and only while its lease is live;
* a commit requires *both* the holder and the current fence, so a stalled
  worker cannot overwrite a newer attempt;
* an expired lease interrupts the run instead of silently re-queueing it;
* waiting for input is a state, not a failure, and does not hold a lease;
* an idempotency key stops a retried submit creating a second task.
"""

from __future__ import annotations

import time

from alan.core.runs import (
    ST_CANCELLED,
    ST_COMPLETED,
    ST_FAILED,
    ST_INTERRUPTED,
    ST_LEASED,
    ST_QUEUED,
    ST_RUNNING,
    ST_WAITING_INPUT,
    RunStore,
    summarise,
)


def _store(tmp_path=None, **kw) -> RunStore:
    return RunStore((tmp_path / "runs.json") if tmp_path else None, **kw)


# -- claiming and leases --------------------------------------------------

def test_create_and_claim(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("do the thing", agent="coding")
    run = store.claim(task.id, holder="w1")
    assert run is not None
    assert run.status == ST_LEASED
    assert run.holder == "w1"
    assert run.fence == 1
    assert store.task(task.id).status == ST_LEASED


def test_second_claim_refused_while_leased(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    assert store.claim(task.id, holder="w1") is not None
    assert store.claim(task.id, holder="w2") is None


def test_claim_after_release_makes_a_new_attempt_with_a_new_fence(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    first = store.claim(task.id, holder="w1")
    store.finish(first.id, holder="w1", fence=first.fence, status=ST_FAILED)

    second = store.claim(task.id, holder="w2")
    assert second is not None
    assert second.attempt == 2
    assert second.fence > first.fence


def test_claim_refused_for_settled_task(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    store.finish(run.id, holder="w1", fence=run.fence, status=ST_COMPLETED)
    assert store.claim(task.id, holder="w2") is None


def test_lease_expiry_interrupts_instead_of_requeueing(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1", lease_seconds=0.0)
    time.sleep(0.01)

    # Any later operation notices the expired lease.
    assert store.task(task.id).status == ST_INTERRUPTED
    # And it was NOT requeued: a crashed attempt must be looked at, not rerun.
    assert store.claim(task.id, holder="w2") is None
    assert store.runs(task.id)[0].id == run.id
    assert store.runs(task.id)[0].status == ST_INTERRUPTED


def test_expired_lease_then_reopen_allows_a_fresh_claim(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    store.claim(task.id, holder="w1", lease_seconds=0.0)
    time.sleep(0.01)
    store.task(task.id)  # trigger expiry

    reopened = store.reopen(task.id)
    assert reopened.status == ST_QUEUED
    fresh = store.claim(task.id, holder="w2")
    assert fresh is not None
    assert fresh.attempt == 2


# -- fencing --------------------------------------------------------------

def test_stale_holder_cannot_commit(tmp_path) -> None:
    """The fencing token: a worker past its lease must not write."""
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1", lease_seconds=0.0)
    fence = run.fence
    time.sleep(0.01)
    store.task(task.id)  # expire: run becomes interrupted, task interrupted

    # The old holder wakes up and tries to commit with its now-stale fence.
    assert store.finish(run.id, holder="w1", fence=fence, status=ST_COMPLETED) is False
    assert store.task(task.id).status == ST_INTERRUPTED


def test_wrong_holder_cannot_commit_even_with_the_right_fence(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    assert store.finish(run.id, holder="w2", fence=run.fence) is False
    assert store.finish(run.id, holder="w1", fence=run.fence + 1) is False
    assert store.finish(run.id, holder="w1", fence=run.fence) is True


def test_heartbeat_rejects_a_stale_fence(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    assert store.heartbeat(run.id, holder="w1", fence=run.fence) is True
    assert store.heartbeat(run.id, holder="w1", fence=run.fence - 1) is False
    assert store.heartbeat(run.id, holder="other", fence=run.fence) is False


def test_start_moves_leased_to_running(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    assert store.start(run.id, holder="w1", fence=run.fence) is True
    assert store.task(task.id).status == ST_RUNNING
    # A finished run cannot be started again.
    store.finish(run.id, holder="w1", fence=run.fence, status=ST_COMPLETED)
    assert store.start(run.id, holder="w1", fence=run.fence) is False


# -- waiting input --------------------------------------------------------

def test_waiting_input_releases_the_lease(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    store.start(run.id, holder="w1", fence=run.fence)

    assert store.request_input(
        run.id, holder="w1", fence=run.fence, question="which repo?",
        options=["a", "b"],
    ) is True
    assert store.task(task.id).status == ST_WAITING_INPUT
    # The lease is free: someone else could claim, but a paused task refuses.
    assert store.claim(task.id, holder="w2") is None


def test_supplying_input_queues_a_fresh_attempt(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    store.request_input(run.id, holder="w1", fence=run.fence, question="q?")

    answered = store.supply_input(task.id, "the answer")
    assert answered.status == ST_QUEUED
    assert store.answer(task.id) == "the answer"

    fresh = store.claim(task.id, holder="w1")
    assert fresh is not None
    assert fresh.attempt == 2


def test_supplying_input_on_a_non_paused_task_is_a_noop(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    assert store.supply_input(task.id, "hi") is None


def test_stale_holder_cannot_request_input_after_losing_the_lease(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    assert store.request_input(
        run.id, holder="w2", fence=run.fence, question="q?"
    ) is False


# -- idempotency and cancel ----------------------------------------------

def test_same_key_returns_the_same_task(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create("x", key="k")
    b = store.create("x", key="k")
    assert a.id == b.id
    assert len(store) == 1
    assert store.task_by_key("k").id == a.id


def test_attempts_are_bounded_then_reopen_resets_the_runway(tmp_path) -> None:
    """A persistently failing job must stop on its own, not spin forever."""
    store = _store(tmp_path)
    task = store.create("x")
    task.max_attempts = 2

    first = store.claim(task.id, holder="w1")
    store.finish(first.id, holder="w1", fence=first.fence, status=ST_FAILED)
    second = store.claim(task.id, holder="w1")
    store.finish(second.id, holder="w1", fence=second.fence, status=ST_FAILED)

    # Out of attempts: refused, and the task says why.
    assert store.claim(task.id, holder="w1") is None
    failed = store.task(task.id)
    assert failed.status == ST_FAILED
    assert "gave up after 2" in failed.error

    # The operator can give it a fresh runway explicitly.
    reopened = store.reopen(task.id)
    assert reopened.status == ST_QUEUED
    assert store.claim(task.id, holder="w1") is not None


def test_cancel_stops_an_active_run(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.create("x")
    run = store.claim(task.id, holder="w1")
    assert store.cancel(task.id, "operator said stop") is True
    assert store.task(task.id).status == ST_CANCELLED
    assert store.latest_run(task.id).status == ST_CANCELLED
    # Cancelling a settled task is refused.
    assert store.cancel(task.id) is False


# -- persistence and recovery --------------------------------------------

def test_state_survives_a_reload(tmp_path) -> None:
    path = tmp_path / "runs.json"
    first = RunStore(path)
    task = first.create("x", key="k")
    run = first.claim(task.id, holder="w1")
    first.finish(run.id, holder="w1", fence=run.fence, status=ST_COMPLETED, result={"out": 1})

    second = RunStore(path)
    reloaded = second.task(task.id)
    assert reloaded.status == ST_COMPLETED
    assert reloaded.result == {"out": 1}
    assert second.answer(task.id) == ""
    assert len(second.runs(task.id)) == 1


def test_recover_marks_a_crashed_lease(tmp_path) -> None:
    path = tmp_path / "runs.json"
    first = RunStore(path)
    task = first.create("x")
    first.claim(task.id, holder="w1", lease_seconds=0.0)
    time.sleep(0.01)

    second = RunStore(path)
    found = second.recover()
    assert len(found) == 1
    assert found[0].status == ST_INTERRUPTED
    assert second.task(task.id).status == ST_INTERRUPTED
    # And the interruption is durable, not just in memory.
    third = RunStore(path)
    assert third.task(task.id).status == ST_INTERRUPTED


def test_corrupt_file_is_ignored(tmp_path) -> None:
    path = tmp_path / "runs.json"
    path.write_text("not json at all", encoding="utf-8")
    store = RunStore(path)
    assert len(store) == 0
    assert store.create("x").id


def test_no_path_means_memory_only() -> None:
    store = RunStore(None)
    task = store.create("x")
    assert store.claim(task.id, holder="w") is not None


# -- reporting ------------------------------------------------------------

def test_summarise_counts_by_status(tmp_path) -> None:
    store = _store()
    a = store.create("a")
    b = store.create("b")
    c = store.create("c")
    store.claim(a.id, holder="w")
    run = store.claim(b.id, holder="w")
    store.finish(run.id, holder="w", fence=run.fence, status=ST_COMPLETED)
    store.cancel(c.id)

    counts = summarise(store.tasks())
    assert counts[ST_LEASED] == 1
    assert counts[ST_COMPLETED] == 1
    assert counts[ST_CANCELLED] == 1


def test_events_are_emitted(tmp_path) -> None:
    from alan.core.events import EventBus

    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("task.*", lambda e: seen.append(e.name))
    bus.subscribe("run.*", lambda e: seen.append(e.name))
    store = RunStore(tmp_path / "r.json", bus=bus)
    task = store.create("x")
    run = store.claim(task.id, holder="w")
    store.finish(run.id, holder="w", fence=run.fence, status=ST_COMPLETED)

    assert "task.created" in seen
    assert "run.claimed" in seen
    assert "run.finished" in seen
