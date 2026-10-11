"""Voice: talk to Alan instead of typing, and hear the answer back.

Alan does not transcribe anything itself. [whisper-flow](https://github.com/
dimastatz/whisper-flow) runs as a sidecar in its own environment
(``env\\whisper\\python``, like cognee and browser -- ADR-003's rule, applied
again), and the push-to-talk client in ``src\\whisper_recorder.py`` runs under
*that* interpreter because PyAudio and the websocket client have no business in
the main environment.

The transcript comes back on stdout and is handed to exactly the pipeline a
typed ``ask`` uses, so a spoken request gets the same memory, the same
permission gate and the same verified reporting as a typed one. Voice raises no
capability: the transcript is plain text, and an agent still cannot approve
itself.

Speaking is the mirror image and the same rule. [OmniVoice](https://github.com/
k2-fsa/OmniVoice) serves from ``env\\tts\\python`` on 8182 through
``src\\tts_server.py``; ``say()`` posts the reply and plays what comes back.
The two halves have separate switches because they cost differently -- whisper
is a resident 150 MB, OmniVoice is ~2.5 GB -- so an operator on a 4 GB machine
can keep the microphone and give up the voice.

Nothing here may break a turn. Speech output is decoration over an answer that
has already been rendered, so ``say()`` returns a reason rather than raising:
a sidecar that is down costs the spoken reply and never the answer itself.

Layout, following the browser sidecar:

* ``whisper_recorder.py`` lives beside ``src\\alan`` (not inside it) because it
  is executed by the sidecar interpreter, which deliberately cannot import Alan.
* ``tts_server.py`` is the same shape in the other direction: a server, run by
  the TTS interpreter, that needs OmniVoice and the standard library and
  nothing from Alan.
* this module holds Alan's own logic -- is the sidecar up, is the client
  installed, run it and collect the transcript, post the reply and play it --
  and is unit-testable without a microphone, a model or a running server.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import HOME, VoiceSettings

#: The pinned sidecar source (git-ignored; restored by a clone + setup).
VENDOR = HOME / "vendor" / "whisper-flow"
#: The sidecar interpreter. Voice needs it; nothing else in Alan does.
RECORDER_PY = HOME / "env" / "whisper" / "python" / "python.exe"
#: The push-to-talk client. Standalone on purpose: see this module's docstring.
RECORDER = HOME / "src" / "whisper_recorder.py"
#: The wire protocol the client speaks (docs/protocol.md in the vendor clone).
PROTOCOL_VERSION = 1
#: TTS has its own version counter. Sharing one with the microphone would mean
#: a bump for either half silently invalidated the other's compatibility check.
TTS_PROTOCOL_VERSION = 1
#: The pinned TTS source (git-ignored, same rule as VENDOR above).
TTS_VENDOR = HOME / "vendor" / "OmniVoice"
#: The TTS sidecar interpreter, and the server it runs. Neither can import
#: Alan -- the ``._pth`` src hook is stripped at setup -- so both are executed
#: as files rather than imported as modules.
TTS_PY = HOME / "env" / "tts" / "python" / "python.exe"
TTS_SERVER = HOME / "src" / "tts_server.py"


def ws_url(settings: VoiceSettings) -> str:
    """/ws for the configured sidecar address."""
    return settings.base_url.replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1
    ) + "/ws"


def ready(settings: VoiceSettings) -> tuple[bool, str]:
    """Ask the sidecar whether it is up and speaks the protocol we speak.

    ``/ready`` reports ``protocol_version``; a client that ignores it and a
    server that changed the wire format fail in ways that look like silence or
    like garbage text, which is far worse than refusing to start.
    """
    url = settings.base_url.rstrip("/") + "/ready"
    try:
        with urlopen(url, timeout=settings.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"not reachable at {url} ({reason})"

    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        return False, (
            f"speaks protocol v{version}, Alan speaks v{PROTOCOL_VERSION} "
            "-- update the older side"
        )
    models = payload.get("models") or []
    if settings.model and models and settings.model not in models:
        return False, f"model {settings.model!r} not loaded; the sidecar has {models}"
    return True, f"ready on {url} (whisper {settings.model}, protocol v{version})"


def recorder_available() -> tuple[bool, str]:
    """Is the push-to-talk client installed? Checked before opening the mic."""
    if not RECORDER_PY.is_file():
        return False, (
            f"the sidecar interpreter is missing ({RECORDER_PY}) - "
            "run scripts\\setup-whisper.ps1"
        )
    if not RECORDER.is_file():
        return False, f"the recorder is missing ({RECORDER})"
    if not VENDOR.is_dir():
        return False, (
            f"the pinned source is missing ({VENDOR}) - clone it with "
            "git clone https://github.com/dimastatz/whisper-flow.git vendor\\whisper-flow"
        )
    return True, "installed"


def recorder_command(settings: VoiceSettings) -> list[str]:
    """argv for the push-to-talk client, built from settings.

    Assembled here rather than in PowerShell so the settings file is the single
    place a voice request is shaped, and so tests can assert the exact command
    without a microphone.
    """
    command = [
        str(RECORDER_PY),
        str(RECORDER),
        "--url", ws_url(settings),
        "--model", settings.model,
        "--silence-ms", str(settings.silence_ms),
        "--max-seconds", str(settings.max_seconds),
        "--threshold", str(settings.threshold),
    ]
    # Only sent when set: the protocol spells auto-detect as the *absence* of
    # the language field, and "" is rejected as an unknown language. So an
    # empty setting must not become "--language ''".
    if settings.language:
        command += ["--language", settings.language]
    if settings.prompt:
        command += ["--prompt", settings.prompt]
    if settings.api_key:
        command += ["--api-key", settings.api_key]
    return command


def listen(
    settings: VoiceSettings,
    *,
    runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    max_seconds: float | None = None,
) -> str:
    """Record one utterance and return the transcript ("" when nothing was said).

    Partials go to the terminal as the operator speaks -- the client writes
    them to stderr, which stays attached to the console -- while only the final
    transcript is captured from stdout. The process is bounded: the client
    stops itself on silence, and this wait is bounded again in case a wedged
    socket never gets there.
    """
    budget = max_seconds if max_seconds is not None else settings.max_seconds
    try:
        result = runner(
            recorder_command(settings),
            capture_output=True,
            text=True,
            # Client start + model already loaded + the utterance itself, with
            # room for a slow first transcription.
            timeout=budget + 60.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"voice capture timed out after {budget + 60.0:.0f}s - the sidecar "
            "may still be loading its model; see data\\logs\\whisper"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"could not run the voice client: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {result.returncode}"
        raise RuntimeError(f"voice capture failed: {tail}")
    return (result.stdout or "").strip()


def tts_ready(settings: VoiceSettings) -> tuple[bool, str]:
    """Is the *speech output* sidecar up? Kept apart from :func:`ready`, which
    is the microphone's.

    Same reason for checking ``protocol_version`` here: a client that ignores
    it and a server that changed the wire format fail as silence or as a
    screech of malformed audio, which is worse than a refusal to start.
    """
    url = settings.tts_base_url.rstrip("/") + "/ready"
    try:
        with urlopen(url, timeout=settings.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"not reachable at {url} ({reason})"

    version = payload.get("protocol_version")
    if version != TTS_PROTOCOL_VERSION:
        return False, (
            f"speaks protocol v{version}, Alan speaks v{TTS_PROTOCOL_VERSION} "
            "-- update the older side"
        )
    # Up but broken is a different state from not up, and the distinction is
    # the whole reason /ready reports load_error instead of dying: one says
    # "start it", the other says "look at the log".
    if payload.get("load_error"):
        return False, f"model failed to load: {payload['load_error']}"

    loaded = "resident" if payload.get("model_loaded") else "not loaded yet"
    return True, (
        f"ready on {url} (model {payload.get('model')}, {payload.get('dtype')}, "
        f"{loaded}, protocol v{version})"
    )


#: Block-then-inline code, headers, list markers, links, emphasis and tables --
#: the syntax a model writes and a speaker cannot say. Ordered deliberately:
#: fences first so their contents are never rewritten by a later rule, list
#: markers before emphasis so ``* item`` is not mistaken for italics, and
#: emphasis before links so ``**[x](y)**`` unwraps in the right direction.
#:
#: ``MULTILINE`` anchors ``^``/``$`` to line starts, which is what makes the
#: heading and list rules behave; ``DOTALL`` is what lets the fence rule
#: actually span a multi-line block. Each pattern is applied to prose that
#: merely *contains* markup, so every rule is anchored or character-classed to
#: leave ordinary punctuation alone.
_MD_SPEECH_RULES: tuple[tuple[str, str], ...] = (
    (r"```.*?```", " "),                          # fenced code, spans lines
    (r"`([^`]*)`", r"\1"),                        # inline code
    (r"^#{1,6}\s*", ""),                          # ATX heading marks
    (r"^\s*[-*+]\s+", "  "),                      # bullet -> a breath
    (r"^\s*\d+\.\s+", "  "),                      # numbered -> a breath
    (r"^\s*[|>]\s*", ""),                         # table/quote gutters
    (r"\*\*\*([^*]+)\*\*\*", r"\1"),
    (r"\*\*([^*]+)\*\*", r"\1"),
    (r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1"),
    (r"__([^_]+)__", r"\1"),
    (r"\[([^\]]+)\]\([^)]*\)", r"\1"),            # [text](url) -> text
    (r"\|", " "),                                 # table cells
    (r"^---+$", " "),                             # horizontal rules
)
_MD_FLAGS = re.MULTILINE | re.DOTALL


def for_speech(text: str) -> str:
    """Strip markdown so the reply can be *said* rather than recited.

    Reads back to a listener as the answer, not as "hash hash heading asterisk
    asterisk". Exposed rather than folded into ``say()`` because it is the
    fiddly half and belongs under its own tests: synthesis is one HTTP call,
    this is the part that can quietly ruin every spoken reply.
    """
    cleaned = text or ""
    for pattern, replacement in _MD_SPEECH_RULES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=_MD_FLAGS)
    # Collapse the runs of whitespace the rules above leave behind, and cap
    # what is sent: the sidecar refuses over MAX_CHARS anyway, and sending a
    # known-too-long string wastes a round trip to learn what the cap is.
    return re.sub(r"\s+", " ", cleaned).strip()[:1200]


def _synthesize(settings: VoiceSettings, text: str) -> bytes:
    """POST the text to the sidecar and return the WAV bytes."""
    url = settings.tts_base_url.rstrip("/") + "/synthesize"
    body = json.dumps({"text": text, "instruct": settings.tts_instruct or None})
    request = Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=settings.tts_timeout) as response:
        return response.read()


def _play(wav: bytes) -> None:
    """Write the WAV to a scratch file and play it through the default device.

    A file rather than ``SND_MEMORY``: winsound's in-memory path is fussy about
    buffer lifetime across Python versions, and a one-line scratch file works
    on all of them. Deleted after, so a session of replies leaves no litter.
    """
    import winsound  # Windows-only, imported lazily: a non-Windows run that
                     # only checks readiness must not fail to import here.

    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = Path(handle.name)
    try:
        handle.write(wav)
        handle.close()
        # SND_SYNC, not SND_ASYNC: the caller's turn should not report success
        # while the sentence it is announcing has not finished being said.
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_SYNC)
    finally:
        path.unlink(missing_ok=True)


def say(
    settings: VoiceSettings,
    text: str,
    *,
    synthesizer: Callable[[VoiceSettings, str], bytes] | None = None,
    player: Callable[[bytes], None] | None = None,
) -> tuple[bool, str]:
    """Speak one reply. Returns ``(spoken, note)`` and **never raises**.

    Never raising is the contract, not a convenience. Speech output sits after
    an answer that has already been rendered to the terminal, so the only thing
    a failure can usefully do is cost the sound -- and an exception here would
    take down a completed turn to report that a speaker was unavailable.

    ``synthesizer`` and ``player`` are injectable so tests exercise the logic
    (the disabled case, the empty case, the failure path) without a sidecar, a
    model or a sound card.
    """
    if not settings.tts_enabled:
        return False, "speech output is off - \\[voice] tts_enabled = true turns it on"

    spoken = for_speech(text)
    if not spoken:
        return False, "nothing to say"

    synthesize = synthesizer or _synthesize
    play = player or _play

    try:
        wav = synthesize(settings, spoken)
    except (URLError, OSError, ValueError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, (
            f"synthesis failed at {settings.tts_base_url} ({reason}) - "
            "start it with scripts\\tts-serve.ps1; the answer above stands"
        )
    if not wav:
        return False, "sidecar returned no audio"

    if not settings.tts_play:
        # Synthesized but deliberately not played: a health check that proves
        # the whole path works on a box with no speaker, without needing one.
        return True, f"synthesized {len(wav)} bytes (\\[voice] tts_play = false)"

    try:
        play(wav)
    except (OSError, RuntimeError, ImportError) as exc:
        return False, f"audio came back but could not be played: {exc}"
    return True, f"said {len(wav)} bytes"


def describe_speech(settings: VoiceSettings) -> dict:
    """One row for ``alan doctor``: the output half of voice, if it is on."""
    if not settings.tts_enabled:
        return {
            "state": "off",
            "enabled": False,
            "reachable": False,
            "note": "disabled in settings",
        }

    reachable, note = tts_ready(settings)
    if reachable:
        state, text = "ready", note
    else:
        # Enabled but not answering names the command that fixes it: a bare
        # port number sends the reader hunting through scripts.
        state = "configured"
        text = f"{note} - start it with scripts\\tts-serve.ps1"
    return {
        "state": state,
        "enabled": True,
        "reachable": reachable,
        "note": text,
    }


def describe(settings: VoiceSettings) -> dict:
    """One row for ``alan doctor``: configured, reachable, client installed."""
    if not settings.enabled:
        return {
            "state": "off",
            "enabled": False,
            "reachable": False,
            "client": False,
            "note": "disabled in settings",
        }

    reachable, note = ready(settings)
    client_ok, client_note = recorder_available()

    if reachable and client_ok:
        state, text = "ready", f"{note}; client installed"
    else:
        # Enabled but not working is a configuration state worth surfacing,
        # and the note has to name the fix -- a port on its own sends the
        # reader hunting.
        state = "configured"
        parts = [note] if reachable else [
            f"{note} - start it with scripts\\whisper-serve.ps1"
        ]
        if not client_ok:
            parts.append(f"client: {client_note}")
        elif reachable:
            parts.append("client installed")
        text = "; ".join(parts)

    return {
        "state": state,
        "enabled": True,
        "reachable": reachable,
        "client": client_ok,
        "note": text,
    }


def build_argv_for_tests(settings: VoiceSettings) -> Sequence[str]:
    """Alias kept deliberately thin: tests assert against ``recorder_command``."""
    return recorder_command(settings)
