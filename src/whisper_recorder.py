"""Push-to-talk microphone client for the whisper-flow sidecar.

This runs under the *sidecar* interpreter (``env\\whisper\\python``), never
Alan's: it needs PyAudio and the websocket client, and neither belongs in the
main environment. That is why it is a standalone script rather than a module
inside ``alan`` -- the sidecar environments deliberately cannot import Alan
(see ``scripts\\setup-cognee.ps1`` for the ``._pth`` stripping), and a script
with stdlib-only imports at module scope keeps that boundary honest.

Wire protocol (``docs/protocol.md`` in ``vendor\\whisper-flow``): binary frames
carry 16 kHz mono int16 PCM, text frames are JSON control messages
(``start`` / ``flush`` / ``stop``). Results arrive as
``{"is_partial": bool, "data": {"text": ...}, "time": ms}``.

**Partials are not monotonic** -- each one replaces the previous one for the
same segment, and committed words are prepended to every later result. So the
transcript is assembled from finals only, never by appending partials.

Output contract (what ``alan voice`` relies on):

* stdout: the final transcript, and nothing else (may be empty)
* stderr: live partials as you speak, so the operator sees their words land
* exit 0 on success, including "heard nothing"
  exit 2 when the sidecar is unreachable or speaks another protocol version
  exit 3 when there is no usable microphone
"""

from __future__ import annotations

import argparse
import array
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, Optional

SAMPLE_RATE = 16000
CHUNK_SIZE = 1024          # 64 ms per frame at 16 kHz
PROTOCOL_VERSION = 1

EXIT_OK = 0
EXIT_SIDECAR = 2
EXIT_MIC = 3


# --------------------------------------------------------------------------
# pure helpers (importable without PyAudio, numpy or a websocket client)
# --------------------------------------------------------------------------

