"""Voice input: the glue between Alan and the whisper-flow sidecar.

No test here needs a microphone, a model or a running server. The sidecar is
either a stub HTTP server on loopback or a failure path, the recorder is a
fake argv, and every recording call goes through an injected runner. What is
asserted is the part Alan owns: the endpoint it derives, the command it
builds, the transcript it accepts, and the refusal it raises when something is
missing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alan import voice  # noqa: E402
from alan.config import Settings, VoiceSettings, load_settings  # noqa: E402
import whisper_recorder as recorder  # noqa: E402


class _ReadyHandler(BaseHTTPRequestHandler):
    """A stand-in sidecar: GET /ready returns whatever the test set."""

    payload: bytes = b"{}"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_args):  # keep the test output clean
        pass


class ReadyProbeTest(unittest.TestCase):
    """`ready()` is the check that keeps a broken sidecar from looking like
    silence or like garbled text."""

    def _server(self, payload: dict) -> HTTPServer:
        _ReadyHandler.payload = json.dumps(payload).encode("utf-8")
        server = HTTPServer(("127.0.0.1", 0), _ReadyHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_reports_ready_when_the_protocol_matches(self) -> None:
        server = self._server({
            "status": "ok", "protocol_version": voice.PROTOCOL_VERSION,
            "models": ["tiny.en.pt"],
        })
        settings = VoiceSettings(base_url=f"http://127.0.0.1:{server.server_port}")

        ok, note = voice.ready(settings)

        self.assertTrue(ok, note)
        self.assertIn("tiny.en.pt", note)

    def test_refuses_a_sidecar_speaking_another_protocol(self) -> None:
        server = self._server({"protocol_version": 99, "models": []})
        settings = VoiceSettings(base_url=f"http://127.0.0.1:{server.server_port}")

        ok, note = voice.ready(settings)

        self.assertFalse(ok)
        self.assertIn("protocol v99", note)

    def test_refuses_a_model_the_sidecar_does_not_have(self) -> None:
        server = self._server({"protocol_version": voice.PROTOCOL_VERSION,
                               "models": ["base.en.pt"]})
        settings = VoiceSettings(base_url=f"http://127.0.0.1:{server.server_port}")

        ok, note = voice.ready(settings)

        self.assertFalse(ok)
        self.assertIn("not loaded", note)

    def test_a_sidecar_that_is_not_running_says_where_to_start_it(self) -> None:
        # Port 1 is reserved and nothing listens on it.
        settings = VoiceSettings(base_url="http://127.0.0.1:1", timeout=0.2)

        ok, note = voice.ready(settings)

        self.assertFalse(ok)
        self.assertIn("not reachable", note)


class EndpointTest(unittest.TestCase):
    def test_ws_url_comes_from_the_http_address(self) -> None:
        self.assertEqual(voice.ws_url(VoiceSettings()), "ws://127.0.0.1:8181/ws")

    def test_https_becomes_wss(self) -> None:
        settings = VoiceSettings(base_url="https://example.test:9000")
        self.assertEqual(voice.ws_url(settings), "wss://example.test:9000/ws")

    def test_client_defaults_agree_with_the_settings_defaults(self) -> None:
        """The recorder and the settings file must not drift into two truths."""
        args = recorder.parse_args([])
        settings = VoiceSettings()
        self.assertEqual(args.model, settings.model)
        self.assertEqual(args.language, settings.language)  # both "" = auto-detect
        self.assertEqual(args.silence_ms, settings.silence_ms)
        self.assertEqual(args.threshold, settings.threshold)


class RecorderAvailabilityTest(unittest.TestCase):
    def test_missing_sidecar_environment_names_the_setup_script(self) -> None:
        with TemporaryDirectory() as tmp:
            fake = Path(tmp)
            with mock.patch.object(voice, "RECORDER_PY", fake / "nope" / "python.exe"):
                ok, note = voice.recorder_available()
        self.assertFalse(ok)
        self.assertIn("setup-whisper.ps1", note)

    def test_missing_vendor_clone_names_the_clone_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            py = root / "python.exe"
            py.write_bytes(b"")
            script = root / "recorder.py"
            script.write_text("# stub", encoding="utf-8")
            with mock.patch.object(voice, "RECORDER_PY", py), \
                    mock.patch.object(voice, "RECORDER", script), \
                    mock.patch.object(voice, "VENDOR", root / "vendor"):
                ok, note = voice.recorder_available()
        self.assertFalse(ok)
        self.assertIn("git clone", note)


class CommandShapeTest(unittest.TestCase):
    def test_command_is_the_sidecar_interpreter_running_the_recorder(self) -> None:
        command = voice.recorder_command(VoiceSettings())
        self.assertEqual(command[0], str(voice.RECORDER_PY))
        self.assertEqual(command[1], str(voice.RECORDER))
        self.assertIn("--url", command)
        self.assertIn("ws://127.0.0.1:8181/ws", command)

    def test_prompt_and_api_key_are_omitted_when_unset(self) -> None:
        command = voice.recorder_command(VoiceSettings())
        self.assertNotIn("--prompt", command)
        self.assertNotIn("--api-key", command)

    def test_prompt_and_api_key_are_passed_when_set(self) -> None:
        settings = VoiceSettings(prompt="getUserById", api_key="secret")
        command = voice.recorder_command(settings)
        self.assertEqual(command[command.index("--prompt") + 1], "getUserById")
        self.assertEqual(command[command.index("--api-key") + 1], "secret")

    def test_language_is_omitted_when_unset_so_the_client_can_auto_detect(self) -> None:
        """Auto-detect is the *absence* of the field on the wire: passing
        ``--language ''`` would make the client send an empty string, which the
        server rejects as an unknown language."""
        self.assertNotIn("--language", voice.recorder_command(VoiceSettings()))

    def test_language_is_passed_when_pinned(self) -> None:
        command = voice.recorder_command(VoiceSettings(language="hi"))
        self.assertEqual(command[command.index("--language") + 1], "hi")

    def test_the_device_is_always_spelled_out_including_the_default(self) -> None:
        """Sent even as -1.

        An omitted flag and an explicit -1 mean the same thing to PyAudio, but
        spelling it out keeps the command a complete record of the request --
        which is what a bug report or a doctor row has to work from when the
        wrong microphone was used. It is worth spelling out because it is the
        whole difference between working and "heard nothing": measured on this
        machine, the Windows default input peaked at 54 against a threshold of
        500, while another device carried real signal.
        """
        default = voice.recorder_command(VoiceSettings())
        self.assertIn("--device", default)
        self.assertEqual(default[default.index("--device") + 1], "-1")

        pinned = voice.recorder_command(VoiceSettings(device=4))
        self.assertEqual(pinned[pinned.index("--device") + 1], "4")


class ListenTest(unittest.TestCase):
    def _settings(self) -> VoiceSettings:
        return VoiceSettings()

    def test_transcript_is_taken_from_stdout_and_trimmed(self) -> None:
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout="  hello there  \n", stderr="")

        self.assertEqual(voice.listen(self._settings(), runner=runner), "hello there")

    def test_a_failing_client_raises_with_its_last_line(self) -> None:
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(
                argv, 2, stdout="", stderr="warning: first\nwhisper sidecar unreachable: no"
            )

        with self.assertRaises(RuntimeError) as caught:
            voice.listen(self._settings(), runner=runner)
        self.assertIn("unreachable", str(caught.exception))

    def test_a_wedged_client_reports_a_timeout_not_a_traceback(self) -> None:
        def runner(argv, **_kwargs):
            raise subprocess.TimeoutExpired(argv, 60)

        with self.assertRaises(RuntimeError) as caught:
            voice.listen(self._settings(), runner=runner)
        self.assertIn("timed out", str(caught.exception))

    def test_the_wait_is_bounded_by_the_settings(self) -> None:
        seen = {}

        def runner(argv, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        voice.listen(VoiceSettings(max_seconds=12.0), runner=runner)
        self.assertEqual(seen["timeout"], 72.0)

    def test_stderr_is_left_attached_so_partials_reach_the_operator(self) -> None:
        """The client writes "listening on device N ..." and every live partial
        to stderr. Capturing it -- as capture_output=True does -- silences all
        of it, and a screen that shows nothing after "voice mode on" is
        indistinguishable from a hang. That is how a hands-free loop got
        abandoned on its very first turn in practice, so the routing is
        asserted rather than left to a docstring nobody can run.
        """
        seen: dict = {}

        def runner(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr=None)

        voice.listen(self._settings(), runner=runner)
        self.assertIsNone(seen.get("stderr"), "stderr must be inherited, not piped")
        self.assertIsNotNone(seen.get("stdout"), "stdout is the transcript, so it is captured")
        self.assertNotIn("capture_output", seen)

    def test_a_failure_still_summarises_when_stderr_was_inherited(self) -> None:
        """With stderr inherited the client's own explanation has already
        reached the console, so the exception is a compact summary. It must
        still *be* one: result.stderr is None here, and a naive tail-of-lines
        would slice into nothing."""
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr=None)

        with self.assertRaises(RuntimeError) as caught:
            voice.listen(self._settings(), runner=runner)
        self.assertIn("exit code 2", str(caught.exception))


class SettingsTest(unittest.TestCase):
    def test_voice_settings_load_from_the_install(self) -> None:
        """Whatever the install has configured, it lands on a VoiceSettings."""
        self.assertIsInstance(load_settings().voice, VoiceSettings)

    def test_code_default_is_off(self) -> None:
        """A fresh checkout must not offer a microphone nobody asked for."""
        self.assertFalse(VoiceSettings().enabled)

    def test_nonsense_silence_window_is_clamped_not_trusted(self) -> None:
        from alan.config import _apply_tables

        settings = _apply_tables(Settings(), {
            "voice": {"silence_ms": 0, "threshold": -5, "max_seconds": 0.1},
        })
        self.assertEqual(settings.voice.silence_ms, 200)
        self.assertEqual(settings.voice.threshold, 1)
        self.assertEqual(settings.voice.max_seconds, 5.0)

    def test_an_input_device_index_is_read_back(self) -> None:
        from alan.config import _apply_tables

        settings = _apply_tables(Settings(), {"voice": {"device": 3}})
        self.assertEqual(settings.voice.device, 3)

    def test_a_device_index_below_the_default_sentinel_is_floored(self) -> None:
        """-1 means "the Windows default"; anything lower is a typo, and
        handing it to PyAudio fails obscurely at record time."""
        from alan.config import _apply_tables

        settings = _apply_tables(Settings(), {"voice": {"device": -7}})
        self.assertEqual(settings.voice.device, -1)
        self.assertEqual(VoiceSettings().device, -1)


class RecorderLogicTest(unittest.TestCase):
    """The push-to-talk client's own logic, which decides when a turn ends."""

    def test_silence_alone_never_ends_a_turn(self) -> None:
        detector = recorder.SilenceDetector(threshold=500, silence_ms=1000, max_seconds=60)
        for _ in range(100):
            self.assertFalse(detector.feed(0, 64.0))
        self.assertFalse(detector.speech_seen)

    def test_the_loudest_frame_is_kept_even_below_the_threshold(self) -> None:
        """The peak is what tells a muted microphone apart from a quiet room.

        Both produce an empty transcript, but only one is a hardware problem,
        so the detector keeps the loudest frame it saw even when no frame ever
        crossed the threshold.
        """
        detector = recorder.SilenceDetector(threshold=500, silence_ms=1000, max_seconds=60)
        for level in (12, 54, 40):
            detector.feed(level, 64.0)
        self.assertFalse(detector.speech_seen)
        self.assertEqual(detector.peak_seen, 54)

    def test_speech_then_pause_ends_the_turn(self) -> None:
        detector = recorder.SilenceDetector(threshold=500, silence_ms=1000, max_seconds=60)
        self.assertFalse(detector.feed(2000, 64.0))          # speaking
        for _ in range(15):                                   # 960 ms of quiet
            self.assertFalse(detector.feed(0, 64.0))
        self.assertTrue(detector.feed(0, 64.0))              # 1024 ms: stop

    def test_continuous_speech_is_cut_off_at_max_seconds(self) -> None:
        import time

        # The margin here is deliberately wider than one Windows timer tick.
        # `time.sleep` rounds down to the machine's tick (15.6 ms by default),
        # so `sleep(0.06)` measurably returns in ~47 ms on a loaded box --
        # which is *less* than the 50 ms cap this test used to set, making the
        # assertion fail roughly one run in five on Windows and never on a
        # machine with a finer clock. The behaviour under test is a backstop
        # firing once elapsed time passes a cap; it does not need the two
        # numbers to be 10 ms apart to be tested.
        detector = recorder.SilenceDetector(threshold=500, silence_ms=1000, max_seconds=0.05)
        self.assertFalse(detector.feed(2000, 64.0))  # speaking, under the cap
        time.sleep(0.25)
        self.assertTrue(detector.feed(2000, 64.0))  # elapsed past the cap

    def test_a_loud_word_restarts_the_silence_clock(self) -> None:
        detector = recorder.SilenceDetector(threshold=500, silence_ms=1000, max_seconds=60)
        detector.feed(2000, 64.0)
        for _ in range(10):
            detector.feed(0, 64.0)
        detector.feed(2000, 64.0)  # another word: the pause so far is forgotten
        self.assertEqual(detector.silent_ms, 0.0)

    def test_finals_are_joined_and_partials_are_dropped(self) -> None:
        messages = [
            {"is_partial": True, "data": {"text": "we can"}},
            {"is_partial": False, "data": {"text": "we can change reality"}},
            {"is_partial": True, "data": {"text": "by changing our"}},
            {"is_partial": False, "data": {"text": "by changing our mind"}},
        ]
        self.assertEqual(
            recorder.final_texts(messages), "we can change reality by changing our mind"
        )

    def test_empty_finals_produce_an_empty_transcript(self) -> None:
        self.assertEqual(recorder.final_texts([{"is_partial": True, "data": {}}]), "")

    def test_peak_measures_the_loudest_sample(self) -> None:
        import array

        samples = array.array("h", [0, 10, -3000, 5])
        self.assertEqual(recorder.peak(samples.tobytes()), 3000)
        self.assertEqual(recorder.peak(b""), 0)

    def test_start_frame_omits_language_and_prompt_when_unset(self) -> None:
        """Auto-detect is spelled by absence: the server rejects ``language:
        ""`` outright as an unknown language."""
        self.assertEqual(
            recorder.start_message("small.pt"),
            {"type": "start", "model": "small.pt"},
        )

    def test_start_frame_sends_language_and_prompt_when_given(self) -> None:
        self.assertEqual(
            recorder.start_message("small.pt", language="hi", prompt="getUserById"),
            {"type": "start", "model": "small.pt",
             "language": "hi", "prompt": "getUserById"},
        )

    def test_start_frame_never_carries_an_empty_language(self) -> None:
        self.assertNotIn("language", recorder.start_message("small.pt", language=""))
        self.assertNotIn("prompt", recorder.start_message("small.pt", prompt=""))

    def test_ready_url_is_derived_from_the_websocket_endpoint(self) -> None:
        self.assertEqual(
            recorder.ready_url("ws://127.0.0.1:8181/ws"), "http://127.0.0.1:8181/ready"
        )
        self.assertEqual(
            recorder.ready_url("ws://x/ws", explicit="http://y/ready"), "http://y/ready"
        )


