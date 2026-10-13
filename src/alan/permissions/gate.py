"""The permission gate.

Brief section 12 makes this mandatory, and section 16 requires that no agent can
bypass it. The guarantee is structural rather than advisory:

* every capability check happens in :meth:`PermissionGate.check`;
* the only caller of a tool executor is the registry, which checks first;
* an agent's risk ceiling is *passed in* to its context, so an agent cannot
  raise its own limit;
* denials are emitted as events, so a bypass attempt is visible.

On top of the tiered policies sits an optional :class:`~alan.permissions.rules.RuleSet`
-- the operator's allow/ask/block per action class. Rules can tighten a decision
(always ask, or refuse outright) or loosen *one specific thing* (skip the prompt
for reads the policy would confirm), but they run *after* the hard checks:
FORBIDDEN stays dead and nothing exceeds ``ceiling``. See rules.py for why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..interfaces.types import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from .guards import GuardSet
from .rules import RuleSet

#: Capabilities that are always refused, regardless of configuration.
#: Present so an integrator cannot "solve" a problem by enabling them.
FORBIDDEN = frozenset({"shell.run_privileged", "permissions.elevate"})


@dataclass
class Policy:
    """One rule: what risk a capability is treated as, and any constraints."""

    capability: str
    risk: RiskLevel = RiskLevel.SAFE
    allow: bool = True
    requires_confirmation: bool = False
    allowed_prefixes: tuple[str, ...] = ()
    denied_patterns: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class PermissionGate:
    """Decides whether a capability may run.

    ``ceiling`` is the maximum risk permitted in this session. Agents may be
    given a *lower* ceiling; only the operator can raise the gate's.
    """

    ceiling: RiskLevel = RiskLevel.MEDIUM_RISK
    policies: dict[str, Policy] = field(default_factory=dict)
    auto_confirm: bool = False
    confirmer: Callable[[PermissionRequest], bool] | None = None
    decisions: list[PermissionDecision] = field(default_factory=list)
    #: Operator rules (allow/ask/block), consulted after the hard checks.
    rules: RuleSet | None = None
    #: Deny-only monotonic guards (from DeepSeek Harness's ``ctx.tools.guard()``).
    #: Consulted *before* the confirmation logic so an ``allow`` rule can never
    #: rescue a capability a guard refuses -- see :mod:`.guards`.
    guards: GuardSet | None = None
    #: The reason the last check was denied by a guard, if it was. Read by the
    #: registry so a refusal names the guard instead of just "permission denied".
    last_reason: str = ""

    # -- configuration -------------------------------------------------
    def set_policy(self, policy: Policy) -> None:
        self.policies[policy.capability] = policy

    def set_rules(self, rules: RuleSet | None) -> None:
        self.rules = rules or None

    def set_guards(self, guards: GuardSet | None) -> None:
        self.guards = guards or None

    def policy_for(self, capability: str, default_risk: RiskLevel) -> Policy:
        """Most specific matching policy, else a derived default.

        ``shell.*`` matches ``shell.execute``; an exact name always wins.
        """
        if capability in self.policies:
            return self.policies[capability]
        if "." in capability:
            prefix = capability.split(".", 1)[0] + ".*"
            if prefix in self.policies:
                return self.policies[prefix]
        return Policy(capability=capability, risk=default_risk)

    # -- the check -----------------------------------------------------
    def check(
        self,
        request: PermissionRequest,
        *,
        emit: Callable[..., object] | None = None,
    ) -> PermissionDecision:
        """Return the decision. Records it and emits an event."""
        decision = self._evaluate(request)
        self.decisions.append(decision)

        if emit is not None:
            from ..core.events import Events

            emit(
                Events.PERMISSION_REQUESTED,
                capability=request.capability,
                risk=str(request.risk),
                agent=request.agent,
                reason=request.reason,
                arguments=request.redacted()["arguments"],
            )
            if decision is PermissionDecision.ALLOWED:
                emit(Events.PERMISSION_GRANTED, capability=request.capability)
            elif decision is PermissionDecision.DENIED:
                emit(Events.PERMISSION_DENIED, capability=request.capability)
        return decision

    def _evaluate(self, request: PermissionRequest) -> PermissionDecision:
        capability = request.capability
        self.last_reason = ""
        if capability in FORBIDDEN:
            return PermissionDecision.DENIED

        # Operator rules first, so "block" short-circuits before policy work.
        # An allow/ask effect is applied further down, *after* the hard checks:
        # a rule must never be able to reach something the gate refuses.
        rule_effect: str | None = None
        if self.rules is not None:
            rule = self.rules.match(capability)
            if rule is not None:
                if rule.effect == "block":
                    return PermissionDecision.DENIED
                rule_effect = rule.effect

        policy = self.policy_for(capability, request.risk)
        risk = max(policy.risk, request.risk)

        if not policy.allow:
            return PermissionDecision.DENIED
        if risk > self.ceiling:
            return PermissionDecision.DENIED

        target = str(request.arguments.get("path") or request.arguments.get("url") or "")
        for pattern in policy.denied_patterns:
            if pattern and pattern.lower() in target.lower():
                return PermissionDecision.DENIED
        if policy.allowed_prefixes:
            if not any(target.lower().startswith(p.lower()) for p in policy.allowed_prefixes):
                return PermissionDecision.DENIED

        # Deny-only guards. This runs *after* the hard checks and *before*
        # the confirmation logic, which is what makes it monotonic: everything
        # below only ever tightens (asking for confirmation), so no ``allow``
        # rule and no ``requires_confirmation = False`` can widen a refusal
        # made here. A guard that refuses short-circuits to DENIED.
        if self.guards is not None:
            guard = self.guards.denying(request)
            if guard is not None:
                self.last_reason = guard.explain()
                return PermissionDecision.DENIED

        requires_confirmation = policy.requires_confirmation
        if rule_effect == "allow":
            requires_confirmation = False
        elif rule_effect == "ask":
            requires_confirmation = True

        if requires_confirmation and not self.auto_confirm:
            if self.confirmer is None:
                return PermissionDecision.CONFIRMATION_REQUIRED
            if not self.confirmer(request):
                return PermissionDecision.DENIED

        return PermissionDecision.ALLOWED

    # -- helpers -------------------------------------------------------
    def permits(self, request: PermissionRequest) -> bool:
        return self.check(request) is PermissionDecision.ALLOWED

    def raise_ceiling(self, risk: RiskLevel) -> None:
        """Operator-only. Agents have no path to this."""
        self.ceiling = risk

    def reset(self) -> None:
        self.decisions.clear()


def default_gate(
    ceiling: RiskLevel = RiskLevel.MEDIUM_RISK,
    rules: RuleSet | None = None,
) -> PermissionGate:
    """A sensible default policy set.

    Reads are safe, writes are low risk, package installs need confirmation,
    deletes and outbound calls are high risk, and privileged execution is
    refused outright. ``rules``, when given, layers the operator's
    allow/ask/block rules on top (never above ``ceiling``).
    """
    gate = PermissionGate(ceiling=ceiling, rules=rules)
    for capability, risk in (
        ("filesystem.read", RiskLevel.SAFE),
        ("filesystem.list", RiskLevel.SAFE),
        ("memory.search", RiskLevel.SAFE),
        ("memory.retrieve", RiskLevel.SAFE),
        ("browser.search", RiskLevel.SAFE),
        ("filesystem.write", RiskLevel.LOW_RISK),
        ("filesystem.edit", RiskLevel.LOW_RISK),
        ("shell.execute", RiskLevel.MEDIUM_RISK),
        # Starting a program the user named is the same class of act as running
        # a local command, so it sits at the same tier -- and unlike
        # ``browser.open`` it pulls nothing from the network into the prompt.
        # ``system.open`` takes no arguments and refuses non-executable file
        # types, so it cannot become a general "run this file".
        ("app.launch", RiskLevel.MEDIUM_RISK),
        ("code.test", RiskLevel.MEDIUM_RISK),
        ("filesystem.delete", RiskLevel.HIGH_RISK),
        ("browser.open", RiskLevel.HIGH_RISK),
        ("browser.click", RiskLevel.HIGH_RISK),
        ("shell.install", RiskLevel.MEDIUM_RISK),
    ):
        gate.set_policy(
            Policy(
                capability=capability,
                risk=risk,
                requires_confirmation=(risk >= RiskLevel.HIGH_RISK),
            )
        )

    # Handing a coding task to another agent is a different class of act from
    # launching a program the user named, even though both go through
    # ``app.launch`` at the same risk tier.
    #
    # ``system.open`` starts Notepad because the user said "open notepad". This
    # one hands a 550B model a shell, the operator's API keys and a directory,
    # and lets it rewrite files -- so a single mistaken word ("fix it" meant for
    # a scratch file) becomes a real edit to a real project. That asymmetry is
    # worth a confirmation prompt even though the risk number is identical.
    #
    # Read-only delegation deliberately does *not* require confirmation. Asking
    # before every inspection would train the user to type "y" reflexively, which
    # is worse than no prompt when the prompt matters.
    #
    # MEDIUM_RISK rather than HIGH_RISK so it stays reachable under the default
    # ceiling: the point is the question, not the refusal.
    gate.set_policy(
        Policy(
            capability="code.delegate_write",
            risk=RiskLevel.MEDIUM_RISK,
            requires_confirmation=True,
        )
    )
    return gate
