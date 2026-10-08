"""Phase-by-phase timing of one `alan ask`, to find where the seconds go.

Alan's trace records durations for individual events, but not for the phases
around them -- so a slow turn is visible without being explainable. This wraps
the pieces of the pipeline that can block (memory read, memory write, model
call, teardown) and prints the real cost of each, in the order they happen.

Read-only: it sends one question and prints timings. It does not modify
settings or data.

Usage:  python profile_ask.py "your question here"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan import runtime as rt_mod  # noqa: E402
from alan.assistant import Alan  # noqa: E402
from alan.config import load_settings  # noqa: E402
from alan.core import orchestrator as orch_mod  # noqa: E402

T0 = time.perf_counter()
_marks: list[tuple[str, float]] = []


def mark(label: str) -> None:
    _marks.append((label, time.perf_counter()))


def _wrap(obj: type, method: str, label: str) -> None:
    """Time one method on a class without editing the class."""
    original = getattr(obj, method)

    def timed(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            mark(f"{label}  [{time.perf_counter() - start:6.2f}s]")

    setattr(obj, method, timed)


def main() -> int:
    question = " ".join(sys.argv[1:]) or "What is the capital of France? One word."

    _wrap(rt_mod, "build_runtime", "build_runtime (probes + providers)")
    _wrap(orch_mod.Orchestrator, "_recall", "memory recall (cognee search)")
    _wrap(orch_mod.Orchestrator, "_memory_decision", "memory decision + write")
    _wrap(orch_mod.Orchestrator, "_execute_step", "tool step")

    from alan.models.router import ModelRouter
    _wrap(ModelRouter, "complete", "model complete")

    settings = load_settings()
    mark("start")
    print(f'question: {question!r}\n')
    print(f"memory backend : {settings.memory.backend}")
    print(f"model          : {settings.ollama.model}  max_tokens={settings.max_tokens}")
    print()

    assistant = Alan(settings)
    mark("Alan() constructed")

    try:
        reply = assistant.ask(question)
    finally:
        assistant.close()
        mark("close()")

    print()
    print("=== phase timeline ===")
    previous = _marks[0][1]
    for label, at in _marks:
        print(f"  +{at - previous:6.2f}s   {label}")
        previous = at
    print()
    print(f"  TOTAL {time.perf_counter() - T0:6.2f}s")
    print(f"  reply: {reply.strip()[:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
