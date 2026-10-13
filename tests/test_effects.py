"""The effect ledger (ported from rakazo's ExternalEffect records).

What is under test:

* the three-write protocol (intent -> applied/failed) is durable;
* a process that dies mid-effect surfaces the record as INTERRUPTED rather
  than silently retrying or silently skipping;
* an idempotency key makes a repeated call inside one run return the recorded
  output instead of executing twice;
* an effect outside the run scope is *not* deduplicated;
* a corrupt ledger file does not stop the system starting;
* a settled record is never evicted by the size cap while an unsettled one is.
"""

from __future__ import annotations

import json

import pytest

from alan.core.effects import (
    Effect,
    EffectLedger,
    EffectStatus,
    effect_key,
    summarise,
)
from alan.tools.registry import ToolRegistry
from alan.permissions.gate import default_gate
from alan.interfaces.types import RiskLevel, ToolSpec


def test_key_is_deterministic_and_order_independent() -> None:
    a = effect_key("shell.execute", {"cmd": "ls", "cwd": "/tmp"}, "run1")
    b = effect_key("shell.execute", {"cwd": "/tmp", "cmd": "ls"}, "run1")
    assert a == b
    assert a != effect_key("shell.execute", {"cmd": "ls", "cwd": "/tmp"}, "run2")
    assert a != effect_key("shell.execute", {"cmd": "ls"}, "run1")


def test_begin_marks_intent_and_survives_reload(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path)
    effect = ledger.begin("fs.write", {"path": "/tmp/x"}, key="k1", run_id="r1")
    assert effect.status is EffectStatus.INTENT

    reloaded = EffectLedger(path)
    # An unsettled effect found on disk is a crash: it is now INTERRUPTED.
    assert reloaded.status("k1") is EffectStatus.INTERRUPTED
    assert len(reloaded.interrupted()) == 1


def test_recover_returns_the_interrupted_effects(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path)
    ledger.begin("fs.write", {"path": "/tmp/x"}, key="k1")
    ledger.begin("shell.execute", {"cmd": "ls"}, key="k2")
    ledger.mark_applied("k2", output="ok")

    reopened = EffectLedger(path)
    found = reopened.recover()
    assert [e.key for e in found] == ["k1"]
    assert reopened.status("k1") is EffectStatus.INTERRUPTED
    assert reopened.status("k2") is EffectStatus.APPLIED


def test_applied_effect_is_reported_and_not_rerun(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path)
    ledger.begin("fs.write", {"path": "/tmp/x"}, key="k1", run_id="r1")
    ledger.mark_applied("k1", output="written")

    again = ledger.begin("fs.write", {"path": "/tmp/x"}, key="k1", run_id="r1")
    assert again.status is EffectStatus.APPLIED
    assert ledger.is_applied("k1")
    assert ledger.replay("k1") == {"output": "written", "ok": True}


def test_failed_effect_can_be_retried() -> None:
    ledger = EffectLedger(None)
    ledger.begin("fs.write", {"p": 1}, key="k1")
    ledger.mark_failed("k1", "disk full")
    assert ledger.status("k1") is EffectStatus.FAILED

    retried = ledger.begin("fs.write", {"p": 1}, key="k1")
    assert retried.status is EffectStatus.INTENT
    assert ledger.status("k1") is EffectStatus.INTENT


def test_unknown_key_lookups_are_none() -> None:
    ledger = EffectLedger(None)
    assert ledger.get("nope") is None
    assert ledger.status("nope") is None
    assert ledger.is_applied("nope") is False
    assert ledger.replay("nope") is None
    assert ledger.mark_applied("nope") is None
    assert ledger.mark_failed("nope", "x") is None


def test_summary_counts_by_status() -> None:
    ledger = EffectLedger(None)
    ledger.begin("a", {}, key="1")
    ledger.begin("b", {}, key="2")
    ledger.mark_applied("2", output=1)
    ledger.begin("c", {}, key="3")
    ledger.mark_failed("3", "boom")

    counts = summarise(ledger.entries())
    assert counts["applied"] == 1
    assert counts["failed"] == 1
    assert counts["intent"] == 1  # key "1" was begun but never settled


def test_size_cap_refuses_to_evict_unsettled_entries(tmp_path) -> None:
    """The cap only evicts settled records -- never one still being decided.

    Ten INTENT entries are all kept even past ``max_entries``, because each is
    a crash someone still has to look at. Losing one to a size limit would
    defeat the ledger's entire purpose.
    """
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path, max_entries=3)
    for i in range(10):
        ledger.begin("a", {"i": i}, key=f"intent-{i}")
    assert len(ledger) == 10
    assert len(ledger.pending()) == 10  # still live, not yet a crash

    # Reload: every one of them is now a crash, and none was evicted.
    reloaded = EffectLedger(path, max_entries=3)
    assert len(reloaded.interrupted()) == 10


