"""Voice input: talk to Alan instead of typing.

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

Layout, following the browser sidecar:

* ``whisper_recorder.py`` lives beside ``src\\alan`` (not inside it) because it
  is executed by the sidecar interpreter, which deliberately cannot import Alan.
* this module holds Alan's own logic -- is the sidecar up, is the client
  installed, run it and collect the transcript -- and is unit-testable without
  a microphone, a model or a running server.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, Sequence
from urllib.error import URLError
from urllib.request import urlopen

from .config import HOME, VoiceSettings

#: The pinned sidecar source (git-ignored; restored by a clone + setup).
VENDOR = HOME / "vendor" / "whisper-flow"
#: The sidecar interpreter. Voice needs it; nothing else in Alan does.
RECORDER_PY = HOME / "env" / "whisper" / "python" / "python.exe"
#: The push-to-talk client. Standalone on purpose: see this module's docstring.
RECORDER = HOME / "src" / "whisper_recorder.py"
#: The wire protocol the client speaks (docs/protocol.md in the vendor clone).
PROTOCOL_VERSION = 1


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


def say(settings: VoiceSettings, text: str) -> None:
    """(Reserved for TTS.) Kept so callers have one obvious place to add it.

    Speaking answers back is deliberately not wired: Alan's replies are read in
    a terminal, and a voice channel that only half works -- hears fine, mumbles
    the answer -- is worse than none. This is where it goes when it does.
    """
    raise NotImplementedError(
        f"speech output is not implemented; asked to say {text!r} via {settings!r}"
    )


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
