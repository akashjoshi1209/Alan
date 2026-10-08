"""Crash-safe file writes shared by Alan's stores.

Both memory providers persist through a temp file and a rename. Two details
matter on Windows and on a slow removable drive:

* **The temp name must be unique per write.** A shared ``L2.tmp`` collides when
  a background writer and a foreground reader write the same level at the same
  moment, and the loser gets ``WinError 5: Access is denied``.
* **A rename can lose a race with an indexer or antivirus.** Retrying briefly is
  the difference between a lost memory and a spurious failure in the user's chat.

Nothing here needs to be clever; it needs to be predictable.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..interfaces.types import new_id


def write_atomic(path: Path, text: str, *, retries: int = 5, pause: float = 0.05) -> bool:
    """Replace ``path`` with ``text`` atomically. False if it could not be done."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{new_id()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
    except OSError:
        _unlink(temp)
        return False

    for attempt in range(retries):
        try:
            os.replace(temp, path)
            return True
        except PermissionError:
            # Another handle on the target; back off and try again.
            if attempt == retries - 1:
                break
            time.sleep(pause * (attempt + 1))
        except OSError:
            break
    _unlink(temp)
    return False


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass