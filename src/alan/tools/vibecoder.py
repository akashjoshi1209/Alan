"""Alan directs Vibecoder; Vibecoder writes the code.

Alan does not implement features itself. For anything that changes code it
delegates to ``vibecoder``, a headless one-shot coding agent installed
separately, then reports what happened. That division exists because a 1.5b
local model cannot be trusted to write code, while a 550B model behind
``vibecoder`` can -- and because a tool that reports honestly is more useful than
a tool that reports optimistically.

Four behaviours here are load-bearing, and all four were found by running the real
CLI rather than by reading its help text:

**The prompt must use ``--prompt``, never a positional argument.** Vibecoder's
dispatcher accepts a bare task word only when ``process.argv.length === 3``. Add
``--cwd`` or ``--max-steps`` and the prompt is silently discarded: no work, no
session file, and *exit code 0*. Passing the task positionally therefore produces a
perfect-looking no-op, which is the single worst failure mode available here --
it would let Alan report success forever.

**Exit code 0 means nothing.** Success, total inaction and "the agent gave up" are
all ``0``. ``_interpret`` therefore never reads ``returncode`` as an outcome; it
reads the transcript. :func:`delegate` returns ``acted=False`` for an empty log.

**Truncation is reported, not hidden.** Hitting ``--max-steps`` makes Vibecoder
print a structured ``reached max steps without completion`` block listing each
step. That is the most useful thing it ever emits and it becomes ``not_done``.

**Claims are not proof.** Vibecoder answered "a comprehensive math library
providing symbolic computation" about a two-line file. So file modifications are
checked against the filesystem (mtime and size) and every claim is labelled
verified or unverified. Alan reports the distinction instead of laundering it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..interfaces.types import RiskLevel, ToolSpec

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")

#: Vibecoder's own truncation notice. Present in the transcript when the agent ran
#: out of budget, which is the difference between "finished" and "stopped early".
_MAX_STEPS = re.compile(
    r"reached max steps without completion.*?(?=\Z|\n\s*\n)", re.S | re.I
)

#: One `step N: tool_name` line per step in that same block.
_STEP_LINE = re.compile(r"^\s*step\s+(\d+)\s*:\s*(\S+)", re.M)

#: Tool-call markers in the live stream: a glyph, the tool name, then a JSON blob,
#: and separately a bracketed result line "[read_file -> ...]".
#:
#: The leading glyph is deliberately excluded from the pattern. Vibecoder changed
#: its banner between two runs on this machine -- an emoji became a plain dot and
#: the arrow changed shape -- and a pattern naming those characters silently
#: stopped matching, which made a run that had edited files report that it had
#: done nothing. Matching the ASCII tool name is version-proof: the names are the
#: stable part of the contract, the decoration is not.
_TOOL_CALL = re.compile(r"[^\w\s]{0,3}\s*([A-Za-z_][\w.-]*)\s*\{")
_TOOL_ARROW = re.compile(r"^\s*\[([A-Za-z_][\w.-]*)\s*(?:\u27a1|\u2192|->)")

#: Vibecoder's own tool names. A brace counts as a tool call only when the name in
#: front of it is one of these, so an unrelated JSON fragment in the output
#: cannot be mistaken for work performed.
_KNOWN_TOOLS = frozenset({
    "bash", "read_file", "edit_file", "write_file", "list_dir", "glob",
    "grep", "search", "apply_patch", "run_command", "todo", "fetch",
    "web_search", "final_answer",
})

#: Files a coding agent must not rewrite, whatever it claims to be doing. Listed so
#: the report can say *why* something was reverted rather than just "blocked".
_GUARDED_NAMES = frozenset(
    {".env", ".env.local", "id_rsa", "credentials", "secrets.json"}
)

#: Directories a coding task must never be pointed at, whatever the request says.
#:
#: Not a sandbox -- Vibecoder can reach any of them if it decides to. They are
#: refused *before* launch, for two reasons.
#:
#: ``ALAN_HOME`` is the harness supervising the run. An agent allowed to edit the
#: code that decides what to delegate to it, and that verifies the result, can
#: silence the honesty contract by editing the file that writes the report. That
#: is a one-shot way to make every future report say "all done".
#:
#: A filesystem root, a home directory or a system directory means the directory
#: was never really resolved -- it defaulted to somewhere unhelpful -- and handing
#: a shell-enabled agent the drive root is how unrelated files get rewritten.
#: Matching is prefix-based, so the whole tree under Alan's home is refused, not
#: just its top directory: ``src`` is where the verification code lives.
#: Names that are a system directory only when they sit directly on a drive root.
#: Matched by exact leaf name, never by substring -- see :func:`_is_system_location`.
_SYSTEM_LEAVES = frozenset(
    {"windows", "program files", "program files (x86)", "users"}
    if os.name == "nt"
    else set()
)


def _is_drive_root(path: Path) -> bool:
    """Whether ``path`` is a bare drive root such as ``C:\\``.

    Compared as text rather than via ``anchor``, because a drive root's ``name`` is
    the empty string: an equality test against ``name`` silently never matched,
    and every system directory was allowed through.
    """
    text = str(path).rstrip("\\/")
    return len(text) == 2 and text[1] == ":"


def _is_system_location(resolved: Path) -> str:
    """Whether ``resolved`` is a drive root or a system directory.

    System directories are matched as *direct children of a drive root*
    (``C:\\Windows``, ``C:\\Program Files``) and not as a substring anywhere in the
    path. Checking the whole path looked safer and was much worse: ``C:\\Users``
    appears in the path to almost every real project on Windows, so
    ``C:\\Users\\ADMIN\\vibecoder`` was refused as a system location -- blocking
    the single directory most likely to be the one the user wanted worked on.
    """
    if _is_drive_root(resolved):
        return f"{resolved} is a drive root"
    if _is_drive_root(resolved.parent) and resolved.name.lower() in _SYSTEM_LEAVES:
        return f"{resolved} is a system location"
    return ""


def _refusal_for(path: Path) -> str:
    """Why ``path`` must not be handed to a coding agent, or "" if it is fine."""
    try:
        resolved = path.resolve()
    except OSError:
        return f"{path} could not be resolved, so it is not safe to work in"

    system = _is_system_location(resolved)
    if system:
        return (
            f"{system}. That means the project directory was never set, so there "
            "is no project to work in."
        )

    try:
        if resolved == Path.home().resolve():
            return (
                f"{resolved} is the home directory. Point [vibecoder].project_dir "
                "at the project, or run Alan from inside it."
            )
    except OSError:
        pass

    # Alan's own source, and everything under it. Imported lazily: this module is
    # reached during config load, so a module-level import of HOME would be
    # circular.
    try:
        from ..config import HOME

        home_tree = Path(HOME).resolve()
        if resolved == home_tree or home_tree in resolved.parents:
            return (
                f"{resolved} is inside Alan's own source tree ({home_tree}). "
                "Delegating there would let the agent rewrite the harness that "
                "reports on it, including the code that verifies its work. Use "
                "vibecoder.delegate's cwd to name a different project."
            )
    except Exception:  # noqa: BLE001 - a missing HOME must not block real work
        pass

    return ""


#: A write that lands inside one of these is reported loudly. Vibecoder runs with
#: the operator's keys and a shell; the point is not to sandbox it (impossible from
#: here) but to notice when a routine fix wandered somewhere it should not have.
_SENSITIVE_DIR_PARTS = (".ssh", ".aws", ".gnupg", "AppData/Roaming/npm")

#: Windows launcher names, resolved without a shell so no argument can be
#: interpreted as syntax. `.CMD` needs the interpreter because CreateProcess will
#: not run it directly.
_LAUNCHERS = ("vibecoder.CMD", "vibecoder.cmd", "vibecoder.ps1", "vibecoder")

#: Default ceiling. Vibecoder's own help documents 40; 25 keeps a runaway task
#: bounded while still allowing the read-then-edit-then-test shape of real work.
_DEFAULT_MAX_STEPS = 25

#: A task that touches nothing should still finish well inside this. Used only to
#: flag a suspiciously silent run, never to cancel one.
_SUSPICIOUS_SECONDS = 5.0

#: How much transcript a run may keep. A verbose agent can emit hundreds of
#: kilobytes; the *last* of it decides the verdict (the claim is written at the
#: end), so the buffer drops the oldest lines -- never the newest -- and the
#: dropped run is labelled in the warnings.
MAX_TRANSCRIPT_CHARS = 200_000

#: Keep up to this much buffered before dropping old lines, so the final join
#: stays bounded in memory no matter how much a runaway agent prints.
_TRANSCRIPT_SLACK = 2


def strip_ansi(text: str) -> str:
    """Remove terminal control sequences from Vibecoder's output.

    Vibecoder colours its stream heavily and Alan's JSON tool result would
    otherwise carry escape bytes into the model's context and into the log file.
    """
    return _ANSI.sub("", text or "")


def _floor_int(value: Any, default: int) -> int:
    """An int that cannot crash a runtime build on a mis-typed settings table.

    ``[vibecoder] max_steps = \"lots\"`` used to raise inside the constructor --
    one typo in one file made the whole assistant fail to start. A wrong value
    is worth a default, not a dead runtime.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def find_launcher(explicit: str = "") -> str:
    """Locate the ``vibecoder`` executable, or return "" when it is not installed.

    An ``explicit`` path is honoured *exclusively*. It used to fall through to a
    PATH lookup when the name did not resolve, which quietly discarded the
    operator's choice: a typo pointed at nothing, and Alan ran a different binary
    than the one configured, reporting results as though they came from the
    intended one. A setting that silently does something else is worse than a
    setting that refuses.
    """
    if explicit:
        found = shutil.which(explicit) or (
            explicit if Path(explicit).is_file() else ""
        )
        return found

    for name in _LAUNCHERS:
        found = shutil.which(name)
        if found:
            return found

    home = Path.home()
    candidates = (
        home / "AppData/Roaming/npm/vibecoder.CMD",
        home / "AppData/Roaming/npm/vibecoder.cmd",
        home / ".local/bin/vibecoder",
        home / ".bun/bin/vibecoder",
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


@dataclass
class FileChange:
    """One file Vibecoder touched, as far as the filesystem can confirm."""

    path: str
    changed: bool
    detail: str

    def describe(self) -> str:
        state = "verified on disk" if self.changed else "NOT changed on disk"
        return f"{self.path} - {state} ({self.detail})"


@dataclass
class VibecoderResult:
    """What Vibecoder actually did, separated from what it says it did."""

    acted: bool
    exit_code: int
    duration_ms: float
    directory: str = ""
    model: str = ""
    claim: str = ""
    tools: list[str] = field(default_factory=list)
    verified: list[FileChange] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    not_done: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    transcript: str = ""

    @property
    def truncated(self) -> bool:
        """Did Vibecoder run out of budget instead of finishing?

        Matched on ``max-steps`` with the hyphen, because that is how the message
        is worded. Testing for the spaced form silently returned False on every
        run that actually truncated, which is the one case the flag exists for.
        """
        return any("max-steps" in item.lower() for item in self.not_done)

    def report(self) -> str:
        """Render the two-section report Alan shows the user.

        Structured so the honesty requirement is enforced by the shape of the
        output rather than by trusting a model to volunteer it: anything not
        verified appears under its own heading, and an empty "Done" section says
        so in words instead of leaving a blank.
        """
        head = f"Vibecoder via {self.model or 'its default route'}"
        head += f" - exit {self.exit_code}, {self.duration_ms / 1000:.1f}s"
        if self.tools:
            head += f", {len(self.tools)} tool call(s): {', '.join(self.tools)}"
        if self.directory:
            head += f"\nWorking in: {self.directory}"

        lines = [head]

        lines.append("")
        if self.verified:
            lines.append("DONE (verified on disk):")
            lines.extend(f"  - {item.describe()}" for item in self.verified)
            if self.claim:
                lines.append("")
                lines.append("CLAIMED (Vibecoder's own summary):")
                lines.append(f"  - {self.claim}")
        elif self.claim:
            # One statement, labelled honestly. Listing the same sentence under
            # both DONE and CLAIMED is redundant and reads as padding, so the
            # unverified answer gets one home and says plainly it is unverified.
            lines.append("DONE (claimed by Vibecoder, NOT verified on disk):")
            lines.append(f"  - {self.claim}")
        else:
            lines.append("DONE: nothing. Vibecoder returned no result.")

        lines.append("")
        if self.not_done:
            lines.append("NOT DONE:")
            lines.extend(f"  - {item}" for item in self.not_done)
        else:
            lines.append("NOT DONE: nothing outstanding was reported.")

        if self.warnings:
            lines.append("")
            lines.append("WARNINGS:")
            lines.extend(f"  - {item}" for item in self.warnings)

        return "\n".join(lines)


def _snapshot(root: Path, targets: Iterable[str]) -> dict[str, tuple[float, int]]:
    """Record (mtime, size) for each candidate file before the run.

    Taken *before* launching so a change can be distinguished from a file that was
    already like that. Without a before-state, "did Vibecoder write this?" is
    unanswerable and every claim has to be taken on trust.
    """
    out: dict[str, tuple[float, int]] = {}
    for relative in targets:
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            continue
        out[relative] = (stat.st_mtime, stat.st_size)
    return out


def _candidate_files(root: Path, limit: int = 400) -> list[str]:
    """Existing files worth watching for a change.

    Prefers whatever the transcript mentions, but is computed independently so a
    hallucinated path simply fails the existence check instead of being trusted.
    """
    names: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in {"node_modules", "__pycache__"}
            ]
            here = Path(dirpath)
            for name in filenames:
                try:
                    names.append(str((here / name).relative_to(root)))
                except ValueError:
                    continue
                if len(names) >= limit:
                    return names
    except OSError:
        return names
    return names


