"""Dump a .pyc in full detail: docstrings, constants, and disassembly.

Companion to recover_pyc.py. The first pass gave structure; this pass gives
everything needed to reconstruct behaviour rather than just shape, which
matters because a reconstructed module that merely *imports* is worthless --
it has to pass the same tests the original did.

Usage:  python dump_pyc.py <path/to/module.pyc> [out.txt]
"""

from __future__ import annotations

import dis
import marshal
import sys
import types
from pathlib import Path


def load(pyc: Path) -> types.CodeType:
    return marshal.loads(pyc.read_bytes()[16:])


def emit(code: types.CodeType, out: list[str], depth: int = 0) -> None:
    pad = "    " * depth
    kind = "CLASS" if _is_class(code) else "def"
    out.append("")
    out.append(f"{pad}{'=' * 4} {kind} {code.co_name} {'=' * 4}")
    out.append(f"{pad}argcount={code.co_argcount} posonly={code.co_posonlyargcount} "
               f"kwonly={code.co_kwonlyargcount} flags={code.co_flags:#x}")
    out.append(f"{pad}varnames={code.co_varnames}")
    out.append(f"{pad}names={code.co_names}")
    out.append(f"{pad}freevars={code.co_freevars} cellvars={code.co_cellvars}")
    out.append(f"{pad}--- constants ---")
    for i, c in enumerate(code.co_consts):
        if isinstance(c, types.CodeType):
            out.append(f"{pad}  [{i}] <code {c.co_name}>")
        else:
            out.append(f"{pad}  [{i}] {c!r}")
    doc = code.co_consts[0] if code.co_consts and isinstance(code.co_consts[0], str) else None
    if doc:
        out.append(f"{pad}--- docstring ---")
        for line in doc.splitlines():
            out.append(f"{pad}| {line}")
    out.append(f"{pad}--- disassembly ---")
    for line in dis.Bytecode(code).dis().splitlines():
        out.append(f"{pad}{line}")
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            emit(c, out, depth + 1)


def _is_class(code: types.CodeType) -> bool:
    return "__qualname__" in code.co_varnames


def main() -> int:
    pyc = Path(sys.argv[1])
    out: list[str] = []
    code = load(pyc)
    out.append(f"=== {pyc} ===")
    out.append(f"module docstring: {code.co_consts[0]!r}")
    emit(code, out)
    text = "\n".join(out)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text, encoding="utf-8")
        print(f"wrote {len(text)} chars to {sys.argv[2]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())