"""Start the local sidecars a chat session expects -- detached, never waited on.

The launcher used to own this job, and to wait for it: a cognee boot sat on
the critical path of opening a chat window, and narrated itself while doing
so. It does not any more. The probes here are plain TCP connects (milliseconds
when a sidecar answers, refused just as fast when it does not), the starts are
detached processes that own their own readiness, and nothing in this module
can hold a prompt hostage to a sidecar's boot.

Only Ollama is started. Memory no longer needs a process at all: the default
backend is a SQLite file (``alan.memory.providers.LocalMemory``), so the cognee
sidecar -- 817 MB of models, a 1-2 minute cold boot, and an HTTP round trip on
the critical path of every message -- is no longer on the startup path.
``scripts\\cognee-serve.ps1`` remains for anyone who sets
``[memory] backend = "cognee"`` deliberately.

``ALAN_NO_OLLAMA=1`` opts out of the model server, the same switch
``scripts\\run.ps1`` has always honoured.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from .config import HOME

#: Kept for the doctor and for the opt-in cognee launcher, which still probe
#: this port when ``[memory] backend = "cognee"`` is configured.
COGNEE_PORT = 8012
OLLAMA_PORT = 11435

#: A sidecar that has not answered within this window is assumed dead and is
#: started again; until then, one boot in flight holds off the next. Five
#: minutes is far beyond a normal boot (ollama is seconds) and far below
#: "never": a crashed start still gets retried.
_START_FLAG_SECONDS = 300


def listening(port: int) -> bool:
    """Is something accepting connections on ``127.0.0.1:port``?

    A connect, not a request: the question "is it up" is answered by the
    handshake, and a hung sidecar that is not completing one is not up.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _flag_path(root: Path, name: str) -> Path:
    return root / "data" / f"starting-{name}.flag"


def _started_recently(root: Path, name: str) -> bool:
    """Has an ensure tried to start this sidecar in the last few minutes?

    Fire-and-forget has a race without this: two chats opened a second apart
    both see the port down (a boot takes longer than a second), both start a
    sidecar, and on a small machine both die for RAM. The breadcrumb closes
    it -- the same file, with the same window, that ``scripts\\run.ps1``
    honours, so the two launchers hold each other off too.
    """
    try:
        age = time.time() - _flag_path(root, name).stat().st_mtime
    except OSError:
        return False
    return age < _START_FLAG_SECONDS


def _mark_starting(root: Path, name: str) -> None:
    flag = _flag_path(root, name)
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    except OSError:
        pass  # a home we cannot write is a home whose starts may duplicate


def _start_detached(args: Sequence[str]) -> None:
    """Launch ``args`` with no console window and no line back to us."""
    kwargs: dict = {}
    if sys.platform == "win32":
        # A new process group, detached from this console: closing the chat
        # window must not take the sidecar down with it.
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    try:
        subprocess.Popen(
            list(args),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError:
        pass  # a start that fails is reported by the next doctor, not here


def ensure_detached(home: Path | None = None) -> None:
    """Bring up Ollama (local models) if it is down.

    Fires and forgets on purpose -- see the module docstring. Safe to call on
    every run and safe to call twice: the script checks its own port first, so
    the worst a race costs is one extra no-op process.

    Memory is deliberately absent from here. It used to be the reason this
    function existed, and it cost every chat turn a sidecar probe; now that
    the default memory backend is a file, starting a process for it would be
    strictly worse than not.
    """
    root = home or HOME
    if os.environ.get("ALAN_NO_OLLAMA") != "1":
        exe = root / "env" / "ollama" / "ollama.exe"
        if (
            exe.is_file()
            and not listening(OLLAMA_PORT)
            and not _started_recently(root, "ollama")
        ):
            # The sidecar reads these from its own environment; a fresh
            # detached process does not inherit the launcher's anymore.
            os.environ.setdefault("OLLAMA_MODELS", str(root / "models"))
            os.environ.setdefault("OLLAMA_HOST", f"127.0.0.1:{OLLAMA_PORT}")
            _mark_starting(root, "ollama")
            _start_detached([str(exe), "serve"])
