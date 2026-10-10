"""Execution, browser and coding interfaces.

Three separate contracts on purpose. They have very different risk profiles and
must not be collapsed into one "do things" interface:

* :class:`ExecutionProvider` — arbitrary local command execution. Highest risk.
* :class:`BrowserProvider` — reaches authenticated, remote state.
* :class:`CodingProvider` — mutates a repository, gated by diff review.

Open Interpreter and SWE-agent inform the *shape* of these (ADR-004, ADR-005);
neither is a dependency. browser-use implements :class:`BrowserProvider` in an
isolated sidecar (ADR-003).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
@dataclass
class CommandResult:
    """Outcome of a command. ``timed_out`` is separate from a non-zero exit."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, limit: int = 500) -> str:
        text = (self.stdout or self.stderr or "").strip()
        return text[:limit] + ("..." if len(text) > limit else "")


class ExecutionProvider(ABC):
    """Runs things on this machine, behind Alan's permission gate."""

    name: str = "execution"

    @abstractmethod
    def run_command(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | None = None,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run a shell command. Implementations must not bypass the gate."""

    @abstractmethod
    def run_python(self, source: str, *, timeout: float = 60.0) -> CommandResult:
        """Execute a Python snippet in a restricted namespace."""

    @abstractmethod
    def read_file(self, path: str, *, max_bytes: int = 1_000_000) -> str:
        """Read a text file."""

    @abstractmethod
    def write_file(self, path: str, content: str, *, overwrite: bool = True) -> bool:
        """Write a text file."""

    @abstractmethod
    def list_dir(self, path: str, *, pattern: str = "*") -> list[str]:
        """List directory entries matching a glob."""

    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# Browser
# --------------------------------------------------------------------------
@dataclass
class BrowserResult:
    """A browser action's observable outcome."""

    ok: bool
    content: str = ""
    url: str = ""
    title: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    screenshot: bytes | None = None


class BrowserProvider(ABC):
    """Browser automation, never handed to a model directly (brief section 16)."""

    name: str = "browser"

    @abstractmethod
    def open(self, url: str, *, timeout: float = 30.0) -> BrowserResult:
        """Open a URL and return its readable content."""

    @abstractmethod
    def search(self, query: str, *, engine: str = "") -> BrowserResult:
        """Run a web search and return result titles/links/snippets."""

    @abstractmethod
    def navigate(self, url: str) -> BrowserResult:
        """Navigate the current page."""

    @abstractmethod
    def click(self, selector: str) -> BrowserResult:
        """Click an element identified by a CSS selector."""

    @abstractmethod
    def type_text(self, selector: str, text: str) -> BrowserResult:
        """Type into an input identified by a CSS selector."""

    @abstractmethod
    def extract(self, selector: str) -> BrowserResult:
        """Extract text from the first match of a selector."""

    def close(self) -> BrowserResult:
        """Release the page/session."""
        return BrowserResult(ok=True)

    def available(self) -> tuple[bool, str]:
        return True, "ready"


# --------------------------------------------------------------------------
# Coding
# --------------------------------------------------------------------------
@dataclass
class RepoAnalysis:
    """What Alan's coding agent learned about a repository before editing."""

    root: str
    language: str = ""
    files: int = 0
    entry_points: list[str] = field(default_factory=list)
    #: Human-readable command, for showing the user or briefing an agent.
    test_command: str = ""
    #: The same command as an argv list, for execution. Kept separate from
    #: ``test_command`` because an install path can contain spaces, and
    #: splitting a display string to execute it is exactly the bug this
    #: design exists to avoid.
    test_argv: list[str] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class DiffSummary:
    """A reviewable change set."""

    files_changed: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    files_content: dict[str, str] = field(default_factory=dict)


class CodingProvider(ABC):
    """Inspect, edit, test and review a repository (ADR-005)."""

    name: str = "coding"

    @abstractmethod
    def analyze(self, repo: str) -> RepoAnalysis:
        """Inspect a repository: language, layout, entry points, test command."""

    @abstractmethod
    def edit(self, repo: str, edits: dict[str, str]) -> DiffSummary:
        """Write file contents and return the resulting diff."""

    @abstractmethod
    def run_tests(self, repo: str, *, command: str = "", timeout: float = 600.0) -> CommandResult:
        """Run the repository's test suite."""

    @abstractmethod
    def review(self, diff: DiffSummary) -> str:
        """Review a change set and report risks, concerns and a verdict."""

    def available(self) -> tuple[bool, str]:
        return True, "ready"
