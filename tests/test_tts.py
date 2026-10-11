"""Speech output: the outbound half of voice.

Three things are worth proving here, and only the third needs a server:

* ``say()`` never raises. Speech sits after an answer already on screen, so a
  speaker that is off or down may cost the sound but must never cost the turn.
* ``for_speech()`` turns markup into something a listener can follow. It is
  the fiddly half -- one bad rule mangles every spoken reply silently, while
  synthesis itself is a single HTTP call.
* ``tts_ready()`` refuses a mismatched protocol, because a version skew here
  fails as noise rather than as an error anyone would read.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import textwrap
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alan import voice  # noqa: E402
from alan.config import VoiceSettings  # noqa: E402


class _TtsHandler(BaseHTTPRequestHandler):
    """A stand-in TTS sidecar: whatever the test set, plus the POST body it saw."""

    payload: bytes = b"{}"
    status: int = 200
    seen: list = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        length = int(self.headers.get("Content-Length") or 0)
        type(self).seen.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        self.wfile.write(b"RIFF" + b"\x00" * 64)

    def log_message(self, *_args):  # keep the test output clean
        pass


def _server(payload: dict, status: int = 200) -> HTTPServer:
    _TtsHandler.payload = json.dumps(payload).encode("utf-8")
    _TtsHandler.status = status
    _TtsHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _TtsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TtsReadyTest(unittest.TestCase):
    """The output sidecar gets its own probe, with its own protocol number."""

    def _settings(self, server: HTTPServer, **kw) -> VoiceSettings:
        return VoiceSettings(
            tts_base_url=f"http://127.0.0.1:{server.server_port}", **kw
        )

    def test_reports_ready_when_the_protocol_matches(self) -> None:
        server = _server({
            "status": "ok",
            "protocol_version": voice.TTS_PROTOCOL_VERSION,
            "model": "k2-fsa/OmniVoice",
            "dtype": "float32",
            "model_loaded": True,
        })
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        ok, note = voice.tts_ready(self._settings(server))

        self.assertTrue(ok, note)
        self.assertIn("OmniVoice", note)
        self.assertIn("resident", note)

    def test_refuses_a_sidecar_speaking_another_protocol(self) -> None:
        server = _server({"protocol_version": 99})
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        ok, note = voice.tts_ready(self._settings(server))

        self.assertFalse(ok)
        self.assertIn("protocol v99", note)

    def test_up_but_failed_load_is_not_reported_as_ready(self) -> None:
        """The distinction /ready exists for: 'start it' vs 'read the log'."""
        server = _server({
            "protocol_version": voice.TTS_PROTOCOL_VERSION,
            "load_error": "RuntimeError: CUDA missing",
        })
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        ok, note = voice.tts_ready(self._settings(server))

        self.assertFalse(ok)
        self.assertIn("CUDA missing", note)

    def test_a_sidecar_that_is_not_running_says_so_without_raising(self) -> None:
        # Port 9 is the discard port: nothing listens, so this is a real
        # connection failure rather than a mocked one.
        settings = VoiceSettings(tts_base_url="http://127.0.0.1:9")

        ok, note = voice.tts_ready(settings)

        self.assertFalse(ok)
        self.assertIn("not reachable", note)


class ForSpeechTest(unittest.TestCase):
    """Markup has to disappear on the way to the speaker, not reach it."""

    def test_strips_heading_and_emphasis(self) -> None:
        spoken = voice.for_speech("# Answer\n\nIt is **done** and *verified*.")
        self.assertEqual(spoken, "Answer It is done and verified.")

    def test_keeps_the_text_of_a_link_but_drops_the_url(self) -> None:
        spoken = voice.for_speech("See [the docs](https://example.com/x) for more.")
        self.assertEqual(spoken, "See the docs for more.")

    def test_bullets_become_pauses_and_survive_as_text(self) -> None:
        spoken = voice.for_speech("- first point\n- second point")
        self.assertEqual(spoken, "first point second point")

    def test_fenced_code_is_dropped_whole_rather_than_read_line_by_line(self) -> None:
        spoken = voice.for_speech("run this:\n```python\nx = 1\ny = 2\n```\ndone")
        self.assertEqual(spoken, "run this: done")

    def test_inline_code_keeps_its_content(self) -> None:
        self.assertEqual(voice.for_speech("call `alan doctor`"), "call alan doctor")

    def test_table_cells_flatten_to_prose(self) -> None:
        self.assertEqual(voice.for_speech("| a | b |"), "a b")

    def test_plain_prose_is_left_alone(self) -> None:
        """The rule that would silently ruin every reply is the one that eats
        punctuation from ordinary sentences."""
        prose = "Alan answered in 1.5s, not 2.0s -- 40% faster (verified)."
        self.assertEqual(voice.for_speech(prose), prose)

    def test_output_is_capped_to_what_the_sidecar_will_accept(self) -> None:
        self.assertEqual(len(voice.for_speech("word " * 2000)), 1200)

    def test_empty_input_is_empty_not_an_error(self) -> None:
        self.assertEqual(voice.for_speech(""), "")
        self.assertEqual(voice.for_speech(None), "")


class SayTest(unittest.TestCase):
    """`say()` returns a reason; it never raises. That is the whole contract."""

    def _settings(self, **kw) -> VoiceSettings:
        base = {"tts_enabled": True, "tts_play": True}
        base.update(kw)
        return VoiceSettings(**base)

    def test_disabled_returns_a_reason_and_never_touches_the_sidecar(self) -> None:
        def explode(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("a disabled channel must not synthesize")

        ok, note = voice.say(VoiceSettings(tts_enabled=False), "hello",
                             synthesizer=explode, player=explode)

        self.assertFalse(ok)
        self.assertIn("tts_enabled", note)

    def test_blank_text_is_not_worth_a_round_trip(self) -> None:
        ok, note = voice.say(self._settings(), "   \n  ",
                             synthesizer=lambda *_: b"RIFF")

        self.assertFalse(ok)
        self.assertEqual(note, "nothing to say")

    def test_markdown_is_stripped_before_it_is_sent(self) -> None:
        sent = []

        voice.say(self._settings(), "# Hi\n\nThis is **bold**.",
                  synthesizer=lambda _s, text: sent.append(text) or b"RIFF",
                  player=lambda _w: None)

        self.assertEqual(sent, ["Hi This is bold."])

    def test_a_sidecar_that_is_down_costs_the_sound_not_the_turn(self) -> None:
        def fail(_settings, _text):
            raise URLError("connection refused")

        ok, note = voice.say(self._settings(), "the answer stands",
                             synthesizer=fail, player=lambda _w: None)

        self.assertFalse(ok)
        self.assertIn("tts-serve.ps1", note)
        self.assertIn("the answer above stands", note)

    def test_an_unexpected_error_is_still_contained(self) -> None:
        """A bug in synthesis must not escape into the caller's turn either."""
        ok, note = voice.say(
            self._settings(), "hello",
            synthesizer=lambda *_: (_ for _ in ()).throw(ValueError("bad wav")),
            player=lambda _w: None,
        )
        self.assertFalse(ok)
        self.assertIn("bad wav", note)

    def test_success_reaches_the_player(self) -> None:
        played = []

        ok, note = voice.say(self._settings(), "hello",
                             synthesizer=lambda *_: b"RIFF" * 8,
                             player=played.append)

        self.assertTrue(ok, note)
        self.assertEqual(len(played), 1)
        self.assertTrue(played[0].startswith(b"RIFF"))

    def test_tts_play_false_synthesizes_without_playing(self) -> None:
        """A health check that exercises the whole path on a box with no
        speaker -- without needing one to pass."""
        def explode(_wav):  # pragma: no cover - must not be reached
            raise AssertionError("tts_play = false must not play")

        ok, note = voice.say(self._settings(tts_play=False), "hello",
                             synthesizer=lambda *_: b"RIFF", player=explode)

        self.assertTrue(ok, note)
        self.assertIn("tts_play", note)

    def test_a_player_that_fails_is_reported_not_raised(self) -> None:
        def no_device(_wav):
            raise OSError("no audio device")

        ok, note = voice.say(self._settings(), "hello",
                             synthesizer=lambda *_: b"RIFF", player=no_device)

        self.assertFalse(ok)
        self.assertIn("no audio device", note)

    def test_a_player_bug_is_contained_like_any_other_failure(self) -> None:
        """The winsound SND_SYNC AttributeError was exactly this: a defect in
        the default player escaped ``say()`` because the except tuple was
        narrow. Any player failure must cost the sound, never the turn."""
        def broken(_wav):
            raise AttributeError("module 'winsound' has no attribute 'SND_SYNC'")

        ok, note = voice.say(self._settings(), "hello",
                             synthesizer=lambda *_: b"RIFF", player=broken)

        self.assertFalse(ok)
        self.assertIn("could not be played", note)

    def test_empty_audio_from_the_sidecar_is_a_failure(self) -> None:
        ok, note = voice.say(self._settings(), "hello",
                             synthesizer=lambda *_: b"", player=lambda _w: None)

        self.assertFalse(ok)
        self.assertIn("no audio", note)


