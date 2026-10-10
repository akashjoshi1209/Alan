"""Start the local sidecars a chat session expects -- detached, never waited on.

The launcher used to own this job, and to wait for it: a cognee boot sat on
the critical path of opening a chat window, and narrated itself while doing
so. It does not any more. The probes here are plain TCP connects (milliseconds
when a sidecar answers, refused just as fast when it does not), the starts are
detached processes that own their own readiness, and nothing in this module
can hold a prompt hostage to a sidecar's boot.

Until a sidecar answers, the layer above falls back -- memory to the local
JSON store -- and ``alan doctor`` names which one is in use, so the fallback
is visible rather than silent. ``ALAN_NO_MEMORY=1`` / ``ALAN_NO_OLLAMA=1``
opt out, the same switches ``scripts\\run.ps1`` has always honoured.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .config import HOME

COGNEE_PORT = 8012
OLLAMA_PORT = 11435


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
    """Bring up cognee (memory) and Ollama (local models) if they are down.

    Fires and forgets on purpose -- see the module docstring. Safe to call on
    every run and safe to call twice: each sidecar's own script checks its
    port first, so the worst a race costs is one extra no-op process.
    """
    root = home or HOME
    if os.environ.get("ALAN_NO_MEMORY") != "1":
        serve = root / "scripts" / "cognee-serve.ps1"
        runtime = root / "env" / "cognee" / "python" / "python.exe"
        if serve.is_file() and runtime.is_file() and not listening(COGNEE_PORT):
            _start_detached(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(serve),
                ]
            )
    if os.environ.get("ALAN_NO_OLLAMA") != "1":
        exe = root / "env" / "ollama" / "ollama.exe"
        if exe.is_file() and not listening(OLLAMA_PORT):
            # The sidecar reads these from its own environment; a fresh
            # detached process does not inherit the launcher's anymore.
            os.environ.setdefault("OLLAMA_MODELS", str(root / "models"))
            os.environ.setdefault("OLLAMA_HOST", f"127.0.0.1:{OLLAMA_PORT}")
            _start_detached([str(exe), "serve"])
