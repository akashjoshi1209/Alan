"""Tests for opening applications and websites.

Two things are being defended here, and they pull in opposite directions:
"open X" must resolve *anything* a person might reasonably name, and it must
never become a way to run a command. These tests pin both, because the failure
modes are quiet -- a wrong application opens a window the user then has to
explain, and a too-liberal launcher is an arbitrary code execution hole wearing
a convenience feature.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alan.core.planner import HeuristicPlanner
from alan.interfaces.types import RiskLevel
from alan.tools import opening


class TargetRecognitionTest(unittest.TestCase):
    """Deciding what a bare string *is*."""

    def test_a_dotted_name_with_a_plausible_suffix_is_a_website(self) -> None:
        for target in ("youtube.com", "www.example.co.uk", "en.wikipedia.org",
                       "http://127.0.0.1:11435", "localhost:11434"):
            self.assertTrue(opening.looks_like_url(target), target)

    def test_a_bare_word_is_not_a_website(self) -> None:
        # This is the property that lets "open notepad" and "open youtube.com"
        # share a verb. Without it every application name reads as a hostname.
        for target in ("notepad", "youtube", "calculator", "my app"):
            self.assertFalse(opening.looks_like_url(target), target)

    def test_a_dotted_filename_is_not_a_website(self) -> None:
        # notes.txt and setup.exe are spelled exactly like example.com. Reading
        # them as hostnames would send "open setup.exe" to a browser.
        for target in ("notes.txt", "setup.exe", "report.pdf", "archive.zip",
                       "photo.png", "main.py", "library.dll"):
            self.assertFalse(opening.looks_like_url(target), target)

    def test_com_is_still_a_tld(self) -> None:
        # The ambiguous-suffix list excludes .com on purpose: it is a genuine
        # TLD, and "open youtube.com" is the single most ordinary request here.
        self.assertTrue(opening.looks_like_url("youtube.com"))

    def test_normalisation_adds_a_scheme(self) -> None:
        self.assertEqual(opening.normalize_url("youtube.com"), "https://youtube.com")
        self.assertEqual(opening.normalize_url("WWW.YouTube.com."),
                         "https://WWW.YouTube.com")
        self.assertEqual(opening.normalize_url("HTTP://x.com"), "HTTP://x.com")

    def test_a_bare_port_becomes_http_not_https(self) -> None:
        # Local development servers are almost never behind a certificate, and
        # defaulting to https turns the commonest such request into a failure.
        self.assertEqual(opening.normalize_url("localhost:3000"), "http://localhost:3000")

    def test_normalisation_leaves_a_program_name_alone(self) -> None:
        self.assertEqual(opening.normalize_url("notepad"), "notepad")


class ResolutionTest(unittest.TestCase):
    """Turning a target into something the OS can start. Never launches."""

    def test_a_full_path_is_taken_at_face_value(self) -> None:
        found = opening.resolve(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "notepad.exe"))
        self.assertEqual(found["kind"], "path")
        self.assertTrue(found["path"].lower().endswith("notepad.exe"))

    def test_a_url_resolves_without_touching_the_filesystem(self) -> None:
        found = opening.resolve("youtube.com")
        self.assertEqual(found["kind"], "url")
        self.assertEqual(found["target"], "https://youtube.com")
        self.assertIsNone(found["path"])

    def test_a_real_file_wins_over_a_url_reading(self) -> None:
        # Someone naming a file that exists meant the file. Checked by standing
        # where the file is, so the real existence test decides rather than a
        # mock standing in for it.
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "example.com.exe").write_text("MZ", encoding="utf-8")
            (Path(tmp) / "example.com").write_text("not a website", encoding="utf-8")
            os.chdir(tmp)
            try:
                # Spelled exactly like a hostname, but it is a file, and it is
                # an executable, so it is launched rather than browsed to.
                found = opening.resolve("example.com.exe")
                self.assertEqual(found["kind"], "path")
                # A real file named like a website is still a file. It is then
                # refused by the extension allow-list -- and the point of the
                # test is that it is *refused*, not opened in a browser.
                with self.assertRaises(ValueError) as caught:
                    opening.resolve("example.com")
            finally:
                os.chdir(original)
        self.assertIn("will not execute", str(caught.exception))

    def test_a_bare_site_name_is_reported_as_an_assumption(self) -> None:
        # "open youtube" has no installed application and no dot. Rather than
        # failing a request a person would consider ordinary, the name is read
        # as a site -- but the result says it guessed, so the caller can admit it.
        with mock.patch.object(opening, "_BUILTINS", {}), \
             mock.patch.object(opening, "_app_paths", return_value=None), \
             mock.patch.object(opening, "_start_menu", return_value=None), \
             mock.patch.object(opening, "_which", return_value=None):
            found = opening.resolve("youtube")
        self.assertEqual(found["target"], "https://youtube.com")
        self.assertTrue(found["assumed"])
        self.assertIn("youtube", found["note"])
        self.assertIn("explicit", found["note"],
                      "the user should be told how to remove the guesswork")

    def test_an_installed_program_beats_the_website_guess(self) -> None:
        # The guess is a last resort. Notepad is on this machine; a request to
        # open it must not be answered with a website.
        found = opening.resolve("notepad")
        self.assertNotEqual(found["kind"], "url")
        self.assertNotIn("assumed", found)

    def test_multi_word_names_are_never_guessed_into_urls(self) -> None:
        # Two words cannot be a hostname, so there is nothing to guess, and the
        # error is the useful one.
        with mock.patch.object(opening, "_BUILTINS", {}), \
             mock.patch.object(opening, "_app_paths", return_value=None), \
             mock.patch.object(opening, "_start_menu", return_value=None), \
             mock.patch.object(opening, "_which", return_value=None):
            with self.assertRaises(FileNotFoundError) as caught:
                opening.resolve("some program")
        self.assertIn("example.com", str(caught.exception),
                      "the error should say how to be explicit")


class RefusalTest(unittest.TestCase):
    """The launcher must not become a command line."""

    def test_shell_metacharacters_are_refused_not_escaped(self) -> None:
        # Escaping would imply we meant to honour them. We do not.
        for target in ("calc & format c:", "calc|format c", "notepad; del x",
                       "a>b", "a`b`", "echo %PATH%", "x^y"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError) as caught:
                    opening.resolve(target)
                self.assertIn("interpret", str(caught.exception))

    def test_arguments_cannot_be_smuggled_in(self) -> None:
        # "open notepad" is a launcher, not a way to run a program with a chosen
        # command line. Anything that is not a bare name or a real path is
        # refused, so "notepad foo.txt" never becomes notepad-with-an-argument.
        for target in ("notepad foo.txt", "notepad C:somewhere\\else.txt",
                       "notepad /s"):
            with self.subTest(target=target):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    opening.resolve(target)

    def test_non_executable_files_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("script.bat", "macro.vbs", "data.csv", "hosts",
                         "notes.md", "payload.js"):
                path = Path(tmp) / name
                path.write_text("x", encoding="utf-8")
                with self.subTest(name=name):
                    with self.assertRaises(ValueError) as caught:
                        opening.resolve(str(path))
                    self.assertIn("will not execute", str(caught.exception))

    def test_the_allow_list_is_named_in_the_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thing.md"
            path.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                opening.resolve(str(path))
        for suffix in opening.ALLOWED_SUFFIXES:
            self.assertIn(suffix, str(caught.exception))

    def test_an_empty_target_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            opening.resolve("   ")

    def test_an_over_long_target_is_refused(self) -> None:
        # A pasted command line is not an application name.
        with self.assertRaises(ValueError) as caught:
            opening.resolve("a" * (opening.MAX_TARGET + 1))
        self.assertIn("command line", str(caught.exception))

    def test_control_characters_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            opening.resolve("notepad\x00.exe")

    def test_nothing_executable_is_ever_shelled_out(self) -> None:
        """The launcher builds no command string, so there is nothing to inject.

        Checked against the parsed syntax tree rather than the text of the
        module, because a grep would also match the module's own explanation of
        why it never uses ``shell=True`` -- a comment can assert a safety
        property as fluently as it can violate one.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(opening))
        forbidden = {"system", "popen", "execv", "execve", "execl", "execvp",
                     "spawnl", "spawnv", "Popen", "run", "check_output"}

        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    called.add(func.attr)
                elif isinstance(func, ast.Name):
                    called.add(func.id)
                for keyword in node.keywords:
                    self.assertNotEqual(
                        keyword.arg, "shell",
                        "a shell= keyword would reintroduce the interpreter this "
                        "module exists to avoid",
                    )
        self.assertEqual(called & forbidden, set(),
                         f"unexpected process-spawning call(s): {called & forbidden}")

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("subprocess", imported)


