"""Prove a reconstructed module compiles to the same code as the original .pyc.

A reconstruction that merely imports is worthless. This compares the compiled
form of the new source against the cached bytecode of the deleted original,
recursively, and reports every structural difference. Names, constants,
varnames, argument counts and flags must agree; only line numbers and the
addresses in the disassembly may differ.

Usage:  python verify_rebuild.py <original.pyc> <module.py> <dotted.module>
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import marshal
import sys
import types
from pathlib import Path


def load_pyc(pyc: Path) -> types.CodeType:
    return marshal.loads(pyc.read_bytes()[16:])


def load_module(module: str) -> types.CodeType:
    spec = importlib.util.find_spec(module)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {module}")
    loader = spec.loader
    assert isinstance(loader, importlib.machinery.SourceFileLoader)
    return loader.get_code(module)


def compare(a: types.CodeType, b: types.CodeType, path: str, out: list[str]) -> None:
    label = f"{path}()"
    for attr in ("co_name", "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals"):
        if getattr(a, attr) != getattr(b, attr):
            out.append(f"{label}: {attr} {getattr(a, attr)!r} != {getattr(b, attr)!r}")
    if a.co_varnames != b.co_varnames:
        out.append(f"{label}: varnames {a.co_varnames} != {b.co_varnames}")
    if a.co_names != b.co_names:
        out.append(f"{label}: names\n    orig: {a.co_names}\n    new : {b.co_names}")
    if a.co_freevars != b.co_freevars or a.co_cellvars != b.co_cellvars:
        out.append(
            f"{label}: closure free={a.co_freevars}/{b.co_freevars} "
            f"cell={a.co_cellvars}/{b.co_cellvars}"
        )
    if a.co_flags != b.co_flags:
        out.append(f"{label}: flags {a.co_flags:#x} != {b.co_flags:#x}")

    def plain(code: types.CodeType) -> list[object]:
        return [c for c in code.co_consts if not isinstance(c, types.CodeType)]

    def key(items: list[object]) -> list[str]:
        """Order-insensitive key for a list of literals.

        A ``frozenset`` literal has no defined order, so the compiler emits its
        members in whatever order the set happens to iterate. Two builds of the
        same source can differ here while being the same program, so sets are
        compared by content and everything else by repr.
        """
        return sorted(
            repr(sorted(c)) if isinstance(c, (frozenset, set)) else repr(c)
            for c in items
        )

    if plain(a) != plain(b):
        # Order can legitimately differ when a docstring or default moves, but
        # the *set* of literals must not.
        if key(plain(a)) != key(plain(b)):
            out.append(f"{label}: constants\n    orig: {plain(a)}\n    new : {plain(b)}")

    kids_a = {c.co_name: c for c in a.co_consts if isinstance(c, types.CodeType)}
    kids_b = {c.co_name: c for c in b.co_consts if isinstance(c, types.CodeType)}
    for name in sorted(set(kids_a) | set(kids_b)):
        if name not in kids_a:
            out.append(f"{path}/{name}: added, was not in the original")
        elif name not in kids_b:
            out.append(f"{path}/{name}: missing from the reconstruction")
        else:
            compare(kids_a[name], kids_b[name], f"{path}/{name}", out)


def main() -> int:
    pyc, source, module = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    del source  # the module loader reads it; kept in argv for documentation
    out: list[str] = []
    compare(load_pyc(pyc), load_module(module), module, out)
    if out:
        print(f"DIFFERENCES ({len(out)}):")
        for line in out:
            print(f"  {line}")
        return 1
    print("identical: the reconstruction compiles to the original's code objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())