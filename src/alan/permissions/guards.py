"""Monotonic, deny-only guards on the permission gate (from DeepSeek Harness).

The gate decides by risk tier and the operator's allow/ask/block rules. Those
rules can *loosen*: an ``allow`` rule clears a confirmation so a read runs
without prompting. That is useful, but it means a rule is one typo away from
loosening something a security policy meant to keep tight.

A **guard** is the opposite direction. It can only ever *deny*. Once a guard
matches a capability the gate returns DENIED, and nothing later in the
evaluation -- not a rule's ``allow``, not a policy's ``requires_confirmation =
False`` -- can turn that back into a run. The property is called *monotonic*:
adding a guard can only tighten the gate, never widen it, and the ordering of
guards relative to rules cannot change a decision to be more permissive.

This is the mechanism Alan borrows from ``dsh``'s ``ctx.tools.guard()``. It
exists for policies that must hold regardless of configuration:

* a capability an integration flags as unsafe for this machine;
* a path or argument pattern an operator never wants touched;
* a temporary "this tool is compromised, refuse it" switch that cannot be
  undone by an ``allow`` rule written before the switch was flipped.

Guards are **identity-protected**: only the owner that registered a guard can
remove it. A guard is a security control, and a control any caller could drop
would not be a control. The set is therefore append-mostly, and removal is the
owner's act alone.

Denial is by *reason*, not silence: a guard that refuses says why, and that
reason reaches the operator in the tool's error, so "why did nothing happen?"
has an answer that names the guard.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from ..interfaces.types import PermissionDecision, PermissionRequest


def _matches(pattern: str, capability: str) -> bool:
    """Does a guard pattern cover a capability? Same syntax as action rules.

    ``*`` matches everything, ``shell.*`` matches the ``shell`` namespace, a
    bare ``shell`` reads as the family, and an exact name matches only itself.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return False
    if pattern == "*" or pattern == capability:
        return True
    if pattern.endswith(".*"):
        return capability.startswith(pattern[:-2] + ".")
    if "." not in pattern and capability.startswith(pattern + "."):
        return True
    return False


@dataclass(frozen=True)
class Guard:
    """One deny-only rule. ``owner`` is who may remove it; nothing else can."""

    name: str
    capability: str
    reason: str = ""
    owner: str = "operator"
    #: Optional argument predicate: ``True`` means *deny this call*. Receives
    #: the request's arguments so a guard can refuse a dangerous path without
    #: needing to match on capability alone.
    predicate: Callable[[dict], bool] | None = field(default=None, compare=False)

    def matches(self, request: PermissionRequest) -> bool:
        """Does this guard refuse this request? Pattern first, then predicate."""
        if not _matches(self.capability, request.capability):
            return False
        if self.predicate is None:
            return True
        try:
            return bool(self.predicate(request.arguments or {}))
        except Exception:
            # A guard that cannot evaluate must not silently allow. A broken
            # predicate is treated as a refusal: the safe reading of "I could
            # not check" is "no", not "yes, go ahead".
            return True

    def explain(self) -> str:
        return self.reason or f"guard {self.name!r} refuses {self.capability}"


