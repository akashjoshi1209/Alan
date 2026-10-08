r"""Measure cognee write latency against the JSON store, warm and cold.

A raw docstring, and it has to stay one: the prose below quotes Windows paths
like ``data\cognee\latency_probe``. In an ordinary triple-quoted string ``\c`` and
``\l`` are invalid escape sequences, so Python emits a SyntaxWarning at compile
time and will raise SyntaxError outright in a future release. Prefixing with `r`
keeps the paths readable instead of forcing doubled backslashes throughout.

The only cognee figure recorded so far is a single COLD `remember` of 559 s.
That is not a usable basis for deciding whether cognee should stay the default
backend, because it conflates two very different costs:

  * a one-off process warm-up (relational migrations, GLiNER + fastembed load)
  * the marginal cost of writing through the graph store

This measures both separately: a first write to a fresh dataset (cold), then
repeats on the now-warm server. The JSON store is timed on identical input so
the comparison is like-for-like.

Nothing here is part of the product; it exists so the backend decision rests on
a number instead of a guess. Run detached -- a cold cognee write can take
minutes.

Measured on a warm sidecar (three writes of an L3 fact each):

    cognee remember (warm)   1.3 - 1.5 s
    cognee search            0.7 - 2.0 s
    json   remember (warm)   0.002 - 0.02 s     ~120-140x faster

cognee's cost is almost entirely one-off startup -- 559 s on the very first
write (extractor model download), ~35 s for the first write after a sidecar
restart, then a flat ~1.4 s. Reading only the first number would suggest
cognee is unusable; it is not.

Uses the `latency_probe` dataset under data\cognee\, never `alan`, so it cannot
write into real memory. The probe dataset is left in place after a run so a
re-run measures the steady state instead of paying cold start again; delete
data\cognee\latency_probe to reset it.

Usage:  python measure_cognee.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ALAN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ALAN / "src"))

from alan.config import MemorySettings  # noqa: E402
from alan.interfaces.types import MemoryItem, MemoryLevel, MemoryType  # noqa: E402
from alan.memory.providers.cognee_provider import CogneeProvider  # noqa: E402
from alan.memory.providers.json_store import JsonMemory  # noqa: E402

# A distinctive marker per run so this never collides with a real dataset and
# so an accidental reuse is obvious in the output.
FACTS = [
    f"cognee latency probe {i}: Alan's browser sidecar listens on port {8022 + i}"
    for i in range(1, 4)
]


def _item(text: str) -> MemoryItem:
    return MemoryItem(
        content=text,
        level=MemoryLevel.L3_PROJECT,
        type=MemoryType.FACT,
        source="probe",
        importance=0.5,
    )


def timed(label: str, store, fact: str) -> float | None:
    # perf_counter, not monotonic: on Windows time.monotonic() resolves to
    # about 15.6 ms, which is coarser than a JSON write. Using it reports the
    # baseline as a meaningless 0.00 and makes a ratio against it undefined.
    start = time.perf_counter()
    try:
        store.remember(_item(fact))
    except Exception as exc:
        print(f"  {label:<26} FAILED after {time.perf_counter() - start:6.2f}s"
              f"  {type(exc).__name__}: {str(exc)[:70]}", flush=True)
        return None
    elapsed = time.perf_counter() - start
    print(f"  {label:<26} {elapsed:7.3f}s", flush=True)
    return elapsed


def main() -> int:
    root = ALAN / "data" / "cognee" / "latency_probe"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    print("cognee write latency (dataset: data\\cognee\\latency_probe)", flush=True)

    print("\nJSON store -- baseline", flush=True)
    json_root = ALAN / "data" / "json_probe"
    json_store = JsonMemory(json_root)
    json_times = [
        timed(f"json remember #{i}", json_store, fact)
        for i, fact in enumerate(FACTS, 1)
    ]
    # The JSON baseline is a scratch measurement; do not leave it lying around
    # next to real session data.
    shutil.rmtree(json_root, ignore_errors=True)

    print("\ncognee -- COLD (fresh dataset, models already loaded on disk)", flush=True)
    cognee = CogneeProvider(
        # A dedicated dataset: this probe must never be able to write into the
        # `alan` dataset a real session uses.
        MemorySettings(enabled=True, backend="cognee", dataset="latency_probe"),
        index_root=root,
        background_writes=False,
    )
    ok, reason = cognee.available()
    print(f"  available: {ok}  {reason}", flush=True)
    if not ok:
        print("  cognee unavailable; nothing measured", flush=True)
        return 1

    cold = timed("cognee remember #1 (cold)", cognee, FACTS[0])

    print("\ncognee -- WARM (same dataset, warm server)", flush=True)
    warm = [
        timed(f"cognee remember #{i + 1} (warm)", cognee, fact)
        for i, fact in enumerate(FACTS[1:], 1)
    ]

    print("\nrecall check -- does what was written come back?", flush=True)
    start = time.perf_counter()
    try:
        hits = cognee.search("which port does the browser sidecar use", top_k=5)
        print(f"  search returned {len(hits)} item(s) in "
              f"{time.perf_counter() - start:.2f}s", flush=True)
        for item in hits[:3]:
            print(f"    - {item.content[:76]}", flush=True)
    except Exception as exc:
        print(f"  search FAILED {type(exc).__name__}: {str(exc)[:80]}", flush=True)

    print("\nsummary", flush=True)
    if cold is not None:
        print(f"  cold cognee write : {cold:8.2f}s", flush=True)
    good_warm = [t for t in warm if t is not None]
    if good_warm:
        mean = sum(good_warm) / len(good_warm)
        print(f"  warm cognee write : {mean:8.2f}s  (mean of {len(good_warm)})",
              flush=True)
        if cold is not None and mean > 0:
            print(f"  cold overhead     : {cold - mean:8.2f}s  (one-off warm-up)",
                  flush=True)
    good_json = [t for t in json_times if t is not None]
    if good_json and good_warm:
        mean_json = sum(good_json) / len(good_json)
        print(f"  json write        : {mean_json:8.4f}s  (mean of {len(good_json)})",
              flush=True)
        if mean_json > 0:
            print(f"  cognee is {(sum(good_warm) / len(good_warm)) / mean_json:,.0f}x "
                  f"slower per warm write", flush=True)
        else:
            print("  JSON writes round to 0 even on perf_counter: they are faster "
                  "than this platform can measure.", flush=True)
    print("\ndone", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())