"""ECC's eight development tools, ported as read-only ``ecc.*`` capabilities.

ECC's TypeScript tools execute: they run the test suite, apply the formatter,
audit dependencies. Alan's port keeps the *knowledge* and drops the execution,
for three reasons that are all the same reason -- Alan's permission gate must
stay the single path to side effects (ADR-006):

* ``run_tests`` / ``lint_check`` / ``format_code`` detect the project's
  toolchain and return the command to run, exactly as an advisor would;
* ``check_coverage`` / ``dependency_analyzer`` / ``security_audit`` read files
  that already exist and analyse them locally;
* ``git_summary`` / ``changed_files`` shell out to git, but with an argv list
  (never a string) and a validated ref, so a crafted branch name cannot smuggle
  a second command past the boundary.

Everything is SAFE risk: these do not mutate the tree. Commands that *would*
mutate (``--fix``, ``--write``) are only ever printed. The gate still sees each
call, so an operator rule on ``ecc.*`` can block the lot.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Sequence

from ..interfaces.tools import ToolProvider
from ..interfaces.types import RiskLevel, ToolSpec

#: Directory names never descended into: dependencies, build output, git
#: internals and the virtualenvs that hold tens of thousands of files.
_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "coverage", "__pycache__",
    ".venv", "venv", "env", "vendor", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".opencode",
}

#: Source files worth scanning for secrets and risky patterns.
_SCAN_SUFFIXES = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".properties", ".sh", ".env", ".txt",
}

#: ``(label, regex, family)`` -- family decides which ``type`` filter shows it.
_SECRET_PATTERNS = (
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "secrets"),
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "secrets"),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "secrets"),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "secrets"),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "secrets"),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "secrets"),
    (
        "Hardcoded credential",
        re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"),
        "secrets",
    ),
)

_CODE_PATTERNS = (
    ("subprocess with shell=True", re.compile(r"shell\s*=\s*True"), "code"),
    ("shell: true (node)", re.compile(r"shell\s*:\s*true"), "code"),
    ("eval()", re.compile(r"\beval\s*\("), "code"),
    ("pickle.loads()", re.compile(r"\bpickle\.loads?\s*\("), "code"),
    ("yaml.load() without Loader", re.compile(r"\byaml\.load\s*\((?!.*Loader)"), "code"),
    ("innerHTML assignment", re.compile(r"\binnerHTML\s*="), "code"),
    ("dangerouslySetInnerHTML", re.compile(r"\bdangerouslySetInnerHTML\b"), "code"),
    ("os.system()", re.compile(r"\bos\.system\s*\("), "code"),
)

#: Git refs accepted on the command line: no spaces, no shell metacharacters,
#: never starting with ``-`` (a ref of ``--upload-pack=...`` is a classic).
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@^~{}+-]*$")


def _safe_ref(ref: str) -> bool:
    return bool(_SAFE_REF.match(ref or "")) and "--" not in ref


def _root(cwd: str) -> Path:
    return Path(cwd).expanduser() if cwd else Path.cwd()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file():
        return "bun"
    return "npm"


def _git(args: Sequence[str], root: Path, timeout: float = 25.0) -> tuple[bool, str]:
    """Run one git argv list. No shell, ever; failure returns the message.

    25s rather than something snappier because the first git spawn on this
    machine passes through Defender and observed cold ``git status`` runs
    reached double digits; a timeout that fires on a healthy repo reads as a
    broken one.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "git is not installed (or not on PATH)."
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} timed out."
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip() or "git failed."
    return True, proc.stdout


def _detect_python(root: Path) -> str:
    """Which python test framework, or '' when this is not a python project."""
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini")
    if any((root / m).is_file() for m in markers) or (root / "tests").is_dir():
        if (root / "pytest.ini").is_file():
            return "pytest"
        pyproject = _read_text(root / "pyproject.toml")
        if "[tool.pytest" in pyproject or "pytest" in pyproject:
            return "pytest"
        if any((root / m).is_file() for m in markers):
            return "pytest"
        return "unittest"
    return ""


def _detect_js_framework(root: Path) -> str:
    pkg = _read_json(root / "package.json")
    dev = pkg.get("devDependencies") or {}
    deps = pkg.get("dependencies") or {}
    names = set(dev) | set(deps)
    for known in ("vitest", "jest", "mocha", "ava"):
        if known in names:
            return known
    if (root / "vitest.config.ts").is_file() or (root / "vitest.config.js").is_file():
        return "vitest"
    if (root / "jest.config.js").is_file() or (root / "jest.config.ts").is_file():
        return "jest"
    if (root / "package.json").is_file():
        return "node:test"
    return ""


