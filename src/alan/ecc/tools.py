"""Gated, read-only tools over the ECC library.

Three tools, all returning text and nothing else: ``skill.list`` (what is
there), ``skill.search`` (which entries fit a query) and ``skill.read`` (one
entry's instructions, capped so a 400-line skill cannot blow a local model's
context window). They register through the ordinary :class:`ToolRegistry`, so
every call passes the permission gate like any built-in -- the operator's
rules for ``skill.*`` govern them exactly as they govern ``filesystem.*``.

The planner reaches ``skill.read`` through the ``load_skill`` plan action; the
other two exist for interactive use and for a plan that must find an entry
before naming one.
"""

from __future__ import annotations

from typing import Sequence

from ..interfaces.tools import ToolProvider
from ..interfaces.types import RiskLevel, ToolSpec
from .catalog import EccCatalog, Skill

#: Refused so a plan step cannot pull an unbounded document into the prompt.
DEFAULT_MAX_CHARS = 4000


class SkillTools(ToolProvider):
    """Read-only discovery for the ECC library. Never executes an entry."""

    name = "skills"

    def __init__(self, catalog: EccCatalog, *, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.catalog = catalog
        self.max_chars = max(500, max_chars)

    def available(self) -> tuple[bool, str]:
        c = self.catalog.counts()
        return True, (
            f"{c['skill']} skills, {c['command']} commands, "
            f"{c['agent']} agents from {self.catalog.root}"
        )

    def tools(self) -> Sequence[ToolSpec]:
        return (self._read(), self._search(), self._list())

    # -- specs ----------------------------------------------------------
    def _read(self) -> ToolSpec:
        return ToolSpec(
            name="skill.read",
            description=(
                "Load one ECC skill, command or agent prompt by name and "
                "return its instructions (read-only, capped)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "entry name, e.g. tdd-workflow",
                    },
                    "kind": {
                        "type": "string",
                        "description": "skill, command or agent; blank searches all",
                    },
                    "query": {
                        "type": "string",
                        "description": "plain-text fallback when no name is known",
                    },
                },
            },
            risk=RiskLevel.SAFE,
            timeout=15.0,
            executor=self._read_entry,
            tags=("ecc", "skills"),
        )

    def _search(self) -> ToolSpec:
        return ToolSpec(
            name="skill.search",
            description=(
                "Search the ECC library (skills, commands, agents) by keywords; "
                "returns names and one-line descriptions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "keywords to search for",
                    },
                    "kind": {
                        "type": "string",
                        "description": "skill, command or agent; blank searches all",
                    },
                },
            },
            risk=RiskLevel.SAFE,
            timeout=15.0,
            executor=self._search_entries,
            tags=("ecc", "skills"),
        )

    def _list(self) -> ToolSpec:
        return ToolSpec(
            name="skill.list",
            description="List ECC library entries by family (skill, command, agent).",
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "skill, command or agent; blank lists all",
                    },
                },
            },
            risk=RiskLevel.SAFE,
            timeout=15.0,
            executor=self._list_entries,
            tags=("ecc", "skills"),
        )

    # -- executors ------------------------------------------------------
    def _read_entry(self, name: str = "", kind: str = "", query: str = "") -> str:
        entry = self.catalog.get(kind, name) if kind and name else None
        if entry is None and name:
            entry = self.catalog.find(name)
        if entry is None and query:
            hits = self.catalog.search(query, kind=kind or None, limit=1)
            entry = hits[0] if hits else None
        if entry is None:
            wanted = name or query or "(nothing)"
            close = self.catalog.search(name or query, limit=3)
            suggestions = ", ".join(e.name for e in close) or "none"
            return (
                f"no ECC entry named {wanted!r}"
                f"{f' (kind={kind})' if kind else ''}; close matches: "
                f"{suggestions}. Use skill.search to find one."
            )
        return self._render(entry)

    def _render(self, entry: Skill) -> str:
        body = entry.content
        note = ""
        if len(body) > self.max_chars:
            body = body[: self.max_chars]
            note = (
                f"\n\n[... truncated at {self.max_chars} chars; run "
                f"`alan skills --read {entry.name}` for the full text]"
            )
        head = f"# ECC {entry.ref}"
        if entry.description:
            head += f"\n{entry.description}"
        return f"{head}\n\n{body}{note}"

    def _search_entries(self, query: str = "", kind: str = "") -> str:
        hits = self.catalog.search(query, kind=kind or None, limit=10)
        if not hits:
            return (
                f"no ECC entries match {query!r}"
                f"{f' in kind={kind}' if kind else ''}. "
                "Try skill.list to browse."
            )
        lines = [f"{len(hits)} match(es) for {query!r}:"]
        for entry in hits:
            lines.append(f"- {entry.ref} -- {entry.description[:140]}")
        return "\n".join(lines)

    def _list_entries(self, kind: str = "") -> str:
        kinds = (kind,) if kind else ("skill", "command", "agent")
        lines: list[str] = []
        for family in kinds:
            entries = self.catalog.all(family)
            if not entries:
                lines.append(f"{family}s: (none)")
                continue
            names = [e.name for e in entries]
            shown = ", ".join(names[:40])
            if len(names) > 40:
                shown += f", ... and {len(names) - 40} more"
            lines.append(f"{family}s ({len(names)}): {shown}")
        return "\n".join(lines)
