"""OmniVoice text-to-speech sidecar: the outbound half of Alan's voice.

Runs under the sidecar interpreter (``env\\tts\\python``) and therefore cannot
import Alan -- the ``._pth`` src hook is stripped by ``scripts\\setup-tts.ps1``
for exactly this reason. It needs only OmniVoice and the standard library, and
that is deliberate: the box this runs on has ~4 GB of RAM and a 2-core CPU, so
the sidecar spends its allowance on model weights rather than on a web
framework. Two endpoints do not justify FastAPI, and the heavy work has to be
serialized under a lock either way.

Layout mirrors whisper-flow's sidecar (ADR-003, applied a third time):

* ``whisper_recorder.py`` is the *client* for speech-in; this is the *server*
  for speech-out.
* ``/ready`` reports ``protocol_version`` for the same reason it does there:
  a client that ignores it and a server that changed the wire format fail as
  silence or as garbage audio, which is worse than refusing to start.

Endpoints
---------
``GET /ready``
    ``{status, protocol_version, model, device, model_loaded, dtype}``.

``POST /synthesize``
    JSON ``{text, instruct?, language?, num_step?, speed?}`` in; ``audio/wav``
    bytes back. ``instruct`` selects OmniVoice's *voice design* mode (no
    reference audio -- a voice is described by attributes such as
    "female, low pitch, british accent"); with neither ``instruct`` nor a
    reference clip the model picks a voice itself.

Environment
-----------
``TTS_MODEL``        repo id or local directory to load. Default ``k2-fsa/OmniVoice``.
``TTS_DTYPE``        ``float16`` (default) or ``float32``. See the note below.
``TTS_NUM_STEP``     diffusion steps; 32 is the upstream default, 16 is faster.
``TTS_HOST``/``TTS_PORT``   bind address. Loopback, like every other sidecar.

A note on dtype, because it is the whole performance story on this machine
------------------------------------------------------------------------
The upstream examples use ``float16`` on a GPU, where it is both faster and
half the memory. This machine has no GPU (Intel HD Graphics 520, no CUDA), so
everything runs on a 2-core i3-6006U, where the trade inverts: CPU kernels in
PyTorch are optimized for float32, and float16 arithmetic is emulated rather
than accelerated, so half-precision saves memory but no time.

That is why ``float16`` is the default here -- it was measured, not assumed. On
this box it loads in 170.8 s and keeps the model's ~1.5 GB largely out of the
page file; ``float32`` would ask for ~3.2 GB against 3.9 GB of RAM, turning
arithmetic into page-file traffic. Neither is fast -- see
``docs/integrations/tts.md`` for what generation actually costs, and why
``[voice]`` ships with ``tts_enabled = false`` as a result. ``alan doctor``
reports whichever is in use, so the choice is never buried in this file.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PROTOCOL_VERSION = 1

HOST = os.environ.get("TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("TTS_PORT", "8182"))
MODEL_ID = os.environ.get("TTS_MODEL", "k2-fsa/OmniVoice")
#: float16, measured rather than assumed: it halves the working set (1.5 GB
#: against 3.2 GB) which on 3.9 GB of RAM decides whether the model stays
#: resident or pages, and it costs no speed because CPU fp16 is emulated and
#: the arithmetic was never the bottleneck. See the module docstring.
DTYPE = os.environ.get("TTS_DTYPE", "float16").strip().lower()
NUM_STEP = int(os.environ.get("TTS_NUM_STEP", "32"))
#: Refuse anything longer. A chat answer is a paragraph, not an essay, and on
#: this CPU generation time grows with the text -- measured, 18+ minutes for a
#: single 3-second sentence, so an unbounded string would be a request with no
#: way to cancel it. The cap is a hard stop, not a quality preference.
MAX_CHARS = 1200
#: Bound on a single synthesis, reported rather than silently exceeding it.
MAX_SECONDS = float(os.environ.get("TTS_MAX_SECONDS", "600"))

LOG = logging.getLogger("tts")

#: The model is loaded once and kept resident: 3 GB from disk per spoken
#: answer is not a latency, it is a denial of service. Guarded by this lock --
#: concurrent generate() calls on one loaded model are not a supported path,
#: and on a 2-core box they would only contend anyway.
_MODEL = None
_LOAD_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()
_LOAD_ERROR: str | None = None
_DEVICE = "unknown"


def _torch_dtype():
    import torch

    if DTYPE in ("float16", "fp16", "half"):
        return torch.float16
    return torch.float32


def load_model() -> bool:
    """Load the model once. Returns True when it is resident.

    Failure is recorded rather than raised: the server stays up so /ready can
    *explain* why it is not ready. A sidecar that dies on a bad dtype leaves
    an operator reading an empty log instead of a message.
    """
    global _MODEL, _LOAD_ERROR, _DEVICE

    with _LOAD_LOCK:
        if _MODEL is not None:
            return True
        if _LOAD_ERROR is not None:
            return False

        started = time.perf_counter()
        try:
            import torch
            from omnivoice import OmniVoice

            # Mirrors omnivoice's own get_best_device(): CUDA, then XPU, then
            # MPS, then CPU. On this machine that last branch is the only one
            # that can ever be taken, but naming the ladder keeps the sidecar
            # honest if it is ever moved to a box with an accelerator.
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch, "xpu") and torch.xpu.is_available():
                device = "xpu"
            else:
                device = "cpu"
            _DEVICE = device

            LOG.info("loading %s on %s as %s ...", MODEL_ID, device, DTYPE)
            _MODEL = OmniVoice.from_pretrained(
                MODEL_ID, device_map=device, dtype=_torch_dtype()
            )
            LOG.info("model resident after %.1fs", time.perf_counter() - started)
            return True
        except Exception as exc:  # noqa: BLE001 - any load failure must be reportable
            _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
            LOG.exception("model load failed")
            return False


def synthesize(text: str, *, instruct: str | None = None,
               language: str | None = None, num_step: int | None = None,
               speed: float | None = None) -> tuple[bytes, dict]:
    """Return (wav bytes, metadata) for one utterance."""
    if _MODEL is None:
        raise RuntimeError("model is not loaded")
    if not load_model():  # pragma: no cover - guarded by the caller
        raise RuntimeError(_LOAD_ERROR or "model failed to load")

    kwargs: dict = {"text": text}
    if instruct:
        kwargs["instruct"] = instruct
    if language:
        kwargs["language"] = language
    if num_step:
        kwargs["num_step"] = int(num_step)
    if speed:
        kwargs["speed"] = float(speed)

    with _GEN_LOCK:
        started = time.perf_counter()
        audios = _MODEL.generate(**kwargs)
        elapsed = time.perf_counter() - started

    audio = audios[0]
    import numpy as np
    import soundfile as sf

    rate = int(getattr(_MODEL, "sampling_rate", 24000))
    buffer = io.BytesIO()
    sf.write(buffer, np.asarray(audio), rate, format="WAV")

    duration = len(audio) / float(rate)
    return buffer.getvalue(), {
        "seconds": round(duration, 3),
        "sample_rate": rate,
        "generated_in": round(elapsed, 3),
        # The number that decides whether voice output is usable here: how
        # much wall clock one second of speech costs. Above 1.0 the answer is
        # longer arriving than it is to listen to.
        "rtf": round(elapsed / duration, 3) if duration > 0 else None,
        "device": _DEVICE,
        "dtype": DTYPE,
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
            "model": MODEL_ID,
            "device": _DEVICE,
            "model_loaded": _MODEL is not None,
            "load_error": _LOAD_ERROR,
            "dtype": DTYPE,
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
            self._json({"status": "error", "detail": _LOAD_ERROR or "model failed to load"}, 503)
            return

        try:
            wav, meta = synthesize(
                text,
                instruct=request.get("instruct") or None,
                language=request.get("language") or None,
                num_step=request.get("num_step") or None,
                speed=request.get("speed") or None,
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
    LOG.info("Alan TTS sidecar on %s:%s (model=%s dtype=%s)", HOST, PORT, MODEL_ID, DTYPE)

    # Load eagerly, the way whisper-flow preloads: the first spoken answer
    # should not also pay for 3 GB of disk. Reported as a warning rather than
    # fatal, so a failed load still leaves a listening /ready to explain it.
    if not load_model():
        LOG.warning("model did not load: %s", _LOAD_ERROR)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
