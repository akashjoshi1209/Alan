"""ECC's knowledge surface: skills, commands and agent prompts.

ECC ships three families of markdown documents, all local files:

* ``skills/<name>/SKILL.md`` -- workflow instructions with YAML frontmatter;
* ``commands/<name>.md``     -- slash-command prompts;
* ``agents/<name>.md``       -- specialist persona prompts.

Alan treats all three as one read-only library. Nothing here executes and
nothing is written back: an entry is parsed once at load time, found with
:meth:`EccCatalog.search` or the :meth:`EccCatalog.match_*` helpers, and pulled
into a run by the gated ``skill.read`` tool. The library informs an answer the
same way memory recall does -- it is evidence in the prompt, not an action.

Frontmatter is parsed with a deliberately small YAML subset (plain and quoted
scalars, block scalars, one level of nested mapping) because the main
environment has no PyYAML and a library loader must not need one. Behaviour is
therefore identical on every install, which matters for a component whose
search results drive which instructions a model follows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

#: Lookup preference when a bare name exists in more than one family.
KINDS = ("skill", "command", "agent")

#: Words that frame a name as an invocation ("the X skill"). Single-word names
#: only match with one of these nearby -- "report on the budget" must not drag
#: in a skill that happens to be called "report".
_FRAMING = r"(?:skill|command|workflow|guide)"


@dataclass
class Skill:
    """One library entry: local markdown, parsed once, never executed."""

    kind: str
    name: str
    description: str
    content: str
    path: Path
    metadata: dict[str, str] = field(default_factory=dict)
    argument_hint: str = ""

    @property
    def ref(self) -> str:
        """``skill:tdd-workflow`` -- unique across families."""
        return f"{self.kind}:{self.name}"


# -- frontmatter -----------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-fenced frontmatter from the body.

    Handles the shapes ECC actually uses: plain scalars, single- and
    double-quoted scalars (which may contain ``:`` and span lines), block
    scalars (``>`` / ``|`` with optional chomping ``-``), and one level of
    nested mapping such as ``metadata:``. Unrecognised lines are skipped
    rather than raised on: one exotic document must not lose the other 400.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == "---"), None
    )
    if end is None:
        return {}, text

    fields: dict[str, Any] = {}
    i = 1
    while i < end:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line[0] in " \t":
            i += 1
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2).strip()

        # Block scalar: value on its own line, body is the indented run.
        if rest in (">", ">-", ">+", "|", "|-", "|+"):
            i += 1
            block: list[str] = []
            while i < end and (not lines[i].strip() or lines[i][0] in " \t"):
                block.append(lines[i].strip())
                i += 1
            fields[key] = "\n".join(block).strip()
            continue

        # Empty value: one level of nested mapping (``metadata:``).
        if not rest:
            i += 1
            nested: dict[str, str] = {}
            while i < end and lines[i].strip() and lines[i][0] in " \t":
                sub = re.match(r"^\s+([A-Za-z0-9_-]+):\s*(.+)$", lines[i])
                if sub:
                    nested[sub.group(1)] = _unquote(sub.group(2).strip())
                i += 1
            if nested:
                fields[key] = nested
            continue

        # Scalar; a quote left open by a wrapped line joins the next one.
        value = rest
        while _quote_open(value) and i + 1 < end:
            i += 1
            value += " " + lines[i].strip()
        fields[key] = _unquote(value)
        i += 1

    return fields, "\n".join(lines[end + 1:]).strip()


def _quote_open(value: str) -> bool:
    if value.startswith('"'):
        return not (len(value) > 1 and value.endswith('"'))
    if value.startswith("'"):
        return not (len(value) > 1 and value.endswith("'"))
    return False


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _first_line(body: str) -> str:
    """A usable description for an entry whose frontmatter has none."""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("---"):
            continue
        line = re.sub(r"^#+\s*", "", line)
        return line[:180]
    return ""


def _read_entry(kind: str, fallback_name: str, path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields, body = parse_frontmatter(text)
    name = str(fields.get("name") or "").strip() or fallback_name
    if not name:
        return None
    description = str(fields.get("description") or "").strip()
    if not description:
        description = _first_line(body)
    raw_meta = fields.get("metadata")
    metadata = (
        {str(k): str(v) for k, v in raw_meta.items()}
        if isinstance(raw_meta, dict)
        else {}
    )
    return Skill(
        kind=kind,
        name=name,
        description=description,
        content=body.strip(),
        path=path,
        metadata=metadata,
        argument_hint=str(
            fields.get("argument-hint") or fields.get("argument_hint") or ""
        ),
    )


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


# -- the catalog cache -------------------------------------------------------

#: Sits *beside* the tree it describes (``vendor\ECC.catalog-cache.json``):
#: it travels with the folder the way this product expects folders to move,
#: and it never writes inside the vendored tree itself.
_CACHE_SUFFIX = ".catalog-cache.json"


def _fingerprint(root: Path) -> dict[str, list[int]]:
    """``{relative path: [mtime_ns, size]}`` for every entry file.

    A stat, not a read -- this is the whole point of the cache's validator:
    proving nothing changed must cost a fraction of the parse it spares.
    """
    out: dict[str, list[int]] = {}
    for folder, pattern in (("skills", "*/SKILL.md"), ("commands", "*.md"), ("agents", "*.md")):
        base = root / folder
        if not base.is_dir():
            continue
        for doc in sorted(base.glob(pattern)):
            try:
                st = doc.stat()
            except OSError:
                continue
            out[str(doc.relative_to(root))] = [st.st_mtime_ns, st.st_size]
    return out


def _entry_to_json(entry: Skill) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "name": entry.name,
        "description": entry.description,
        "content": entry.content,
        "path": str(entry.path),
        "metadata": entry.metadata,
        "argument_hint": entry.argument_hint,
    }


def _entry_from_json(data: dict[str, Any]) -> Skill:
    return Skill(
        kind=str(data["kind"]),
        name=str(data["name"]),
        description=str(data.get("description") or ""),
        content=str(data.get("content") or ""),
        path=Path(str(data["path"])),
        metadata={str(k): str(v) for k, v in (data.get("metadata") or {}).items()},
        argument_hint=str(data.get("argument_hint") or ""),
    )


# -- the catalog -------------------------------------------------------------

_SLASH = re.compile(r"^\s*/([A-Za-z0-9][\w-]*)")


class EccCatalog:
    """Every ECC entry Alan can find, with the three ways to find one."""

    def __init__(self, root: Path, entries: Sequence[Skill]) -> None:
        self.root = root
        self.entries: tuple[Skill, ...] = tuple(entries)
        self._by_ref = {(e.kind, e.name.lower()): e for e in entries}
        #: Built once by :meth:`_match_data`; see the note there.
        self._match_cache: tuple[tuple[Skill, str, str], ...] | None = None

    @classmethod
    def load(cls, root: Path) -> "EccCatalog":
        """Read every entry under ``root``. Missing folders are simply absent.

        Parsing ~450 markdown files costs about a second -- long enough to
        feel at every prompt -- so the parsed catalog is cached beside the
        tree and validated by stat (mtime + size per entry): an edit
        invalidates itself, a moved folder simply misses, and any cache
        problem falls through to a full read. The cache can therefore only
        ever save time, never change the answer.
        """
        cache_path = root.parent / f"{root.name}{_CACHE_SUFFIX}"
        files = _fingerprint(root)
        try:
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
            if blob.get("files") == files:
                return cls(root, [_entry_from_json(d) for d in blob["entries"]])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # missing, unreadable or stale: fall through to a full read
        entries: list[Skill] = []
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                doc = child / "SKILL.md"
                if child.is_dir() and doc.is_file():
                    entry = _read_entry("skill", child.name, doc)
                    if entry is not None:
                        entries.append(entry)
        for kind, folder in (("command", "commands"), ("agent", "agents")):
            base = root / folder
            if base.is_dir():
                for doc in sorted(base.glob("*.md")):
                    entry = _read_entry(kind, doc.stem, doc)
                    if entry is not None:
                        entries.append(entry)
        try:
            cache_path.write_text(
                json.dumps(
                    {
                        "files": files,
                        "entries": [_entry_to_json(e) for e in entries],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # a read-only folder is not a reason to fail a load
        return cls(root, entries)

    # -- lookup ---------------------------------------------------------
    def counts(self) -> dict[str, int]:
        out = {kind: 0 for kind in KINDS}
        for entry in self.entries:
            out[entry.kind] += 1
        return out

    def all(self, kind: str | None = None) -> list[Skill]:
        if not kind:
            return list(self.entries)
        return [e for e in self.entries if e.kind == kind]

    def get(self, kind: str, name: str) -> Skill | None:
        return self._by_ref.get((kind.strip().lower(), name.strip().lower()))

    def find(self, name: str) -> Skill | None:
        """Resolve a bare name (or a ``kind:name`` ref), skills first."""
        ref = name.strip()
        if ":" in ref:
            kind, _, rest = ref.partition(":")
            hit = self.get(kind, rest)
            if hit is not None:
                return hit
        lowered = ref.lower()
        for kind in KINDS:
            hit = self._by_ref.get((kind, lowered))
            if hit is not None:
                return hit
        return None

    def search(
        self, query: str, *, kind: str | None = None, limit: int = 10
    ) -> list[Skill]:
        """Rank entries that share words with ``query`` (name hits weigh more)."""
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        scored: list[tuple[int, int, Skill]] = []
        for entry in self.entries:
            if kind and entry.kind != kind:
                continue
            name = entry.name.lower()
            spaced = name.replace("-", " ").replace("_", " ")
            name_tokens = set(_tokens(name))
            desc_tokens = set(re.split(r"\W+", entry.description.lower()))
            score = 0
            if query.strip().lower() == spaced or query.strip().lower() == name:
                score += 6
            elif query.strip().lower() in spaced:
                score += 4
            score += sum(3 for t in terms if t in name_tokens)
            score += sum(1 for t in terms if t in desc_tokens)
            if score:
                scored.append((score, len(entry.name), entry))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2].name))
        return [entry for _, _, entry in scored[:limit]]

    # -- planner matching ------------------------------------------------
    def _candidates(self) -> list[Skill]:
        """Skills before commands (``the X skill`` usually means one), then agents;
        longer names first so ``security-review`` beats a shorter overlap."""
        order = {kind: i for i, kind in enumerate(KINDS)}
        return sorted(
            (e for e in self.entries if e.kind in ("skill", "command")),
            key=lambda e: (order[e.kind], -len(e.name)),
        )

    def _match_data(self) -> tuple[tuple[Skill, str, str], ...]:
        """``(entry, lowercased name, hyphen-to-space name)``, built once.

        The two derived strings are the only per-entry material ``match_explicit``
        needs, and the candidate order is fixed for the life of the catalog, so
        every call used to re-sort the entries and re-derive both strings for all
        of them -- then run a regex per entry, twice. On a 455-entry library that
        measured ~380 ms per call, and the planner calls it twice per turn.

        Precomputing the strings and gating each regex behind a plain ``in``
        test (see :meth:`match_explicit`) keeps the result byte-for-byte
        identical while cutting the cost to the handful of entries whose name
        actually appears in the request.
        """
        cached = self._match_cache
        if cached is None:
            cached = tuple(
                (entry, entry.name.lower(), re.sub(r"[-_]+", " ", entry.name.lower()))
                for entry in self._candidates()
            )
            self._match_cache = cached
        return cached

    def match_explicit(self, text: str) -> Skill | None:
        """The request names an entry outright.

        Three shapes, all deliberately literal: a leading ``/name``; a
        hyphenated or underscored name appearing verbatim (``security-review``);
        or any name framed by an invocation word (``the tdd workflow skill``,
        ``skill called report``). A lone common word never matches.
        """
        slash = _SLASH.match(text)
        if slash:
            hit = self.find(slash.group(1))
            if hit is not None:
                return hit
        lowered = text.lower()

        # Both regexes below require the entry's name to appear in the request as
        # a substring, so ``in`` -- a C-level scan -- rules almost every entry out
        # before any pattern is built. The boundary checks then only run for names
        # that are genuinely present, which is exactly when they can change the
        # answer.
        for entry, name, _spaced in self._match_data():
            if "-" not in name and "_" not in name:
                continue
            if name not in lowered:
                continue
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered):
                return entry

        for entry, _name, spaced in self._match_data():
            if spaced not in lowered:
                continue
            pattern = (
                rf"\b{re.escape(spaced)}\s+{_FRAMING}\b"
                rf"|\b{_FRAMING}\s+(?:named\s+|called\s+)?{re.escape(spaced)}\b"
            )
            if re.search(pattern, lowered):
                return entry
        return None

    def match_implicit(self, text: str) -> Skill | None:
        """Whole-name overlap for multi-word names, and nothing weaker.

        Every token of a name must appear as its own word (``what's the tdd
        workflow?`` finds ``tdd-workflow``); single-word names are excluded
        entirely. Matches only what would otherwise be plain chat -- coding,
        schedule and memory intents are decided before this is consulted.
        """
        words = set(_tokens(text))
        if len(words) < 2:
            return None
        best: Skill | None = None
        best_key = (0, 0)
        for entry in self._candidates():
            tokens = _tokens(entry.name)
            if len(tokens) < 2:
                continue
            if all(token in words for token in tokens):
                key = (len(tokens), len(entry.name))
                if key > best_key:
                    best, best_key = entry, key
        return best

    # -- prompt ----------------------------------------------------------
    def hint(self) -> str:
        """One block for the system prompt: how many entries, how to load one."""
        c = self.counts()
        return (
            f"ECC library loaded: {c['skill']} skills, {c['command']} commands, "
            f"{c['agent']} agent prompts. When a request names one, its "
            f"instructions appear in the findings above -- follow them, and say "
            f"plainly when you did not."
        )
