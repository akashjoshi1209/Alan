"""Alan's own local execution provider (ADR-004).

Open Interpreter was rejected as a dependency (it is now a Rust fork of OpenAI
Codex that installs by piping a remote script into a shell). The capability is
small, so Alan implements it directly and owns every permission decision.

Three deliberate choices:

* **No shell string by default.** :meth:`run_command` prefers an argument list
  and only uses the shell when the caller explicitly passes a string, so a model
  cannot accidentally turn one argument into a chain of commands.
* **Python snippets run out of process.** :meth:`run_python` spawns the same
  interpreter rather than :func:`exec`, so an infinite loop is a timeout instead
  of a wedged assistant, and a crash cannot take Alan down with it.
* **Bounded everything.** Every call has a timeout and a capture limit; nothing
  here can block forever (brief section 15).

This is *not* a security sandbox. It is a capability that the permission gate
refuses by default (``[tools] execution = false``) and that an agent can only
reach within its own risk ceiling.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from ..interfaces.capabilities import CommandResult, ExecutionProvider

#: Refuse to build a command that obviously targets these.
_BLOCKED_TOKENS = (
    "format c:", "vssadmin", "bcdedit", "diskpart", "cipher /w",
    "remove-item -recurse c:", "rm -rf /", "rm -rf c:",
)


class LocalExecution(ExecutionProvider):
    """Runs commands, Python and file operations on this machine."""

    name = "local"

    def __init__(
        self,
        *,
        root: Path | str | None = None,
        default_timeout: float = 120.0,
        max_output: int = 200_000,
        allow_shell: bool = False,
    ) -> None:
        #: Optional jail. When set, paths outside this directory are refused.
        self.root = Path(root).resolve() if root else None
        self.default_timeout = default_timeout
        self.max_output = max_output
        self.allow_shell = allow_shell

    # -- guards --------------------------------------------------------
    def _resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() and self.root is not None:
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if self.root is not None:
            root = self.root.resolve()
            # Path.is_relative_to needs 3.9+; the manual form stays explicit.
            if resolved != root and root not in resolved.parents:
                raise PermissionError(
                    f"{resolved} is outside the allowed root {root}"
                )
        return resolved

    @staticmethod
    def _guard(command: str) -> None:
        lowered = command.lower()
        for token in _BLOCKED_TOKENS:
            if token in lowered:
                raise PermissionError(f"refused: command targets {token!r}")

    # -- ExecutionProvider ---------------------------------------------
    def run_command(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | None = None,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        use_shell = isinstance(command, str)
        if use_shell and not self.allow_shell:
            raise PermissionError(
                "shell strings are disabled; pass the command as an argument list"
            )
        text = command if isinstance(command, str) else " ".join(command)
        self._guard(text)

        workdir = self._resolve(cwd) if cwd else (self.root or Path.cwd())
        merged = {**os.environ, **(env or {})}
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603 - gated, non-shell by default
                command,
                shell=use_shell,
                cwd=str(workdir),
                env=merged,
                capture_output=True,
                text=True,
                timeout=min(timeout, self.default_timeout * 10),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                exit_code=124,
                stdout=_clip(exc.stdout or "", self.max_output),
                stderr=f"timed out after {timeout}s",
                duration_ms=(time.perf_counter() - started) * 1000,
                timed_out=True,
            )
        except (OSError, ValueError) as exc:
            return CommandResult(
                exit_code=127,
                stderr=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        return CommandResult(
            exit_code=completed.returncode,
            stdout=_clip(completed.stdout or "", self.max_output),
            stderr=_clip(completed.stderr or "", self.max_output),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def run_python(self, source: str, *, timeout: float = 60.0) -> CommandResult:
        """Execute a snippet in a child interpreter, bounded and isolated."""
        self._guard(source)
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, "-I", "-c", source],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.root or Path.cwd()),
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                exit_code=124,
                stderr=f"timed out after {timeout}s",
                duration_ms=(time.perf_counter() - started) * 1000,
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(
                exit_code=127,
                stderr=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=_clip(completed.stdout or "", self.max_output),
            stderr=_clip(completed.stderr or "", self.max_output),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def read_file(self, path: str, *, max_bytes: int = 1_000_000) -> str:
        target = self._resolve(path)
        data = target.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def write_file(self, path: str, content: str, *, overwrite: bool = True) -> bool:
        target = self._resolve(path)
        if target.exists() and not overwrite:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True

    def delete_file(self, path: str) -> bool:
        """Not part of the interface contract: available for the fs tools."""
        target = self._resolve(path)
        if target.is_dir():
            raise PermissionError("refusing to delete a directory recursively")
        if target.exists():
            target.unlink()
            return True
        return False

    def list_dir(self, path: str, *, pattern: str = "*") -> list[str]:
        target = self._resolve(path)
        if not target.is_dir():
            raise NotADirectoryError(f"{target} is not a directory")
        return sorted(
            str(entry)
            for entry in target.iterdir()
            if fnmatch.fnmatch(entry.name, pattern)
        )

    def available(self) -> tuple[bool, str]:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv
                [sys.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot run the local interpreter: {exc}"
        version = (completed.stdout or completed.stderr).strip()
        return True, f"ready ({version}, root={self.root or 'unrestricted'})"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"