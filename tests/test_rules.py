"""Operator permission rules (Phase 1): allow/ask/block layered over the gate.

The rules exist so the tiered policies can be tightened or loosened per action
class without touching code. What the tests pin down:

* rule matching: exact capability, ``namespace.*``, bare ``namespace``, ``*``;
* the last matching rule wins;
* rules run *after* the hard checks: FORBIDDEN stays refused and an ``allow``
  rule can never lift anything above ``ceiling``;
* ``ask`` forces a confirmation even for a capability the tier would allow;
* an unknown effect is a hard error, never a silent no-op (it is a security
  setting; the file must not lie about what it means);
* configuration round-trips through ``_apply_tables`` so a typo in
  settings.toml surfaces where the operator can see it.

Offline by construction, like the rest of the core suite.
"""

from __future__ import annotations

import unittest

from alan.config import Settings, _apply_tables
from alan.interfaces.types import PermissionDecision, PermissionRequest, RiskLevel
from alan.permissions.gate import FORBIDDEN, default_gate
from alan.permissions.rules import ActionRule, RuleSet, parse_rules


def _request(capability: str, risk: RiskLevel = RiskLevel.MEDIUM_RISK):
    return PermissionRequest(capability, risk)


class TestRuleMatching(unittest.TestCase):
    def test_exact_capability_matches_only_itself(self) -> None:
        rule = ActionRule(capability="shell.execute", effect="block")
        self.assertTrue(rule.matches("shell.execute"))
        self.assertFalse(rule.matches("shell.run"))
        self.assertFalse(rule.matches("filesystem.write"))

    def test_namespace_star_covers_the_family(self) -> None:
        rule = ActionRule(capability="shell.*", effect="block")
        self.assertTrue(rule.matches("shell.execute"))
        self.assertTrue(rule.matches("shell.run_script"))
        self.assertFalse(rule.matches("shell"))
        self.assertFalse(rule.matches("filesystem.write"))

    def test_bare_namespace_covers_the_family_too(self) -> None:
        rule = ActionRule(capability="shell", effect="block")
        self.assertTrue(rule.matches("shell.execute"))

    def test_wildcard_matches_everything(self) -> None:
        rule = ActionRule(capability="*", effect="block")
        self.assertTrue(rule.matches("anything.at.all"))
        self.assertTrue(rule.matches("shell.execute"))

    def test_last_match_wins(self) -> None:
        rules = parse_rules(
            [
                {"capability": "filesystem.write", "effect": "ask"},
                {"capability": "filesystem.write", "effect": "allow"},
            ]
        )
        self.assertEqual(rules.match("filesystem.write").effect, "allow")
        rules = parse_rules(
            [
                {"capability": "filesystem.write", "effect": "allow"},
                {"capability": "filesystem.*", "effect": "ask"},
            ]
        )
        # The later, more specific rule wins for capabilities it covers.
        self.assertEqual(rules.match("filesystem.write").effect, "ask")


