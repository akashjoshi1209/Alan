"""The effect ledger: durable proof of what a run actually did.

Ported from rakazo's ``ExternalEffect`` records and DeepSeek Harness's
event-sourced session. The problem both solve is the same: a process that
crashes between *deciding* to do something and *finishing* it leaves the world
in a state nobody can see. On restart the agent either repeats the action
(double-charging a card, sending a message twice) or, worse, believes it
already happened.

The ledger closes that window with a three-write protocol:

    1. ``begin()``    -- write an INTENT record *before* the side effect runs.
    2. ``mark_*()``   -- write APPLIED or FAILED *after* it returns.
    3. ``recover()``  -- on load, any leftover INTENT is a process that died
                         mid-flight. It becomes INTERRUPTED and is *surfaced*,
                         never silently re-run.

That last step is the point. An interrupted effect is *unknown*, not "failed"
(which invites a retry) and not "applied" (which invites skipping). The ledger
turns it into something an operator or a supervisor can decide on, which is
what rakazo's ``waiting_input`` pause state is for.

Idempotency is by **key**, not by hope. The default key hashes the tool, its
arguments and the run scope, so re-invoking the same call inside the same run
returns the recorded result instead of executing again. A caller who knows the
real identity of an effect (a payment id, a message id) passes its own key and
gets exactly-once across runs too.

Everything is written through a temp file and ``os.replace``: a crash during a
save leaves the previous complete file, never a half-written one.

The ledger is *deny-only* in the security sense too: it can refuse to re-run
something, but nothing in it can grant a capability the permission gate has
not already granted. It sits beside the gate, not in front of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..interfaces.types import new_id, redact, utcnow


class EffectStatus(str, Enum):
    """Lifecycle of one side effect. Only ``APPLIED`` means "it happened"."""

    #: Written; the side effect has not been observed to finish.
    INTENT = "intent"
    #: The side effect completed successfully.
    APPLIED = "applied"
    #: The side effect ran and raised / returned failure.
    FAILED = "failed"
    #: The process died between INTENT and an outcome. Unknown, not failed.
    INTERRUPTED = "interrupted"
    #: Replay decided the recorded outcome stands; nothing was re-run.
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


#: Statuses that mean "do not run this again".
TERMINAL = (EffectStatus.APPLIED, EffectStatus.SKIPPED)


def effect_key(tool: str, arguments: dict | None = None, run_id: str = "") -> str:
    """Deterministic idempotency key for a call.

    Canonicalised JSON so argument *order* and whitespace cannot produce two
    keys for the same call. Scoped by ``run_id`` (a trace, a task id) so the
    same write repeated in a *different* request is a legitimate new effect --
    dedup is inside one run, not forever.
    """
    payload = json.dumps(
        {"tool": tool, "args": arguments or {}, "run": run_id},
        sort_keys=True,
        default=str,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class Effect:
    """One recorded side effect, durable across restarts."""

    key: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    trace_id: str = ""
    status: EffectStatus = EffectStatus.INTENT
    #: Replay payload: what to hand back if this key is seen again.
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id("fx_")
        if not self.created_at:
            self.created_at = utcnow().isoformat(timespec="seconds")
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "tool": self.tool,
            "arguments": self.arguments,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "status": str(self.status),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Effect:
        try:
            status = EffectStatus(str(data.get("status", "intent")))
        except ValueError:
            status = EffectStatus.INTENT
        return cls(
            key=str(data.get("key", "")),
            tool=str(data.get("tool", "")),
            arguments=dict(data.get("arguments") or {}),
            run_id=str(data.get("run_id", "")),
            trace_id=str(data.get("trace_id", "")),
            status=status,
            result=dict(data.get("result") or {}),
            error=str(data.get("error", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            id=str(data.get("id", "")),
        )

    @property
    def applied(self) -> bool:
        """Has this effect been observed to complete?"""
        return self.status in TERMINAL


class EffectLedger:
    """Thread-safe, optionally on-disk record of side effects.

    Pass ``path=None`` for an in-memory ledger (tests, short-lived runs).
    Every mutation persists immediately when a path is set, because a ledger
    that only writes on clean shutdown protects against nothing -- the crash
    is exactly the event it exists for.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        bus: Any = None,
        max_entries: int = 2000,
    ) -> None:
        self.path = Path(path) if path else None
        self.bus = bus
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._effects: dict[str, Effect] = {}
        self._order: list[str] = []
        #: Keys marked INTERRUPTED at load but not yet handed to a caller by
        #: :meth:`recover`. Loading must fix the state immediately (so a later
        #: ``begin`` cannot mistake a crash for a clean resume), while
        #: ``recover`` stays the single act that *reports* them -- once.
        self._unreported: list[str] = []
        if self.path is not None:
            self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        """Read the file and recover any effect left mid-flight."""
        assert self.path is not None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Missing or corrupt file both mean "no history" -- a corrupt
            # ledger must not stop the system from starting, and silently
            # starting fresh is safe because a fresh ledger records the *next*
            # effect correctly. The old file is left in place for a human.
            return
        entries = raw.get("effects", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return
        for item in entries:
            if not isinstance(item, dict):
                continue
            effect = Effect.from_dict(item)
            if not effect.key:
                continue
            self._effects[effect.key] = effect
            self._order.append(effect.key)
        # Fix crash state immediately and make it durable: an entry left as
        # INTENT on disk would be re-recovered on every subsequent load, and
        # worse, a ``begin`` on it would look like a clean resume.
        if self._recover_locked():
            self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {"effects": [self._effects[k].to_dict() for k in self._order if k in self._effects]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            # A failed persist is not a reason to fail the operation that
            # triggered it: the in-memory record is still correct, and the
            # next successful save catches up.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self) -> None:
        """Force a persist. Public for callers that mutate an entry in place."""
        with self._lock:
            self._save()

    # -- recovery -------------------------------------------------------
    def _recover_locked(self) -> list[Effect]:
        """INTENT entries are a crash. Mark them INTERRUPTED, not failed."""
        interrupted: list[Effect] = []
        for effect in self._effects.values():
            if effect.status is EffectStatus.INTENT:
                effect.status = EffectStatus.INTERRUPTED
                effect.updated_at = utcnow().isoformat(timespec="seconds")
                effect.error = effect.error or "process ended before an outcome was recorded"
                interrupted.append(effect)
        for effect in interrupted:
            if effect.key not in self._unreported:
                self._unreported.append(effect.key)
        return interrupted

    def recover(self) -> list[Effect]:
        """Return every effect that died mid-flight, once each.

        Loading fixes the state straight away (so a later ``begin`` cannot
        mistake a crash for a clean resume); this is the act that *reports*
        those crashes to a caller, and each is reported exactly once. The
        caller decides what to do with the list -- replay, ask, or abandon --
        because *the ledger cannot know* whether the side effect actually
        landed on the far side.
        """
        with self._lock:
            self._recover_locked()
            reported = [self._effects[k] for k in self._unreported if k in self._effects]
            self._unreported = [k for k in self._unreported if k in self._effects]
            if reported:
                self._save()
                for effect in reported:
                    self._emit("effect.interrupted", effect)
        return reported

    # -- the protocol ---------------------------------------------------
    def begin(
        self,
        tool: str,
        arguments: dict | None = None,
        *,
        key: str = "",
        run_id: str = "",
        trace_id: str = "",
    ) -> Effect:
        """Record intent before running a side effect.

        If the key is already APPLIED the *existing* effect is returned with
        ``status = APPLIED`` -- the caller checks that and skips execution,
        which is what makes a retry exactly-once. A FAILED or INTERRUPTED key
        is reset to INTENT and returned, because a retry after an unknown or
        failed outcome is precisely what a retry is for.
        """
        k = key or effect_key(tool, arguments, run_id)
        with self._lock:
            existing = self._effects.get(k)
            if existing is not None:
                if existing.applied:
                    return existing
                existing.status = EffectStatus.INTENT
                existing.error = ""
                existing.updated_at = utcnow().isoformat(timespec="seconds")
                self._save()
                self._emit("effect.resumed", existing)
                return existing
            effect = Effect(
                key=k,
                tool=tool,
                arguments=_safe(_redact_deep(arguments or {})),
                run_id=run_id,
                trace_id=trace_id,
            )
            self._effects[k] = effect
            self._order.append(k)
            self._trim_locked()
            self._save()
            self._emit("effect.recorded", effect)
            return effect

    def mark_applied(self, key: str, result: Any = None, *, output: Any = None) -> Effect | None:
        """Record success. ``output`` is what a replay should return."""
        with self._lock:
            effect = self._effects.get(key)
            if effect is None:
                return None
            effect.status = EffectStatus.APPLIED
            effect.error = ""
            payload: dict[str, Any] = {}
            if output is not None:
                payload["output"] = _safe(output)
            elif result is not None:
                payload["output"] = _safe(result)
            payload["ok"] = True
            effect.result = payload
            effect.updated_at = utcnow().isoformat(timespec="seconds")
            self._save()
            self._emit("effect.applied", effect)
            return effect

    def mark_failed(self, key: str, error: str = "") -> Effect | None:
        """Record an observed failure. Retryable: ``begin`` will reset it."""
        with self._lock:
            effect = self._effects.get(key)
            if effect is None:
                return None
            effect.status = EffectStatus.FAILED
            effect.error = redact(str(error or "effect failed"))
            effect.updated_at = utcnow().isoformat(timespec="seconds")
            self._save()
            self._emit("effect.failed", effect)
            return effect

    def skip(self, key: str) -> Effect | None:
        """Mark an already-decided effect as deliberately not re-run."""
        with self._lock:
            effect = self._effects.get(key)
            if effect is None:
                return None
            if effect.status in (EffectStatus.APPLIED, EffectStatus.SKIPPED):
                return effect
            effect.status = EffectStatus.SKIPPED
            effect.updated_at = utcnow().isoformat(timespec="seconds")
            self._save()
            return effect

    # -- queries --------------------------------------------------------
    def get(self, key: str) -> Effect | None:
        with self._lock:
            return self._effects.get(key)

    def status(self, key: str) -> EffectStatus | None:
        effect = self.get(key)
        return effect.status if effect else None

    def is_applied(self, key: str) -> bool:
        effect = self.get(key)
        return bool(effect and effect.applied)

    def entries(self) -> list[Effect]:
        with self._lock:
            return [self._effects[k] for k in self._order if k in self._effects]

    def pending(self) -> list[Effect]:
        """Effects with no recorded outcome yet (still running, or lost)."""
        return [e for e in self.entries() if e.status is EffectStatus.INTENT]

    def interrupted(self) -> list[Effect]:
        return [e for e in self.entries() if e.status is EffectStatus.INTERRUPTED]

    def failed(self) -> list[Effect]:
        return [e for e in self.entries() if e.status is EffectStatus.FAILED]

    def replay(self, key: str) -> dict[str, Any] | None:
        """The recorded output for an applied effect, or ``None``.

        Does *not* mark anything: a caller may inspect a result without
        consuming it. ``skip`` is the act of recording "we did not re-run".
        """
        effect = self.get(key)
        if effect is None or not effect.applied:
            return None
        return dict(effect.result)

    # -- maintenance ----------------------------------------------------
    def forget(self, key: str) -> bool:
        with self._lock:
            if key not in self._effects:
                return False
            del self._effects[key]
            self._order = [k for k in self._order if k != key]
            self._save()
            return True

    def clear(self) -> int:
        with self._lock:
            count = len(self._effects)
            self._effects.clear()
            self._order.clear()
            self._save()
            return count

    def _trim_locked(self) -> None:
        """Drop the oldest applied effects past the cap.

        Only *applied* (settled) entries are evicted. An INTENT, INTERRUPTED
        or FAILED entry is never dropped by the cap: those are exactly the
        ones someone still has to look at, and losing one to a size limit
        would defeat the ledger's purpose.
        """
        while len(self._order) > self.max_entries:
            victim = None
            for k in self._order:
                eff = self._effects.get(k)
                if eff is not None and eff.status in (EffectStatus.APPLIED, EffectStatus.SKIPPED):
                    victim = k
                    break
            if victim is None:
                break
            self._effects.pop(victim, None)
            self._order.remove(victim)

    def _emit(self, name: str, effect: Effect) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(
                name,
                tool=effect.tool,
                effect_id=effect.id,
                status=str(effect.status),
                run_id=effect.run_id,
                trace_id=effect.trace_id,
            )
        except Exception:  # noqa: BLE001 - observability must not break writes
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._effects)


def _redact_deep(value: Any) -> Any:
    """:func:`~alan.interfaces.types.redact` applied through a structure.

    ``redact`` takes a string, but tool arguments are nested. Walking the
    structure here keeps secret-masking working for ``{"headers": {"auth":
    "sk-..."}}`` instead of only for flat top-level strings.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_deep(v) for v in value]
    return value


def _safe(value: Any) -> Any:
    """JSON-normalise a value, falling back to its repr if that fails.

    A tool's output may be an arbitrary object (paths, datetimes, a custom
    class). Recording it must never raise -- the effect already happened, and
    failing to serialise it would lose the fact that it did -- so anything the
    encoder cannot handle becomes text via ``default=str``. Only if *that*
    fails does the repr step in. The result is always something
    :func:`json.dumps` accepts, which is what the on-disk write needs.
    """
    try:
        return json.loads(json.dumps(value, default=str, ensure_ascii=True))
    except (TypeError, ValueError):
        return repr(value)


def summarise(effects: Sequence[Effect]) -> dict[str, Any]:
    """Counts by status -- for the CLI, the daemon and the resume prompt."""
    counts: dict[str, int] = {str(s): 0 for s in EffectStatus}
    for effect in effects:
        counts[str(effect.status)] = counts.get(str(effect.status), 0) + 1
    return counts
