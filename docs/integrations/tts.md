# Voice output (Piper, with OmniVoice for a GPU)

Alan can answer out loud. Speech *in* is [whisper.md](whisper.md); this is the
other direction of the same channel — the reply goes back through the speaker
instead of only the screen.

Two engines answer the same port, and the choice is a flag on the serve script:

- **[Piper](https://github.com/rhasspy/piper) (MIT) — the default here.** A VITS
  voice is a few tens of MB of ONNX and synthesizes **faster than real time** on
  this CPU (RTF ~0.2, measured below), which is what makes voice output usable on
  a 4 GB / 2-core box. It has no voice design: a voice is a released artifact,
  chosen by name at start-up.
- **[OmniVoice](https://github.com/k2-fsa/OmniVoice) (k2-fsa, Apache-2.0)** — a
  613M-parameter diffusion model based on Qwen3-0.6B with *voice design*: you
  describe how Alan should sound rather than picking a catalogue voice. Higher
  ceiling, but measured RTF > 356 here (~18 minutes a sentence), so it is kept
  for a machine with a GPU.

Either way it runs locally. **No reply text leaves the machine**, for the same
reason speech in doesn't: the engine is on disk under `data\tts\`, the server
binds loopback only, and there is no key anywhere in the path.

## Layout

| Piece | Where | Why there |
|---|---|---|
| Piper server | `src\tts_server_piper.py` | **Alan's own**; stdlib + `piper`, same protocol as the OmniVoice server |
| OmniVoice server | `src\tts_server.py` | **Alan's own**, not OmniVoice's: upstream ships a Gradio demo and a CLI, neither of which is an API a client can call |
| Piper voices | `data\tts\piper\<voice>.onnx` (git-ignored) | ~20–60 MB each, chosen by name at start-up |
| OmniVoice source | `vendor\OmniVoice` (git-ignored) | read, not merged — same rule as `vendor\whisper-flow` |
| Environment | `env\tts\python` | torch / transformers / piper stay out of the main env; the `._pth` has Alan's `src` stripped, so it cannot `import alan` |
| Lifecycle script | `scripts\tts-serve.ps1` | start / `-Stop` / `-Status` / `-Engine` / `-Voice` |
| Setup script | `scripts\setup-tts.ps1` | builds `env\tts\python` (Piper always; CPU torch only when OmniVoice is vendored) |
| Voice download | `scripts\pull-tts-voice.ps1` → `piper.download_voices` | a few tens of MB, per voice |
| Model download | `scripts\pull-tts-model.ps1` → `src\pull_tts_model.py` | OmniVoice only: ~3.04 GB, resumable, one connection at a time |
| Alan glue | `src\alan/voice.py` — `say()`, `tts_ready()`, `for_speech()`, `describe_speech()` | readiness, the contract, the doctor row |

Ports: **8182** here · whisper 8181 · cognee 8012 · browser 8022 · Ollama
11435 · 8001 is OmniVoice's own demo default, avoided on purpose. No overlap.

## Setup (once)

Piper, the default:

```powershell
scripts\setup-tts.ps1           # builds env\tts\python, installs piper-tts
scripts\pull-tts-voice.ps1      # downloads en_US-ryan-medium (~60 MB)
scripts\tts-serve.ps1           # load it and listen on 8182
```

Then turn it on in `config\settings.toml` (this install already has it on):

```toml
[voice]
tts_enabled = true
```

Adding OmniVoice, optionally, for a machine with a GPU:

```powershell
git clone https://github.com/k2-fsa/OmniVoice.git vendor\OmniVoice
scripts\setup-tts.ps1           # re-run: adds CPU torch + the OmniVoice package
scripts\pull-tts-model.ps1      # downloads the model (~3.04 GB, resumable)
scripts\tts-serve.ps1 -Stop
scripts\tts-serve.ps1 -Engine omnivoice
```

## Why the servers are stdlib

Both `src\tts_server.py` and `src\tts_server_piper.py` use `http.server` and
nothing else, and that is a deliberate choice rather than an omission. Each has
exactly two routes:

| Route | Method | In | Out |
|---|---|---|---|
| `/ready` | GET | — | JSON: `protocol_version`, `model`, `device`, `model_loaded`, `load_error`, `dtype`, `max_chars` |
| `/synthesize` | POST | `{"text": ..., "instruct": ...}` | `audio/wav` + `X-TTS-*` headers (`generated_in`, `rtf`, sample rate) |

A web framework would add a dependency tree to a machine with 3.9 GB of RAM to
serve two routes whose heavy work has to be serialized under a lock anyway —
two simultaneous generations on a 2-core box is not parallelism, it is just two
sentences arriving later. The RAM belongs to the voice.

`/ready` reports `load_error` rather than dying when the engine fails to load,
and `tts_ready()` treats "up but broken" as different from "not up": one says
*start it*, the other says *read the log*. A silent crash would otherwise look
exactly like a sidecar nobody started. The Piper server adds one message worth
having: a missing voice names the file and the `pull-tts-voice.ps1 -Voice …`
command that fetches it.

## The contract: `say()` never raises

`say(settings, text) -> tuple[bool, str]`

Speech output sits **after** an answer that has already been rendered to the
terminal. The only thing a failure can usefully cost is the sound — so an
exception here would take down a completed turn to report that a speaker was
unavailable. Every path returns a reason instead:

| Situation | Returns | Note names |
|---|---|---|
| `[voice] tts_enabled = false` | `(False, …)` | the key that turns it on |
| Reply is empty after stripping | `(False, "nothing to say")` | — |
| Sidecar down / timed out | `(False, …)` | `scripts\tts-serve.ps1`, and *the answer above stands* |
| Synthesis returned 0 bytes | `(False, …)` | "no audio" |
| No audio device | `(False, …)` | the playback error |
| `tts_play = false` | `(True, …)` | synthesized only — a health check needing no speaker |
| Success | `(True, …)` | bytes said **and the seconds taken** |

The success notes carry the elapsed wall time — `said 48213 bytes in 0.8s` —
measured on Alan's side rather than reported by the sidecar. That is deliberate:
it makes the cost of a spoken reply visible for *whatever* engine answers on
`tts_base_url`, which is the number that decides whether speech is a feature or
a demo on a given machine. A reply cut off at the sidecar's 1200-character cap
says so too, because hearing three quarters of an answer without being told is
worse than not hearing it.

`synthesizer` and `player` are injectable keyword arguments, which is how the
tests cover every row above without a sidecar, a model, or a sound card.

The `instruct` field is OmniVoice's voice-design dial. Piper has no equivalent,
so it accepts the field and ignores it — which is why `say()` can post the same
body to either engine without knowing which one is listening.

## Speaking markdown, not reading it

A reply arrives as markdown. Read aloud literally, it becomes *"hash hash
heading asterisk asterisk done"* — invisible on screen, fatal to a listener.
So `for_speech()` strips it first:

```text
# Answer
Run `alan doctor`, then see [the docs](https://example.com) for details.
```
becomes
```text
Answer Run alan doctor, then see the docs for details.
```

It removes fenced and inline code (keeping the code's *content* for inline
code, dropping whole fences), ATX headings, emphasis, links (keeping the text,
dropping the URL), list markers (replaced with a pause), table gutters and
horizontal rules — then collapses whitespace and caps the result at 1200
characters, the sidecar's own limit.

It is a separate, separately-tested function because it is the part that can
quietly ruin **every** spoken reply while looking fine on screen. Synthesis is
one HTTP call; this is the fiddly half.

## `/voicemode`: the hands-free loop

`say()` alone makes a typed session audible. `/voicemode` inside chat is what
joins the two halves into a conversation: the prompt switches from the keyboard
to the microphone, and each reply is spoken.

It is a **toggle on the ordinary chat loop**, not a nested session. That is the
deliberate shape — the conversation, its history, Tab completion and the
keyboard all keep working across the switch, and leaving is a word away rather
than something you climb out of.

Entry is probed first, and the two halves are treated differently:

| | Unavailable at `/voicemode` |
|---|---|
| Microphone (whisper) | **Refuses to enter.** A mode that opens onto a dead microphone reads as a broken prompt when it is really a sidecar nobody started, so the note names `scripts\whisper-serve.ps1`. |
| Speaker (TTS) | **Enters anyway**, voice in / text out, with a note saying so. A hands-free conversation that prints its answers is still a conversation. |
| `tts_enabled = false` | **Enters anyway**, and says the replies will be printed. |

Getting out again is matched on an **exact normalized phrase**, not a prefix.
Normalizing is what makes it forgiving — Whisper renders `Stop listening.` as
often as not, and a command you have to say exactly right is one you will say
wrong at the moment you want it most. But `"stop listening to that podcast"`
exiting the mode would be a silent, confusing failure, while a missed rephrasing
costs one Ctrl+C, which always works. So the asymmetry is deliberate: it errs
toward staying in.

Ctrl+C ends the *mode*, not the session — the recorder is the thing that was
interrupted, so treating it as "quit chat" would punish the wrong gesture.

Every transcript is echoed as `you>` **before** the question is built on it, so
a mis-hearing is checkable rather than discovered in the reply. Silence
re-arms the listener instead of ending the mode: a pause is how you finish a
turn, not a failure.

## Two switches, because the two halves cost differently

| | `voice.enabled` | `voice.tts_enabled` |
|---|---|---|
| Direction | speech → text | text → speech |
| Engine | whisper-flow | Piper (or OmniVoice) |
| Resident cost while up | ~150 MB | Piper: tens of MB · OmniVoice: ~1.5–3.2 GB |
| Started by | `run.ps1 voice` | **by hand**, once |

Neither implies the other. On a 4 GB machine an operator must be able to keep
the microphone and give up the voice, and `alan doctor` says exactly that — it
reports `voice` and `speech` as **separate rows**, so it can say *hearing
works, speech is off* rather than collapsing both into one verdict.

`run.ps1` deliberately does **not** auto-start the TTS sidecar: even the small
engine is resident while it is up, and a command whose whole point is immediacy
should not open with a model load. Start it once and leave it running.

## Precision is a measurement, not a convention

*(OmniVoice only — Piper loads a quantized ONNX voice and has no dtype knob.)*

```powershell
$env:TTS_DTYPE = 'float16'   # or float32
scripts\tts-serve.ps1 -Engine omnivoice
```

OmniVoice's own examples use `float16` because they target a GPU. That habit
inverts on this machine:

- **CPU kernels in PyTorch are built for `float32`.** With no CUDA and no
  AVX-512-FP16 on a Skylake i3, `float16` is *emulated* — it saves memory but
  does not make the arithmetic faster.
- **`float32` doubles the working set** — roughly 3.2 GB against 1.5 GB — and
  on 3.9 GB of RAM that is not a memory saving, it is page-file traffic instead
  of arithmetic.

`float16` is therefore the default here: measured, it loads in 170.8 s and
keeps the model largely out of the page file, while costing no speed, because
the arithmetic is not what is taking the time either way — see Performance
below. `TTS_NUM_STEP` (diffusion steps, default 32) is the other lever: fewer
steps is roughly proportional less compute, at some cost in audio quality.

## Performance

Measured on this machine — Intel Core i3-6006U (2C/4T @ 2.0 GHz), 3,971 MB RAM,
Intel HD Graphics 520, **no CUDA**.

> **RTF** (real-time factor) = generation seconds ÷ audio seconds. Below 1.0
> means the sentence is ready before you would have finished hearing it. It is
> the number that decides whether speaking is a feature or a demo.

### Piper: RTF ~0.2, the reason this ships on

One ~7-second utterance (~115 characters), per voice, load once then warm:

| Voice | Generation | Audio | RTF |
|---|---|---|---|
| `en_US-ryan-medium` (default) | 1.41 s | 6.54 s | **0.22** |
| `en_US-joe-medium` | 1.43 s | 7.05 s | 0.20 |
| `en_US-hfc_female-medium` | 1.42 s | 7.27 s | 0.20 |
| `en_US-lessac-medium` | 1.39 s | 7.17 s | 0.19 |
| `en_US-amy-medium` | 1.68 s | 8.93 s | 0.19 |
| `en_GB-alba-medium` | 1.69 s | 7.96 s | 0.21 |

Roughly **5× faster than real time**, so a reply is *shorter* arriving than it
is to listen to. The `-high` voices (`en_US-ryan-high`, `en_US-lessac-high`) are
more expressive at RTF ≈ 1.1–1.2 — still usable, just slightly behind real time
— and are the ones to try if `-medium` sounds too plain. `data\tts\piper` is
git-ignored, so trying a voice costs a download and nothing else.

### OmniVoice: RTF > 356, the reason it is not the default

| | |
|---|---|
| Load from `data\tts\model` | **170.8 s** |
| Generation of a 3.2 s sentence | **did not finish in 1,069 s (18 min)** — run stopped |
| Implied RTF | **> 356** |

For comparison, OmniVoice's README quotes RTF **0.025** — 40× faster than real
time. That is roughly **14,000× slower here**, and the gap is not a
configuration problem:

- **613M parameters × 32 diffusion steps** is the cost, and it scales with the
  model rather than with care. There is no flag that turns a multi-GBFLOP
  workload into something a 2-core Skylake finishes interactively.
- **`float16` does not help.** PyTorch's CPU kernels are built for `float32`;
  with no CUDA and no AVX-512-FP16 on this CPU, `float16` is emulated. It
  halves the memory (which did keep the model out of the worst of the
  page-file thrash — load succeeded at 170.8 s) but buys no arithmetic.
- **`float32` would not close the gap either.** It is the native dtype, but it
  doubles a working set that is already competing for 3.9 GB of RAM. Even a
  favourable 5× swing leaves RTF ≈ 70 — still an order of magnitude past
  usable. Not measured, because it cannot change the verdict.
- **`TTS_NUM_STEP=16`** would roughly halve it. RTF ≈ 178 is still three
  orders of magnitude from a feature.

So OmniVoice stays selectable and stays off by default, and **`tts_enabled`
ships `true` because Piper answers the same port at RTF ~0.2.** One engine's
ceiling is not the other's floor.

## How this shipped

The integration was engine-agnostic by construction, which is the part that
paid off. `say()` is a single HTTP POST; `for_speech()`, the doctor row, the
config keys and every existing test are untouched by which server answers on
8182. Swapping the engine was exactly the two steps this doc used to predict:

1. a second server in `src\tts_server_piper.py` (same protocol, a different
   backend), and
2. `scripts\tts-serve.ps1` starting that instead — now the `-Engine` flag.

Nothing in `src\alan\voice.py` changed. That is the test of whether a seam is
real: adding a whole engine behind it moved no client code.

## Lifecycle

```powershell
scripts\tts-serve.ps1                            # start Piper (no-op if already up)
scripts\tts-serve.ps1 -Voice en_US-amy-medium    # a different Piper voice
scripts\tts-serve.ps1 -Engine omnivoice          # the GPU engine
scripts\tts-serve.ps1 -Status                    # ready JSON, or the err log tail
scripts\tts-serve.ps1 -Stop                      # only one engine can hold the port
```

Logs live in `data\logs\tts\` (`tts.log`, `tts.err.log`, `tts.pid`). The start
waits up to 240 s for readiness — OmniVoice needs it to load 3 GB from disk on a
cold cache; Piper is resident in well under a second and simply returns early.

`alan doctor` reports it:

```text
✔    speech      ready on http://127.0.0.1:8182/ready (model en_US-ryan-medium, onnx, resident, protocol v1)
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `speech configured - … start it with scripts\tts-serve.ps1` | sidecar not running | `scripts\tts-serve.ps1` |
| `voice 'x' not found …` in `/ready` | voice not downloaded | `scripts\pull-tts-voice.ps1 -Voice x` |
| `model failed to load: …` (OmniVoice) | weights missing, or too little RAM | `scripts\pull-tts-model.ps1`, then check `data\logs\tts\tts.err.log` |
| `speaks protocol vN, Alan speaks v1` | sidecar and Alan out of step | update whichever is older |
| First reply is much slower than the rest | engine loading from disk on first use | start the sidecar before asking |
| It synthesizes but you hear nothing | no output device, or `tts_play = false` | check `[voice] tts_play` and the system volume |
| A reply is cut off at ~1200 characters | `for_speech()` caps to the sidecar's `MAX_CHARS` | expected: it is a cap, not a failure |
| Started OmniVoice and it seems hung | RTF > 356: it is generating, not stuck | `-Stop`, then start Piper |
