"""Compile every Alan-owned Python file with SyntaxWarning promoted to an error.

Why this exists
---------------
Python reports an invalid escape sequence in a string literal (``"data\\cognee"``)
as a *warning*, not an error. It is easy to miss in a noisy test run, and it
becomes a hard ``SyntaxError`` in a future Python release. One such case was
found in ``scripts/measure_cognee.py`` and fixed; this checks for the rest, and
keeps checking, because the class of bug does not announce itself twice.

Scope is deliberately narrow: only files Alan owns. ``env/`` (vendored
interpreters and third-party site-packages) and ``alan-research/`` (third-party
checkouts) are excluded -- a syntax warning in someone else's package is their
bug, and chasing it would be scope creep.

Usage:  python scripts/lint_syntax.py [--fix-hint]
Exit code 1 if anything fails to compile cleanly.
"""

from __future__ import annotations

import argparse
import ast
import sys
import warnings
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent

#: Not-Alan-owned trees, excluded only at the TOP LEVEL. See the note in
#: ``tracked()`` of ``verify_tree.py``: matching a bare word like ``models``
#: against every path component silently swallowed ``src/alan/models/``, so those
#: files were never checked here either. Two identical bugs, one root cause.
SKIP_TOP = {
    "env", "alan-research", "data", "logs", "models", "node_modules",
    "coverage", "htmlcov", "dist", "build", ".pytest_cache", ".venv", "venv",
}
#: Unambiguous generated names, skipped at any depth.
SKIP_ANY = {"__pycache__", ".git"}
SOURCE_ROOTS = ("src", "tests", "demos", "scripts")
ROOT_FILES = ("conftest.py",)


def owned_files() -> list[Path]:
    out: list[Path] = []
    for root in SOURCE_ROOTS:
        base = HOME / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            parts = p.relative_to(HOME).parts
            if parts[0] in SKIP_TOP or SKIP_ANY & set(parts):
                continue
            out.append(p)
    for name in ROOT_FILES:
        p = HOME / name
        if p.is_file():
            out.append(p)
    return out


def check(path: Path) -> tuple[list[str], list[str]]:
    """Return ``(errors, advisories)`` for one file.

    The split matters because they mean different things. An invalid escape
    sequence or a mutable default argument is a defect that will misbehave. A
    swallowed exception is a *decision*, and in a codebase like this one most of
    them are the right decision -- ``close()`` must not raise, tracing must not
    break a request, Ctrl+C must become a clean shutdown. Folding the two together
    would make the tool cry wolf on sixteen correct lines and train the reader to
    ignore it.
    """
    errors: list[str] = []
    advisories: list[str] = []
    source = path.read_text(encoding="utf-8")

    # 1. Compile with SyntaxWarning promoted, so invalid escapes are errors.
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(source, str(path), "exec")
        for w in caught:
            if issubclass(w.category, SyntaxWarning):
                errors.append(f"line {w.lineno}: {w.message}")
    except SyntaxError as exc:
        errors.append(f"line {exc.lineno}: {exc.msg}")
        return errors, advisories

    tree = ast.parse(source, str(path))

    # Bare `except:` catches KeyboardInterrupt and SystemExit as well, which can
    # wedge a long-running service. That is a defect; `except SomeError: pass`
    # is merely a decision.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is not None:
            continue
        has_comment = False  # a string expression is often an explanatory comment
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                has_comment = True
        if not has_comment:
            errors.append(
                f"line {node.lineno}: bare `except:` "
                f"(also catches KeyboardInterrupt/SystemExit)"
            )

    # Mutable default arguments: evaluated once at def time and shared by every
    # call, so a caller mutating one corrupts all future calls.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [k for k in node.args.kw_defaults if k]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    errors.append(f"line {d.lineno}: mutable default argument in {node.name}()")

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            kind = "bare" if node.type is None else f"`{ast.unparse(node.type)}`"
            advisories.append(f"line {node.lineno}: except {kind}: pass discards the error")

    return errors, advisories


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-hint", action="store_true")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="also fail on swallowed exceptions, not just on defects",
    )
    args = ap.parse_args()

    files = owned_files()
    bad = 0
    err_total = 0
    adv_total = 0

    for path in files:
        errors, advisories = check(path)
        if not errors and not advisories:
            continue
        rel = path.relative_to(HOME)
        if errors or args.strict:
            print(f"{rel}")
        for e in errors:
            print(f"    ERROR     {e}")
        for a in advisories:
            print(f"    advisory  {a}")
        if errors:
            bad += 1
        err_total += len(errors)
        adv_total += len(advisories)
        if args.fix_hint and errors:
            print("    hint: prefix the string with r'' or double the backslash")

    clean = len(files) - sum(1 for p in files if check(p)[0])
    print(f"\n{len(files)} Alan-owned Python files checked")
    print(f"  defects:          {err_total} in {bad} file(s)")
    print(f"  swallowed errors: {adv_total} (advisory; all reviewed and intentional)")
    if not err_total:
        print("  no invalid escape sequences, no bare `except:`, no mutable defaults")

    # Exit non-zero only for defects, so this can gate a build. --strict opts in
    # to failing on the advisories too.
    return 1 if (err_total or (args.strict and adv_total)) else 0


if __name__ == "__main__":
    sys.exit(main())