class PlaySoundTest(unittest.TestCase):
    """The real player reaches winsound, and only flags that exist may reach it."""

    def test_play_calls_winsound_without_the_broken_constant(self) -> None:
        fake = types.ModuleType("winsound")
        fake.SND_FILENAME = 0x00020000
        calls = []
        fake.PlaySound = lambda name, flags: calls.append((name, flags))

        with mock.patch.dict(sys.modules, {"winsound": fake}):
            voice._play(b"RIFF" + b"\x00" * 64)

        self.assertEqual(len(calls), 1)
        _, flags = calls[0]
        # SND_SYNC does not exist on winsound (playback is synchronous by
        # default); a future rewrite must not reach for it again.
        self.assertEqual(flags, fake.SND_FILENAME)
        self.assertFalse(hasattr(fake, "SND_SYNC"))


class SynthesizeRequestTest(unittest.TestCase):
    """The wire shape: JSON in, WAV out, markdown already stripped."""

    def test_posts_json_and_returns_the_wav(self) -> None:
        server = _server({})
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = VoiceSettings(
            tts_enabled=True,
            tts_base_url=f"http://127.0.0.1:{server.server_port}",
            tts_instruct="low pitch",
        )

        wav = voice._synthesize(settings, "hello there")

        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertEqual(_TtsHandler.seen, [{"text": "hello there",
                                             "instruct": "low pitch"}])

    def test_an_empty_instruct_is_sent_as_null_not_as_an_empty_voice(self) -> None:
        server = _server({})
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = VoiceSettings(
            tts_enabled=True,
            tts_base_url=f"http://127.0.0.1:{server.server_port}",
            tts_instruct="",
        )

        voice._synthesize(settings, "hello")

        # OmniVoice treats an empty instruct as "describe a voice of nothing";
        # absent is what means "let the model pick".
        self.assertIsNone(_TtsHandler.seen[-1]["instruct"])


