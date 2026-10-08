"""Alan's coding provider (ADR-005).

The shape follows SWE-agent's discipline — a narrow tool set, a controlled loop
and the diff as the unit of review — but the implementation is Alan's own, because
SWE-agent's dependency set (datasets, pandas, flask) is research tooling Alan has
no use for.

Nothing here decides *what* to change; that is the CodingAgent's job. This layer
only answers four mechanical questions about a repository, which keeps it
testable without a model:

    analyze -> what language, layout, entry points and test command?
    edit    -> what exactly changes?
    test    -> does the suite still pass?
    review  -> is the change safe to show a human?
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import time
from pathlib import Path

from ..interfaces.capabilities import (
    CodingProvider,
    CommandResult,
    DiffSummary,
    ExecutionProvider,
    RepoAnalysis,
)

#: Suffix -> language name. Only languages with a recognisable test runner get
#: an entry in ``_TEST_RUNNERS``, so an unlisted language degrades to "no test
#: command" rather than to a wrong one.
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".ps1": "powershell",
    ".sh": "shell",
    ".bat": "batch",
}

#: Language -> (argv, human-readable label). The argv comes first because that
#: is what is executed; the label is only ever shown. Python deliberately runs
#: through *this* interpreter rather than a bare ``pytest``, so a portable
#: install on removable media uses the Python it was shipped with.
_TEST_RUNNERS = {
    "python": ([sys.executable, "-m", "pytest", "-q"], "python -m pytest -q"),
    "typescript": (["npm", "test"], "npm test"),
    "javascript": (["npm", "test"], "npm test"),
    "rust": (["cargo", "test"], "cargo test"),
    "go": (["go", "test", "./..."], "go test ./..."),
}

_RUNNER_MISSING = (
    "the test runner was not found. Alan's own interpreter runs a Python suite "
    "once pytest is installed into it: "
    "env\\python\\python.exe -m pip install pytest"
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        ".tox",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        "build",
        "dist",
        "out",
        "target",
        "vendor",
        "site-packages",
        "coverage",
        "htmlcov",
        "models",
        "data",
    }
)

_ENTRY_POINT_NAMES = frozenset(
    {
        "main.py",
        "__main__.py",
        "app.py",
        "cli.py",
        "manage.py",
        "main.go",
        "main.rs",
        "index.js",
        "index.ts",
    }
)

_TEST_FILE = re.compile(r"(^test_|_test$|\.test\.|\.spec\.|^tests?\.)")


class LocalCoding(CodingProvider):
    """Repository analysis, editing, test running and diff review."""

    name = "local-coding"

    def __init__(
        self,
        execution: ExecutionProvider | None = None,
        *,
        max_files: int = 20_000,
        max_diff_lines: int = 400,
    ) -> None:
        self.execution = execution
        self.max_files = max_files
        self.max_diff_lines = max_diff_lines

    def analyze(self, repo: str) -> RepoAnalysis:
        root = Path(repo).expanduser()
        if not root.is_dir():
            raise NotADirectoryError(f"{root} is not a directory")
        counts: dict[str, int] = {}
        files = 0
        entry_points: list[str] = []
        has_tests = False
        truncated = False

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in place. Walking a vendored ``node_modules`` or a Python
            # environment to count source files is how a one-second question
            # becomes a thirty-second one.
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _SKIP_DIRS and not name.startswith(".")
            ]
            here = Path(dirpath)
            for name in filenames:
                if files >= self.max_files:
                    truncated = True
                    break
                path = here / name
                language = _LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
                if language:
                    counts[language] = counts.get(language, 0) + 1
                files += 1
                lowered = name.lower()
                try:
                    relative = path.relative_to(root).as_posix()
                except ValueError:
                    relative = name
                if lowered in _ENTRY_POINT_NAMES:
                    entry_points.append(relative)
                if _TEST_FILE.match(lowered):
                    has_tests = True
            if truncated:
                break

        if not counts:
            return RepoAnalysis(root=str(root), notes="no recognised source files")
        language = max(counts, key=lambda key: counts[key])

        test_command = ""
        test_argv: list[str] = []
        if has_tests:
            runner = _TEST_RUNNERS.get(language)
            if runner:
                test_argv = list(runner[0])
                test_command = runner[1]

        notes = f"{files} files scanned; {len(entry_points)} entry point(s)"
        if truncated:
            notes += f"; stopped at the {self.max_files}-file limit"

        return RepoAnalysis(
            root=str(root),
            language=language,
            files=files,
            entry_points=sorted(set(entry_points))[:10],
            test_command=test_command,
            test_argv=test_argv,
            conventions=_conventions(counts, has_tests),
            notes=notes,
        )

    def edit(self, repo: str, edits: dict[str, str]) -> DiffSummary:
        """Write file contents and return the resulting unified diff.

        Original content is read first so the caller gets a real diff even when
        the write later fails.
        """
        root = Path(repo).expanduser()
        summary = DiffSummary()
        patches: list[str] = []

        for relative, content in sorted(edits.items()):
            target = root / relative
            try:
                original = (
                    target.read_text(encoding="utf-8", errors="replace")
                    if target.is_file()
                    else ""
                )
            except OSError:
                original = ""

            patch = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{relative}" if original else "/dev/null",
                    tofile=f"b/{relative}",
                    n=3,
                )
            )

            if not patch:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            summary.files_changed.append(relative)
            summary.files_content[relative] = content
            patches.append(patch)
            summary.additions += sum(
                1
                for line in patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            summary.deletions += sum(
                1
                for line in patch.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )

        summary.patch = "".join(patches)
        return summary

    def run_tests(
        self, repo: str, *, command: str = "", timeout: float = 600.0
    ) -> CommandResult:
        if self.execution is None:
            return CommandResult(
                exit_code=127,
                stderr="local execution is disabled; cannot run tests",
            )

        root = Path(repo).expanduser()
        argv: list[str] = []
        if command:
            # A caller-supplied string is split once, here, and only ever
            # executed as an argv list. Never through a shell.
            argv = command.split()
        else:
            argv = list(self.analyze(repo).test_argv)
        if not argv:
            return CommandResult(
                exit_code=0,
                stdout="no test command could be determined for this repository",
            )

        started = time.perf_counter()
        result = self.execution.run_command(argv, cwd=str(root), timeout=timeout)
        result.duration_ms = (time.perf_counter() - started) * 1000
        if _runner_missing(result, argv):
            result.stderr = f"{result.stderr}\n{_RUNNER_MISSING}".strip()
        return result

    def review(self, diff: DiffSummary) -> str:
        """Heuristic review: risk signals, not judgement.

        A model may add reasoning on top, but the checks that matter for safety
        are mechanical and must not depend on a model being available.
        """
        findings: list[str] = []
        verdict = "no concerns found"

        if not diff.files_changed:
            return "No files changed."

        for relative, content in diff.files_content.items():
            name = relative.lower()
            lines = content.splitlines()
            if any(
                marker in name
                for marker in (
                    "__pycache__",
                    ".env",
                    "id_rsa",
                    "secrets",
                    ".pem",
                    "credentials",
                )
            ):
                findings.append(f"{relative}: sensitive path in the change set")
            if len(content) > 400_000:
                findings.append(f"{relative}: very large file ({len(content)} chars)")
            for number, line in enumerate(lines, start=1):
                stripped = line.strip()
                if len(line) > 200:
                    findings.append(
                        f"{relative}:{number}: very long line ({len(line)} chars)"
                    )
                if stripped.startswith("subprocess") or stripped.startswith("os.system"):
                    findings.append(f"{relative}:{number}: spawns a process directly")
                if "eval(" in stripped or "exec(" in stripped:
                    findings.append(f"{relative}:{number}: dynamic execution (eval/exec)")
                if "verify=False" in stripped:
                    findings.append(f"{relative}:{number}: TLS verification disabled")
                if stripped.startswith("rm ") or "shutil.rmtree(" in stripped:
                    findings.append(
                        f"{relative}:{number}: destructive filesystem operation"
                    )

        if diff.additions + diff.deletions > 800:
            findings.append(
                f"large change: +{diff.additions}/-{diff.deletions} across "
                f"{len(diff.files_changed)} files; review in slices"
            )

        if findings:
            verdict = f"{len(findings)} concern(s); human review required"

        return (
            f"{verdict}\nfiles: {len(diff.files_changed)}  +{diff.additions}"
            f"/-{diff.deletions}\n"
            + "\n".join(f"- {finding}" for finding in findings)
        )

    def available(self) -> tuple[bool, str]:
        if self.execution is None:
            return False, "local execution is disabled, so code cannot be tested"
        return True, "ready"


def _runner_missing(result: CommandResult, argv: list[str]) -> bool:
    """Did the suite fail to start, rather than fail?

    Two distinct shapes: 127 means the process could not be launched at all (a
    bare ``pytest`` or ``cargo`` that is not installed), while ``python -m
    pytest`` starts fine and exits 1 with "No module named". Both mean *the
    runner is absent*, which is worth distinguishing from a failing test suite.
    """
    # Without this, a missing pytest is reported as a failing test suite, which
    # sends the coding agent to fix tests that were never run.
    if result.exit_code == 127:
        return True
    if argv and argv[0] == sys.executable and "No module named" in (result.stderr or ""):
        return True
    return False


def _conventions(counts: dict[str, int], has_tests: bool) -> list[str]:
    """A few inferred conventions, used to brief the coding agent.

    ``has_tests`` is passed in rather than re-inferred: it was already decided by
    the scan, and a second guess here can contradict ``test_command``.
    """
    out: list[str] = []
    total = sum(counts.values())
    if counts.get("python", 0) > 200:
        out.append("large Python codebase")
    if has_tests:
        out.append("has tests")
    else:
        out.append("no test files found")
    if total:
        dominant, count = max(counts.items(), key=lambda pair: pair[1])
        out.append(f"mostly {dominant} ({count / total:.0%})")
    return out