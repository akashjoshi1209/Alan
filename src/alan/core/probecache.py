"""Short-lived cache for liveness probes.

Every Alan invocation used to re-ask every backend whether it was up before it
would do anything: a cognee health check, an Ollama model list, a browser
sidecar ping. Measured on this machine that was ~5 s of the ~8 s spent before
the first token -- for answers that had not changed since the previous command.

Caching a probe is only safe if a stale "up" cannot outlive the thing that made
it. Three rules keep that honest:

* **Positives expire fast.** 120 s. Long enough to cover a burst of commands,
  short enough that a sidecar the operator just stopped is noticed quickly.
* **Negatives expire almost immediately.** 3 s. A backend that is starting up
  must be picked up as soon as it is listening, so a failed probe is never
  remembered for long. Caching failures only helps tight loops, and those are
  the ones that most need to notice a sidecar coming up.
* **Diagnostics bypass the cache.** ``alan doctor`` exists to tell the truth
  about right now, so it probes fresh. Reporting a remembered "ready" from a
  diagnostic is exactly the kind of lie this module exists to prevent.

Entries are keyed by provider identity *and* what was probed -- a model name, a
URL, a dataset -- so changing the model or pointing at another port cannot
reuse a stale answer.

Probes may return a payload of any JSON type (a reason string, a health dict).
Payloads are stored verbatim and returned unchanged.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR

#: How long a successful probe may be reused. A model that is pulled does not
#: un-pull itself, and a sidecar that answered a moment ago is not gone, so a
#: positive can be trusted for longer than a negative. `alan doctor` bypasses
#: the cache, which is what keeps this from becoming a stale "ready".
#:
#: This is now an *idle* window (see :func:`probe`), so it is no longer a tax
#: paid by a backend that is genuinely busy. 300 s is kept as a deliberately
#: conservative ceiling: a wrong "ready" is corrected by the first real request
#: failing, via `forget_key`, not by waiting for the clock.
POSITIVE_TTL = 300.0

#: How long a failed probe may be reused. Deliberately tiny.
NEGATIVE_TTL = 3.0

_STATE_PATH = DATA_DIR / "state" / "probes.json"

#: Set ``ALAN_PROBE_CACHE=0`` to make every probe hit the backend for real.
#: Needed by tests, which stub the transport and would otherwise be answered by
#: a real probe from an earlier run, and useful to an operator chasing a
#: backend that keeps flapping.
_CACHE_ENV = "ALAN_PROBE_CACHE"

#: Once a cached answer is this old, a hit refreshes its timestamp instead of
#: just reusing it. This keeps a burst of commands from writing the cache file
#: every time while still sliding the window forward.
_REFRESH_AFTER = 0.25

#: In-process copy of the cache file, so one command does not re-read it for
#: every probe. A single ``alan ask`` probes the same backend several times, and
#: each of those used to be a separate disk read. Entries are only seconds old
#: within a run, and any real failure calls :func:`forget_key`, so staleness
#: cannot accumulate into a wrong answer.
_MEMO: dict = {}
_MEMO_AT: float = 0.0
_MEMO_TTL: float = 5.0

#: Which file ``_MEMO`` describes. The memo is a process-wide global, so it must
#: not survive a change of ``_STATE_PATH``: the tests repoint that at a scratch
#: file, and a memo still describing the real one would answer their probes from
#: state the test never wrote. Comparing the path makes a repoint self-invalidating
#: instead of depending on every caller remembering to clear it.
_MEMO_PATH: str = ""


def _enabled() -> bool:
    raw = os.environ.get(_CACHE_ENV, "").strip().lower()
    return raw not in ("0", "off", "false", "no")


def _load() -> dict:
    global _MEMO, _MEMO_AT, _MEMO_PATH
    now = time.time()
    key = str(_STATE_PATH)
    if key == _MEMO_PATH and _MEMO and now - _MEMO_AT < _MEMO_TTL:
        return _MEMO
    try:
        raw = _STATE_PATH.read_text(encoding="utf-8")
    except (OSError, ValueError):
        _MEMO, _MEMO_AT, _MEMO_PATH = {}, 0.0, key
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        # A truncated or hand-edited cache is not worth failing over: the worst
        # case of ignoring it is one extra probe. The memo is left unset, so the
        # next call re-reads and re-decides rather than pinning this verdict.
        _MEMO, _MEMO_AT, _MEMO_PATH = {}, 0.0, ""
        return {}
    data = data if isinstance(data, dict) else {}
    _MEMO, _MEMO_AT, _MEMO_PATH = data, now, key
    return data


def _store(data: dict) -> None:
    from .atomic import write_atomic

    global _MEMO, _MEMO_AT, _MEMO_PATH
    _MEMO, _MEMO_AT, _MEMO_PATH = data, time.time(), str(_STATE_PATH)
    try:
        write_atomic(_STATE_PATH, json.dumps(data, indent=1, sort_keys=True))
    except (OSError, TypeError, ValueError):
        # Read-only home, an unserialisable payload, or a permissions problem:
        # this cache is an optimisation, never a dependency.
        return


def probe(
    key: str,
    check: Callable[[], tuple[bool, Any]],
    *,
    fresh: bool = False,
) -> tuple[bool, Any]:
    """Return ``(ok, payload)``, reusing a recent answer when there is one."""
    if not _enabled():
        return check()
    now = time.time()
    data = _load()
    entry = data.get(key)

    if not fresh and isinstance(entry, dict) and "ok" in entry and "value" in entry:
        ok = bool(entry["ok"])
        age = now - float(entry.get("at") or 0.0)
        ttl = POSITIVE_TTL if ok else NEGATIVE_TTL
        # `age < 0` means a clock jump backwards; treat it as expired rather
        # than trusting a timestamp from the future.
        if 0 <= age < ttl:
            # Slide the window forward for a backend still being used. Without
            # this the entry expired on a fixed schedule measured from the last
            # *write*, so a backend in steady use still aged out: the probe cost
            # ~3 s and Alan re-probed after every 300 s of continuous activity,
            # which is most of the session. The cache is still bounded -- it
            # simply measures idle time now instead of absolute age, and any
            # real failure calls `forget_key`, which removes the entry outright.
            if age >= ttl * _REFRESH_AFTER:
                entry["at"] = now
                data[key] = entry
                _store(data)
            return ok, entry["value"]

    ok, value = check()

    # The answer being *unchanged* is the common case for a healthy backend, and
    # it used to be treated as "nothing to record": the function returned without
    # touching `at`. That froze the timestamp at first creation, so the entry
    # still expired on a fixed 300 s schedule no matter how often it was used --
    # the sliding window above could never be reached, because control only got
    # here after the entry had already expired. A backend in steady use therefore
    # re-paid the full ~3 s probe every 300 s of every session.
    #
    # Identical payload, different freshness: only the timestamp moves, so this
    # stays a cheap single-key write rather than a reserialisation of everything.
    entry_ok = isinstance(entry, dict) and entry.get("ok") == ok
    if entry_ok and entry.get("value") == value and float(entry.get("at") or 0.0) == now:
        return ok, value
    data[key] = {"ok": ok, "value": value, "at": now}
    _store(data)
    return ok, value


def probe_reason(
    key: str,
    check: Callable[[], tuple[bool, str]],
    *,
    fresh: bool = False,
) -> tuple[bool, str]:
    """:func:`probe` for the common case of a ``(ok, reason)`` string probe."""
    ok, value = probe(key, check, fresh=fresh)
    return ok, "" if value is None else str(value)


def forget() -> None:
    """Drop every cached answer. Used by tests and ``alan doctor --refresh``."""
    _store({})


def forget_key(key: str) -> None:
    """Drop one cached answer.

    Called when a backend that was recently probed as healthy then fails a real
    request. That is proof the probe was wrong, and keeping it would have the
    next command report a backend that is not there.
    """
    data = _load()
    if key in data:
        del data[key]
        _store(data)


__all__ = [
    "NEGATIVE_TTL",
    "POSITIVE_TTL",
    "forget",
    "forget_key",
    "probe",
    "probe_reason",
]
