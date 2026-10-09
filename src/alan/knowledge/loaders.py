"""File loading for the knowledge base (v1: text, markdown and source code).

The second brain's first job is the user's own words, so v1 deliberately reads
only formats that are already plain text. PDF, DOCX and HTML need parser
libraries Alan does not ship; they are refused with a clear reason rather than
ingested as mojibake, which would poison retrieval with noise that *looks* like
content.

Two kinds of refusal are security, not tidiness: directories that hold nothing
the user wrote (``.git``, ``node_modules``, the bundled Python) are never walked,
and files whose *names* look like secrets are never read, even though they are
text -- the index is local, but a key in an index is a key in every backup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

KIND_MARKDOWN = "markdown"
KIND_CODE = "code"
KIND_TEXT = "text"

MARKDOWN_EXT = {".md", ".markdown", ".mdx"}
CODE_EXT = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt",
    ".kts", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".swift", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".sql", ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue",
    ".svelte", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".gradle", ".lua", ".r", ".pl", ".ex", ".exs",
}
TEXT_EXT = {".txt", ".text", ".log", ".csv", ".tsv", ".rst", ".org", ".adoc"}

#: Directories never walked. The bundled Python, virtualenvs, VCS data and
#: dependency trees are huge and contain nothing the user wrote.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", "vendor", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode", "site-packages", ".tox", ".eggs",
}
#: Filenames that are text but must never be indexed: a secret in an index is a
#: secret in every copy of that index.
SKIP_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    ".netrc", ".npmrc", ".pypirc", "credentials", "secrets",
}
#: Extension-less files that are still plain text worth indexing.
_NAMED_TEXT = {"dockerfile", "makefile", "procfile", "readme", "license", "notice"}


@dataclass
class LoadedDoc:
    """One file, read and normalised. ``text`` always uses ``\n`` line endings."""

    text: str
    kind: str
    title: str
    path: str


def detect_kind(path: Path) -> str | None:
    """The :data:`KIND_*` for a file, or ``None`` when v1 will not read it."""
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_EXT:
        return KIND_MARKDOWN
    if suffix in TEXT_EXT:
        return KIND_TEXT
    if suffix in CODE_EXT:
        return KIND_CODE
    if not suffix and path.name.lower() in _NAMED_TEXT:
        return KIND_TEXT
    return None


def is_indexable(path: Path) -> bool:
    """Whether ``path`` should be handed to :func:`load` at all."""
    name = path.name.lower()
    if name in SKIP_NAMES or path.name.startswith("."):
        return False
    return detect_kind(path) is not None


def walk(root: Path):
    """Yield indexable files under ``root`` (or ``root`` itself if it is a file)."""
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place: os.walk only descends into what survives here, which is
        # what keeps a walk of `~` from opening the bundled Python tree.
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for filename in sorted(filenames):
            candidate = Path(dirpath) / filename
            if is_indexable(candidate):
                yield candidate


def load(path: Path, *, max_bytes: int) -> LoadedDoc:
    """Read one file. Raises ``ValueError`` for anything v1 refuses, with a reason."""
    kind = detect_kind(path)
    if kind is None:
        raise ValueError(f"unsupported file type: {path.suffix or path.name}")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(
            f"file is {len(raw)} bytes, over the {max_bytes}-byte cap"
        )
    if _looks_binary(raw):
        raise ValueError("file looks binary, not text")
    text = _decode(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return LoadedDoc(text=text, kind=kind, title=_title(path, text, kind), path=str(path))


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _looks_binary(raw: bytes) -> bool:
    if b"\x00" in raw[:8192]:
        return True
    sample = raw[:8192]
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        pass
    try:
        sample.decode("cp1252")
        return False
    except UnicodeDecodeError:
        return True


def _title(path: Path, text: str, kind: str) -> str:
    """A human title: the first markdown heading, else the file stem."""
    if kind == KIND_MARKDOWN:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem
            if stripped:
                break
    return path.stem