def _detect_linter(root: Path) -> str:
    if (root / "biome.json").is_file() or (root / "biome.jsonc").is_file():
        return "biome"
    for name in (".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs",
                 ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs",
                 "eslint.config.ts"):
        if (root / name).is_file():
            return "eslint"
    if (root / ".ruff.toml").is_file() or "[tool.ruff]" in _read_text(root / "pyproject.toml"):
        return "ruff"
    if (root / ".pylintrc").is_file() or "pylintrc" in _read_text(root / "pyproject.toml"):
        return "pylint"
    if (root / ".flake8").is_file() or "[tool.flake8]" in _read_text(root / "pyproject.toml"):
        return "flake8"
    if (root / ".golangci.yml").is_file() or (root / ".golangci.yaml").is_file():
        return "golangci-lint"
    if (root / "package.json").is_file():
        return "eslint"
    return ""


def _detect_formatter(root: Path, file_path: str = "") -> str:
    if (root / "biome.json").is_file() or (root / "biome.jsonc").is_file():
        return "biome"
    for name in (".prettierrc", ".prettierrc.json", ".prettierrc.yaml",
                 "prettier.config.js", "prettier.config.cjs"):
        if (root / name).is_file():
            return "prettier"
    if "[tool.black]" in _read_text(root / "pyproject.toml"):
        return "black"
    if (root / ".clang-format").is_file():
        return "clang-format"
    if (root / "rustfmt.toml").is_file() or (root / ".rustfmt.toml").is_file():
        return "rustfmt"
    if (root / "go.mod").is_file():
        return "gofmt"
    suffix = Path(file_path).suffix.lower()
    by_ext = {
        ".py": "black", ".rs": "rustfmt", ".go": "gofmt",
        ".js": "prettier", ".ts": "prettier", ".jsx": "prettier",
        ".tsx": "prettier", ".json": "prettier", ".md": "prettier",
    }
    return by_ext.get(suffix, "")


