"""Clone the research repositories, shallowly, each isolated in its own category.

Design constraints, all of them deliberate:

* **Shallow.** ``--depth 1 --single-branch``. These repositories exist to be
  read, not built, and ``prefect``/``AutoGPT``/``llama.cpp`` carry years of
  history that would cost gigabytes to fetch for no research value.
* **Isolated.** One directory per repository, under its category. No monorepo,
  no submodules, no shared build tree. A repository must never be able to
  perturb another, or Alan.
* **No builds, no installs, no hooks.** ``GIT_TERMINAL_PROMPT=0`` so a private
  or moved repository fails fast instead of hanging on a credential prompt,
  and ``--no-recurse-submodules`` so a submodule pointer cannot pull code we
  have not evaluated. Third-party code is never executed here: cloning is the
  only operation performed.
* **Failure is data.** A repository that 404s, is private, or is renamed is
  recorded with its error rather than aborting the run, because "this project
  does not exist under that name" is a finding the audit needs.

Usage:  python acquire.py [--only NAME ...] [--timeout SECONDS]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "repos.json"
RESULTS = ROOT / "clone-results.json"


def load_manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["repositories"]


def run(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}
    try:
        done = subprocess.run(
            args, cwd=str(cwd), timeout=timeout, env=env,
            capture_output=True, text=True, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return done.returncode, (done.stdout + done.stderr).strip()


def dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return round(total / 1_048_576, 1)


def clone_one(entry: dict, timeout: int) -> dict:
    name, url = entry["name"], entry["url"]
    dest = ROOT / entry["category"] / name
    result: dict = {
        "name": name,
        "category": entry["category"],
        "url": url,
        "priority": entry["priority"],
        "kind": entry["kind"],
        "path": str(dest.relative_to(ROOT)),
        "status": "pending",
    }

    if (dest / ".git").exists():
        result["status"] = "already-present"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        code, out = run(
            ["git", "clone", "--depth", "1", "--single-branch", "--no-recurse-submodules",
             "--no-tags", url, str(dest)],
            cwd=ROOT, timeout=timeout,
        )
        result["seconds"] = round(time.time() - started, 1)
        if code != 0:
            result["status"] = "failed"
            # Last 6 lines: the useful part of a git failure is at the end.
            result["error"] = "\n".join(out.splitlines()[-6:])
            return result

    code, commit = run(["git", "rev-parse", "HEAD"], cwd=dest, timeout=60)
    result["commit"] = commit if code == 0 else ""
    code, date = run(["git", "log", "-1", "--format=%cI"], cwd=dest, timeout=60)
    result["commit_date"] = date if code == 0 else ""
    code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dest, timeout=60)
    result["branch"] = branch if code == 0 else ""
    result["size_mb"] = dir_size_mb(dest)
    result["status"] = "cloned" if result.get("commit") else "no-commit"
    return result


def main() -> int:
    args = sys.argv[1:]
    timeout = 900
    only: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--only":
            only = args[i + 1].split(",")
            i += 2
        elif args[i] == "--timeout":
            timeout = int(args[i + 1])
            i += 2
        else:
            i += 1

    entries = load_manifest()
    if only:
        entries = [e for e in entries if e["name"] in only]

    results: list[dict] = []
    for entry in entries:
        r = clone_one(entry, timeout)
        results.append(r)
        size = r.get("size_mb", "-")
        secs = r.get("seconds", "-")
        print(
            f"{r['status']:>16}  {r['name']:<24} {size!s:>8} MB  {secs!s:>6}s  {r.get('commit', '')[:12]}",
            flush=True,
        )
        if r["status"] == "failed":
            tail = (r.get("error") or "").splitlines()
            if tail:
                print(f"                    -> {tail[-1]}", flush=True)

    # Merge into any previous run so a resumed acquisition keeps earlier results.
    existing: dict[str, dict] = {}
    if RESULTS.exists():
        try:
            existing = {
                x["name"]: x
                for x in json.loads(RESULTS.read_text(encoding="utf-8"))["repositories"]
            }
        except (ValueError, KeyError):
            existing = {}
    for r in results:
        existing[r["name"]] = r

    merged = sorted(existing.values(), key=lambda x: (x["category"], x["name"]))
    RESULTS.write_text(
        json.dumps({"repositories": merged}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    ok = sum(
        1 for r in merged
        if r["status"] in {"cloned", "already-present", "no-commit"}
    )
    print(f"\n{ok}/{len(merged)} available, {len(merged) - ok} failed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
