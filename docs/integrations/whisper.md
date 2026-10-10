# Voice input (whisper-flow)

Alan can be asked by ear. Say `scripts\run.ps1 voice`, speak a question, and the
answer comes back through the same pipeline a typed `ask` would use.

The transcriber is [whisper-flow](https://github.com/dimastatz/whisper-flow) —
a real-time transcription service built on OpenAI Whisper — pinned and run
locally. **Nothing is sent anywhere**: the model is the bundled `tiny.en`
(~72 MB), the server binds loopback only, and there is no cloud key in the
path.

## Layout

| Piece | Where | Why there |
|---|---|---|
| Service source | `vendor\whisper-flow` (git-ignored, pinned) | Same rule as `vendor\ECC`: a third-party tree is read, not merged |
| Environment | `env\whisper\python` | torch + PyAudio + openai-whisper stay out of the main env; the `._pth` has Alan's `src` stripped, so it cannot `import alan` |
| Server script | `scripts\whisper-serve.ps1` | start / `-Stop` / `-Status`, like `cognee-serve.ps1` |
| Setup script | `scripts\setup-whisper.ps1` | builds the sidecar env (CPU-only torch, then the pinned source) |
| Push-to-talk client | `src\whisper_recorder.py` | standalone, because the *sidecar* interpreter runs it |
| Alan glue | `src\alan/voice.py`, the `voice` command in `cli.py` | readiness, command shape, transcript handoff |

Ports: **8181** here · cognee 8012 · browser 8022 · Ollama 11435. No overlap.

## Setup (once)

```powershell
git clone https://github.com/dimastatz/whisper-flow.git vendor\whisper-flow
scripts\setup-whisper.ps1        # builds env\whisper\python (~10 min, CPU torch)
scripts\pull-stt-model.ps1       # downloads an STT model ('small' by default)
```

`pull-stt-model.ps1` uses Whisper's own downloader (hash-verified) and writes
into `vendor\whisper-flow\whisperflow\models`, which is git-ignored with the
rest of `vendor\` — models stay on the machine, never in the repo. `tiny.en`
is already there (it ships inside the package).

Then turn it on in `config\settings.toml` (`[voice] enabled = true` — off in
code, on in this install, like ECC).

## Use

```powershell
scripts\run.ps1 voice                       # starts the sidecar, then listens
scripts\run.ps1 voice --transcribe-only     # transcript only; do not ask
scripts\run.ps1 voice --prompt "getUserById getId"   # bias spelling this turn
```

Recording is push-to-talk in one breath: speak, then pause. Partials appear as
you go; the turn ends after `[voice] silence_ms` of quiet (default 1.2 s), or
at `[voice] max_seconds` (default 60 s) no matter what.

Lifecycle, if you want it without a chat:

```powershell
scripts\whisper-serve.ps1          # start (no-op if already up)
scripts\whisper-serve.ps1 -Status  # UP / DOWN
scripts\whisper-serve.ps1 -Stop
```

`ALAN_NO_VOICE=1` stops `run.ps1` from auto-starting it, the way
`ALAN_NO_MEMORY=1` does for cognee.

## Models

whisper-flow serves any `*.pt` in `vendor\whisper-flow\whisperflow\models`
(`GET /ready` lists them); `[voice] model` picks one, and the sidecar preloads
that one at startup. Both measured on this machine (4 GB RAM, CPU-only):

| Model | On disk | Languages | RAM once loaded | 4 s of speech |
|---|---|---|---|---|
| `tiny.en.pt` | 72 MB | English only | ~150 MB | 3.3 s |
| `small.pt` | 461 MB | 99 (auto-detects) | ~930 MB | 12.8 s |

`small` is what this install uses — noticeably better on names and noise, at
about 4× the transcription time and a **70 s one-time load when the sidecar
starts** (this box has 3.9 GB of RAM total; expect ~380 MB left after both are
loaded). If that is too slow, `scripts\pull-stt-model.ps1 base` is the middle
ground; `-List` shows what is installed.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` in code, `true` here | `alan voice` refuses without it |
| `base_url` | `http://127.0.0.1:8181` | loopback only |
| `model` | `tiny.en.pt` (code) / `small.pt` (here) | must be in `GET /ready`'s `models`; the sidecar preloads this one at startup |
| `language` | empty | empty = auto-detect (needs a multilingual model); set `en`, `hi`, ... to force |
| `prompt` | empty | vocabulary hint (Whisper's `initial_prompt`) |
| `silence_ms` | `1200` | end of a turn, once speech has been heard (clamped 200–10000) |
| `max_seconds` | `60.0` | hard stop (clamped 5–600) |
| `threshold` | `500` | peak PCM amplitude counting as speech; raise it in a noisy room |

## How it fits Alan's rules

- **The transcript is plain text and enters the ordinary pipeline** — same
  memory, same permission gate, same verified reporting. Voice grants nothing.
- **A protocol mismatch refuses to record.** `/ready` reports
  `protocol_version`; the client speaks v1 and says so rather than failing
  later as silence.
- **Partials never become the answer.** Whisper-flow's partials replace each
  other and carry committed prefixes, so the transcript is assembled from
  finals only.
- **The mic is opened last.** Sidecar reachability and client installation are
  checked first, so "not started" never reads as "no microphone".

## Troubleshooting

| Symptom | Fix |
|---|---|
| `whisper sidecar not reachable` | `scripts\whisper-serve.ps1` — or use `run.ps1 voice`, which starts it |
| First start is slow | By design: the sidecar preloads `[voice] model` before it reports ready, so the cold load (~70 s for `small` on this box) happens once per start, not on your first question. `-Status` reports honestly rather than guessing |
| `voice client ... setup-whisper.ps1` | The sidecar env is missing; run the setup script |
| `no microphone` | Windows has no default input device, or the app lacks microphone permission |
| Records but hears nothing | The mic is not picking up the room. Raise/lower `[voice] threshold`, or check the Windows input level for the device |
| Slow or laggy partials | `small` is ~4× slower than `tiny.en` on this machine (measured: 12.8 s to transcribe 4 s of speech vs 3.3 s). Trade accuracy for speed with `base`, or accept the delay — push-to-talk records first, so it costs wait, not words |
| Gibberish or half sentences | Pull a larger model (`scripts\pull-stt-model.ps1 medium`), point `[voice] model` at it and restart the sidecar; a `[voice] prompt` with your names and jargon also helps |
| Wrong language | Auto-detect chose badly on a short phrase: set `[voice] language` (`en`, `hi`, ...) and restart. A `.en` model ignores language entirely — it cannot do other languages at all |

## Verified

On this machine (2026-10-10), against the running sidecar:

- **Transcription is correct.** Windows SAPI speech ("Hello Alan. What time is
  it in Tokyo?") converted to 16 kHz mono int16 and posted to
  `/transcribe_pcm_chunk` came back as exactly that sentence.
- **The STT model is correct on both models.** The same SAPI clip posted to
  `/transcribe_pcm_chunk` came back as exactly `"Hello Alan. What time is it in
  Tokyo?"` under `tiny.en.pt` *and* `small.pt` — and the multilingual model
  auto-detected `language: "en"` with no language hint given.
- **The microphone path runs.** The client opened the default input device,
  streamed for the configured window, endpointed and exited cleanly; a silent
  room yields an empty transcript and `heard nothing`, never a hang.
- **The pipeline runs.** `alan voice --transcribe-only` went from settings to
  readiness probe to recording to exit 0 with no side effects.

What has *not* been verified here is a human voice in this room: played-back
speech was not audible to this machine's microphone (idle noise measured at
peak 2488 / rms 16, so the capture chain is live — the capsule simply does not
hear the speakers). Speak into it once and watch the partials; that is the
test that matters.

## Not built (yet)

Speech *out* — `alan.voice.say()` is a deliberate `NotImplementedError` in
`src\alan/voice.py`, marking the one obvious extension: an answer channel that
only half works is worse than none.
