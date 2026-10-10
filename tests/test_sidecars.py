"""The chat/ask sidecar ensure: honest probes, fire-and-forget starts.

What matters about :mod:`alan.sidecars` is the negative space -- nothing in it
can wait, raise, or narrate. The tests hold that line: starts are recorded
calls rather than real processes, a sidecar that is already up is left alone,
and a failed start is swallowed (its next ``alan doctor`` is where trouble is
reported, not the chat window it would have blocked).
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from alan import sidecars


class ListeningTest(unittest.TestCase):
    def test_an_accepting_port_is_listening(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            self.assertTrue(sidecars.listening(port))
        finally:
            sock.close()

    def test_a_closed_port_is_not_listening(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertFalse(sidecars.listening(port))

    def test_an_unroutable_port_is_refused_not_waited_for(self) -> None:
        # connect() to a local port that is not listening must come back on
        # its own: this is the call that runs on the way to a prompt.
        self.assertFalse(sidecars.listening(1))  # port 1: nothing serves it


class _HomeTest(unittest.TestCase):
    """Base: a scratch home, with the sidecar switches scrubbed clean."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="alan-sidecars-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("ALAN_NO_MEMORY", "ALAN_NO_OLLAMA", "OLLAMA_MODELS", "OLLAMA_HOST"):
            os.environ.pop(var, None)

    def _install_launchers(self) -> None:
        """A home holding both sidecars' real launcher files."""
        serve = self.home / "scripts" / "cognee-serve.ps1"
        serve.parent.mkdir(parents=True)
        serve.write_text("# fake\n", encoding="utf-8")
        runtime = self.home / "env" / "cognee" / "python" / "python.exe"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("", encoding="utf-8")
        exe = self.home / "env" / "ollama" / "ollama.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")


class EnsureDetachedTest(_HomeTest):
    def test_a_bare_home_starts_nothing(self) -> None:
        with mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        start.assert_not_called()

    def test_sidecars_already_up_are_left_alone(self) -> None:
        self._install_launchers()
        with mock.patch.object(sidecars, "listening", return_value=True), \
                mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        start.assert_not_called()

    def test_sidecars_down_start_detached(self) -> None:
        """Only Ollama is started now.

        cognee used to be launched from here too, and that was the whole
        problem: an 817 MB model load on the startup path of every chat. Memory
        is a file, so the only sidecar a chat needs is the model server.
        """
        self._install_launchers()
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        started = [" ".join(str(part) for part in call.args[0])
                   for call in start.call_args_list]
        self.assertEqual(len(started), 1, started)
        ollama = started[0]
        self.assertIn("ollama.exe", ollama)
        self.assertTrue(ollama.endswith(" serve"), ollama)
        self.assertFalse(any("cognee" in cmd for cmd in started),
                         "memory no longer needs a sidecar")

    def test_the_documented_no_switches_still_honoured(self) -> None:
        self._install_launchers()
        os.environ["ALAN_NO_OLLAMA"] = "1"
        try:
            with mock.patch.object(sidecars, "listening", return_value=False), \
                    mock.patch.object(sidecars, "_start_detached") as start:
                sidecars.ensure_detached(self.home)
            start.assert_not_called()
        finally:
            del os.environ["ALAN_NO_OLLAMA"]

    def test_a_detached_ollama_inherits_its_models_and_port(self) -> None:
        # The detached process does not see the launcher's environment, so
        # the ensure sets it here -- same port the bundled scripts use.
        self._install_launchers()
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached"):
            sidecars.ensure_detached(self.home)
        self.assertEqual(os.environ.get("OLLAMA_MODELS"), str(self.home / "models"))
        self.assertEqual(os.environ.get("OLLAMA_HOST"), "127.0.0.1:11435")

    def test_a_failed_start_is_swallowed(self) -> None:
        with mock.patch("subprocess.Popen", side_effect=OSError("no")):
            sidecars._start_detached(["does-not-matter"])


class StartBreadcrumbTest(_HomeTest):
    """One boot in flight at a time -- the race that kills a 4GB machine."""

    def _booting_sidecar(self) -> None:
        """A home whose launchers exist and whose sidecar is down mid-boot."""
        self._install_launchers()

    def test_a_fresh_breadcrumb_holds_the_next_start(self) -> None:
        self._booting_sidecar()
        sidecars._mark_starting(self.home, "ollama")
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        start.assert_not_called()

    def test_a_stale_breadcrumb_allows_a_retry(self) -> None:
        # A start that died without ever answering must not block retries
        # forever: past the window, the next chat may try again.
        self._booting_sidecar()
        sidecars._mark_starting(self.home, "ollama")
        flag = sidecars._flag_path(self.home, "ollama")
        stale = time.time() - sidecars._START_FLAG_SECONDS - 1
        os.utime(flag, (stale, stale))
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        start.assert_called_once()

    def test_a_start_leaves_its_breadcrumb(self) -> None:
        self._booting_sidecar()
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached"):
            sidecars.ensure_detached(self.home)
        self.assertTrue(sidecars._flag_path(self.home, "ollama").is_file())
        self.assertFalse(sidecars._flag_path(self.home, "cognee").exists(),
                         "memory must not leave a start breadcrumb: it starts nothing")

    def test_the_cognee_launcher_existing_starts_nothing(self) -> None:
        """A cognee install must not be resurrected by its own script being on
        disk. cognee only runs when a user asks for that backend by name."""
        self._install_launchers()
        with mock.patch.object(sidecars, "listening", return_value=False), \
                mock.patch.object(sidecars, "_start_detached") as start:
            sidecars.ensure_detached(self.home)
        started = " ".join(" ".join(str(p) for p in c.args[0])
                           for c in start.call_args_list)
        self.assertNotIn("cognee", started)

    def test_an_up_sidecar_leaves_no_breadcrumb(self) -> None:
        self._booting_sidecar()
        with mock.patch.object(sidecars, "listening", return_value=True), \
                mock.patch.object(sidecars, "_start_detached"):
            sidecars.ensure_detached(self.home)
        self.assertFalse(sidecars._flag_path(self.home, "cognee").exists())


class LauncherWiringTest(unittest.TestCase):
    """Both chat surfaces fire the ensure; the .cmd keeps voice on run.ps1."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_chat_and_ask_both_ensure(self) -> None:
        source = (self.ROOT / "src" / "alan" / "cli.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("ensure_detached()"), 2,
            "chat and ask are the two surfaces run.ps1 used to warm up",
        )

    def test_voice_still_routes_through_the_blocking_launcher(self) -> None:
        cmd = (self.ROOT / "Alan.cmd").read_text(encoding="utf-8")
        self.assertIn('"%~1"=="voice"', cmd)
        self.assertIn("run.ps1", cmd)
