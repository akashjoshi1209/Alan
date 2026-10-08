"""Verify Alan's own source tree against a checksum manifest, and repair it.

Why this exists
---------------
Files in this tree have been deleted by something outside Alan's control, three
times, with no action from any command run here:

    src/alan/tools/coding.py
    src/alan/core/orchestrator.py
    src/alan/interfaces/types.py

Each one broke the product: `runtime.py` imports `LocalCoding`,
`core/__init__.py` imports `Orchestrator`, and `interfaces/memory.py` imports
`MemoryItem` -- so a missing file means Alan does not start, and the only
surviving artefact is a stale `__pycache__` bytecode cache that a
`PYTHONDONTWRITEBYTECODE` build will not even consult.

Recovery from bytecode is possible (`dump_pyc.py` + `verify_rebuild.py` can
prove a reconstruction faithful) but it is slow and lossy for comments. A
checksum manifest plus a mirror detects the loss in milliseconds and restores
the exact bytes. That is the difference between a broken install and a
one-command repair, so it is worth the twenty lines.

Usage
-----
    python scripts/verify_tree.py                 # report only
    python scripts/verify_tree.py --restore       # repair from the mirror
    python scripts/verify_tree.py --mirror D:\\some\\mirror
    python scripts/verify_tree.py --rebuild       # rewrite the manifest

Exit code is 1 if anything is missing or altered, so this can gate a script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
MANIFEST = HOME / "data" / "state" / "tree-manifest.json"
DEFAULT_MIRROR = Path(r"D:\alan-src-backup-20261005-050950")

#: Generated, vendored or volatile directories, skipped only at the TOP LEVEL of
#: the project. These are the ones whose names are ordinary words -- ``models``,
#: ``data``, ``logs``, ``build``, ``dist``, ``env``, ``coverage`` -- and which are
#: therefore all too easy to mistake for source directories.
SKIP_TOP = {
    "env", "data", "logs", "models", "node_modules", "alan-research",
    "coverage", "htmlcov", "dist", "build", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".venv", "venv",
}

#: Generated directories skipped at ANY depth, because these names are
#: unambiguous: they are never a hand-written source directory.
SKIP_ANY = {"__pycache__", ".git", ".hg", ".svn"}

SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3", ".bin", ".onnx", ".gguf"}

#: Exact relative paths that exist but must never be hashed, mirrored or backed up.
#:
#: ``config/.env`` holds API keys. It was being copied byte-identically into the
#: backup tree -- which is the single most likely directory on this machine to be
#: zipped, uploaded somewhere or committed by accident. That made the one file
#: whose loss is cheapest (it is two lines to recreate, and ``.env.example`` is
#: tracked) the one file whose leak is most expensive. The example file stays
#: tracked, so the shape and the variable names remain protected.
#:
#: Matched as an exact path, per the lesson in :func:`tracked`: a bare ``.env``
#: name would also catch unrelated files elsewhere in the tree.
SKIP_EXACT = {"config/.env"}


def tracked() -> list[Path]:
    """Every source file worth hashing, relative to HOME.

    The two skip sets are kept apart on purpose. An earlier version used a single
    set matched against *every* path component, which meant ``models`` -- a word
    chosen to exclude the top-level GGUF model store -- also excluded
    ``src/alan/models/``, i.e. ``ModelRouter``, one of Alan's load-bearing
    components. Two files had never been protected, and the mirror had no copy of
    either, so a deletion would have been both invisible and unrecoverable.

    A name-based exclusion must therefore record *where* it applies. Matching a
    bare word against a whole path is a bet that the name is unambiguous
    everywhere, and ``models`` shows how that bet fails.
    """
    out: list[Path] = []
    for rel_root in ("src", "tests", "scripts", "docs", "config", "demos"):
        base = HOME / rel_root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(HOME)
            if rel.as_posix() in SKIP_EXACT:
                continue
            parts = rel.parts
            if parts[0] in SKIP_TOP:
                continue
            if SKIP_ANY & set(parts):
                continue
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            out.append(p)
    for name in ("README.md", "pytest.ini", "conftest.py", "pyproject.toml"):
        p = HOME / name
        if p.is_file():
            out.append(p)
    return out


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


#: Only these top-level entries may exist in a mirror. Anything else is debris
#: from a faulty copy and gets removed, so a mistake cannot silently accumulate.
MIRROR_TOP = {"src", "tests", "scripts", "docs", "config", "demos"}


def sync(mirror: Path, known: dict[str, str]) -> int:
    """Copy Alan's tracked files into the mirror, then prove the mirror agrees.

    Driven by ``tracked()`` rather than a hand-written file list. That is the
    point: a separate copy list is a second source of truth, and two sources of
    truth drift. An earlier ad-hoc copy computed its relative paths with a wrong
    ``len("D:\\Alan\\")``, wrote into directories named ``c`` and ``ripts``, and
    reported success while leaving the real copies stale. One list, no arithmetic.
    """
    present = build()

    # 1. Remove debris: any top-level entry in the mirror that Alan does not have.
    if mirror.is_dir():
        for child in sorted(mirror.iterdir()):
            if child.name not in MIRROR_TOP and child.name not in {
                Path(f).parts[0] for f in present
            }:
                print(f"  removing stray  {child.name}")
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    # 2. Copy anything missing or differing.
    written = 0
    for rel in sorted(present):
        dst = mirror / rel
        if dst.is_file() and digest(dst) == present[rel]:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOME / rel, dst)
        written += 1

    # 3. Purge anything excluded from tracking. Removing it from the manifest is
    #    only half the job: the copy is already sitting in the backup tree, and a
    #    file that is merely untracked is still a secret on disk.
    for rel in sorted(SKIP_EXACT):
        stale = mirror / rel
        if stale.is_file():
            stale.unlink()
            print(f"  removed secret {rel} from mirror")

    # 4. Prove it. A sync that does not verify is a sync that lies.
    bad = [
        rel for rel, want in present.items()
        if not (mirror / rel).is_file() or digest(mirror / rel) != want
    ]
    total = sum(1 for p in mirror.rglob("*") if p.is_file())
    print(f"  wrote {written}, {len(present) - written} already current, {total} files in mirror")
    if bad:
        for rel in bad[:10]:
            print(f"  MISMATCH {rel}")
        return 1
    print("  mirror is byte-identical to Alan")
    return 0


def build() -> dict[str, str]:
    return {
        str(p.relative_to(HOME)).replace("\\", "/"): digest(p)
        for p in tracked()
    }


def load() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8")).get("files", {})
    except ValueError:
        return {}


def save(files: dict[str, str]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps({"root": str(HOME), "count": len(files), "files": files}, indent=1),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true", help="repair from the mirror")
    ap.add_argument("--rebuild", action="store_true", help="rewrite the manifest")
    ap.add_argument("--sync", action="store_true", help="copy Alan into the mirror")
    ap.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    args = ap.parse_args()

    if args.rebuild:
        files = build()
        save(files)
        print(f"manifest rebuilt: {len(files)} files -> {MANIFEST}")
        return 0

    known = load()
    if not known and not args.sync:
        print(f"no manifest at {MANIFEST}; run with --rebuild to create one")
        return 2

    if args.sync:
        return sync(args.mirror, known)

    present = build()
    missing = sorted(set(known) - set(present))
    changed = sorted(f for f in set(known) & set(present) if known[f] != present[f])
    added = sorted(set(present) - set(known))

    for f in missing:
        print(f"  MISSING   {f}")
    for f in changed:
        print(f"  ALTERED   {f}")
    for f in added:
        print(f"  untracked {f}")

    healthy = not missing and not changed
    print(
        f"{len(present)} files checked, {len(known)} expected: "
        f"{'healthy' if healthy else f'{len(missing)} missing, {len(changed)} altered'}"
    )

    if not healthy and args.restore:
        repaired = 0
        for f in missing + changed:
            src = args.mirror / f
            if src.is_file():
                dst = HOME / f
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"  restored  {f}")
                repaired += 1
            else:
                print(f"  UNRECOVERABLE (not in mirror) {f}")
        if repaired:
            still = [f for f in missing + changed if f in set(known) - set(build())]
            print(f"restored {repaired} file(s)" + (f"; still missing: {still}" if still else ""))
            save(build())
        return 0 if not [f for f in missing + changed if not (args.mirror / f).is_file()] else 1

    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