def _interpret(
    transcript: str, exit_code: int, elapsed: float, model: str
) -> tuple[str, str, list[str], list[str], list[str]]:
    """Pull claim, tools, not-done and warnings out of the transcript.

    Separated from the subprocess so it can be tested against captured real
    transcripts without ever launching the CLI.
    """
    warnings: list[str] = []
    not_done: list[str] = []

    # The banner names the model: an emoji, then the model id, then an arrow and
    # the route. Matched on the id itself rather than the emoji, because the
    # glyph is mangled by whatever codepage the output passed through, while the
    # model id is ASCII and survives intact.
    if not model:
        banner = re.search(
            r"(?:heavy|chat|offline)\s*:\s*([\w.-]+/[\w.-]+/[\w.-]+)", transcript
        ) or re.search(r"\b([\w.-]+/[\w.-]+/[\w.-]+)\b", transcript)
        if banner:
            model = banner.group(1)

    tools: list[str] = []
    for match in _TOOL_CALL.finditer(transcript):
        name = match.group(1)
        if name in _KNOWN_TOOLS and name not in tools:
            tools.append(name)
    for line in transcript.splitlines():
        arrow = _TOOL_ARROW.match(line)
        if arrow:
            name = arrow.group(1)
            if name not in tools:
                tools.append(name)

    truncation = _MAX_STEPS.search(transcript)
    if truncation:
        block = truncation.group(0).strip()
        steps = _STEP_LINE.findall(block)
        detail = ", ".join(f"{n}. {tool}" for n, tool in steps)
        not_done.append(
            "Vibecoder hit --max-steps and stopped before finishing"
            + (f" (steps: {detail})" if detail else "")
        )
        warnings.append(
            "Vibecoder asked for a focused task or a higher --max-steps"
        )

    if not tools:
        not_done.append(
            "Vibecoder made no tool calls - it answered without inspecting or "
            "changing anything, so its reply describes work it did not do"
        )

    for line in transcript.splitlines():
        lowered = line.lower()
        if "error:" in lowered or "fatal:" in lowered:
            snippet = strip_ansi(line).strip()
            if snippet:
                not_done.append(f"error reported: {snippet[:200]}")

    if elapsed < _SUSPICIOUS_SECONDS and not tools:
        warnings.append(
            f"finished in {elapsed:.1f}s having done nothing - treat as a failure"
        )

    # The claim is the prose Vibecoder wrote for the operator. Everything else in
    # the stream is structure: the model banner, tool markers, truncation notices
    # and "trimmed N messages" bookkeeping. Mixing those into the claim produces a
    # summary that reads as confident nonsense, which is precisely the failure
    # this module exists to prevent.
    banner_line = re.compile(r"\b(heavy|chat|offline)\s*:\s*[\w.-]+/[\w.-]+/[\w.-]+")
    noise_line = re.compile(
        r"^\(?\s*(trimmed|reached max steps|step \d+\s*:|no previous conversation"
        r"|could not load|Progress:|\d+ step\(s\) ran)",
        re.I,
    )
    claim_parts: list[str] = []
    for raw in transcript.splitlines():
        line = strip_ansi(raw).strip()
        if not line:
            continue
        if _TOOL_CALL.search(raw) or _TOOL_ARROW.match(raw):
            continue
        if banner_line.search(line):
            continue
        if noise_line.match(line):
            continue
        # A bare model banner with no arrow is still structure, not prose.
        if re.fullmatch(r"[\w.-]+/[\w.-]+/[\w.-]+", line):
            continue
        claim_parts.append(line)
    claim = " ".join(claim_parts).strip()

    # Checked after the claim is built, because the test is "ended without a
    # summary": tools happened, but no truncation notice and nothing in prose.
    # Observed on the real CLI -- a run stopped part-way emits its tool lines and
    # then simply stops, with no notice to read. Without this the report showed a
    # clean "NOT DONE: nothing outstanding", the most dangerous shape available:
    # work was started and abandoned while the summary claimed all was well.
    if tools and not claim and not truncation:
        not_done.append(
            "Vibecoder started work but stopped without finishing or saying why "
            f"- its last tool call was {tools[-1]}. Nothing after that point can "
            "be assumed done."
        )

    return model, claim, tools, not_done, warnings