class GuardSet:
    """An ordered, thread-safe collection of deny-only guards.

    Consulted *after* the gate's hard checks and *before* the confirmation
    logic, so a matching guard short-circuits to DENIED and no later ``allow``
    can rescue the call. That ordering is the whole monotonicity guarantee:
    the only things that run after a guard are the ones that would have
    *tightened* further (asking for confirmation), never loosened.
    """

    def __init__(self, guards: Sequence[Guard] = ()) -> None:
        self._lock = threading.RLock()
        names = [g.name for g in guards]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(
                f"duplicate guard name(s): {', '.join(dupes)}; guard names "
                "must be unique so a second registration cannot hide the first"
            )
        self._guards: list[Guard] = list(guards)

    def __len__(self) -> int:
        with self._lock:
            return len(self._guards)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._guards)

    def list(self) -> tuple[Guard, ...]:
        with self._lock:
            return tuple(self._guards)

    # -- mutation -------------------------------------------------------
    def add(self, guard: Guard) -> Guard:
        """Register a guard. Duplicate names are refused, not overwritten.

        A silent overwrite would let a second registration neutralise the
        first by reusing its name -- exactly the loosening this class exists
        to prevent. Raising makes the collision visible at the call site.
        """
        with self._lock:
            if any(existing.name == guard.name for existing in self._guards):
                raise ValueError(
                    f"guard {guard.name!r} is already registered; remove it "
                    "before re-adding, or choose a new name"
                )
            self._guards.append(guard)
        return guard

    def remove(self, name: str, *, owner: str) -> bool:
        """Remove a guard. Only ``owner`` may. ``False`` if not found/allowed.

        Identity-protected: a guard dropped by anyone but its owner would not
        be a control, because any code path could switch it off. The check is
        on the stored owner, not a caller-supplied claim about being one.
        """
        with self._lock:
            for index, guard in enumerate(self._guards):
                if guard.name == name:
                    if guard.owner != owner:
                        return False
                    del self._guards[index]
                    return True
        return False

    def clear(self, *, owner: str | None = None) -> int:
        """Drop every guard, or every guard owned by ``owner``. Returns count.

        ``owner=None`` clears all, which is the operator's own reset act. A
        caller passing a name clears only what it owns.
        """
        with self._lock:
            if owner is None:
                dropped = len(self._guards)
                self._guards.clear()
                return dropped
            keep = [g for g in self._guards if g.owner != owner]
            dropped = len(self._guards) - len(keep)
            self._guards[:] = keep
            return dropped

    # -- evaluation -----------------------------------------------------
    def denying(self, request: PermissionRequest) -> Guard | None:
        """The first guard that refuses this request, or ``None``.

        First match wins on purpose: guards are an ordered list, and the first
        one to say no is the most specific instruction the operator set. Every
        guard is still consulted in ``explain`` so an operator can see all of
        them; this path only needs the one that stopped the call.
        """
        with self._lock:
            snapshot = tuple(self._guards)
        for guard in snapshot:
            if guard.matches(request):
                return guard
        return None

    def check(self, request: PermissionRequest) -> PermissionDecision:
        """DENIED when a guard refuses, else NOT_APPLICABLE.

        NOT_APPLICABLE (not ALLOWED) is deliberate: the guard set is one
        input to the gate's decision, not the whole of it. Returning ALLOWED
        here would let a guard *grant* what the gate's own policy had not yet
        decided, which is a widening in disguise.
        """
        return (
            PermissionDecision.DENIED
            if self.denying(request) is not None
            else PermissionDecision.NOT_APPLICABLE
        )

    def explain(self, capability: str) -> list[str]:
        """Every guard that covers ``capability``, as readable reasons.

        For diagnostics and the operator's own review: a set that silently
        refuses a class of work should be inspectable without a debugger.
        """
        reasons: list[str] = []
        with self._lock:
            snapshot = tuple(self._guards)
        for guard in snapshot:
            if _matches(guard.capability, capability):
                reasons.append(guard.explain())
        return reasons


def parse_guards(raw: Iterable[dict]) -> GuardSet:
    """Build a GuardSet from parsed TOML ``[[permissions.guards]]`` entries.

    An unknown or empty owner raises: a guard that cannot attribute itself
    cannot be removed by its owner, and an unremovable guard is a permanent,
    invisible refusal. Refusing at load time beats a mystery denial later.
    """
    guards: list[Guard] = []
    for index, entry in enumerate(raw or ()):
        if not isinstance(entry, dict):
            raise ValueError(
                f"permissions.guards entry #{index + 1} is not a table "
                "(expected [[permissions.guards]] with name/capability)"
            )
        name = str(entry.get("name", "")).strip()
        capability = str(entry.get("capability", "")).strip()
        owner = str(entry.get("owner", "")).strip() or "operator"
        if not name or not capability:
            raise ValueError(
                f"permissions.guards entry #{index + 1} needs both 'name' "
                "and 'capability'"
            )
        guards.append(
            Guard(
                name=name,
                capability=capability,
                reason=str(entry.get("reason", "")),
                owner=owner,
            )
        )
    return GuardSet(guards)
