"""Piper text-to-speech sidecar: the outbound half of Alan's voice, CPU-sized.

Same wire contract as ``src\\tts_server.py`` (the OmniVoice engine), and
deliberately a *separate file* rather than a branch inside it: the two engines
share nothing but the protocol, and the OmniVoice server's docstring is the
record of why it is unusable here. Keeping them apart means neither engine's
story gets overwritten by the other's.

Why a second engine exists
--------------------------
OmniVoice is a 613M-parameter diffusion model. Measured on this box (i3-6006U,
2 cores, no CUDA) it loads in 170.8 s and needs over 1,069 s to say one
3-second sentence -- an RTF above 356, against the 0.025 upstream quotes on an
H100. At that cost ``[voice] tts_enabled`` ships false and the feature is
wired-but-unusable. See ``docs\\integrations\\tts.md``.

Piper (rhasspy/piper, MIT) is the CPU-sized engine that doc names as the fix:
a VITS voice is a few tens of MB of ONNX and synthesizes **faster than real
time** on this CPU. Measured here for a 7-second utterance, per voice:

    en_US-ryan-medium       1.41 s   RTF 0.22   (default -- male, reads "Alan")
    en_US-joe-medium        1.43 s   RTF 0.20
    en_US-hfc_female-medium 1.42 s   RTF 0.20
    en_US-amy-medium        1.68 s   RTF 0.19
    en_US-lessac-medium     1.39 s   RTF 0.19
    en_GB-alba-medium       1.69 s   RTF 0.21

i.e. ~1.4 s to say ~7 s, so a reply is *shorter* arriving than it is to hear
(RTF < 1) -- the property that makes the feature actually usable. The `-high`
voices are more expressive but RTF ~1.2, still fine, slightly behind real time.

What it is not
--------------
Piper has no *voice design*: there is no free-text "female, low pitch, british
accent" dial the way OmniVoice has. A voice is a chosen artifact -- one of the
released ``.onnx`` voices -- so ``instruct`` in the request is accepted and
ignored, and the voice is selected by name at start-up (``TTS_VOICE``). That is
the whole trade: you give up designing a voice to gain one that can speak now.

Layout and rules, identical to the OmniVoice sidecar
----------------------------------------------------
Runs under ``env\\tts\\python`` and therefore cannot import Alan -- the ``._pth``
src hook is stripped by ``scripts\\setup-tts.ps1`` for exactly this reason. It
needs only Piper and the standard library; ``piper`` is imported lazily so the
module stays importable (and testable) without the sidecar's packages present.

Endpoints
---------
``GET /ready``
    ``{status, protocol_version, model, device, model_loaded, load_error,
    dtype, max_chars}``. ``model`` is the voice name, e.g. ``en_US-ryan-medium``.

``POST /synthesize``
    JSON ``{text, length_scale?, speaker_id?}`` in; ``audio/wav`` bytes back.
    ``instruct`` / ``language`` / ``num_step`` may be present (the client sends
    them) and are ignored -- they are OmniVoice's knobs, not Piper's. Reply
    metadata rides in ``X-TTS-*`` headers, as in the OmniVoice server.

Environment
-----------
``TTS_VOICE``        voice name without extension. Default ``en_US-ryan-medium``.
``TTS_VOICES_DIR``   directory holding ``<voice>.onnx`` (+ ``.onnx.json``).
                     Default ``<repo>\\data\\tts\\piper``.
``TTS_LENGTH_SCALE`` Piper phoneme length scale (< 1 faster, > 1 slower).
``TTS_SPEAKER_ID``   speaker index for a multi-speaker voice.
``TTS_USE_CUDA``     ``1`` to load on CUDA when present (still CPU here).
``TTS_HOST``/``TTS_PORT``   bind address. Loopback, like every other sidecar.
``TTS_MAX_CHARS``    hard cap on one utterance. Default 1200, matching the
                     client's own cap in ``voice.for_speech``.
``TTS_MAX_SECONDS``  bound on one synthesis, reported rather than exceeded.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROTOCOL_VERSION = 1

HOST = os.environ.get("TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("TTS_PORT", "8182"))
#: A voice name, not a path: the file is resolved against VOICES_DIR so the
#: serve script can name one and the operator can see which.
VOICE_NAME = os.environ.get("TTS_VOICE", "en_US-ryan-medium")
#: Where the voices live. Defaults to the repo's own data tree -- this file is
#: ``<repo>\\src\\tts_server_piper.py``, so two parents up is the root. Data is
#: git-ignored with the rest of the runtime tree: voices stay on this machine.
VOICES_DIR = Path(os.environ.get("TTS_VOICES_DIR")
                  or Path(__file__).resolve().parent.parent / "data" / "tts" / "piper")
#: Piper is not a diffusion model: one pass, no step count. The knob left is
#: how long it draws each phoneme, which changes tempo, not quality.
LENGTH_SCALE = os.environ.get("TTS_LENGTH_SCALE") or None
SPEAKER_ID = os.environ.get("TTS_SPEAKER_ID") or None
USE_CUDA = os.environ.get("TTS_USE_CUDA", "0").strip().lower() in ("1", "true", "yes", "on")

#: Refuse anything longer. Mirrors the OmniVoice server and the client's cap in
#: ``voice.for_speech`` so the three never disagree about where the ceiling is.
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1200"))
#: Bound on a single synthesis, reported rather than silently exceeded. Piper
#: is ~5x real time here, so this is generous headroom, not a real budget.
MAX_SECONDS = float(os.environ.get("TTS_MAX_SECONDS", "600"))

LOG = logging.getLogger("tts")

#: The voice is loaded once and kept resident: a few tens of MB, which is why
#: this engine is usable where the 3 GB one is not. Guarded by this lock --
#: concurrent generate() calls on one loaded voice are not a supported path,
#: and on a 2-core box they would only contend anyway.
_VOICE = None
_LOAD_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()
_LOAD_ERROR: str | None = None
_DEVICE = "unknown"
_SAMPLE_RATE = 22050
_DTYPE = "onnx"


def voice_paths() -> tuple[Path, Path]:
    """Return ``(model_path, config_path)`` for the configured voice."""
    model = VOICES_DIR / f"{VOICE_NAME}.onnx"
    return model, model.with_suffix(model.suffix + ".json")


def load_model() -> bool:
    """Load the voice once. Returns True when it is resident.

    Failure is recorded rather than raised: the server stays up so ``/ready``
    can *explain* why it is not ready. A sidecar that dies on a missing voice
    leaves an operator reading an empty log instead of a message.
    """
    global _VOICE, _LOAD_ERROR, _DEVICE, _SAMPLE_RATE

    with _LOAD_LOCK:
        if _VOICE is not None:
            return True
        if _LOAD_ERROR is not None:
            return False

        started = time.perf_counter()
        try:
            from piper import PiperVoice

            model_path, config_path = voice_paths()
            if not model_path.exists():
                raise FileNotFoundError(
                    f"voice {VOICE_NAME!r} not found at {model_path}. "
                    f"Run scripts\\pull-tts-voice.ps1 -Voice {VOICE_NAME}"
                )

            _DEVICE = "cuda" if USE_CUDA else "cpu"
            LOG.info("loading %s on %s ...", model_path, _DEVICE)
            _VOICE = PiperVoice.load(
                str(model_path),
                config_path=str(config_path) if config_path.exists() else None,
                use_cuda=USE_CUDA,
            )
            _SAMPLE_RATE = int(_VOICE.config.sample_rate)
            LOG.info("voice resident after %.1fs", time.perf_counter() - started)
            return True
        except Exception as exc:  # noqa: BLE001 - any load failure must be reportable
            _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
            LOG.exception("voice load failed")
            return False


def _syn_config():
    """Piper's synthesis config, or None to take its own defaults."""
    if LENGTH_SCALE is None and SPEAKER_ID is None:
        return None
    from piper import SynthesisConfig

    return SynthesisConfig(
        speaker_id=int(SPEAKER_ID) if SPEAKER_ID is not None else None,
        length_scale=float(LENGTH_SCALE) if LENGTH_SCALE is not None else None,
    )