def _check_guard(root: Path, changed: list[str]) -> list[str]:
    """Flag a change that landed somewhere it should not have."""
    out: list[str] = []
    for relative in changed:
        name = Path(relative).name
        posix = relative.replace("\\", "/")
        if name in _GUARDED_NAMES:
            out.append(
                f"refused to touch {relative}: a secret or credential file is not "
                "something a coding task should change"
            )
        elif any(part in posix for part in _SENSITIVE_DIR_PARTS):
            out.append(
                f"Vibecoder wrote inside {relative}, which is a credential or "
                "shell-configuration location"
            )
    return out


def delegate(
    task: str,
    cwd: str,
    *,
    launcher: str = "",
    provider: str = "nvidia",
    max_steps: int = _DEFAULT_MAX_STEPS,
    timeout: float = 900.0,
    read_only: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> VibecoderResult:
    """Run one Vibecoder task and return what can be verified about it.

    ``on_progress`` receives each transcript line as it arrives. A real edit took
    23 s on the reference machine, and a tool that says nothing for 23 s reads as
    hung, so progress is streamed rather than buffered until exit.

    ``read_only`` is a *promise to the user*, not a sandbox: it appends an
    instruction to the task text because Vibecoder has no such flag. It is
    labelled as an instruction in the returned claim so nobody mistakes it for an
    enforced guarantee.
    """
    started = time.perf_counter()

    # A setting typo must not turn `timeout = 0` into an instant kill-loop:
    # the floor keeps a one-second run possible and the math below sane.
    timeout = max(1.0, float(timeout))

    root = Path(cwd).expanduser()
    if not root.is_dir():
        return VibecoderResult(
            acted=False,
            exit_code=-1,
            duration_ms=0.0,
            not_done=[f"{root} is not a directory, so there is nothing to work in"],
            warnings=["nothing was run"],
        )

    refusal = _refusal_for(root)
    if refusal:
        return VibecoderResult(
            acted=False,
            exit_code=-1,
            duration_ms=0.0,
            directory=str(root),
            not_done=[f"refused to run: {refusal}"],
            warnings=[
                "Vibecoder was not started, so nothing was changed. Alan's own "
                "model did not write the code either."
            ],
        )

    exe = find_launcher(launcher)
    if not exe:
        return VibecoderResult(
            acted=False,
            exit_code=-1,
            duration_ms=0.0,
            not_done=[
                "Vibecoder is not installed or not on PATH, so the coding task "
                "was not attempted"
            ],
            warnings=["Alan's own model did not write any code in its place"],
        )

    prompt = task.strip()
    if read_only:
        prompt += (
            "\n\nThis is a READ-ONLY inspection task. Do not create, edit or "
            "delete any file. Report what you found."
        )

    # --prompt, never positional: see the module docstring.
    argv = [
        exe,
        "--prompt",
        prompt,
        "--cwd",
        str(root.resolve()),
        "--max-steps",
        str(max(1, _floor_int(max_steps, _DEFAULT_MAX_STEPS))),
    ]
    # `--provider` reaches only vibecoder's CHAT route. Its hybrid router
    # classifies a coding task as heavy and then serves it from
    # routing.heavyProvider in ~/.vibecoder/config.json, regardless of this
    # flag -- observed directly: with provider="groq" the banner still read
    # `→ heavy: nvidia/nvidia/nemotron-3-ultra-550b-a55b`. So the flag is
    # passed because it is correct to pass it, but the file above is what
    # actually decides which model writes code. Reported as "Vibecoder via X"
    # from the banner, i.e. from what ran rather than from what was asked for.
    if provider:
        argv += ["--provider", provider]

    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["CI"] = "1"

    before = _snapshot(root, _candidate_files(root))

    # The tail of a run is worth more than its head: the claim, the truncation
    # notice and the last tool call are all written at the end. Keep the newest
    # lines, drop the oldest ones past the cap, and say so in the report.
    transcript_parts: list[str] = []
    buffered_chars = 0
    transcript_capped = False
    timed_out = False
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        return VibecoderResult(
            acted=False,
            exit_code=-1,
            duration_ms=0.0,
            not_done=[f"could not start Vibecoder: {exc}"],
        )

    deadline = started + timeout
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            transcript_parts.append(line)
            buffered_chars += len(line)
            if buffered_chars > MAX_TRANSCRIPT_CHARS * _TRANSCRIPT_SLACK:
                dropped = transcript_parts.pop(0)
                buffered_chars -= len(dropped)
                transcript_capped = True
            if on_progress is not None:
                clean = strip_ansi(line).strip()
                if clean:
                    on_progress(clean)
            if time.perf_counter() > deadline:
                timed_out = True
                proc.kill()
                break
        proc.wait(timeout=max(1.0, deadline - time.perf_counter()))
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
    except OSError:
        proc.kill()
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass

    elapsed = time.perf_counter() - started
    transcript = strip_ansi("".join(transcript_parts))
    exit_code = proc.returncode if proc.returncode is not None else -1
    model, claim, tools, not_done, warnings = _interpret(
        transcript, exit_code, elapsed, ""
    )

    if timed_out:
        not_done.insert(
            0,
            f"Vibecoder was still working after {timeout:.0f}s and was stopped. "
            "Any edit it had not yet written is missing.",
        )

    if transcript_capped:
        # The verdict is written at the end of the stream, so a bounded run's
        # report is still trustworthy; only the oldest lines were discarded.
        warnings.append(
            f"Vibecoder printed more than {MAX_TRANSCRIPT_CHARS // 1000}k "
            "characters; Alan kept the tail of its output"
        )

    # Verify against the filesystem instead of believing the transcript.
    verified: list[FileChange] = []
    for relative in _candidate_files(root):
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            continue
        old = before.get(relative)
        if old is None:
            verified.append(FileChange(relative, True, "new file"))
        elif (stat.st_mtime, stat.st_size) != old:
            verified.append(
                FileChange(
                    relative,
                    True,
                    f"mtime+size changed ({old[1]}B -> {stat.st_size}B)",
                )
            )

    guard = _check_guard(root, [item.path for item in verified])
    if guard:
        warnings.extend(guard)

    unverified: list[str] = []
    if tools and not verified and not claim:
        unverified.append(
            "Vibecoder used tools but left no readable summary, so there is "
            "nothing to check its work against"
        )

    return VibecoderResult(
        acted=bool(tools) or bool(verified),
        exit_code=exit_code,
        duration_ms=elapsed * 1000,
        directory=str(root),
        model=model,
        claim=claim,
        tools=tools,
        verified=verified,
        unverified=unverified,
        not_done=not_done,
        warnings=warnings,
        transcript=transcript,
    )


class VibecoderTools:
    """The ``vibecoder.*`` namespace Alan uses to delegate and to report."""

    name = "vibecoder"

    def __init__(self, settings: Any, *, launcher: str = "") -> None:
        self.settings = settings
        # Read once, with a default for a caller that passes a bare object. The
        # settings block is authoritative when present; the argument exists only
        # so the tool can be constructed in a test without a full Settings.
        config = getattr(settings, "vibecoder", None)
        self.launcher = launcher or str(getattr(config, "launcher", "") or "")
        self.provider = str(getattr(config, "provider", "nvidia") or "nvidia")
        self.project_dir = str(getattr(config, "project_dir", "") or "")
        self.max_steps = _floor_int(
            getattr(config, "max_steps", ""), _DEFAULT_MAX_STEPS
        )
        self.timeout = _floor_int(getattr(config, "timeout", ""), 900)

    def resolve_dir(self, cwd: str = "") -> str:
        """Work out which directory a task runs in, absolutely.

        Order of preference: the directory the request named, then
        ``[vibecoder].project_dir``, then the directory Alan was started from.

        The last one is a guess, and it is the right guess: someone who runs
        ``alan ask "fix the failing test"`` inside a project means that project,
        and refusing would make the common case need configuration. It is still a
        guess, so the result goes through :func:`_refusal_for` before anything
        runs -- a launch from ``C:\\`` resolves to a drive root and is refused
        rather than handed to a shell-enabled agent.
        """
        chosen = (cwd or self.project_dir or "").strip()
        if not chosen:
            return ""
        path = Path(chosen).expanduser()
        if not path.is_absolute():
            base = Path(self.project_dir).expanduser() if self.project_dir else Path.cwd()
            path = base / path
        return str(path.resolve())

    def working_dir(self, cwd: str = "") -> str:
        """The directory a task would run in, whether or not it may.

        Falls back to the process directory when nothing is configured, so the
        refusal can name a real directory instead of saying nothing was known.
        """
        resolved = self.resolve_dir(cwd)
        if resolved:
            return resolved
        try:
            return str(Path.cwd().resolve())
        except OSError:
            return ""

    def tools(self) -> Sequence[ToolSpec]:
        return (self._inspect(), self._delegate(), self._report())

    def available(self) -> tuple[bool, str]:
        exe = find_launcher(self.launcher)
        if not exe:
            return False, "vibecoder is not installed or not on PATH"
        return True, exe

    # -- specs ---------------------------------------------------------
    def _schema(self) -> dict[str, Any]:
        """The input schema shared by the inspect and delegate tools.

        Built by one function so the two cannot drift. Drift matters here: if
        ``vibecoder.inspect`` ever declared a different shape, an inspection plan
        would fail on an argument the tool did not accept, and the failure would
        look like Vibecoder not acting.
        """
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "the task, in plain language",
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "project directory to work in. Defaults to "
                        "[vibecoder].project_dir, then to the directory Alan is "
                        "running in."
                    ),
                },
                "read_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "inspect and report without changing files",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "ceiling on Vibecoder's own step budget",
                },
                "timeout": {"type": "number", "description": "wall-clock limit"},
            },
            "required": ["task"],
        }

    def _inspect(self) -> ToolSpec:
        """Read-only delegation: shown a file, changed nothing.

        A separate tool rather than a flag on ``vibecoder.delegate``, because the
        two ask for different permission sets and only the write one should cost
        a confirmation prompt. Reading a file should be cheap; if every
        inspection also asked "are you sure?", the user would learn to answer
        "yes" without reading, which is precisely when the question matters.
        """
        return ToolSpec(
            name="vibecoder.inspect",
            description=(
                "Have Vibecoder read a file or project and explain what it "
                "actually contains. Vibecoder's own model may invent a description "
                "of a file it never opened, so a run that made no tool calls is "
                "reported as having done nothing."
            ),
            input_schema=self._schema(),
            output_schema={"type": "object"},
            risk=RiskLevel.LOW_RISK,
            permissions=("app.launch", "filesystem.read"),
            timeout=960.0,
            # Forced, not defaulted: a caller that omits ``read_only`` on a tool
            # named "inspect" must not receive a write. Forcing it here is what
            # makes the name of the tool a guarantee rather than a hint.
            executor=self._run_inspect,
            tags=("coding", "vibecoder", "read"),
        )

    def _delegate(self) -> ToolSpec:
        return ToolSpec(
            name="vibecoder.delegate",
            description=(
                "Delegate a coding task to Vibecoder, which writes the code. "
                "Returns what Vibecoder did, verified against the filesystem."
            ),
            input_schema=self._schema(),
            output_schema={"type": "object"},
            # ``code.delegate_write`` is the capability that costs a confirmation
            # prompt. Launching an app the user named, and letting another agent
            # rewrite a project, are the same risk tier but not the same act --
            # only one of them is a one-shot way to change a file the user meant
            # to leave alone. See :func:`alan.permissions.gate.default_gate`.
            risk=RiskLevel.MEDIUM_RISK,
            permissions=("app.launch", "filesystem.read", "code.delegate_write"),
            timeout=960.0,
            executor=self._run,
            tags=("coding", "vibecoder"),
        )

    def _run_inspect(
        self,
        task: str,
        cwd: str = "",
        read_only: bool = True,  # accepted so a caller's flag cannot error
        max_steps: int = 0,
        timeout: float = 0.0,
    ) -> dict[str, Any]:
        """``vibecoder.inspect``'s executor: read-only, always."""
        return self._run(
            task,
            cwd=cwd,
            read_only=True,
            max_steps=max_steps,
            timeout=timeout,
        )

    def _run(
        self,
        task: str,
        cwd: str = "",
        read_only: bool = False,
        max_steps: int = 0,
        timeout: float = 0.0,
    ) -> dict[str, Any]:
        # Defaults come from settings, not from the schema, so the schema's
        # advertised numbers and the numbers actually used cannot drift apart.
        if not max_steps:
            max_steps = self.max_steps
        if not timeout:
            timeout = self.timeout
        resolved = self.resolve_dir(cwd)
        if not resolved:
            resolved = self.working_dir(cwd)
            if not resolved:
                return {
                    "acted": False,
                    "exit_code": -1,
                    "duration_ms": 0,
                    "tools_used": [],
                    "verified": [],
                    "not_done": [
                        "No working directory was given, [vibecoder].project_dir "
                        "is not set, and the directory Alan is running in could "
                        "not be determined -- so there was nowhere safe to work."
                    ],
                    "warnings": ["nothing was run"],
                    "report": "NOT DONE: nothing ran, because no directory was "
                              "known.",
                }
        result = delegate(
            task,
            resolved,
            launcher=self.launcher,
            provider=self.provider,
            max_steps=max_steps,
            timeout=timeout,
            read_only=read_only,
        )
        return {
            "acted": result.acted,
            "exit_code": result.exit_code,
            "duration_ms": round(result.duration_ms),
            "directory": resolved,
            "tools_used": result.tools,
            "verified": [
                {"path": c.path, "detail": c.detail}
                for c in result.verified
            ],
            "not_done": result.not_done,
            "warnings": result.warnings,
            "report": result.report(),
        }

    def _report(self) -> ToolSpec:
        return ToolSpec(
            name="vibecoder.report",
            description=(
                "The honesty contract for a delegated task: what to tell the user "
                "when a claim was verified on disk, when it was not, and when "
                "nothing was done at all."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "description": (
                            "the run's own report, from vibecoder.delegate. "
                            "Optional: omitting it returns the contract to follow."
                        ),
                    },
                },
                # Deliberately empty. The plan's report step supplies no
                # arguments, so requiring one made this tool fail on every
                # delegated task -- in 0.8 ms, before doing anything. A step that
                # always fails is worse than no step: it fills the trace with a
                # denial and reads as though the report could not be produced.
                "required": [],
            },
            output_schema={"type": "string"},
            risk=RiskLevel.SAFE,
            permissions=("filesystem.read",),
            timeout=30.0,
            executor=self._explain,
            tags=("coding", "vibecoder", "report"),
        )

    #: The contract, as it appears to the model. Worded as instructions because
    #: that is who reads it: the reply has to come from a 1.5b model that is
    #: inclined to describe what it would like to have done.
    _CONTRACT = (
        "Report a delegated coding task in exactly two sections.\n\n"
        "DONE\n"
        "  One line per item. If the filesystem confirmed a file changed, say "
        "'verified on disk' and name the file. If Vibecoder only said it did "
        "something, say 'claimed by Vibecoder, NOT verified'.\n\n"
        "NOT DONE\n"
        "  Anything left outstanding: steps it hit, tools it called, where it "
        "stopped, any error, and anything truncated by --max-steps. If Vibecoder "
        "made no tool calls, say plainly that it did not act.\n\n"
        "Never report a change you did not see confirmed. Never turn a claim into "
        "a fact by omitting the qualifier."
    )

    def _explain(self, report: str = "") -> str:
        """Echo a report, or hand back the contract when given none."""
        text = (report or "").strip()
        if not text:
            return self._CONTRACT
        return f"{text}\n\n{self._CONTRACT}"


__all__ = [
    "FileChange",
    "MAX_TRANSCRIPT_CHARS",
    "VibecoderResult",
    "VibecoderTools",
    "delegate",
    "find_launcher",
    "strip_ansi",
]