class DescribeSpeechTest(unittest.TestCase):
    """The doctor row: off, configured, ready -- and it names the fix."""

    def test_disabled_reports_off(self) -> None:
        described = voice.describe_speech(VoiceSettings(tts_enabled=False))
        self.assertEqual(described["state"], "off")

    def test_enabled_and_up_is_ready(self) -> None:
        with mock.patch.object(
            voice, "tts_ready", lambda _s: (True, "ready on http://x/ready")
        ):
            described = voice.describe_speech(VoiceSettings(tts_enabled=True))
        self.assertEqual(described["state"], "ready")
        self.assertTrue(described["reachable"])

    def test_enabled_but_down_names_the_script_to_start(self) -> None:
        with mock.patch.object(
            voice, "tts_ready", lambda _s: (False, "not reachable")
        ):
            described = voice.describe_speech(VoiceSettings(tts_enabled=True))
        self.assertEqual(described["state"], "configured")
        self.assertIn("tts-serve.ps1", described["note"])


class ConfigTest(unittest.TestCase):
    """The new keys load through the real loader, and the ones that can be set
    silly are clamped."""

    def _load(self, body: str):
        from alan import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.toml"
            path.write_text(body, encoding="utf-8")
            # Point the loader at a scratch file rather than reimplementing its
            # logic in the test: a test that parses the TOML itself proves the
            # test, not the code.
            with mock.patch.object(config_mod, "SETTINGS_FILE", path):
                return config_mod.load_settings()

    def test_voice_settings_carry_the_speech_defaults(self) -> None:
        settings = VoiceSettings()
        self.assertFalse(settings.tts_enabled)
        self.assertEqual(settings.tts_base_url, "http://127.0.0.1:8182")
        self.assertEqual(settings.tts_timeout, 300.0)
        self.assertTrue(settings.tts_play)

    def test_speech_keys_are_read_from_settings(self) -> None:
        settings = self._load(textwrap.dedent("""\
            [voice]
            enabled = true
            tts_enabled = true
            tts_instruct = "low pitch, british accent"
            tts_timeout = 90.0
            tts_play = false
            """)).voice

        self.assertTrue(settings.enabled)
        self.assertTrue(settings.tts_enabled)
        self.assertEqual(settings.tts_instruct, "low pitch, british accent")
        self.assertEqual(settings.tts_timeout, 90.0)
        self.assertFalse(settings.tts_play)

    def test_a_timeout_below_the_model_load_is_clamped_up(self) -> None:
        """1 second would race the model's own load from disk and report a
        sidecar that is merely still starting as broken."""
        settings = self._load("[voice]\ntts_timeout = 1.0\n").voice
        self.assertEqual(settings.tts_timeout, 10.0)

    def test_a_nonsensical_timeout_is_clamped_down(self) -> None:
        settings = self._load("[voice]\ntts_timeout = 99999.0\n").voice
        self.assertEqual(settings.tts_timeout, 1800.0)

    def test_an_absent_voice_section_leaves_the_defaults_alone(self) -> None:
        settings = self._load("[memory]\nbackend = \"local\"\n").voice
        self.assertFalse(settings.tts_enabled)
        self.assertEqual(settings.tts_base_url, "http://127.0.0.1:8182")


