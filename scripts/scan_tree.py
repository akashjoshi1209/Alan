"""Scan Alan's own source for stubs, dead code and other smells.

This is a diagnostic, not part of the product. It deliberately reports
*suspicion* rather than verdicts: a `pass` can be correct (an abstract method,
an intentional no-op) or a hole, and only a human reading the code can tell.

Usage:  python scan_tree.py [--verbose]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Directories that are not source and must never be walked.
PRUNE = {
    "env", "data", "models", "logs", "__pycache__", ".git", "node_modules",
    "browser-profile", "cognee", "browser", "ollama", "cache", "site-packages",
}

#: Things worth a look, and why.
PATTERNS: list[tuple[str, str]] = [
    ("pass", "`pass` body - may be an intentional no-op or an unfinished body"),
    ("NotImplementedError", "raises NotImplementedError - may be an unimplemented interface"),
    ("TODO", "TODO left in source"),
    ("FIXME", "FIXME left in source"),
    ("XXX", "XXX marker left in source"),
    ("HACK", "HACK marker left in source"),
    ("stub", "names the word stub"),
    ("dummy", "names the word dummy"),
    ("placeholder", "names the word placeholder"),
    ("NotImplemented", "NotImplemented without Error"),
    ("return None  #", "explicitly returns None"),
    ("pragma: no cover", "excluded from coverage"),
    ("noqa", "silences a linter"),
    ("except Exception", "bare-ish `except Exception`"),
    ("except: pass", "excepts and discards an error silently"),
    ("sys.exit", "sys.exit outside an entry point"),
    ("os._exit", "os._exit - skips all cleanup"),
    ("input(", "blocks on input()"),
    ("time.sleep", "blocks a thread with sleep"),
]


def source_files() -> list[Path]:
    out: list[Path] = []
    for base in ("src", "scripts", "tests", "demos"):
        root = ROOT / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in PRUNE for part in path.parts):
                continue
            out.append(path)
    return out


def findings() -> list[tuple[Path, int, str, str]]:
    hits: list[tuple[Path, int, str, str]] = []
    for path in source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            hits.append((path, 0, "READ-FAILED", str(exc)))
            continue
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            for needle, why in PATTERNS:
                if needle in line:
                    hits.append((path, number, needle, f"{stripped[:96]}  <- {why}"))
    return hits


def structural() -> list[str]:
    """Things an AST can say that grep cannot."""
    notes: list[str] = []
    for path in source_files():
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            notes.append(f"{path.relative_to(ROOT)}: UNPARSEABLE: {exc}")
            continue

        # A module that declares `__all__` is re-exporting on purpose; an
        # "unused" import there is the whole point of the file.
        is_reexport = any(
            isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "__all__" for t in n.targets)
            for n in tree.body
        )

        # Lines mentioned only inside a docstring are documentation, not code.
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    first = node.body[0]
                    for offset in range(first.lineno, first.end_lineno + 1):
                        doc_lines.add(offset)

        for node in ast.walk(tree):
            # A function that is only a docstring and/or `pass`.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # An `@abstractmethod` with only a docstring *is* the contract.
                # Reporting it as a stub would bury the real ones.
                is_abstract = any(
                    (getattr(d, "id", "") or getattr(d, "attr", "")) == "abstractmethod"
                    for d in node.decorator_list
                )
                if is_abstract:
                    continue
                body = [
                    n for n in node.body
                    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
                ]
                label = f"{path.relative_to(ROOT)}:{node.lineno}: {node.name}()"
                if not body:
                    notes.append(f"{label} has a docstring and nothing else")
                elif len(body) == 1 and isinstance(body[0], ast.Pass):
                    notes.append(f"{label} is just `pass`")
                elif len(body) == 1 and isinstance(body[0], ast.Raise):
                    exc = body[0].exc
                    name = getattr(exc, "id", None) or getattr(exc, "attr", None)
                    if name in ("NotImplementedError", "NotImplemented"):
                        notes.append(f"{label} raises {name}")
                # Only flag "never returns a value" where returning one was
                # plausibly intended: not annotated `-> None`, not a test, not
                # a dunder, not a generator, and not a function whose whole
                # purpose is to fail loudly (which returns nothing *on purpose*).
                annotated_none = isinstance(node.returns, ast.Constant) and (
                    node.returns.value is None
                )
                only_raises = bool(body) and all(isinstance(n, ast.Raise) for n in body)
                if (
                    not annotated_none
                    and not only_raises
                    and not node.name.startswith("__")
                    and not node.name.startswith(("test_", "setUp", "explode"))
                    and not any(
                        isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await))
                        for n in ast.walk(node)
                    )
                    and not any(isinstance(n, ast.Return) for n in ast.walk(node))
                    and node.decorator_list == []
                ):
                    notes.append(f"{label} never returns and is not annotated `-> None`")

            # An except that swallows the error.
            if isinstance(node, ast.ExceptHandler):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    notes.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"`except ...: pass` discards the error entirely"
                    )

            # An import nothing in the code uses.
            if not is_reexport and isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = (alias.asname or alias.name).split(".")[0]
                    if bound in ("*", "annotations"):
                        continue
                    used = any(
                        isinstance(n, ast.Name) and n.id == bound
                        or isinstance(n, ast.Attribute) and getattr(n.value, "id", None) == bound
                        for n in ast.walk(tree)
                        if not isinstance(n, (ast.Import, ast.ImportFrom))
                    )
                    if not used:
                        notes.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: "
                            f"import {bound} is never used in code"
                        )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="print every hit")
    args = parser.parse_args()

    files = source_files()
    print(f"scanned {len(files)} python file(s) under src/ scripts/ tests/ demos/\n")

    hits = findings()
    print(f"=== textual patterns: {len(hits)} line(s) ===")
    by_needle: dict[str, int] = {}
    for _p, _n, needle, _w in hits:
        by_needle[needle] = by_needle.get(needle, 0) + 1
    for needle, count in sorted(by_needle.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4}  {needle}")
    if args.verbose:
        print()
        for path, number, needle, text in hits:
            print(f"  {path.relative_to(ROOT)}:{number}: {text}")

    notes = structural()
    print(f"\n=== structural notes: {len(notes)} ===")
    for note in notes:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())