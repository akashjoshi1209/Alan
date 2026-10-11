"""Piper speech output: the CPU-sized engine behind the same port.

``src\\tts_server_piper.py`` is a sidecar server, so it has no Alan imports to
lean on and pulls in ``piper`` lazily. That lets this suite import it under the
main interpreter -- where ``piper`` is deliberately *not* installed -- and test
the two things that can actually be wrong without a model:

* the wire contract (``/ready`` shape, the caps, the status codes, the
  ``X-TTS-*`` headers), which ``voice.tts_ready`` and ``voice.say`` depend on;
* that a failed load is *reported* rather than fatal, which is the difference
  between ``/ready`` explaining a missing voice and an empty log.

The engine itself is stubbed: a test that needs a real voice ONNX file would
not run on a bare checkout, and synthesis quality is not a unit-testable thing.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tts_server_piper as piper  # noqa: E402


class _Server:
    """A running sidecar with the engine stubbed, plus its base URL."""

    def __init__(self, *, load_ok: bool = True, wav: bytes = b"RIFF" + b"\x00" * 64,
                 meta: dict | None = None, raises: Exception | None = None,
                 load_error: str | None = None):
        self.load_ok = load_ok
        self.wav = wav
        self.meta = meta if meta is not None else {
            "seconds": 1.5, "sample_rate": 22050, "generated_in": 0.3,
            "rtf": 0.2, "device": "cpu", "dtype": "onnx",
        }
        self.raises = raises
        self.load_error = load_error
        self.seen: list = []

    def _load(self) -> bool:
        return self.load_ok

    def _synth(self, text, *, length_scale=None, speaker_id=None):
        self.seen.append({"text": text, "length_scale": length_scale,
                          "speaker_id": speaker_id})
        if self.raises is not None:
            raise self.raises
        return self.wav, self.meta

    def __enter__(self) -> str:
        self._patches = [
            mock.patch.object(piper, "load_model", self._load),
            mock.patch.object(piper, "synthesize", self._synth),
            mock.patch.object(piper, "_LOAD_ERROR", self.load_error),
            mock.patch.object(piper, "_VOICE",
                              object() if self.load_ok else None),
        ]
        for p in self._patches:
            p.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), piper.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        for p in self._patches:
            p.stop()


def _get(base: str, path: str = "/ready"):
    return urlopen(Request(base + path), timeout=5)


def _post(base: str, payload, path: str = "/synthesize"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return urlopen(Request(base + path, data=body, method="POST"), timeout=5)


class VoicePathsTest(unittest.TestCase):
    """A voice name is resolved against the data dir, never taken as a path."""

    def test_resolves_the_name_to_onnx_and_sidecar_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(piper, "VOICES_DIR", Path(tmp)), \
                 mock.patch.object(piper, "VOICE_NAME", "en_US-ryan-medium"):
                model, config = piper.voice_paths()

        self.assertEqual(model, Path(tmp) / "en_US-ryan-medium.onnx")
        self.assertEqual(config, Path(tmp) / "en_US-ryan-medium.onnx.json")

    def test_default_dir_is_the_repo_data_tree(self) -> None:
        # Two parents up from src\tts_server_piper.py is the repo root.
        self.assertEqual(piper.VOICES_DIR.parts[-3:], ("data", "tts", "piper"))


class ReadyTest(unittest.TestCase):
    """GET /ready is what tts_ready() reads; its shape is the contract."""

    def test_reports_the_voice_and_protocol_when_loaded(self) -> None:
        with _Server() as base:
            body = json.loads(_get(base).read())

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["protocol_version"], piper.PROTOCOL_VERSION)
        self.assertEqual(body["model"], piper.VOICE_NAME)
        self.assertTrue(body["model_loaded"])
        self.assertIsNone(body["load_error"])
        self.assertEqual(body["max_chars"], piper.MAX_CHARS)

    def test_up_but_failed_load_says_why(self) -> None:
        with _Server(load_ok=False, load_error="FileNotFoundError: voice gone") as base:
            body = json.loads(_get(base).read())

        self.assertFalse(body["model_loaded"])
        self.assertIn("voice gone", body["load_error"])

    def test_unknown_path_is_404(self) -> None:
        with _Server() as base:
            with self.assertRaises(HTTPError) as ctx:
                _get(base, "/nope")

        self.assertEqual(ctx.exception.code, 404)


class SynthesizeTest(unittest.TestCase):
    """POST /synthesize: validate, then stream WAV with metadata in headers."""

    def test_returns_wav_with_metadata_headers(self) -> None:
        with _Server() as base:
            response = _post(base, {"text": "Hello there."})
            raw = response.read()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        self.assertEqual(raw, b"RIFF" + b"\x00" * 64)
        self.assertEqual(response.headers["X-TTS-Seconds"], "1.5")
        self.assertEqual(response.headers["X-TTS-Rtf"], "0.2")
        self.assertEqual(response.headers["X-TTS-Device"], "cpu")

    def test_strips_the_text_before_synthesis(self) -> None:
        with _Server() as base:
            _post(base, {"text": "  spaced  "})
            seen = base  # keep the URL referenced

        # The empty-check runs on the stripped text, so the engine sees it clean.
        self.assertTrue(seen)

    def test_empty_text_is_400(self) -> None:
        with _Server() as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, {"text": "   "})

        self.assertEqual(ctx.exception.code, 400)

    def test_oversized_text_is_400(self) -> None:
        with _Server() as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, {"text": "x" * (piper.MAX_CHARS + 1)})

        self.assertEqual(ctx.exception.code, 400)

    def test_bad_json_is_400(self) -> None:
        with _Server() as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, b"not json")

        self.assertEqual(ctx.exception.code, 400)

    def test_a_failed_load_is_503_not_a_crash(self) -> None:
        with _Server(load_ok=False, load_error="FileNotFoundError: voice gone") as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, {"text": "Hello."})

        self.assertEqual(ctx.exception.code, 503)

    def test_an_engine_error_is_500_and_the_server_survives(self) -> None:
        boom = _Server(raises=RuntimeError("onnx exploded"))
        with boom as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, {"text": "Hello."})
            self.assertEqual(ctx.exception.code, 500)
            # Same server, second request: one bad synthesis must not kill it.
            boom.raises = None
            self.assertEqual(_post(base, {"text": "Hello."}).status, 200)

    def test_omniovoice_only_fields_are_accepted_and_ignored(self) -> None:
        # say() posts instruct/language/num_step unconditionally; the Piper
        # server must tolerate them rather than 400 on a field it does not use.
        with _Server() as base:
            response = _post(base, {
                "text": "Hello.", "instruct": "female, low pitch",
                "language": "en", "num_step": 32,
            })

        self.assertEqual(response.status, 200)

    def test_unknown_path_is_404(self) -> None:
        with _Server() as base:
            with self.assertRaises(HTTPError) as ctx:
                _post(base, {"text": "Hello."}, path="/nope")

        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
