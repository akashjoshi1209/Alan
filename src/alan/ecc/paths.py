"""Where ECC lives on this machine.

Three candidates, in preference order: an explicit ``[ecc] root`` setting, the
clone vendored next to Alan, then the OpenCode install (ECC's official beta
adapter writes skills and commands there). The first directory that actually
contains a library folder wins; a configured root that points nowhere is not
an error here, ``find_ecc_root`` returns ``None`` and the caller reports it,
because a missing dependency must degrade to a note rather than raise at
startup.
"""

from __future__ import annotations

from pathlib import Path

from ..config import HOME


def find_ecc_root(explicit: str = "") -> Path | None:
    """Resolve the ECC root, or ``None`` when no candidate holds a library."""
    candidates: list[Path] = []
    if explicit.strip():
        candidates.append(Path(explicit).expanduser())
    candidates.append(HOME / "vendor" / "ECC")
    candidates.append(Path.home() / ".config" / "opencode")
    for candidate in candidates:
        if _is_library(candidate):
            return candidate
    return None


def _is_library(path: Path) -> bool:
    try:
        return any(
            (path / sub).is_dir() for sub in ("skills", "commands", "agents")
        )
    except OSError:
        return False