def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    """Command-line options. Every one of them has a server-side default too."""
    parser = argparse.ArgumentParser(
        prog="whisper_recorder",
        description="Stream the microphone to the whisper-flow sidecar and "
                    "print the final transcript on stdout.",
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8181/ws", help="sidecar websocket endpoint")
    parser.add_argument("--ready", default="", help="sidecar /ready URL (default: derived from --url)")
    parser.add_argument("--model", default="tiny.en.pt", help="model name from GET /ready")
    parser.add_argument("--language", default="", help="whisper language code (en, hi, ...); empty = auto-detect, which needs a multilingual model")
    parser.add_argument("--prompt", default="", help="vocabulary hint (whisper initial_prompt)")
    parser.add_argument("--api-key", default="", help="WF_API_KEY, when the sidecar sets one")
    parser.add_argument("--silence-ms", type=int, default=1200, help="stop after this much quiet, once speech started")
    parser.add_argument("--max-seconds", type=float, default=60.0, help="hard stop, whatever the operator does")
    parser.add_argument("--threshold", type=int, default=500, help="peak amplitude that counts as speech")
    parser.add_argument("--device", type=int, default=-1, help="PyAudio input device index (-1 = default)")
    return parser.parse_args(list(argv) if argv is not None else None)


def ready_url(ws_url: str, explicit: str = "") -> str:
    """/ready for a /ws endpoint, unless one was given explicitly.

    ``ws://host:port/ws`` -> ``http://host:port/ready``.
    """
    if explicit:
        return explicit
    http = "http://" if ws_url.startswith("ws://") else "https://"
    return http + ws_url.split("://", 1)[1].rsplit("/", 1)[0] + "/ready"


def peak(data: bytes) -> int:
    """Largest absolute int16 sample in a PCM frame -- the cheap voice check.

    ``array`` rather than numpy: this runs once per 64 ms frame in a loop that
    must never be the thing that drops audio.
    """
    if not data:
        return 0
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0
    return max(abs(value) for value in samples)


class SilenceDetector:
    """Stop conditions for a push-to-talk turn: speak, then pause.

    Recording ends only once speech has actually been seen -- a quiet room must
    not end the turn before the operator says anything -- and then once the
    audio stays below the threshold for ``silence_ms``. ``max_seconds`` is the
    backstop for a room that never goes quiet.
    """

    def __init__(self, threshold: int, silence_ms: int, max_seconds: float) -> None:
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.max_seconds = max_seconds
        self.speech_seen = False
        self.silent_ms = 0.0
        self.started = time.monotonic()

    def feed(self, frame_peak: int, frame_ms: float) -> bool:
        """Feed one frame; True means stop recording."""
        if frame_peak >= self.threshold:
            self.speech_seen = True
            self.silent_ms = 0.0
        elif self.speech_seen:
            self.silent_ms += frame_ms
        if self.speech_seen and self.silent_ms >= self.silence_ms:
            return True
        return (time.monotonic() - self.started) >= self.max_seconds

    @property
    def elapsed(self) -> float:
        """Seconds since the first frame."""
        return time.monotonic() - self.started


def final_texts(messages: Iterable[dict]) -> str:
    """Assemble the transcript from final results only.

    Partials replace each other and carry committed prefixes, so appending them
    would produce "Reality Reality is created ...". Finals are disjoint: each
    one is the complete text of one segment.
    """
    finals: list[str] = []
    for message in messages:
        if message.get("is_partial"):
            continue
        text = str((message.get("data") or {}).get("text", "")).strip()
        if text:
            finals.append(text)
    return " ".join(finals)


def start_message(model: str, language: str = "", prompt: str = "") -> dict:
    """The ``start`` frame: every field optional, and absent means absent.

    The protocol has no empty-string language -- auto-detect is spelled by
    *omitting* the field, and the server rejects ``""`` outright as an unknown
    language. So an empty language and an empty vocabulary hint are both
    dropped rather than sent as empty values.
    """
    message: dict[str, Any] = {"type": "start", "model": model}
    if language:
        message["language"] = language
    if prompt:
        message["prompt"] = prompt
    return message


def check_ready(ready_url_: str, timeout: float = 3.0) -> dict:
    """GET /ready. Raises OSError with a readable reason when unreachable."""
    with urllib.request.urlopen(ready_url_, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise OSError(
            f"the sidecar speaks protocol v{version}, this client speaks "
            f"v{PROTOCOL_VERSION} -- update whichever is older"
        )
    return payload


# --------------------------------------------------------------------------
# the parts that need a microphone
# --------------------------------------------------------------------------

def _open_stream(device: int):
    """Open the mic: 16 kHz, mono, int16. Raises OSError when there is none."""
    try:
        import pyaudio  # noqa: PLC0415 -- deliberately late
    except ImportError as exc:  # pragma: no cover - environment problem
        raise OSError(f"PyAudio is not installed in this interpreter: {exc}") from exc

    audio = pyaudio.PyAudio()
    index = device if device >= 0 else audio.get_default_input_device_info()["index"]
    try:
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=index,
            frames_per_buffer=CHUNK_SIZE,
        )
    except OSError:
        audio.terminate()
        raise
    return audio, stream, index


def _reader(ws, results: "queue.Queue", stop: threading.Event) -> None:
    """Drain the socket on a thread.

    ``recv`` blocks, so reading it on the recording thread would stop the
    microphone; partials would then arrive in bursts and the operator would
    watch their words lag. Close is initiated by the main thread, which makes
    this thread's blocked ``recv`` raise -- that is the loop's exit, not a bug.
    """
    while not stop.is_set():
        try:
            message = ws.recv()
        except Exception:  # socket closed, or closing
            break
        if message is None:
            break
        if isinstance(message, str):
            try:
                results.put(json.loads(message))
            except ValueError:
                continue
    stop.set()


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    # Fail before opening the microphone if there is nothing to talk to: a
    # sidecar that is merely not started must not be reported as "no mic".
    try:
        payload = check_ready(ready_url(args.url, args.ready))
    except (OSError, ValueError) as exc:
        print(f"whisper sidecar unreachable: {exc}", file=sys.stderr)
        return EXIT_SIDECAR

    models = payload.get("models") or []
    if args.model and models and args.model not in models:
        print(
            f"model {args.model!r} is not loaded; this sidecar has {models}",
            file=sys.stderr,
        )
        return EXIT_SIDECAR

    try:
        import websocket  # noqa: PLC0415 -- deliberately late
    except ImportError as exc:  # pragma: no cover - environment problem
        print(f"websocket-client is not installed in this interpreter: {exc}", file=sys.stderr)
        return EXIT_SIDECAR

    try:
        audio, stream, device_index = _open_stream(args.device)
    except OSError as exc:
        print(f"no microphone: {exc}", file=sys.stderr)
        return EXIT_MIC

    frame_ms = 1000.0 * CHUNK_SIZE / SAMPLE_RATE
    detector = SilenceDetector(args.threshold, args.silence_ms, args.max_seconds)
    results: "queue.Queue[dict]" = queue.Queue()
    stop = threading.Event()

    header = [f"X-Api-Key: {args.api_key}"] if args.api_key else []
    print(f"listening on device {device_index} ...", file=sys.stderr)
    try:
        ws = websocket.create_connection(args.url, timeout=10, header=header)
    except Exception as exc:
        stream.close()
        audio.terminate()
        print(f"cannot open {args.url}: {exc}", file=sys.stderr)
        return EXIT_SIDECAR

    reader = threading.Thread(target=_reader, args=(ws, results, stop), daemon=True)
    reader.start()

    finals: list[dict] = []
    last_partial = ""
    try:
        ws.send(json.dumps(start_message(args.model, args.language, args.prompt)))

        # One read per loop: feeding the detector a frame and then reading a
        # *different* frame to send would halve what the detector sees, making
        # the silence timeout take twice as long as it says.
        while True:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            if detector.feed(peak(data), frame_ms):
                break
            ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
            # Partials are the only feedback the operator gets while speaking;
            # reprint the line in place rather than growing a log.
            drained = False
            while True:
                try:
                    message = results.get_nowait()
                except queue.Empty:
                    break
                drained = True
                if message.get("is_partial"):
                    text = str((message.get("data") or {}).get("text", "")).strip()
                    if text != last_partial:
                        last_partial = text
                        print(f"\r  {text:<60}", end="", file=sys.stderr, flush=True)
                else:
                    finals.append(message)
            if drained:
                print(file=sys.stderr)

        # Flush what is queued, then close the session the way the protocol
        # asks. `stop` is what makes the server finalise the last segment.
        ws.send(json.dumps({"type": "flush"}))
        ws.send(json.dumps({"type": "stop"}))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                message = results.get(timeout=0.2)
            except queue.Empty:
                if stop.is_set():
                    break
                continue
            if message.get("is_partial") is False:
                finals.append(message)
    except KeyboardInterrupt:  # operator aborted mid-phrase
        print("\naborted.", file=sys.stderr)
    finally:
        stop.set()
        try:
            ws.close()
        except Exception:
            pass
        reader.join(timeout=1.0)
        stream.close()
        audio.terminate()

    transcript = final_texts(finals)
    if transcript:
        print(transcript)
    elif not detector.speech_seen:
        print("heard nothing (no speech above the silence threshold)", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - needs a microphone
    sys.exit(main())