class SchemeRestrictionTest(unittest.TestCase):
    """Only http and https may reach Windows or the network.

    Any explicit scheme used to count as a web address, which quietly turned
    "open a website" into "do whatever protocol this string names".
    """

    def test_file_urls_are_refused(self) -> None:
        """``file://`` skipped the allow-list an explicit path must pass.

        An explicit ``C:\\...`` path is restricted to a few executable suffixes.
        ``file:///C:/Windows/System32/cmd.exe`` reached the same launcher with no
        restriction at all, so the one spelling of a local path that bypassed
        every check was the one that looked like a URL.
        """
        for target in ("file://c:/windows", "file:///C:/Windows/System32/cmd.exe",
                       "FILE:///C:/temp/x.exe"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError) as caught:
                    opening.resolve(target)
                self.assertIn("refusing", str(caught.exception))

    def test_network_schemes_other_than_http_are_refused(self) -> None:
        """``smb://`` in particular makes Windows connect to a remote host."""
        for target in ("smb://evil-host/share/payload.exe", "ftp://x.com"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    opening.resolve(target)

    def test_web_schemes_are_unaffected(self) -> None:
        for target in ("https://youtube.com", "http://localhost:3000",
                       "HTTP://YouTube.com"):
            with self.subTest(target=target):
                self.assertEqual(opening.resolve(target)["kind"], "url")

    def test_looks_like_url_agrees_with_resolve(self) -> None:
        """The predicate and the resolver must not disagree about a scheme.

        They are separate entry points -- ``looks_like_url`` also decides what
        ``web.open`` will fetch -- so a scheme one accepts and the other refuses
        is a hole in whichever direction the mismatch points.
        """
        for target in ("file://c:/x", "smb://a/b", "ftp://x.com",
                       "https://x.com", "http://x.com"):
            with self.subTest(target=target):
                try:
                    kind_is_url = opening.resolve(target)["kind"] == "url"
                except ValueError:
                    kind_is_url = False  # refused outright is "not a url"
                self.assertEqual(opening.looks_like_url(target), kind_is_url)


class InventedDomainTest(unittest.TestCase):
    """The bare-word site guess must stay a guess about a *word*.

    It tested only for "no spaces", which a dotted filename also satisfies, so
    ``notepad.txt`` resolved to ``https://notepad.txt.com`` -- a domain that
    cannot exist, reached only because nothing matched the name.
    """

    def _resolved(self, target: str):
        try:
            return opening.resolve(target)
        except (ValueError, FileNotFoundError):
            return None

    def test_a_dotted_filename_is_never_turned_into_a_website(self) -> None:
        for target in ("notepad.txt", "setup.exe", "foo.py", "report.docx",
                       "notes.md", "a.tar.gz"):
            with self.subTest(target=target):
                found = self._resolved(target)
                self.assertTrue(found is None or found["kind"] != "url",
                                f"{target!r} became {found}")

    def test_a_real_website_is_still_a_website(self) -> None:
        for target, expected in (("youtube.com", "https://youtube.com"),
                                 ("example.co.uk", "https://example.co.uk"),
                                 ("localhost:11434", "http://localhost:11434")):
            with self.subTest(target=target):
                self.assertEqual(opening.resolve(target)["target"], expected)

    def test_a_single_word_still_guesses_a_site(self) -> None:
        """The feature itself must survive the tightening."""
        for target in ("youtube", "gmail"):
            with self.subTest(target=target):
                found = opening.resolve(target)
                self.assertEqual(found["target"], f"https://{target}.com")
                self.assertTrue(found.get("assumed"),
                                "a guess must always be reported as a guess")

    def test_nothing_launches_for_a_refused_filename(self) -> None:
        with mock.patch.object(opening.os, "startfile") as startfile:
            with self.assertRaises(Exception):
                opening.launch("notepad.txt")
        self.assertEqual(startfile.call_count, 0)


class LaunchTest(unittest.TestCase):
    """What actually gets handed to the operating system."""

    def setUp(self) -> None:
        patcher = mock.patch.object(opening.os, "startfile")
        self.startfile = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_url_goes_to_the_registered_handler(self) -> None:
        result = opening.launch("youtube.com")
        self.assertEqual(result["launched"], True)
        self.startfile.assert_called_once_with("https://youtube.com")

    def test_an_application_is_started_from_its_resolved_path(self) -> None:
        result = opening.launch("notepad")
        self.startfile.assert_called_once()
        target = self.startfile.call_args.args[0]
        self.assertEqual(target, result["path"])
        self.assertTrue(target.lower().endswith(".exe"))

    def test_a_refusal_never_reaches_the_launcher(self) -> None:
        for target in ("calc & format c:", "README.md", "some program"):
            with self.subTest(target=target):
                self.startfile.reset_mock()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    opening.launch(target)
                self.startfile.assert_not_called()

    def test_no_arguments_are_ever_passed(self) -> None:
        # "open notepad" is a launcher. Running a program with a chosen command
        # line is shell.execute, which is gated separately.
        opening.launch("notepad")
        self.startfile.assert_called_once()
        self.assertEqual(len(self.startfile.call_args.args), 1)
        self.assertEqual(self.startfile.call_args.kwargs, {})


class RegisteredToolTest(unittest.TestCase):
    """How the launcher appears to the rest of Alan."""

    def _runtime(self):
        from alan.runtime import build_runtime

        return build_runtime()

    def test_the_tool_is_registered_and_reachable(self) -> None:
        spec = self._runtime().registry.spec("system.open")
        self.assertIsNotNone(spec, "system.open is not registered")
        self.assertEqual(spec.permissions, ("app.launch",))

    def test_it_is_risk_tiered_with_other_local_execution(self) -> None:
        # Launching a program the user named is the same class of act as
        # running a local command, so it sits at the same tier -- and unlike
        # browser.open it pulls nothing from the network into the prompt.
        spec = self._runtime().registry.spec("system.open")
        self.assertEqual(spec.risk, RiskLevel.MEDIUM_RISK)

    def test_it_survives_a_medium_risk_ceiling(self) -> None:
        # The whole point of a separate capability: "open youtube" works without
        # the operator having to raise the ceiling above medium_risk.
        #
        # Asserted against an *explicitly* medium_risk ceiling rather than the
        # shipped one. The shipped value is policy -- it is high_risk today so
        # that web.open is reachable -- while this invariant has to hold
        # whichever way that policy points. Pinning the shipped string here made
        # an unrelated settings change fail a test about system.open.
        from alan.config import Settings
        from alan.runtime import build_runtime

        settings = Settings()
        settings.permissions.ceiling = "medium_risk"
        runtime = build_runtime(settings)
        result = runtime.registry.invoke("system.open", {"target": "notepad"})
        self.assertTrue(result.ok, result.error)

    def test_the_shipped_ceiling_is_a_recognised_risk_level(self) -> None:
        # build_gate() silently substitutes medium_risk for any ceiling it does
        # not recognise, so a typo in settings.toml would quietly undo the
        # operator's setting instead of being reported. Fails safe, but wrong --
        # and invisible without this check.
        from alan.config import load_settings
        from alan.runtime import _RISK_NAMES

        ceiling = load_settings().permissions.ceiling
        self.assertIn(
            ceiling.upper(), _RISK_NAMES,
            f"unrecognised ceiling {ceiling!r} would silently fall back to "
            "medium_risk",
        )

    def test_the_gate_is_still_consulted(self) -> None:
        runtime = self._runtime()
        with mock.patch.object(opening.os, "startfile") as started:
            runtime.registry.invoke("system.open", {"target": "notepad"})
        events = [e.name for e in runtime.bus.history()]
        self.assertIn("permission.requested", events)
        self.assertIn("permission.granted", events)
        started.assert_called_once()

    def test_the_plan_action_maps_to_the_tool(self) -> None:
        # The orchestrator maps a capability name to a tool name explicitly; an
        # unmapped action is a visible bug rather than a silent no-op, so this
        # mapping is part of the contract rather than an internal detail.
        from alan.core.orchestrator import Orchestrator

        source = Orchestrator.__module__
        import alan.core.orchestrator as orchestrator_module

        self.assertIn(source, [orchestrator_module.__name__])
        import inspect

        self.assertIn('"open_target": "system.open"',
                      inspect.getsource(orchestrator_module))

    def test_an_intent_survives_the_round_trip(self) -> None:
        plan = HeuristicPlanner().plan("open youtube")
        self.assertEqual(plan.steps[0].action, "open_target")
        self.assertEqual(plan.steps[0].arguments["target"], "youtube")


class StreamingEscalationTest(unittest.TestCase):
    """``alan ask`` streams by default, and streaming used to have no tools.

    This is the reason "open youtube" failed while every part of it worked: the
    capability existed, the browser worked, and the entry point the user actually
    typed could not reach either.
    """

    def _runtime(self):
        from alan.runtime import build_runtime

        return build_runtime()

    def test_a_command_is_recognised_as_needing_a_tool(self) -> None:
        orchestrator = self._runtime().orchestrator
        for request in ("open youtube", "launch notepad", "go to wikipedia.org"):
            self.assertTrue(orchestrator.requires_capability(request), request)

    def test_a_conversation_does_not_pay_for_the_check(self) -> None:
        orchestrator = self._runtime().orchestrator
        for request in ("hello there", "what is 2 plus 2",
                        "what port is ollama on"):
            self.assertFalse(orchestrator.requires_capability(request), request)

    def test_the_check_never_raises_into_a_chat_turn(self) -> None:
        orchestrator = self._runtime().orchestrator
        with mock.patch.object(orchestrator.planner, "plan",
                               side_effect=RuntimeError("planner exploded")):
            self.assertFalse(orchestrator.requires_capability("anything"),
                             "a broken planner must not break the chat path")

    def test_streaming_a_command_delegates_to_the_full_pipeline(self) -> None:
        from alan.assistant import Alan

        runtime = self._runtime()
        assistant = Alan(runtime=runtime)
        self.addCleanup(assistant.close)
        with mock.patch.object(Alan, "ask", return_value="opened it") as asked:
            output = "".join(assistant.stream("open youtube"))
        asked.assert_called_once()
        self.assertEqual(output, "opened it")

    def test_streaming_a_conversation_still_goes_to_the_model(self) -> None:
        from alan.assistant import Alan

        runtime = self._runtime()
        assistant = Alan(runtime=runtime)
        self.addCleanup(assistant.close)
        # A backend is stubbed rather than relied upon: this test is about which
        # path a conversation takes, and asserting that would otherwise fail
        # whenever the local model happens to be down -- which is not a fact
        # about streaming.
        with mock.patch.object(runtime.router, "providers", [mock.Mock()]):
            with mock.patch.object(Alan, "ask", side_effect=AssertionError(
                    "a conversation must not be planned")) as asked:
                with mock.patch("alan.assistant._stream",
                                return_value=iter(["plain ", "chat"])):
                    output = "".join(assistant.stream("hello there"))
        asked.assert_not_called()
        self.assertEqual(output, "plain chat")

    def test_the_capability_map_has_one_source_of_truth(self) -> None:
        # The streaming path asks whether a tool is needed by consulting the
        # orchestrator's own map. If that map were copied into the assistant, the
        # two would drift and the failure would be silent.
        import inspect

        import alan.core.orchestrator as orchestrator_module

        source = inspect.getsource(orchestrator_module)
        self.assertIn('"open_target": "system.open"', source)
        from alan.assistant import Alan as _Alan  # noqa: F401 - import parity check

        assistant_source = inspect.getsource(sys.modules["alan.assistant"])
        self.assertNotIn("system.open", assistant_source,
                         "the assistant must not carry its own capability map")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