def test_size_cap_evicts_oldest_settled_first(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path, max_entries=3)
    for i in range(5):
        ledger.begin("a", {"i": i}, key=f"k{i}")
        ledger.mark_applied(f"k{i}", output=i)
    ledger.begin("a", {"i": 99}, key="fresh")
    assert ledger.get("k0") is None
    assert ledger.get("k4") is not None
    assert ledger.get("fresh") is not None


def test_corrupt_file_is_ignored_not_fatal(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{ this is not json", encoding="utf-8")
    ledger = EffectLedger(path)
    assert len(ledger) == 0
    # and it can still be used
    ledger.begin("a", {}, key="k")
    assert ledger.get("k") is not None


def test_save_is_atomic_and_leaves_no_tmp(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    ledger = EffectLedger(path)
    ledger.begin("a", {}, key="k")
    ledger.save()
    assert path.exists()
    assert not (tmp_path / "ledger.json.tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["effects"][0]["key"] == "k"


def test_unserialisable_result_is_stored_as_repr() -> None:
    ledger = EffectLedger(None)
    ledger.begin("a", {}, key="k")
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"
    ledger.mark_applied("k", output=Weird())
    assert ledger.replay("k")["output"] == "<weird>"


def test_forget_and_clear() -> None:
    ledger = EffectLedger(None)
    ledger.begin("a", {}, key="k1")
    ledger.begin("b", {}, key="k2")
    assert ledger.forget("k1") is True
    assert ledger.forget("nope") is False
    assert len(ledger) == 1
    assert ledger.clear() == 1
    assert len(ledger) == 0


def test_events_are_emitted_on_the_bus(tmp_path) -> None:
    from alan.core.events import EventBus

    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("effect.*", lambda event: seen.append(event.name))
    ledger = EffectLedger(tmp_path / "l.json", bus=bus)
    ledger.begin("a", {}, key="k")
    ledger.mark_applied("k", output=1)
    assert "effect.recorded" in seen
    assert "effect.applied" in seen


# -- registry integration -------------------------------------------------


def _registry(ledger) -> ToolRegistry:
    reg = ToolRegistry(default_gate(), max_workers=2)
    reg.register(
        ToolSpec(
            name="fs.write",
            description="write",
            executor=lambda path, data: {"path": path, "written": data},
            risk=RiskLevel.LOW_RISK,
            permissions=("filesystem.write",),
        )
    )
    reg.set_ledger(ledger)
    return reg


def test_registry_records_and_replays_within_a_trace(tmp_path) -> None:
    ledger = EffectLedger(tmp_path / "l.json")
    reg = _registry(ledger)

    first = reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t1")
    assert first.ok
    assert len(ledger.entries()) == 1

    # Same call, same trace: the side effect must not run a second time.
    second = reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t1")
    assert second.ok
    assert second.metadata.get("replayed") is True
    assert second.output == first.output
    assert len(ledger.entries()) == 1  # no second record


def test_registry_same_call_in_a_new_trace_reruns(tmp_path) -> None:
    ledger = EffectLedger(tmp_path / "l.json")
    reg = _registry(ledger)

    reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t1")
    again = reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t2")
    assert again.metadata.get("replayed") is not True
    assert len(ledger.entries()) == 2


def test_registry_records_failure(tmp_path) -> None:
    ledger = EffectLedger(tmp_path / "l.json")
    reg = ToolRegistry(default_gate(), max_workers=2)

    def boom(**_):
        raise RuntimeError("nope")

    reg.register(
        ToolSpec(
            name="fs.write", description="write", executor=boom,
            risk=RiskLevel.LOW_RISK, permissions=("filesystem.write",),
        )
    )
    reg.set_ledger(ledger)

    result = reg.invoke("fs.write", {"path": "/a"}, trace_id="t1")
    assert not result.ok
    assert ledger.status(
        effect_key("fs.write", {"path": "/a"}, "t1")
    ) is EffectStatus.FAILED


def test_safe_tools_are_not_recorded(tmp_path) -> None:
    ledger = EffectLedger(tmp_path / "l.json")
    reg = ToolRegistry(default_gate(), max_workers=2)
    reg.register(
        ToolSpec(
            name="fs.read", description="read", executor=lambda path: path,
            risk=RiskLevel.SAFE, permissions=("filesystem.read",),
        )
    )
    reg.set_ledger(ledger)
    reg.invoke("fs.read", {"path": "/a"}, trace_id="t1")
    assert len(ledger) == 0


def test_no_ledger_means_no_behaviour_change() -> None:
    reg = ToolRegistry(default_gate(), max_workers=2)
    reg.register(
        ToolSpec(
            name="fs.write", description="w",
            executor=lambda path, data: data,
            risk=RiskLevel.LOW_RISK, permissions=("filesystem.write",),
        )
    )
    a = reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t1")
    b = reg.invoke("fs.write", {"path": "/a", "data": "x"}, trace_id="t1")
    assert a.ok and b.ok
    assert b.metadata.get("replayed") is None