class VoiceModeTest(unittest.TestCase):
    """/voicemode: what gates entry, and what gets you out again."""

    def test_cannot_enter_without_the_microphone_sidecar(self) -> None:
        """Listening is the half that gates entry; the note names the fix."""
        with mock.patch.object(voice, "ready", lambda _s: (False, "not reachable")):
            ok, note = voice.mode_status(
                VoiceSettings(enabled=True, tts_enabled=True)
            )
        self.assertFalse(ok)
        self.assertIn("cannot listen", note)
        self.assertIn("whisper-serve.ps1", note)

    def test_speaking_unavailable_still_allows_the_mode(self) -> None:
        """The key design call: a hands-free conversation that prints its
        answers is still a conversation. Refusing here would trade a working
        voice input for a speaker that happens to be down."""
        with mock.patch.object(voice, "ready", lambda _s: (True, "ready")), \
                mock.patch.object(voice, "tts_ready", lambda _s: (False, "down")):
            ok, note = voice.mode_status(
                VoiceSettings(enabled=True, tts_enabled=True)
            )
        self.assertTrue(ok)
        self.assertIn("listening; speaking unavailable", note)

    def test_speech_disabled_says_so_rather_than_staying_silent(self) -> None:
        with mock.patch.object(voice, "ready", lambda _s: (True, "ready")):
            ok, note = voice.mode_status(
                VoiceSettings(enabled=True, tts_enabled=False)
            )
        self.assertTrue(ok)
        self.assertIn("printed, not spoken", note)
        self.assertIn("tts_enabled", note)

    def test_both_sides_up_reports_a_full_voice_loop(self) -> None:
        with mock.patch.object(voice, "ready", lambda _s: (True, "ready")), \
                mock.patch.object(voice, "tts_ready", lambda _s: (True, "ready 8182")):
            ok, note = voice.mode_status(
                VoiceSettings(enabled=True, tts_enabled=True)
            )
        self.assertTrue(ok)
        self.assertIn("listening and speaking", note)
        self.assertIn("8182", note)


