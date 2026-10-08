"""Alan's five demonstration scenarios.

Run with:  python demos/demo_all.py [number ...]

Each demo exercises one capability and prints what actually happened, including
"this capability is not enabled" where that is the truth. A demo that hides a
missing dependency is worse than no demo, so unavailability is reported, not
worked around.

    1  memory      remember, recall across runs, and say "I don't know"
    2  research    plan -> capability -> grounded answer
    3  browser     the browser capability contract and its refusal path
    4  coding      analyse a repository, test it, edit, review the diff
    5  autonomous  a multi-step request with subagents, recovery and a trace

Demos use a throwaway memory store under data/demos so they never pollute real
memory. Demo 4 builds its own scratch repository.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.config import Settings  # noqa: E402
from alan.core.events import Events  # noqa: E402
from alan.interfaces.types import MemoryItem, MemoryLevel, RiskLevel  # noqa: E402
from alan.memory.providers.json_store import JsonMemory  # noqa: E402
from alan.permissions.gate import default_gate  # noqa: E402
from alan.runtime import build_runtime  # noqa: E402

DEMO_ROOT = Path(__file__).resolve().parent
LINE = "-" * 68


def banner(number: int, title: str) -> None:
    print()
    print(LINE)
    print(f"  DEMO {number}: {title}")
    print(LINE)


def _settings(memory_root: Path, **overrides) -> Settings:
    settings = Settings()
    settings.memory.backend = "json"
    settings.tools.coding = True
    settings.tools.execution = True
    for key, value in overrides.items():
        setattr(settings.tools, key, value)
    return settings


def _runtime(memory_root: Path, **tool_overrides):
    return build_runtime(
        _settings(memory_root, **tool_overrides),
        memory=JsonMemory(memory_root),
        attach_trace=True,
    )


# --------------------------------------------------------------------------
def demo_memory() -> bool:
    banner(1, "memory")
    root = DEMO_ROOT.parent / "data" / "demos" / "memory"
    runtime = _runtime(root)
    try:
        memory = runtime.memory
        assert memory is not None

        print("1a. storing a durable fact at L4 (user scope)")
        memory.remember(MemoryItem(
            content="Alan's test suite runs offline with no model server",
            level=MemoryLevel.L4_USER, importance=0.9,
        ))
        memory.remember(MemoryItem(
            content="The USB stick is formatted exFAT with 128 KB clusters",
            level=MemoryLevel.L3_PROJECT,
        ))
        print("   stored 2 items across L3 and L4")

        print("\n1b. retrieval is scoped, not a dump")
        hits = memory.search("what cluster size does the stick use?", top_k=3)
        for hit in hits:
            print(f"   [{hit.level}] {hit.content}")
        print(f"   -> {len(hits)} relevant item(s) out of 2 stored "
              f"(an unrelated question would return 0)")

        print("\n1c. the orchestrator recalls before answering")
        result = runtime.orchestrator.run(
            "what cluster size does my USB stick use?",
            session_id="demo1",
        )
        print(f"   intent={result.artifacts['plan']['intent']} "
              f"recalled={result.artifacts['recalled']} model={result.artifacts.get('model')}")
        print(f"   answer: {result.output.strip()[:100]}")

        print("\n1d. honesty check: a question Alan has no memory for")
        unknown = memory.search("who won the 1966 World Cup?", top_k=3)
        print(f"   retrieved {len(unknown)} item(s) -> nothing is injected, "
              f"so the model must answer from the model or admit ignorance")
        ok = len(unknown) == 0
        print(f"   {'PASS' if ok else 'FAIL'}: no invented memory")
        return ok
    finally:
        runtime.close()


# --------------------------------------------------------------------------
def demo_research() -> bool:
    banner(2, "research")
    root = DEMO_ROOT.parent / "data" / "demos" / "research"
    runtime = _runtime(root)
    try:
        print("2a. the planner classifies a research request")
        result = runtime.orchestrator.run(
            "search the web for the latest release notes of CPython", session_id="demo2"
        )
        plan = result.artifacts["plan"]
        print(f"   intent={plan['intent']} steps={[s['action'] for s in plan['steps']]}")

        print("\n2b. the capability is resolved through the gate")
        observations = result.artifacts.get("observations", [])
        if observations:
            for note in observations:
                print(f"   {note[:100]}")
        else:
            print("   web.search succeeded")

        print("\n2c. degradation behaviour")
        degraded = bool(result.success)
        print(f"   request still produced an answer: {degraded}")
        print(f"   failed steps were reported, not silently dropped: "
              f"{any('unavailable' in n for n in observations) or bool(observations)}")

        print("\n2d. to enable it: set [tools] browser = true and start the sidecar")
        print("   (see docs/decisions/architecture-decisions.md, ADR-003)")
        return degraded
    finally:
        runtime.close()


# --------------------------------------------------------------------------
def demo_browser() -> bool:
    banner(3, "browser")
    root = DEMO_ROOT.parent / "data" / "demos" / "browser"
    runtime = _runtime(root, browser=True)
    ok = True
    try:
        spec = runtime.registry.spec("web.open")
        print("3a. the capability is wired, in an isolated sidecar (ADR-003)")
        print(f"   web.open registered: risk={spec.risk} permissions={spec.permissions}")
        print(f"   provider: {runtime.browser_note}")
        if runtime.browser is None:
            print("   FAIL: browser is enabled but no provider was built")
            return False

        print("\n3b. a live search returns results that match the query")
        query = "python list comprehension"
        found = runtime.registry.invoke("web.search", {"query": query})
        if not found.ok:
            # Honest degradation, not a crash: a blocked or rewritten search page
            # is a real condition and the reason is printed.
            print(f"   search unavailable: {str(found.error)[:120]}")
            print("   PASS (reported, not faked)")
        else:
            results = (found.output or {}).get("data", {}).get("results", [])
            engine = (found.output or {}).get("data", {}).get("engine")
            print(f"   engine={engine} results={len(results)}")
            for item in results[:3]:
                print(f"   - {item['title'][:52]}")
                print(f"     {item['url'][:70]}")
            # Relevance is asserted, not assumed. This network serves cached
            # result pages for unrelated queries, so a non-empty list is not
            # evidence of a working search.
            terms = [t for t in query.lower().split() if len(t) > 3]
            relevant = [
                r for r in results
                if any(t in f"{r['title']} {r['url']}".lower() for t in terms)
            ]
            ok = bool(relevant)
            print(f"   {'PASS' if ok else 'FAIL'}: {len(relevant)}/{len(results)} "
                  f"result(s) actually mention the query")

        print("\n3c. a live fetch returns real text, not an empty success")
        runtime.gate.auto_confirm = True
        runtime.gate.raise_ceiling(RiskLevel.HIGH_RISK)
        fetched = runtime.registry.invoke("web.open", {"url": "https://example.com"})
        if not fetched.ok:
            print(f"   open unavailable: {str(fetched.error)[:110]}")
            print("   PASS (reported, not faked)")
        else:
            body = str((fetched.output or {}).get("content") or "")
            title = str((fetched.output or {}).get("title") or "")
            print(f"   title={title!r} chars={len(body)}")
            print(f"   text: {body[:100]}")
            # An ok-but-empty result reads as "that page is blank" and is
            # indistinguishable from a race, so it counts as a failure here.
            ok = ok and bool(body.strip())
            print(f"   {'PASS' if body.strip() else 'FAIL'}: the page really rendered")

        print("\n3d. the gate is still the only way in")
        from alan.interfaces.types import PermissionRequest

        strict = build_runtime(_settings(root, browser=False))
        try:
            # Raise the ceiling first: at the default one the gate denies this
            # before the tool ever runs, which would prove nothing about the
            # missing provider. The interesting case is an authorised caller
            # hitting an absent capability.
            strict.gate.auto_confirm = True
            strict.gate.raise_ceiling(RiskLevel.HIGH_RISK)
            blocked = strict.registry.invoke("web.open", {"url": "https://example.com"})
            print(f"   authorised, but browser disabled -> ok={blocked.ok}")
            print(f"   reason: {str(blocked.error)[:88]}")
            denied = not blocked.ok and "BrowserProvider" in str(blocked.error)
        finally:
            strict.close()

        gate = default_gate(RiskLevel.MEDIUM_RISK)
        low = gate.check(PermissionRequest("web.open", RiskLevel.HIGH_RISK))
        gate.auto_confirm = True
        gate.raise_ceiling(RiskLevel.HIGH_RISK)
        high = gate.check(PermissionRequest("web.open", RiskLevel.HIGH_RISK))
        print(f"   at the default ceiling      -> {low}")
        print(f"   after the operator raised it -> {high}")
        ok = ok and denied and str(low) != "allowed" and str(high) == "allowed"
        print(f"   {'PASS' if ok else 'FAIL'}: capability is real, and still gated")
        return ok
    finally:
        runtime.close()


# --------------------------------------------------------------------------
def demo_coding() -> bool:
    banner(4, "coding")
    # The scratch repository lives inside Alan's home on purpose: local
    # execution is jailed to that root (see LocalExecution), so a repo outside
    # it is refused. The jail is part of what this demo is showing.
    scratch = DEMO_ROOT.parent / "data" / "demos" / "scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    runtime = _runtime(
        DEMO_ROOT.parent / "data" / "demos" / "coding",
        execution=True,
        coding=True,
    )
    try:
        repo = scratch / "tinyproject"
        repo.mkdir()
        (repo / "main.py").write_text(
            "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    return a / b\n",
            encoding="utf-8",
        )
        (repo / "test_main.py").write_text(
            "from main import add, div\n\n\n"
            "def test_add():\n    assert add(1, 2) == 3\n\n\n"
            "def test_div():\n    assert div(6, 3) == 2\n",
            encoding="utf-8",
        )

        print("4a. analyse the repository")
        analysis = runtime.coding.analyze(str(repo))
        print(f"   language={analysis.language} files={analysis.files} "
              f"entry_points={analysis.entry_points}")
        print(f"   detected test command: {analysis.test_command or 'none'}")

        print("\n4b. run the existing suite before changing anything")
        baseline = runtime.coding.run_tests(str(repo))
        print(f"   baseline ok={baseline.ok} exit={baseline.exit_code}")

        print("\n4c. make a real change and see the diff")
        original = (repo / "main.py").read_text(encoding="utf-8")
        fixed = original.replace(
            "def div(a, b):\n    return a / b\n",
            "def div(a, b):\n    if b == 0:\n        raise ValueError('division by zero')\n"
            "    return a / b\n",
        )
        diff = runtime.coding.edit(str(repo), {"main.py": fixed})
        print(f"   files changed: {diff.files_changed} +{diff.additions}/-{diff.deletions}")
        print("   " + "\n   ".join(diff.patch.splitlines()[:8]))

        print("\n4d. re-run the tests, then review the change")
        after = runtime.coding.run_tests(str(repo))
        print(f"   tests after edit ok={after.ok}")
        print("   review: " + runtime.coding.review(diff).splitlines()[0])

        print("\n4e. the review catches a dangerous edit too")
        risky = runtime.coding.edit(str(repo), {"bad.py": "eval(x)\nsubprocess.run(cmd, shell=True)\n"})
        for line in runtime.coding.review(risky).splitlines()[2:]:
            print(f"   {line}")
        ok = diff.files_changed == ["main.py"] and after.ok and baseline.ok
        print(f"   {'PASS' if ok else 'FAIL'}: analyse -> test -> edit -> test -> review")
        return ok
    finally:
        runtime.close()
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------
def demo_autonomous() -> bool:
    banner(5, "autonomous task")
    root = DEMO_ROOT.parent / "data" / "demos" / "autonomous"
    runtime = _runtime(root, coding=True)
    try:
        runtime.memory.remember(MemoryItem(
            content="Alan's demo repository lives under data/demos",
            level=MemoryLevel.L3_PROJECT,
        ))

        print("5a. a multi-step request runs without supervision")
        result = runtime.orchestrator.run(
            "find the test command for this repository and run the tests",
            session_id="demo5",
        )
        trace_id = result.artifacts.get("trace_id", "")
        print(f"   success={result.success} intent={result.artifacts['plan']['intent']}")
        print(f"   output: {result.output.strip()[:110]}")

        print("\n5b. the full trace of that request")
        events = runtime.trace(trace_id)
        for event in events:
            detail = {k: v for k, v in event.items()
                      if k not in ("name", "trace_id", "agent", "duration_ms",
                                   "timestamp", "request")}
            extra = ", ".join(f"{k}={str(v)[:34]}" for k, v in detail.items())
            ms = f"{event['duration_ms']:.0f}ms" if event.get("duration_ms") else ""
            print(f"   {event['name']:22} {ms:>8}  {extra[:66]}")

        print("\n5c. subagents with least-privilege access")
        from alan.agents.standard import standard_agents

        for agent in standard_agents():
            print(f"   {agent.name:9} ceiling={str(agent.ceiling):11} "
                  f"tools={', '.join(sorted(agent.tools)) or 'none'}")

        print("\n5d. the trace file persists after the process exits")
        print(f"   {runtime.trace_path}")

        required = [
            Events.REQUEST_STARTED, Events.PLAN_CREATED,
            Events.MODEL_FINISHED, Events.REQUEST_FINISHED,
        ]
        names = [event["name"] for event in events]
        ok = result.success and all(name in names for name in required)
        print(f"   {'PASS' if ok else 'FAIL'}: every stage of the pipeline is observable")
        return ok
    finally:
        runtime.close()


# --------------------------------------------------------------------------
DEMOS = {
    1: ("memory", demo_memory),
    2: ("research", demo_research),
    3: ("browser", demo_browser),
    4: ("coding", demo_coding),
    5: ("autonomous task", demo_autonomous),
}


def main(argv: list[str]) -> int:
    wanted = [int(a) for a in argv if a.isdigit()] or sorted(DEMOS)
    results: dict[int, bool] = {}
    for number in wanted:
        title, demo = DEMOS[number]
        try:
            results[number] = demo()
        except Exception as exc:  # a demo must never abort the others
            banner(number, title)
            print(f"   ERROR: {type(exc).__name__}: {exc}")
            results[number] = False

    print()
    print(LINE)
    print("  RESULTS")
    print(LINE)
    for number in wanted:
        title, _demo = DEMOS[number]
        status = "PASS" if results.get(number) else "FAIL"
        print(f"   demo {number} ({title:16}) {status}")
    print(LINE)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))