class TestRuleEnforcement(unittest.TestCase):
    def test_block_denies_before_anything_else(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED, parse_rules(
            [{"capability": "shell.*", "effect": "block"}]))
        self.assertEqual(
            gate.check(_request("shell.execute", RiskLevel.MEDIUM_RISK)),
            PermissionDecision.DENIED,
        )

    def test_allow_clears_confirmation(self) -> None:
        # web.open is HIGH_RISK and requires_confirmation by default; under a
        # HIGH ceiling it asks. An explicit allow rule skips the prompt.
        gate = default_gate(RiskLevel.HIGH_RISK, parse_rules(
            [{"capability": "browser.open", "effect": "allow"}]))
        self.assertEqual(
            gate.check(_request("browser.open", RiskLevel.HIGH_RISK)),
            PermissionDecision.ALLOWED,
        )

    def test_allow_cannot_lift_above_ceiling(self) -> None:
        gate = default_gate(RiskLevel.MEDIUM_RISK, parse_rules(
            [{"capability": "browser.open", "effect": "allow"}]))
        self.assertEqual(
            gate.check(_request("browser.open", RiskLevel.HIGH_RISK)),
            PermissionDecision.DENIED,
        )

    def test_allow_cannot_reach_forbidden(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED, parse_rules(
            [{"capability": "shell.run_privileged", "effect": "allow"}]))
        self.assertEqual(
            gate.check(_request("shell.run_privileged", RiskLevel.SAFE)),
            PermissionDecision.DENIED,
        )
        self.assertIn("shell.run_privileged", FORBIDDEN)

    def test_ask_forces_confirmation_on_a_safe_action(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED, parse_rules(
            [{"capability": "filesystem.list", "effect": "ask"}]))
        self.assertEqual(
            gate.check(_request("filesystem.list", RiskLevel.SAFE)),
            PermissionDecision.CONFIRMATION_REQUIRED,
        )

    def test_ask_wins_over_the_default_for_a_confirmed_capability(self) -> None:
        gate = default_gate(RiskLevel.HIGH_RISK, parse_rules(
            [{"capability": "browser.open", "effect": "ask"}]))
        self.assertEqual(
            gate.check(_request("browser.open", RiskLevel.HIGH_RISK)),
            PermissionDecision.CONFIRMATION_REQUIRED,
        )

    def test_no_rules_change_nothing(self) -> None:
        gate = default_gate(RiskLevel.HIGH_RISK)
        self.assertEqual(
            gate.check(_request("web.open", RiskLevel.MEDIUM_RISK)),
            PermissionDecision.ALLOWED,
        )


class TestRuleValidation(unittest.TestCase):
    def test_unknown_effect_is_a_hard_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "maybe"):
            parse_rules([{"capability": "shell.*", "effect": "maybe"}])

    def test_missing_effect_is_a_hard_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_rules([{"capability": "shell.*"}])

    def test_missing_capability_is_a_hard_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_rules([{"effect": "block"}])

    def test_parse_returns_ordered_rules(self) -> None:
        rules = parse_rules(
            [
                {"capability": "a.b", "effect": "block"},
                {"capability": "c.d", "effect": "ask"},
            ]
        )
        self.assertEqual([r.capability for r in rules.rules], ["a.b", "c.d"])
        self.assertEqual(rules.match("a.b").effect, "block")


class TestRuleConfiguration(unittest.TestCase):
    def test_rules_round_trip_through_apply_tables(self) -> None:
        settings = _apply_tables(
            Settings(),
            {
                "permissions": {
                    "rules": [
                        {"capability": "shell.*", "effect": "block"},
                        {"capability": "schedule.*", "effect": "ask"},
                    ]
                }
            },
        )
        self.assertEqual(len(settings.permissions.rules), 2)
        self.assertEqual(settings.permissions.rules[0]["capability"], "shell.*")

    def test_bad_rule_in_configuration_raises_loudly(self) -> None:
        with self.assertRaises(ValueError):
            _apply_tables(
                Settings(),
                {"permissions": {"rules": [{"capability": "x", "effect": "oops"}]}},
            )

    def test_rules_not_a_table_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            _apply_tables(Settings(), {"permissions": {"rules": "block everything"}})


class TestSchedulerConfiguration(unittest.TestCase):
    def test_scheduler_table_applies(self) -> None:
        settings = _apply_tables(
            Settings(), {"scheduler": {"enabled": False, "tick_seconds": 2.0}}
        )
        self.assertFalse(settings.scheduler.enabled)
        self.assertEqual(settings.scheduler.tick_seconds, 2.0)
        self.assertEqual(settings.scheduler.task_timeout, 600.0)

    def test_default_scheduler_is_enabled_and_conservative(self) -> None:
        settings = Settings()
        self.assertTrue(settings.scheduler.enabled)
        self.assertEqual(settings.scheduler.tick_seconds, 5.0)
        self.assertEqual(settings.scheduler.task_timeout, 600.0)


if __name__ == "__main__":
    unittest.main()