class ExitPhraseTest(unittest.TestCase):
    """Leaving has to work on the first try, in the moment you want it."""

    def test_the_documented_phrases_exit(self) -> None:
        for phrase in voice.VOICE_EXIT_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(voice.is_exit_phrase(phrase))

    def test_punctuation_and_case_from_a_transcriber_do_not_break_it(self) -> None:
        """Whisper renders 'Stop listening.' as often as not. A command you
        have to say exactly right is one you will say wrong when it matters."""
        self.assertTrue(voice.is_exit_phrase("Stop listening."))
        self.assertTrue(voice.is_exit_phrase("  STOP LISTENING  "))

    def test_something_that_merely_contains_a_phrase_does_not_exit(self) -> None:
        """'Stop listening to that podcast' is a question about a podcast."""
        self.assertFalse(voice.is_exit_phrase("stop listening to that podcast"))
        self.assertFalse(voice.is_exit_phrase("please exit voice mode later"))

    def test_an_ordinary_question_keeps_the_mode_open(self) -> None:
        for question in (
            "what time is it in tokyo",
            "exit this program",       # 'exit' alone is not a listed phrase
            "voice mode on please",    # enabling, not disabling
            "",
        ):
            with self.subTest(question=question):
                self.assertFalse(voice.is_exit_phrase(question))


class SayCostTest(unittest.TestCase):
    """The note reports what a spoken reply cost -- for any engine on the port."""

    def test_the_note_carries_the_synthesis_time(self) -> None:
        def slow(_settings, _text):
            import time as time_mod

            time_mod.sleep(0.05)
            return b"RIFF" * 8

        ok, note = voice.say(
            VoiceSettings(tts_enabled=True, tts_play=False), "hello",
            synthesizer=slow,
        )
        self.assertTrue(ok, note)
        # Asserted as a shape, not a literal: the exact figure depends on the
        # machine and on what else is running, which is the whole point of
        # reporting it rather than hard-coding it.
        self.assertRegex(note, r"in \d+\.\d+s")
        self.assertGreater(float(re.search(r"in (\d+\.\d+)s", note).group(1)), 0.04)

    def test_a_capped_reply_says_it_was_shortened(self) -> None:
        """Hearing three quarters of an answer without being told is worse
        than not hearing it -- the cap is real and should be visible."""
        ok, note = voice.say(
            VoiceSettings(tts_enabled=True, tts_play=False), "word " * 2000,
            synthesizer=lambda *_: b"RIFF",
        )
        self.assertTrue(ok, note)
        self.assertIn("shortened", note)

    def test_an_uncapped_reply_does_not_claim_to_be_shortened(self) -> None:
        ok, note = voice.say(
            VoiceSettings(tts_enabled=True, tts_play=False), "short answer",
            synthesizer=lambda *_: b"RIFF",
        )
        self.assertTrue(ok, note)
        self.assertNotIn("shortened", note)


class VoicemodeDiscoverabilityTest(unittest.TestCase):
    """/voicemode has to be findable. /help is where you look and Tab is how
    you type it, and both read _HELP_SECTIONS -- so the same single row feeds
    them, and the same single omission would remove it from both silently."""

    def _rows(self) -> list:
        from alan import cli

        return [c for _section, entries in cli._HELP_SECTIONS for c, _d in entries]

    def test_it_is_listed_in_help(self) -> None:
        self.assertIn("/voicemode", self._rows())

    def test_tab_completes_it(self) -> None:
        from alan import cli

        self.assertIn("/voicemode", cli._completion_options())

    def test_it_is_the_only_spelling(self) -> None:
        """No /voice alias: `alan voice` is already a top-level command that
        does something else (one ask, then quit), and a second chat spelling
        differing by three letters would be a trap rather than a convenience."""
        from alan import cli

        self.assertNotIn("/voice", self._rows())
        self.assertNotIn("/voice", cli._completion_options())


if __name__ == "__main__":
    unittest.main()