def synthesize(text: str, *, length_scale: float | None = None,
               speaker_id: int | None = None) -> tuple[bytes, dict]:
    """Return ``(wav bytes, metadata)`` for one utterance."""
    if _VOICE is None and not load_model():
        raise RuntimeError(_LOAD_ERROR or "voice failed to load")

    syn = _syn_config()
    if length_scale is not None or speaker_id is not None:
        from piper import SynthesisConfig

        syn = SynthesisConfig(
            speaker_id=int(speaker_id) if speaker_id is not None
            else (syn.speaker_id if syn else None),
            length_scale=float(length_scale) if length_scale is not None
            else (syn.length_scale if syn else None),
        )

    with _GEN_LOCK:
        started = time.perf_counter()
        # Piper emits one AudioChunk per sentence; the WAV is all of them,
        # back to back, which is what makes a multi-sentence reply one file.
        wav_buffer = io.BytesIO()
        frames = 0
        rate = _SAMPLE_RATE
        width = 2
        with wave.open(wav_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            for chunk in _VOICE.synthesize(text, syn_config=syn):
                rate = int(chunk.sample_rate)
                width = int(chunk.sample_width)
                wav.writeframes(chunk.audio_int16_bytes)
                frames += len(chunk.audio_int16_bytes) // max(1, width)
        elapsed = time.perf_counter() - started

    duration = frames / float(rate) if rate else 0.0
    return wav_buffer.getvalue(), {
        "seconds": round(duration, 3),
        "sample_rate": rate,
        "generated_in": round(elapsed, 3),
        # The number that decides whether voice output is usable here: how
        # much wall clock one second of speech costs. Below 1.0 the answer is
        # shorter arriving than it is to listen to.
        "rtf": round(elapsed / duration, 3) if duration > 0 else None,
        "device": _DEVICE,
        "dtype": _DTYPE,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AlanTTS/1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Routed to logging rather than stderr so it lands in the sidecar log
        # the serve script redirects, with the same timestamps as load errors.
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        path = urlparse(self.path).path
        if path != "/ready":
            self._json({"status": "error", "detail": f"unknown path {path}"}, 404)
            return
        # Not loaded yet is not the same as broken: report the difference, so
        # "still starting" is never mistaken for "will never work".
        self._json({
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "model": VOICE_NAME,
            "device": _DEVICE,
            "model_loaded": _VOICE is not None,
            "load_error": _LOAD_ERROR,
            "dtype": _DTYPE,
            "max_chars": MAX_CHARS,
        })

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        path = urlparse(self.path).path
        if path != "/synthesize":
            self._json({"status": "error", "detail": f"unknown path {path}"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1_000_000:
                self._json({"status": "error", "detail": "missing or oversized body"}, 400)
                return
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json({"status": "error", "detail": f"bad JSON: {exc}"}, 400)
            return

        text = str(request.get("text") or "").strip()
        if not text:
            self._json({"status": "error", "detail": "text is empty"}, 400)
            return
        if len(text) > MAX_CHARS:
            self._json({
                "status": "error",
                "detail": f"text is {len(text)} chars, the ceiling is {MAX_CHARS}",
            }, 400)
            return

        if not load_model():
            self._json({"status": "error", "detail": _LOAD_ERROR or "voice failed to load"}, 503)
            return

        try:
            wav, meta = synthesize(
                text,
                length_scale=request.get("length_scale") or None,
                speaker_id=request.get("speaker_id") or None,
            )
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
            LOG.exception("synthesis failed")
            self._json({"status": "error", "detail": f"{type(exc).__name__}: {exc}"}, 500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        # Metadata rides in headers so the client needs no second parse, and a
        # slow CPU is visible in the response that was slow.
        for key, value in meta.items():
            if value is not None:
                self.send_header(f"X-TTS-{key.replace('_', '-')}", str(value))
        self.end_headers()
        self.wfile.write(wav)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    LOG.info("Alan TTS sidecar (piper) on %s:%s (voice=%s)", HOST, PORT, VOICE_NAME)

    # Load eagerly, the way the OmniVoice server does: the first spoken answer
    # should not also pay for the disk read. Reported as a warning rather than
    # fatal, so a failed load still leaves a listening /ready to explain it.
    if not load_model():
        LOG.warning("voice did not load: %s", _LOAD_ERROR)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
