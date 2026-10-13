"""Monotonic, deny-only guards (ported from DeepSeek Harness's ctx.tools.guard()).

The properties under test are the ones that make a guard a *control* rather
than a suggestion:

* a matching guard denies, and nothing later in evaluation re-allows it;
* an operator ``allow`` rule cannot rescue a guarded capability;
* only the owner can remove a guard;
* a broken predicate refuses instead of silently allowing;
* adding a guard can only tighten the gate (monotonicity).
"""

from __future__ import annotations

import pytest

from alan.interfaces.types import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from alan.permissions.gate import PermissionGate, Policy, default_gate
from alan.permissions.guards import Guard, GuardSet, parse_guards
from alan.permissions.rules import ActionRule, RuleSet


def req(capability: str, risk: RiskLevel = RiskLevel.LOW_RISK, **args) -> PermissionRequest:
    return PermissionRequest(capability=capability, risk=risk, arguments=args)


def test_guard_denies_matching_capability() -> None:
    gate = default_gate()
    gate.set_guards(GuardSet([Guard(name="no-shell", capability="shell.*", reason="locked down")]))

    assert gate.check(req("shell.execute")) is PermissionDecision.DENIED
    assert gate.last_reason == "locked down"


def test_guard_does_not_affect_other_capabilities() -> None:
    gate = default_gate()
    gate.set_guards(GuardSet([Guard(name="no-shell", capability="shell.*")]))

    assert gate.check(req("filesystem.read")) is PermissionDecision.ALLOWED


def test_allow_rule_cannot_rescue_a_guard() -> None:
    """The whole point: rules can loosen, guards cannot be loosened by rules."""
    gate = default_gate()
    gate.set_rules(RuleSet([ActionRule(capability="shell.*", effect="allow")]))
    gate.set_guards(GuardSet([Guard(name="no-shell", capability="shell.*", owner="operator")]))

    assert gate.check(req("shell.execute")) is PermissionDecision.DENIED


def test_guard_runs_before_confirmation_so_allow_rule_is_powerless() -> None:
    """A policy that would confirm must not confirm a guarded call."""
    gate = PermissionGate(ceiling=RiskLevel.MEDIUM_RISK, auto_confirm=True)
    gate.set_policy(
        Policy(capability="shell.*", risk=RiskLevel.MEDIUM_RISK, requires_confirmation=False)
    )
    gate.set_rules(RuleSet([ActionRule(capability="shell.*", effect="allow")]))
    gate.set_guards(GuardSet([Guard(name="no-shell", capability="shell.*")]))

    # auto_confirm=True means every non-guarded call would be ALLOWED.
    assert gate.check(req("shell.execute", RiskLevel.MEDIUM_RISK)) is PermissionDecision.DENIED


def test_only_owner_can_remove_a_guard() -> None:
    guards = GuardSet([Guard(name="g", capability="shell.*", owner="operator")])

    assert guards.remove("g", owner="someone-else") is False
    assert len(guards) == 1
    assert guards.remove("g", owner="operator") is True
    assert len(guards) == 0


def test_duplicate_guard_name_is_refused() -> None:
    guards = GuardSet([Guard(name="g", capability="shell.*")])
    with pytest.raises(ValueError):
        guards.add(Guard(name="g", capability="filesystem.write"))


def test_broken_predicate_refuses_instead_of_allowing() -> None:
    def explode(_args):
        raise RuntimeError("boom")

    guard = Guard(name="g", capability="filesystem.write", predicate=explode)
    assert guard.matches(req("filesystem.write", path="/tmp/x")) is True


def test_predicate_can_target_an_argument() -> None:
    guard = Guard(
        name="no-tmp",
        capability="filesystem.write",
        predicate=lambda args: str(args.get("path", "")).startswith("/etc"),
    )
    assert guard.matches(req("filesystem.write", path="/etc/passwd")) is True
    assert guard.matches(req("filesystem.write", path="/home/x")) is False


def test_guard_set_check_is_not_applicable_when_no_match() -> None:
    guards = GuardSet([Guard(name="g", capability="shell.*")])
    assert guards.check(req("filesystem.read")) is PermissionDecision.NOT_APPLICABLE
    assert guards.check(req("shell.execute")) is PermissionDecision.DENIED


def test_explain_lists_covering_guards() -> None:
    guards = GuardSet(
        [
            Guard(name="a", capability="shell.*", reason="no shells"),
            Guard(name="b", capability="filesystem.write", reason="read-only box"),
            Guard(name="c", capability="shell.execute", reason="also no exec"),
        ]
    )
    reasons = guards.explain("shell.execute")
    assert "no shells" in reasons
    assert "also no exec" in reasons
    assert "read-only box" not in reasons


def test_clear_by_owner_leaves_others() -> None:
    guards = GuardSet(
        [
            Guard(name="a", capability="shell.*", owner="operator"),
            Guard(name="b", capability="shell.*", owner="plugin"),
        ]
    )
    assert guards.clear(owner="plugin") == 1
    assert len(guards) == 1
    assert guards.clear(owner=None) == 1
    assert len(guards) == 0


def test_parse_guards_from_toml_entries() -> None:
    guards = parse_guards(
        [
            {"name": "no-shell", "capability": "shell.*", "reason": "kiosk"},
            {"name": "no-delete", "capability": "filesystem.delete", "owner": "ops"},
        ]
    )
    assert len(guards) == 2
    assert guards.remove("no-delete", owner="ops") is True
    assert guards.remove("no-delete", owner="operator") is False


def test_parse_guards_rejects_incomplete_entry() -> None:
    with pytest.raises(ValueError):
        parse_guards([{"name": "x"}])
    with pytest.raises(ValueError):
        parse_guards(["not-a-table"])


def test_duplicate_parse_guards_rejected() -> None:
    with pytest.raises(ValueError):
        parse_guards(
            [
                {"name": "same", "capability": "shell.*"},
                {"name": "same", "capability": "filesystem.write"},
            ]
        )