class DescribeTest(unittest.TestCase):
    """The doctor row: the difference between 'off', 'configured' and 'ready'."""

    def test_disabled_voice_reports_off(self) -> None:
        self.assertEqual(voice.describe(VoiceSettings(enabled=False))["state"], "off")

    def test_sidecar_up_and_client_installed_is_ready(self) -> None:
        with mock.patch.object(voice, "ready", lambda _s: (True, "ready on http://x/ready")), \
                mock.patch.object(voice, "recorder_available", lambda: (True, "installed")):
            described = voice.describe(VoiceSettings(enabled=True))
        self.assertEqual(described["state"], "ready")
        self.assertIn("client installed", described["note"])

    def test_a_sidecar_that_is_down_names_the_script_to_start(self) -> None:
        with mock.patch.object(voice, "ready", lambda _s: (False, "not reachable")), \
                mock.patch.object(voice, "recorder_available", lambda: (True, "installed")):
            described = voice.describe(VoiceSettings(enabled=True))
        self.assertEqual(described["state"], "configured")
        self.assertIn("whisper-serve.ps1", described["note"])

    def test_a_missing_client_is_reported_next_to_the_sidecar_state(self) -> None:
        with mock.patch.object(voice, "ready", lambda _s: (True, "ready")), \
                mock.patch.object(voice, "recorder_available",
                                  lambda: (False, "run setup-whisper.ps1")):
            described = voice.describe(VoiceSettings(enabled=True))
        self.assertEqual(described["state"], "configured")
        self.assertIn("setup-whisper.ps1", described["note"])


class VoiceCommandTest(unittest.TestCase):
    """`alan voice` must refuse cleanly before it ever opens a microphone."""

    def test_voice_command_is_registered(self) -> None:
        from typer.main import get_command

        from alan.cli import app

        self.assertIn("voice", [c.name for c in get_command(app).commands.values()])

    def test_disabled_voice_says_what_to_enable(self) -> None:
        from typer.testing import CliRunner

        from alan import cli

        settings = Settings()  # voice.enabled is False in code
        with mock.patch.object(cli, "load_settings", lambda: settings):
            result = CliRunner().invoke(cli.app, ["voice"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("voice input is off", result.output)
        self.assertIn("[voice]", result.output)


if __name__ == "__main__":
    unittest.main()
