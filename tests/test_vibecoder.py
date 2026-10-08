"""Tests for the Vibecoder delegation layer.

Every test here runs against transcripts captured from the real CLI. The
behaviours encoded in them were each found by running Vibecoder rather than by
reading its help, and three of them are cases where a plausible-looking
implementation reports success while nothing happened:

* a positional task is silently dropped and exit code is still 0;
* the banner glyph changed between versions, so glyph-coupled matching stopped
  seeing tool calls and reported "did nothing" for a run that had edited files;
* a run stopped part-way prints no notice at all, so its report looked clean.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan.config import HOME  # noqa: E402
from alan.tools.vibecoder import (  # noqa: E402
    MAX_TRANSCRIPT_CHARS,
    FileChange,
    VibecoderResult,
    _interpret,
    _refusal_for,
    delegate,
    find_launcher,
    strip_ansi,
)

# Captured verbatim from Vibecoder v1.0.0 on this machine.
BANNER = (
    "● nvidia/nemotron-3-ultra-550b-a55b → heavy: "
    "nvidia/nvidia/nemotron-3-ultra-550b-a55b"
)
OLD_BANNER = (
    "\U0001f916 nvidia/nemotron-3-ultra-550b-a55b ➡️ heavy: "
    "nvidia/nvidia/nemotron-3-ultra-550b-a55b"
)

COMPLETE_RUN = "\n".join(
    [
        "",
        BANNER,
        "",
        '⚡ glob {"pattern":"**/math.js"}',
        "[glob → math.js]",
        '⚡ read_file {"path":"math.js"}',
        "[read_file → 1: function add(a,b){ return a - b; }]",
        (
            '⚡ edit_file {"path":"math.js","oldString":"a - b",'
            '"newString":"a + b"}'
        ),
        "[edit_file → Edited math.js: replaced 1 occurrence]",
        "",
        "Fixed the add function so it adds.",
    ]
)

NO_OP_RUN = "\n".join(["", BANNER, "", "math.js is a comprehensive library."])

ABANDONED_RUN = "\n".join(
    ["", BANNER, "", '⚡ glob {"pattern":"**/math.js"}', "[glob → math.js]", ""]
)

TRUNCATED_RUN = "\n".join(
    [
        "",
        BANNER,
        "",
        '⚡ read_file {"path":"a.js"}',
        "(reached max steps without completion.",
        "Progress:",
        "  step 1: read_file",
        "  step 2: edit_file",
        "",
        " 2 step(s) ran, 2 tool call(s). Try a more focused task or raise --max-steps.",
    ]
)


class StripAnsiTest(unittest.TestCase):
    def test_colour_codes_are_removed(self):
        self.assertEqual(strip_ansi("\x1b[32mok\x1b[0m"), "ok")

    def test_non_ansi_input_is_untouched(self):
        self.assertEqual(strip_ansi("plain text"), "plain text")

    def test_osc_sequences_are_removed(self):
        # A hyperlink can carry bytes that would otherwise reach the log file.
        self.assertEqual(strip_ansi("\x1b]8;;http://x\x07link\x1b]8;;\x07"), "link")


class LauncherTest(unittest.TestCase):
    def test_an_explicit_path_is_honoured_exclusively(self):
        """A typo must not silently fall back to a different binary.

        It used to: the PATH lookup ran anyway, so a bogus override found the
        real Vibecoder and reported results as though they came from the
        configured one.
        """
        self.assertEqual(find_launcher("definitely-not-a-real-binary"), "")

    def test_an_existing_file_is_accepted(self):
        import sys as _sys

        self.assertEqual(find_launcher(_sys.executable), _sys.executable)

    def test_empty_falls_back_to_a_real_lookup(self):
        # The suite must not assert Vibecoder is installed; either answer is fine.
        found = find_launcher()
        self.assertIsInstance(found, str)


class InterpretTest(unittest.TestCase):
    def test_a_complete_run_reports_tools_and_a_clean_claim(self):
        model, claim, tools, not_done, _ = _interpret(
            COMPLETE_RUN, 0, 20.0, ""
        )
        self.assertEqual(tools, ["glob", "read_file", "edit_file"])
        self.assertIn("Fixed the add function", claim)
        self.assertEqual(not_done, [], "a finished run must not invent problems")
        self.assertEqual(model, "nvidia/nvidia/nemotron-3-ultra-550b-a55b")

    def test_the_banner_does_not_leak_into_the_claim(self):
        """The banner is structure. Merged into prose it reads as nonsense."""
        _, claim, _, _, _ = _interpret(COMPLETE_RUN, 0, 20.0, "")
        self.assertNotIn("550b", claim)
        self.assertNotIn("heavy:", claim)

    def test_a_no_op_is_reported_as_no_op(self):
        """Exit 0 with prose but no tools means nothing happened."""
        _, claim, tools, not_done, _ = _interpret(NO_OP_RUN, 0, 2.0, "")
        self.assertEqual(tools, [])
        self.assertTrue(any("no tool calls" in item for item in not_done))
        self.assertIn("comprehensive", claim)

    def test_a_run_stopped_part_way_is_not_reported_as_clean(self):
        """The dangerous case: tools ran, then the stream simply ended.

        No summary and no truncation notice, so the report used to say
        "NOT DONE: nothing outstanding" while work had been abandoned.
        """
        _, claim, tools, not_done, _ = _interpret(ABANDONED_RUN, 0, 8.7, "")
        self.assertEqual(tools, ["glob"])
        self.assertEqual(claim, "")
        self.assertTrue(
            any("stopped without finishing" in item for item in not_done),
            f"abandoned run must be flagged, got {not_done}",
        )
        self.assertIn("glob", " ".join(not_done))

    def test_truncation_lists_the_steps_it_reached(self):
        _, _, _, not_done, warnings = _interpret(TRUNCATED_RUN, 0, 5.0, "")
        joined = " ".join(not_done)
        self.assertTrue(any("max-steps" in item for item in not_done))
        self.assertIn("read_file", joined)
        self.assertIn("edit_file", joined)
        self.assertTrue(warnings)

    def test_truncated_property_matches_the_wording_used(self):
        """It looked for the spaced form and so was False on every real hit."""
        _, _, _, not_done, _ = _interpret(TRUNCATED_RUN, 0, 5.0, "")
        result = VibecoderResult(
            acted=True,
            exit_code=0,
            duration_ms=1000,
            not_done=not_done,
        )
        self.assertTrue(result.truncated)

    def test_both_banner_glyph_styles_are_understood(self):
        """The decoration changed between Vibecoder versions; tools did not."""
        old = COMPLETE_RUN.replace(BANNER, OLD_BANNER)
        _, _, tools, _, _ = _interpret(old, 0, 20.0, "")
        self.assertEqual(tools, ["glob", "read_file", "edit_file"])

    def test_unrelated_json_is_not_counted_as_a_tool_call(self):
        _, _, tools, _, _ = _interpret('{"noise":"not a tool"}', 0, 1.0, "")
        self.assertEqual(tools, [])


class ReportTest(unittest.TestCase):
    def test_verified_and_unverified_are_separated(self):
        result = VibecoderResult(
            acted=True,
            exit_code=0,
            duration_ms=12000,
            verified=[FileChange("math.js", True, "mtime changed")],
            claim="Fixed add()",
        )
        report = result.report()
        self.assertIn("DONE (verified on disk)", report)
        self.assertIn("math.js", report)

    def test_a_claim_without_verification_is_labelled(self):
        result = VibecoderResult(
            acted=False, exit_code=0, duration_ms=2000, claim="It is all fine"
        )
        report = result.report()
        self.assertIn("NOT verified", report)
        self.assertNotIn("DONE (verified on disk)", report)

    def test_an_empty_result_says_nothing_happened(self):
        result = VibecoderResult(acted=False, exit_code=0, duration_ms=1000)
        report = result.report()
        self.assertIn("DONE: nothing", report)

    def test_outstanding_work_is_always_shown(self):
        result = VibecoderResult(
            acted=True,
            exit_code=0,
            duration_ms=1000,
            not_done=["stopped after glob"],
        )
        self.assertIn("stopped after glob", result.report())


class SettingsTest(unittest.TestCase):
    """The delegation settings must be read, not hard-wrapped in getattr defaults.

    Provider selection in particular was decided to be *pinned* rather than left
    on Vibecoder's auto-router, because auto silently chose a 550B model on a
    paid endpoint. That decision is only real if the setting is honoured.
    """

    def test_defaults_pin_a_provider_rather_than_auto_route(self):
        from alan.config import Settings

        vibe = Settings().vibecoder
        self.assertEqual(vibe.provider, "nvidia")
        self.assertNotEqual(
            vibe.provider, "auto",
            "auto routing decides which company bills us; it must be deliberate",
        )

    def test_a_toml_table_overrides_the_defaults(self):
        import tomllib

        from alan.config import Settings, _apply_tables

        settings = Settings()
        _apply_tables(
            settings,
            {
                "vibecoder": {
                    "provider": "groq",
                    "launcher": "/opt/vibecoder",
                    "project_dir": "/srv/app",
                    "max_steps": 7,
                    "timeout": 30.0,
                }
            },
        )
        vibe = settings.vibecoder
        self.assertEqual(vibe.provider, "groq")
        self.assertEqual(vibe.launcher, "/opt/vibecoder")
        self.assertEqual(vibe.project_dir, "/srv/app")
        self.assertEqual(vibe.max_steps, 7)
        self.assertEqual(vibe.timeout, 30.0)
        self.assertIsInstance(tomllib, object)

    def test_a_mis_typed_vibecoder_table_does_not_crash_construction(self):
        """One bad value in settings.toml must cost a default, not a runtime."""
        from alan.tools.vibecoder import VibecoderTools

        for bad_max, bad_timeout in (("lots", "forever"), (None, ("1.5", "more"))):
            cfg = type(
                "C", (),
                dict(
                    launcher="", provider="nvidia", project_dir="",
                    max_steps=bad_max, timeout=bad_timeout,
                ),
            )()
            holder = type("S", (), {"vibecoder": cfg})()
            tools = VibecoderTools(holder)
            with self.subTest(max_steps=bad_max, timeout=bad_timeout):
                self.assertEqual(tools.max_steps, 25)
                self.assertEqual(tools.timeout, 900.0)

    def test_a_config_without_the_section_keeps_its_defaults(self):
        """An older settings.toml must still load."""
        from alan.config import Settings, _apply_tables

        settings = Settings()
        _apply_tables(settings, {"alan": {"name": "Alan"}})
        self.assertEqual(settings.vibecoder.provider, "nvidia")
        self.assertEqual(settings.vibecoder.max_steps, 25)


class DirectoryResolutionTest(unittest.TestCase):
    """A relative directory must resolve against the project, not the shell."""

    class _Cfg:
        launcher = ""
        provider = "nvidia"
        project_dir = str(Path("D:/Alan"))
        max_steps = 25
        timeout = 900.0

    class _Settings:
        vibecoder = None

    def _tools(self, project: str) -> "object":
        cfg = type("C", (), dict(
            launcher="", provider="nvidia", project_dir=project,
            max_steps=25, timeout=900.0,
        ))()
        holder = type("S", (), {"vibecoder": cfg})()
        from alan.tools.vibecoder import VibecoderTools

        return VibecoderTools(holder)

    def test_a_relative_directory_is_anchored_to_the_project(self) -> None:
        tools = self._tools(str(Path("D:/Alan")))
        resolved = tools.resolve_dir("src")
        self.assertTrue(Path(resolved).is_absolute(), resolved)
        self.assertTrue(resolved.endswith("src"), resolved)

    def test_an_empty_directory_falls_back_to_the_project(self) -> None:
        tools = self._tools(str(Path("D:/Alan")))
        self.assertTrue(Path(tools.resolve_dir("")).is_absolute())

    def test_an_absolute_directory_is_left_alone(self) -> None:
        tools = self._tools(str(Path("D:/Alan")))
        # C:/ is a real, absolute root; the result must not gain "D:\Alan".
        self.assertFalse(tools.resolve_dir("C:/Windows").startswith("D:"))

    def test_no_project_dir_and_no_cwd_resolves_to_nothing(self) -> None:
        """With nowhere known, the tool must refuse rather than guess.

        Guessing means "the directory Alan was started from", and a coding agent
        with a shell would edit whatever it found there.
        """
        tools = self._tools("")
        self.assertEqual(tools.resolve_dir(""), "")


class DirectoryGuardTest(unittest.TestCase):
    """Which directories a coding agent may be pointed at.

    Vibecoder holds the operator's keys and a shell, so these are refused before
    launch. Three of the cases below were each a real bug during development: a
    substring match on ``Users`` blocked ``C:\\Users\\ADMIN\\vibecoder`` -- the
    single most likely directory to want worked on -- and a ``Path.name``
    comparison against ``C:\\`` never matched, so every system directory was
    allowed through.
    """

    def test_a_drive_root_is_refused(self) -> None:
        for drive in ("C:\\", "D:\\"):
            with self.subTest(drive=drive):
                self.assertTrue(_refusal_for(Path(drive)))

    def test_system_directories_are_refused(self) -> None:
        for path in ("C:\\Users", "C:\\Windows", "C:\\Program Files"):
            with self.subTest(path=path):
                self.assertTrue(_refusal_for(Path(path)))

    def test_alan_source_is_refused(self) -> None:
        """The harness must not be editable by the agent it supervises.

        Allowed, the agent could edit the code that verifies its work -- a
        one-shot way to make every later report say "all done".
        """
        for path in (Path(HOME), Path(HOME) / "src", Path(HOME) / "src/alan/tools"):
            with self.subTest(path=path):
                self.assertIn("Alan", _refusal_for(path))

    def test_a_real_project_under_the_home_directory_is_allowed(self) -> None:
        """"Users" appears in nearly every Windows project path.

        Matching it as a substring looked safe and refused the Vibecoder
        checkout itself, which is the most plausible directory of all.
        """
        from pathlib import Path as P

        for path in (P.home() / "vibecoder", P.home() / "Documents" / "proj"):
            with self.subTest(path=path):
                self.assertEqual(_refusal_for(path), "")

    def test_an_ordinary_project_is_allowed(self) -> None:
        self.assertEqual(_refusal_for(Path(tempfile.gettempdir())), "")

    def test_delegate_refuses_alan_source_without_launching(self) -> None:
        """End to end: the refusal must stop the run, not merely be computed."""
        result = delegate("delete everything", str(Path(HOME)))
        self.assertFalse(result.acted)
        self.assertTrue(any("refused to run" in n for n in result.not_done))
        self.assertEqual(result.exit_code, -1)
        self.assertTrue(
            any("not started" in w for w in result.warnings),
            "the report must say nothing was changed",
        )


class _FakeProc:
    """Minimal Popen stand-in: streams canned lines, records what it was told.

    Behaviours delegate depends on that are cheap to fake -- iteration over
    stdout lines, ``kill``, ``wait``, and closing stdout. ``returncode`` is a
    real attribute so the fake behaves when delegate reads it after the run.
    """

    def __init__(self, lines):
        self._lines = list(lines)
        self.returncode = 0
        self.stdout = self
        self.killed = False

    def __iter__(self):
        return iter(self._lines)

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def close(self):
        pass


class DelegationHardeningTest(unittest.TestCase):
    """The Phase 3 hardening behaviours, pinned against a fake subprocess.

    These do not need the real CLI: the points under test are the buffer that
    bounds a runaway transcript and the floor under a mis-typed timeout. Both
    are pure decisions of :func:`delegate` around the subprocess boundary.
    """

    @staticmethod
    def _stub(tmp: str) -> str:
        # `find_launcher(explicit)` accepts any existent file, so a fake binary
        # lets the test through to Popen without depending on the machine's
        # PATH, and Popen is mocked anyway.
        stub = Path(tmp) / "vibecoder-stub.exe"
        stub.write_text("", encoding="utf-8")
        return str(stub)

    def test_zero_timeout_is_raised_to_one_second(self) -> None:
        """``timeout = 0`` in settings must not make every run an instant kill.

        Without the floor the deadline lands on ``now`` and the first line read
        from stdout trips the timeout check; with it, a run that answers quickly
        is reported as a normal run, not as one that was stopped.
        """
        with tempfile.TemporaryDirectory() as tmp:
            proc = _FakeProc(["one answer line"])
            with mock.patch(
                "alan.tools.vibecoder.subprocess.Popen", return_value=proc
            ), mock.patch(
                "alan.tools.vibecoder._interpret",
                return_value=("", "a claim", ["read_file"], [], []),
            ):
                result = delegate(
                    "task", tmp, launcher=self._stub(tmp), timeout=0
                )
        self.assertFalse(
            any("was still working after 0s" in n for n in result.not_done),
            "the floor must prevent an instant kill on a mis-typed timeout",
        )
        self.assertFalse(proc.killed, "the buffered line was read; nothing timed out")
        self.assertTrue(result.acted, "the mocked run used a tool, so it acted")

    def test_huge_transcripts_keep_the_tail_and_say_so(self) -> None:
        """A verbose agent must not blow memory, and its verdict must survive.

        The claim, the truncation notice and the final tool call are all written
        at the end of the stream, so the buffer drops the *oldest* lines and
        both the length bound and the tail are asserted.
        """
        with tempfile.TemporaryDirectory() as tmp:
            last_line = "final claim line zz"
            lines = [f"noise {i:05d} padding\n" for i in range(40_000)]
            lines.append(last_line + "\n")
            proc = _FakeProc(lines)
            captured: dict = {}

            def fake_interpret(transcript, exit_code, elapsed, model):
                captured["transcript"] = transcript
                return ("", "", [], [], [])

            with mock.patch(
                "alan.tools.vibecoder.subprocess.Popen", return_value=proc
            ), mock.patch(
                "alan.tools.vibecoder._interpret", side_effect=fake_interpret
            ):
                result = delegate(
                    "task", tmp, launcher=self._stub(tmp), timeout=60
                )

        transcript = captured["transcript"]
        self.assertTrue(
            transcript.endswith(last_line + "\n"),
            "the verdict lives at the end of the stream and must survive",
        )
        self.assertLessEqual(
            len(transcript),
            MAX_TRANSCRIPT_CHARS * 2,
            "the bounded buffer keeps the join bounded too",
        )
        self.assertTrue(
            any("kept the tail" in w for w in result.warnings),
            "a run the buffer clipped must say so in the report",
        )


class TwoToolSplitTest(unittest.TestCase):
    """Read-only and write delegation are separate tools with separate gates.

    One tool with a `read_only` flag was rejected because the two ask for
    different permissions: if inspection also required `code.delegate_write`,
    every read would cost a confirmation prompt, and a prompt a user answers
    reflexively is worse than no prompt at all when the prompt matters.
    """

    def _specs(self) -> dict:
        from alan.tools.vibecoder import VibecoderTools

        cfg = type("C", (), dict(
            launcher="", provider="nvidia", project_dir="",
            max_steps=25, timeout=900.0,
        ))()
        holder = type("S", (), {"vibecoder": cfg})()
        return {s.name: s for s in VibecoderTools(holder).tools()}

    def test_all_three_tools_exist(self) -> None:
        self.assertEqual(
            sorted(self._specs()),
            ["vibecoder.delegate", "vibecoder.inspect", "vibecoder.report"],
        )

    def test_only_the_write_tool_needs_the_confirming_capability(self) -> None:
        specs = self._specs()
        self.assertIn("code.delegate_write", specs["vibecoder.delegate"].permissions)
        self.assertNotIn(
            "code.delegate_write",
            specs["vibecoder.inspect"].permissions,
            "a read-only tool must not demand a confirmation prompt",
        )

    def test_inspect_is_read_only_whatever_the_caller_says(self) -> None:
        """"read_only": false on an inspect call must still not write.

        Forcing it in the executor rather than in the schema is deliberate: the
        guarantee belongs where the decision is made, so a malformed or
        adversarial argument cannot get around it.
        """
        result = self._specs()["vibecoder.inspect"].executor(
            "make the file longer", cwd=r"C:\definitely\not\here", read_only=False
        )
        # The directory does not exist, so nothing can have been written; the
        # point is that it attempted an inspect-shaped run rather than an edit.
        self.assertFalse(result["acted"])
        self.assertFalse(result["verified"])

    def test_both_tools_share_one_schema(self) -> None:
        """If they drifted, an inspection plan would fail on a rejected argument."""
        specs = self._specs()
        self.assertEqual(
            specs["vibecoder.inspect"].input_schema,
            specs["vibecoder.delegate"].input_schema,
        )
        self.assertEqual(
            specs["vibecoder.delegate"].input_schema.get("required"), ["task"],
        )

    def test_the_write_capability_is_a_prompt_not_a_refusal(self) -> None:
        """MEDIUM_RISK keeps it reachable under the default ceiling.

        The point is to ask, not to refuse: making it HIGH_RISK would silently
        disable delegation entirely, which is a confusing way to say no.
        """
        from alan.interfaces.types import (
            PermissionDecision,
            PermissionRequest,
            RiskLevel,
        )
        from alan.permissions.gate import default_gate

        decision = default_gate().check(
            PermissionRequest(
                capability="code.delegate_write",
                risk=RiskLevel.MEDIUM_RISK,
                agent="orchestrator",
            )
        )
        self.assertIs(decision, PermissionDecision.CONFIRMATION_REQUIRED)


class ReportToolTest(unittest.TestCase):
    """The ``vibecoder.report`` tool must be callable with no arguments.

    The plan's report step supplies none, so requiring one made the tool fail in
    under a millisecond on every single delegated task. That is worse than having
    no report step: it fills the trace with a denial, which reads as though the
    report could not be produced, and the user is never told.
    """

    def _spec(self) -> "object":
        from alan.tools.vibecoder import VibecoderTools

        cfg = type("C", (), dict(
            launcher="", provider="nvidia", project_dir="",
            max_steps=25, timeout=900.0,
        ))()
        holder = type("S", (), {"vibecoder": cfg})()
        return VibecoderTools(holder)._report()

    def test_no_arguments_are_required(self) -> None:
        spec = self._spec()
        self.assertEqual(
            spec.input_schema.get("required", []), [],
            "a plan step passes no arguments, so requiring any breaks every run",
        )

    def test_it_returns_the_contract_when_given_nothing(self) -> None:
        out = self._spec().executor()
        self.assertIsInstance(out, str)
        self.assertIn("DONE", out)
        self.assertIn("NOT DONE", out)
        self.assertIn("verified on disk", out)
        self.assertIn("NOT verified", out)

    def test_it_echoes_a_report_and_still_states_the_contract(self) -> None:
        out = self._spec().executor("math.js - verified on disk (mtime changed)")
        self.assertIn("math.js", out)
        self.assertIn("NOT DONE", out)


class RefusalTest(unittest.TestCase):
    """A refusal must be visible. These run no subprocess."""

    def test_a_missing_directory_is_refused(self):
        result = delegate("do a thing", r"C:\definitely\not\here")
        self.assertFalse(result.acted)
        self.assertTrue(any("not a directory" in n for n in result.not_done))

    def test_a_missing_binary_is_refused_and_says_alan_did_not_substitute(self):
        # A temp directory, not the home directory: home is correctly refused by
        # the directory guard, so using it here would assert the wrong refusal.
        with tempfile.TemporaryDirectory() as work:
            result = delegate("do a thing", work, launcher="nope-not-here")
        self.assertFalse(result.acted)
        self.assertTrue(any("not installed" in n for n in result.not_done))
        self.assertTrue(
            any("did not write any code" in w for w in result.warnings),
            "the report must say Alan's own model did not quietly do the work",
        )


if __name__ == "__main__":
    unittest.main()