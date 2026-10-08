"""Action rules: allow / ask / block, per capability (Dots-style custom rules).

The permission gate decides by *risk tier*; rules let the operator decide by
*action class*, which is what people actually think in: "reads are always fine",
"never send anything without asking me", "this machine may run shell commands".

Three effects, matched in list order with the **last matching rule winning**, so
a broad rule can be followed by a narrow exception:

    allow   run without asking (still subject to the risk ceiling and deny list)
    ask     always ask, even for a capability whose policy would not
    block   refuse outright, even for a capability whose policy would allow it

What rules can never do, deliberately:

* allow anything in the FORBIDDEN set -- ``permissions.elevate`` stays dead;
* push a request above the operator's ceiling -- ``allow`` on ``shell.*`` does
  not make ``PRIVILEGED`` reachable when the ceiling is ``MEDIUM_RISK``;
* be written by an agent. Rules come from ``settings.toml`` and from nowhere
  else; an approval given in chat is one-shot and says how to make it permanent.

Pattern syntax matches the gate's own convention:

    ``*``            every capability
    ``shell.*``      the ``shell`` namespace (``shell.execute``, ``shell.install``)
    ``shell.execute``  exactly this capability

An entry with an unknown effect raises at load time. Silently ignoring a typo
in a *security* setting would turn "I blocked that" into "I meant to block
that", which is the one failure mode this file must not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

EFFECTS = ("allow", "ask", "block")


@dataclass(frozen=True)
class ActionRule:
    """One rule. ``capability`` is the pattern, ``effect`` the decision."""

    capability: str
    effect: str
    reason: str = ""

    def matches(self, capability: str) -> bool:
        """Does this rule's pattern cover ``capability``?"""
        pattern = self.capability.strip()
        if pattern == "*" or pattern == capability:
            return True
        if pattern.endswith(".*"):
            # "shell.*"[:-2] is "shell" (strip the dot-star), not "shell.*"[:-1]
            # which would leave a stray dot and match nothing.
            return capability.startswith(pattern[:-2] + ".")
        # A bare namespace like "shell" reads naturally as "the shell family";
        # accept it rather than making people learn the suffix.
        if "." not in pattern and capability.startswith(pattern + "."):
            return True
        return False


class RuleSet:
    """An ordered list of rules with last-match-wins lookup."""

    def __init__(self, rules: Sequence[ActionRule] | Iterable[ActionRule] = ()) -> None:
        rules = tuple(rules)
        for rule in rules:
            if rule.effect not in EFFECTS:
                raise ValueError(
                    f"unknown permission rule effect {rule.effect!r} for "
                    f"capability {rule.capability!r}; expected one of {', '.join(EFFECTS)}"
                )
            if not rule.capability.strip():
                raise ValueError("a permission rule has an empty capability pattern")
        self.rules: tuple[ActionRule, ...] = rules

    def __bool__(self) -> bool:
        return bool(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def match(self, capability: str) -> ActionRule | None:
        """The rule that decides ``capability``: last match wins."""
        found: ActionRule | None = None
        for rule in self.rules:
            if rule.matches(capability):
                found = rule
        return found

    def effect_for(self, capability: str) -> str | None:
        """Shorthand: the winning effect for a capability, or ``None``."""
        rule = self.match(capability)
        return rule.effect if rule else None


def parse_rules(raw: Sequence[dict]) -> RuleSet:
    """Build a RuleSet from parsed TOML ``[[permissions.rules]]`` entries."""
    rules: list[ActionRule] = []
    for index, entry in enumerate(raw or ()):
        if not isinstance(entry, dict):
            raise ValueError(
                f"permissions.rules entry #{index + 1} is not a table "
                f"(expected [[permissions.rules]] with capability/effect)"
            )
        rules.append(
            ActionRule(
                capability=str(entry.get("capability", "")),
                effect=str(entry.get("effect", "")).strip().lower(),
                reason=str(entry.get("reason", "")),
            )
        )
    return RuleSet(rules)