class EccDevTools(ToolProvider):
    """The eight ``ecc.*`` tools: analyse, detect, advise -- never execute."""

    name = "ecc"

    def available(self) -> tuple[bool, str]:
        return True, "read-only advisory tools (commands are returned, not run)"

    def tools(self) -> Sequence[ToolSpec]:
        return (
            self._run_tests(),
            self._lint_check(),
            self._format_code(),
            self._check_coverage(),
            self._dependency_analyzer(),
            self._security_audit(),
            self._git_summary(),
            self._changed_files(),
        )

    # -- specs ----------------------------------------------------------
    def _run_tests(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.run_tests",
            description=(
                "Detect the project's test framework and return the command to "
                "run it (read-only; the command is printed, not executed)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "coverage": {"type": "boolean", "description": "include coverage flags"},
                    "pattern": {"type": "string", "description": "test name filter (-k)"},
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=30.0,
            executor=self.run_tests,
            tags=("ecc", "dev"),
        )

    def _lint_check(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.lint_check",
            description=(
                "Detect the project's linter and return the check command "
                "(with --fix only when asked; never executed)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "path to lint; blank = whole project"},
                    "fix": {"type": "boolean", "description": "include the fix flag"},
                    "linter": {"type": "string", "description": "force a linter by name"},
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=30.0,
            executor=self.lint_check,
            tags=("ecc", "dev"),
        )

    def _format_code(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.format_code",
            description=(
                "Detect a project's formatter and return the write command "
                "(printed, not executed)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "file to format; blank = whole project"},
                    "formatter": {"type": "string", "description": "force a formatter by name"},
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=30.0,
            executor=self.format_code,
            tags=("ecc", "dev"),
        )

    def _check_coverage(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.check_coverage",
            description=(
                "Read an existing coverage report (lcov / summary JSON / XML) "
                "and compare it to a threshold. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "threshold": {"type": "number", "description": "pass mark, percent (default 80)"},
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=30.0,
            executor=self.check_coverage,
            tags=("ecc", "dev"),
        )

    def _dependency_analyzer(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.dependency_analyzer",
            description=(
                "Read package.json / requirements and summarise dependencies: "
                "counts, exact pins, lockfile status. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=30.0,
            executor=self.dependency_analyzer,
            tags=("ecc", "dev"),
        )

    def _security_audit(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.security_audit",
            description=(
                "Scan source files for hardcoded secrets and risky patterns "
                "(read-only; reports findings, changes nothing)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "all, secrets, code or dependencies (default all)",
                    },
                    "cwd": {"type": "string", "description": "project directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=60.0,
            executor=self.security_audit,
            tags=("ecc", "dev"),
        )

    def _git_summary(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.git_summary",
            description=(
                "Branch, status, recent log and diff stat for a repository "
                "(argv-only git reads; nothing is written)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "base_ref": {"type": "string", "description": "compare against this ref"},
                    "depth": {"type": "integer", "description": "log lines (default 5)"},
                    "cwd": {"type": "string", "description": "repository directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            # git_summary issues up to four git calls; each may meet a cold
            # antivirus scan, so the envelope covers the sum, not one call.
            timeout=100.0,
            executor=self.git_summary,
            tags=("ecc", "git"),
        )

    def _changed_files(self) -> ToolSpec:
        return ToolSpec(
            name="ecc.changed_files",
            description=(
                "List changed, staged, untracked and renamed files from git "
                "status, grouped as a tree. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "repository directory; blank = current"},
                },
            },
            risk=RiskLevel.SAFE,
            timeout=60.0,
            executor=self.changed_files,
            tags=("ecc", "git"),
        )

    # -- executors ------------------------------------------------------
    def run_tests(self, coverage: bool = False, pattern: str = "", cwd: str = "") -> str:
        root = _root(cwd)
        detected = ""
        if _detect_python(root):
            detected = f"python ({_detect_python(root)})"
            command = "python -m pytest"
            if pattern:
                command += f" -k {pattern}"
            if coverage:
                command += " --cov=. --cov-report=term-missing"
        elif (root / "package.json").is_file():
            pm = _package_manager(root)
            framework = _detect_js_framework(root)
            detected = f"javascript ({framework}, {pm})"
            command = f"{pm} test"
            if coverage:
                command += " -- --coverage" if framework == "jest" else " --coverage"
            if pattern:
                command += f" {pattern}"
        elif (root / "Cargo.toml").is_file():
            detected, command = "rust (cargo)", "cargo test"
            if pattern:
                command += f" {pattern}"
        elif (root / "go.mod").is_file():
            detected, command = "go", "go test ./..."
        else:
            return (
                f"no test framework detected in {root}. "
                "Expected pyproject.toml / package.json / Cargo.toml / go.mod."
            )
        return (
            f"detected: {detected}\ncommand: {command}\n"
            "Alan prints the command rather than running it; execute it yourself "
            "or use the code.test capability if test execution is enabled."
        )

    def lint_check(self, target: str = "", fix: bool = False, linter: str = "", cwd: str = "") -> str:
        root = _root(cwd)
        tool = linter or _detect_linter(root)
        if not tool:
            return f"no linter detected in {root}."
        where = f" {target}" if target else ""
        commands = {
            "ruff": f"ruff check{where}" + (" --fix" if fix else ""),
            "biome": f"biome check{where}" + (" --write" if fix else ""),
            "eslint": f"eslint{where}" + (" --fix" if fix else ""),
            "pylint": f"pylint {target or '.'}",
            "flake8": f"flake8{where}",
            "golangci-lint": f"golangci-lint run{where}",
        }
        command = commands.get(tool, f"{tool}{where}")
        note = (
            " --fix/--write is included but NOT executed; run it yourself."
            if fix
            else ""
        )
        return f"detected: {tool}\ncommand: {command}{note}"

    def format_code(self, file_path: str = "", formatter: str = "", cwd: str = "") -> str:
        root = _root(cwd)
        tool = formatter or _detect_formatter(root, file_path)
        if not tool:
            return (
                f"no formatter detected in {root} and no known extension for "
                f"{file_path or '(blank)'}."
            )
        where = f" {file_path}" if file_path else ""
        commands = {
            "black": f"black{where}",
            "prettier": f"prettier --write{where}",
            "biome": f"biome format --write{where}",
            "rustfmt": "rustfmt" + (f" {file_path}" if file_path else ""),
            "gofmt": f"gofmt -w{where}" if file_path else "gofmt -w .",
            "clang-format": f"clang-format -i{where}",
        }
        command = commands.get(tool, f"{tool}{where}")
        return (
            f"detected: {tool}\ncommand: {command}\n"
            "the write flag is included but NOT executed; run it yourself."
        )

    def check_coverage(self, threshold: float = 80.0, cwd: str = "") -> str:
        root = _root(cwd)
        parsed = _parse_coverage(root)
        if parsed is None:
            return (
                f"no coverage report found under {root} "
                "(looked for coverage/coverage-summary.json, coverage/lcov.info, "
                "coverage.xml). Generate one first, e.g. `python -m pytest "
                "--cov` or `npm test -- --coverage`."
            )
        source, pct, files = parsed
        verdict = "PASS" if pct >= threshold else "FAIL"
        lines = [f"source: {source}", f"line coverage: {pct:.1f}% (threshold {threshold:.0f}%) -> {verdict}"]
        if files:
            worst = sorted(files.items(), key=lambda row: row[1])[:8]
            lines.append("lowest files:")
            lines.extend(f"  {value:.1f}%  {name}" for name, value in worst)
        return "\n".join(lines)

    def dependency_analyzer(self, cwd: str = "") -> str:
        root = _root(cwd)
        pkg_path = root / "package.json"
        lines: list[str] = []
        if pkg_path.is_file():
            pkg = _read_json(pkg_path)
            deps = dict(pkg.get("dependencies") or {})
            dev = dict(pkg.get("devDependencies") or {})
            pinned = sorted(
                name
                for name, spec in {**deps, **dev}.items()
                if isinstance(spec, str) and spec and spec[0] in "0123456789"
            )
            pm = _package_manager(root)
            locked = any(
                (root / f).is_file()
                for f in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb")
            )
            lines.append(f"package.json: {len(deps)} dependencies, {len(dev)} devDependencies")
            lines.append(f"package manager: {pm}, lockfile: {'present' if locked else 'MISSING'}")
            if pinned:
                lines.append(
                    f"exact pins ({len(pinned)}): {', '.join(pinned[:10])}"
                    + (", ..." if len(pinned) > 10 else "")
                )
            lines.append(
                f"to check for known vulnerabilities run `{pm} audit` "
                "(network; Alan does not execute it)."
            )
            return "\n".join(lines)

        requirements = root / "requirements.txt"
        if requirements.is_file():
            reqs = [
                line.strip()
                for line in _read_text(requirements).splitlines()
                if line.strip() and not line.strip().startswith(("#", "-"))
            ]
            return (
                f"requirements.txt: {len(reqs)} entries\n"
                "to check for known vulnerabilities run `pip-audit` "
                "(network; Alan does not execute it)."
            )
        return f"no package.json or requirements.txt in {root}."

    def security_audit(self, type: str = "all", cwd: str = "") -> str:
        root = _root(cwd)
        wanted = (type or "all").lower()
        if wanted == "dependencies":
            return self.dependency_analyzer(cwd=cwd)
        if wanted not in ("all", "secrets", "code"):
            return "type must be one of: all, secrets, code, dependencies."

        findings: list[str] = []
        scanned = 0
        for path in _iter_source_files(root):
            scanned += 1
            text = _read_text(path)
            if not text:
                continue
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            for label, pattern, family in (*_SECRET_PATTERNS, *_CODE_PATTERNS):
                if wanted != "all" and family != wanted:
                    continue
                hit = pattern.search(text)
                if hit:
                    line = text.count("\n", 0, hit.start()) + 1
                    findings.append(f"{family}: {label} at {rel}:{line}")
            if len(findings) > 60:
                findings.append("... stopped at 60 findings")
                break

        if not findings:
            return (
                f"no {'secrets' if wanted == 'secrets' else 'risky patterns' if wanted == 'code' else 'findings'} "
                f"in {scanned} files under {root}. This is a local pattern scan, "
                "not a substitute for `npm audit` / `pip-audit`."
            )
        return (
            f"{len(findings)} finding(s) in {scanned} files under {root} "
            "(local pattern scan; review each before acting):\n"
            + "\n".join(f"  - {f}" for f in findings)
        )

    def git_summary(self, base_ref: str = "", depth: int = 5, cwd: str = "") -> str:
        root = _root(cwd)
        # Refuse bad input before touching the repository at all: a crafted
        # ref should be reported as crafted, not as a broken repo.
        if base_ref and not _safe_ref(base_ref):
            return (
                f"rejected base_ref {base_ref!r}: refs may only contain "
                "letters, digits and ._/=@^~{}+-"
            )
        depth = max(1, min(int(depth or 5), 50))
        ok, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
        if not ok:
            return f"not a git repository ({branch})."
        ok, status = _git(["status", "--porcelain=v1"], root)
        if not ok:
            return status
        ok, log = _git(["log", "-n", str(depth), "--oneline", "--decorate"], root)
        if not ok:
            log = log or "(no commits yet)"

        lines = [f"branch: {branch.strip()}"]
        dirty = [line for line in status.splitlines() if line.strip()]
        lines.append(f"working tree: {'clean' if not dirty else f'{len(dirty)} change(s)'}")

        if base_ref:
            ok, diff = _git(["diff", "--stat", f"{base_ref}..HEAD"], root)
            lines.append(f"diff vs {base_ref}:")
            lines.append(diff.rstrip() if ok else diff)
        elif dirty:
            ok, diff = _git(["diff", "--stat", "HEAD"], root)
            if ok and diff.strip():
                lines.append("uncommitted changes:")
                lines.append(diff.rstrip())

        lines.append(f"log (last {depth}):")
        lines.append(log.rstrip())
        return "\n".join(lines)

    def changed_files(self, cwd: str = "") -> str:
        root = _root(cwd)
        ok, status = _git(["status", "--porcelain=v1"], root)
        if not ok:
            return status
        groups: dict[str, list[str]] = {
            "added": [], "modified": [], "deleted": [],
            "renamed": [], "untracked": [],
        }
        for line in status.splitlines():
            if not line.strip():
                continue
            code, path = line[:2], line[3:]
            if code == "??":
                groups["untracked"].append(path)
            elif "R" in code:
                groups["renamed"].append(path)
            elif "A" in code:
                groups["added"].append(path)
            elif "D" in code:
                groups["deleted"].append(path)
            else:
                groups["modified"].append(path)
        if not any(groups.values()):
            return f"clean: no changed files in {root}"
        out: list[str] = []
        for label, paths in groups.items():
            if paths:
                out.append(f"{label} ({len(paths)}):")
                out.extend(f"  {p}" for p in paths[:50])
                if len(paths) > 50:
                    out.append(f"  ... and {len(paths) - 50} more")
        return "\n".join(out)


# -- coverage ---------------------------------------------------------------
def _parse_coverage(root: Path) -> tuple[str, float, dict[str, float]] | None:
    """(source, percent, per-file percents) from the first report found."""
    summary = root / "coverage" / "coverage-summary.json"
    if summary.is_file():
        data = _read_json(summary)
        total = data.get("total") or {}
        lines = total.get("lines") or {}
        pct = float(lines.get("pct") or 0.0)
        files = {
            name: float((info.get("lines") or {}).get("pct") or 0.0)
            for name, info in data.items()
            if name != "total" and isinstance(info, dict)
        }
        return (str(summary), pct, files)

    lcov = root / "coverage" / "lcov.info"
    if lcov.is_file():
        text = _read_text(lcov)
        total_found = total_hit = 0
        files: dict[str, tuple[int, int]] = {}
        current = ""
        for line in text.splitlines():
            if line.startswith("SF:"):
                current = line[3:]
                files.setdefault(current, (0, 0))
            elif line.startswith("LF:"):
                found = int(line[3:] or 0)
                total_found += found
                if current in files:
                    files[current] = (files[current][0] + found, files[current][1])
            elif line.startswith("LH:"):
                hit = int(line[3:] or 0)
                total_hit += hit
                if current in files:
                    files[current] = (files[current][0], files[current][1] + hit)
        pct = (100.0 * total_hit / total_found) if total_found else 0.0
        per_file = {
            name: (100.0 * hit / found if found else 100.0)
            for name, (found, hit) in files.items()
        }
        return (str(lcov), pct, per_file)

    xml = root / "coverage.xml"
    if xml.is_file():
        text = _read_text(xml)
        head = text[:2000]
        match = re.search(r'line-rate="([0-9.]+)"', head)
        if match:
            pct = float(match.group(1)) * 100.0
            # coverage.py writes filename first; keep a variant for writers
            # that reverse the two attributes.
            files = {
                m.group(1): float(m.group(2)) * 100.0
                for m in re.finditer(
                    r'<class[^>]*filename="([^"]*)"[^>]*line-rate="([0-9.]+)"', text
                )
            }
            if not files:
                files = {
                    m.group(2): float(m.group(1)) * 100.0
                    for m in re.finditer(
                        r'<class[^>]*line-rate="([0-9.]+)"[^>]*filename="([^"]*)"', text
                    )
                }
            return (str(xml), pct, files)
    return None


def _iter_source_files(root: Path):
    """Yield scannable files under ``root``, depth- and name-limited."""
    seen = 0
    stack = [root]
    while stack and seen < 400:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if seen >= 400:
                return
            if child.is_dir():
                if child.name not in _SKIP_DIRS:
                    stack.append(child)
            elif child.suffix.lower() in _SCAN_SUFFIXES or child.name == ".env":
                seen += 1
                yield child
