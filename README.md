# Alan

> **A personal assistant that lives on your machine.** It remembers what you
> told it, works while you're away, asks before it touches anything, and never
> claims it did something it didn't.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?style=flat-square)
![Offline-first](https://img.shields.io/badge/offline-first-local%20models-2E7D32?style=flat-square)
![Memory](https://img.shields.io/badge/memory-keeps%20it%20on%20disk-8A2BE2?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-1B9AAA?style=flat-square)

```
        _
       / \    _      _  __ _ _ __
      / _ \  | |    / \/ _` | '_ \
     / ___ \ | |___| | (_| | | | |
    /_/   \_\|_____|_|\__,_|_| |_|
```

**Contents** — [What Alan is](#what-alan-is) · [Why it's different](#why-alan-is-different) · [Quick start](#quick-start) · [Commands](#the-commands) · [Brain](#choosing-a-brain) · [Memory](#memory) · [Failures](#learning-from-failure) · [Always-on](#always-on-scheduling) · [Browsing](#browsing) · [Coding](#coding) · [Skills](#skills-ecc) · [Layout](#layout) · [Anywhere](#location-independence) · [Performance](#performance) · [Troubleshooting](#troubleshooting)

---

## What Alan is

One folder holds the whole assistant — here it's `D:\Alan`: the interpreter,
the libraries, the model runtime, the memory store and the code. Nothing is
installed on the host, nothing phones home, and no conversation leaves the
machine unless *you* point it at a cloud model.

It's a terminal app with a friendly `you>` prompt. Talk to it in plain
English; it plans, calls tools, checks its own work, and reports back.

```text
you> remind me tomorrow at 09:00 to call the dentist
you> what did we decide about the auth refactor last week?
you> fix the add function in math.js
```

Alan finds its own home from the code's location, so the folder can be moved
or renamed without a reinstall (`ALAN_HOME` overrides it).

---

## Why Alan is different

| | |
|:---:|---|
| 🔐 | **It asks before it acts.** Every tool call passes a permission gate with a risk tier. Reads are free; writes, shell and web fetches need your OK — and an agent can never approve itself. Operator rules (`allow` / `ask` / `block`) live in `config\settings.toml`. |
| ✅ | **Verified, not claimed.** Vibecoder once described a two-line file as *"a comprehensive math library providing symbolic computation, units, complex numbers, matrices."* So Alan checks the filesystem before and after every run and labels each item *verified on disk* or *claimed, not verified*. |
| 🧠 | **It remembers.** Every turn is stored locally and the relevant ones are pulled back into the prompt before the next reply. Keyless, offline, no cloud LLM in the loop. |
| 🩹 | **It stops making the same mistake twice.** Failures are written to `lessons.json` and replayed into the prompt *before* the next attempt — and closed the first time the task succeeds. |

---

## Quick start

```powershell
cd D:\Alan
scripts\run.ps1                 # interactive chat at the you> prompt
scripts\run.ps1 ask "What is the capital of France?"
scripts\run.ps1 doctor          # is everything wired up?
```

Or double-click `Alan.cmd` in the folder root.

First launch starts the bundled Ollama server and uses the local model in
`models\`. Add a cloud key later if you want a bigger brain.

**Make `alan` a real command** (once):

```powershell
scripts\install-command.ps1
```

```
alan                     start a chat
alan ask "What is 2+2?"  one question, then exit
alan doctor              check every part of the install
alan --help              everything else
```

**Check the install at a glance** — `alan doctor` is the one command worth
knowing:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Alan doctor                                                                 │
│ models:   2 backend(s): groq:qwen/qwen3.8-27b, ollama:qwen2.5:1.5b          │
│ internet: internet - internet ready (Exa search + Jina Reader)              │
│ vibecoder: C:\Users\ADMIN\AppData\Roaming\npm\vibecoder.CMD                 │
│ tools:    33 registered                                                     │
│ agents:   orchestrator, browser, coding, memory, planner, research,         │
│           reviewer, system                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

*(real output, trimmed to the interesting lines — states change as sidecars
start and stop)*

---

## The commands

| Command | What it does |
|---|---|
| `scripts\run.ps1 providers` | Show every backend and whether it's ready |
| `scripts\run.ps1 memory [query]` | Show memory status (and test a recall) |
| `scripts\run.ps1 ingest "some text"` | Store a note in Alan's memory |
| `scripts\run.ps1 forget --list` | Show everything stored in memory |
| `scripts\run.ps1 forget --contains "text"` | Remove memories matching some text |
| `scripts\run.ps1 lessons` | What earlier failures taught Alan (`--clear` to forget) |
| `scripts\run.ps1 version` | Show version + home folder |
| `scripts\run.ps1 where` | Show where Alan keeps its files |
| `scripts\selftest.ps1` | Run the offline smoke tests |
| `scripts\pull-model.ps1 <name>` | Download another local model |
| `scripts\install-command.ps1` | Make `alan` runnable from any prompt |
| `scripts\cognee-serve.ps1` | Start/stop the memory sidecar |
| `scripts\browser-serve.ps1` | Start/stop the browser sidecar |

Inside `alan chat`:

```text
/remember TEXT        store a note
/recall QUERY         search memory
/memory               memory status
/forget               erase the memory dataset
/schedule add "TASK"  --when "every morning"
/activity             tasks, approvals, recent runs
/approve ID           let a waiting task run
/reject ID            pause it
/telegram             approval-bridge state
```

---

## Choosing a brain

Alan picks between two kinds of backend on its own (`provider = "auto"`):

1. **Cloud API** — OpenAI, Anthropic, Groq, OpenRouter. Fast and powerful;
   needs internet and a key.
2. **Local model** — the bundled Ollama running `qwen2.5:1.5b`. Private and
   offline; needs a reasonably capable machine.

The choice is made by `priority` in `config\settings.toml` — lower wins —
and every cloud provider ships at `0` against the local model's `10`. That's
what makes **adding a key actually change which model answers**. A request
that touches your memory stays on this machine no matter what the priorities
say.

### Use a cloud model

Edit `config\.env` (or copy `.env.example` over it):

```
GROQ_API_KEY=gsk_...
# and/or
OPENAI_API_KEY=sk-...
# and/or
ANTHROPIC_API_KEY=sk-ant-...
# and/or
OPENROUTER_API_KEY=sk-or-v1-...
```

Restart Alan. Ties fall back to registration order:
OpenAI → Anthropic → Groq → OpenRouter → Ollama.

Measured on this machine with Groq configured: **3.5 s** for a prompt that
took **26.7 s** from the local model.

### Use a local model

`qwen2.5:1.5b` is already set up. Want another one?

```powershell
scripts\pull-model.ps1 qwen2.5:3b
```

Then set the model name in `config\settings.toml` under `[ollama]`.

---

## Memory

Alan remembers across conversations. A small local **cognee** sidecar (started
automatically for `chat`/`ask`) stores each turn and pulls relevant memories
back into the system prompt before Alan answers. It runs keyless and offline:
GLiNER extracts entities and relations, fastembed builds the vectors — no cloud
LLM is involved, so your memory never leaves the machine.

- **Automatic** — every turn is remembered in the background after the reply;
  relevant past memories are recalled before the next one.
- **Manual** — in `chat`: `/remember TEXT`, `/recall QUERY`, `/memory`,
  `/forget`.
- **CLI** — `scripts\run.ps1 ingest "..."` or `scripts\run.ps1 memory "query"`.

Memory lives in `data\cognee\`. The **first** write ever downloads the
extractor models (~800 MB) into that folder, so that first write takes minutes.
Later start-ups are slower than a normal app (~3–4 min while cognee migrates
and loads), but a warm write is only about **1.4 s** — fine for something that
happens behind a reply.

Start Alan without memory with `ALAN_NO_MEMORY=1`, or set `enabled = false`
under `[memory]` in `config\settings.toml`.

---

## Learning from failure

Alan does not make the same mistake twice. When an attempt doesn't come off —
a tool that isn't registered, a delegation that lists outstanding work, a model
that wasn't there — the reason and the approach used are written to
`data\state\lessons.json`. The next request that looks like that one is shown
the lesson in its system prompt *before* anything runs.

- **It stops when the failure stops.** A lesson is only shown while the task
  has more failures than successes. The first attempt that works closes it; a
  later relapse reopens it.
- **It says what was tried, and what worked.** The hint names the approach
  already burned, so Alan doesn't simply repeat it.
- **It quotes the task it is about.** Matching is by content words, so a
  reworded retry still finds the lesson.
- **It never breaks anything.** If the file can't be read, Alan forgets the
  lesson and answers anyway. Reasons are redacted before storage, and the file
  is capped at 200 entries.

`alan lessons` shows what's held — still open, and what has since been solved.

---

## Always-on scheduling

Alan can hold a recurring intent while you're not typing: say it in chat and it
remembers, runs it on its own, and tells you in `/activity` what happened.

```text
you> remind me tomorrow at 09:00 to call the dentist
you> /schedule add check the news every morning
alan  Scheduled queued: check the news
you> /activity
```

- **The loop only runs on an explicit surface** — an `alan chat` session or
  `alan serve` owns it. One-shot `alan ask` never spawns background threads;
  nothing starts loops by itself (ADR-008/012).
- **One task at a time, always.** Structural, not a setting. A stalled or
  timed-out worker is abandoned and recorded — never overlapped.
- **Tasks stop at the permission gate like anything else.** A run that needs
  approval becomes `waiting_approval`, shows in `/activity`, and
  `/approve ID` re-queues it. "No" can't become a nag loop.
- **Plain-English timing** (`every morning`, `every day at 07:30`, `in 2 hours`,
  `weekly at 09:00`) is parsed or refused — Alan never guesses.
- **Tasks survive restarts.** `data\state\schedule.json` is written atomically;
  a corrupt file is preserved beside it and never bricks Alan.

| Command | What it does |
|---|---|
| `alan activity` | Tasks, approvals awaiting a decision, recent runs (`--history`) |
| `alan schedule list` | Every scheduled task |
| `alan schedule add "TASK" --when "TIMING"` | Add a task deterministically |
| `alan schedule show <id>` | One task, full detail |
| `alan schedule remove <id-or-phrase>` | Cancel by id or text |
| `alan schedule pause\|resume\|run <id>` | Pause, resume, or run one now |
| `alan serve` | Headless always-on: scheduler + phone bridge, no chat surface |

**Approvals can be answered from your phone.** With a `[telegram]` bot token
and `chat_id` in `config\settings.toml`, every new approval is sent to that
chat and `/approve ID` / `/reject ID` replies work exactly like the chat
commands. The bridge *long-polls* the Bot API — Alan dials out, nothing opens
a port, and only the configured chat may decide. With `notify_tasks = true`
(the default) it also sends a line when a task starts, finishes or fails: your
phone carries *results*, not just decisions.

Operator rules (`allow` / `ask` / `block`, matching `capability.*` patterns)
live in `[[permissions.rules]]` — see
[ADR-008](docs/decisions/architecture-decisions.md) for the design and the
alternatives that were rejected.

---

## Browsing

Alan can search the web and read pages, through two layers behind the same two
tools (`web.search` and `web.open`):

- **Internet backends (default, zero-config).** `web.search` uses Exa — hosted
  semantic search — via `mcporter`; `web.open` reads a page's text through Jina
  Reader (`curl https://r.jina.ai/<url>`). Nothing to start: if mcporter and
  curl exist, this works.
- **Browser sidecar** (`scripts\browser-serve.ps1`, on `127.0.0.1:8022`) runs
  browser-use in its own isolated Python at `env\browser\python`, driving the
  Chrome or Edge already installed — nothing is downloaded. Alan prefers it for
  *interacting* with a page (clicking, screenshots), and it's the fallback when
  the internet backends are unavailable.

Two things worth knowing before you rely on `web.search`:

- **Jina fetches on its own servers.** `web.open` through the internet layer
  can't be pointed at a machine on your own network — the browser path can, so
  that path's guard stays: **every fetch is `high_risk`.** Fetching a URL is
  the capability most able to reach something private, so it needs a permission
  decision first. See [ADR-003](docs/decisions/architecture-decisions.md).
- **Search results are for a reading model.** Exa returns semantically-ranked
  results with snippet highlights; when it has nothing trustworthy it returns
  nothing, and Alan says so instead of showing you a confident wrong answer.

---

## Coding

Alan doesn't write code. You talk to it, it delegates the change to
**Vibecoder** — a separate headless coding agent with a real tool loop — then
it reports what actually happened. Vibecoder is installed on its own; Alan only
ever launches it.

That split exists because Alan's bundled model is a 1.5b one, and a
plausible-looking edit from a model that small is worse than no edit at all:
you can't tell the difference by reading the diff.

```
> fix the add function in math.js
  ┌ planning ...        intent=vibecoder_delegate
  ├ vibecoder.delegate  glob → read_file → edit_file   46.3s
  └ vibecoder.report

DONE (verified on disk):
  - math.js - verified on disk (mtime+size changed 69B -> 65B)

NOT DONE:
  - error reported: [read_file → ERROR: file not found: ...]
```

**Verified, not claimed.** Alan checks the filesystem before and after each run
and labels every item *verified on disk* or *claimed, not verified*. `NOT DONE`
is always shown — the report is attached to the reply rather than left for the
model to restate, because a 1.5b model restating it drops the parts it finds
unflattering.

**Writes ask first.** A change needs your confirmation; a read doesn't. See
`[vibecoder]` in `config\settings.toml`.

**Two things to know:**

- Which model writes code is *not* decided by `vibecoder.provider` — that flag
  only reaches Vibecoder's *chat* route. Its hybrid router classifies a coding
  task as *heavy* and serves it from `routing.heavyProvider`; the real pin
  lives in `~/.vibecoder/config.json`, which Vibecoder deep-merges over the
  defaults. Both are set to Groq here: free, and reliable where the 550B
  NVIDIA route failed 3 of 4 attempts with a silent exit-0 no-op.
- The working directory defaults to *the directory Alan is running in*. Run it
  from inside a project. It is refused from Alan's own source tree, your home
  directory and any drive root.

Vibecoder isn't installed by default; `alan doctor` reports it either way, and
without it Alan can still chat, browse and search.

---

## Skills (ECC)

Alan reads the [ECC](https://github.com/affaan-m/ECC) library — 293 skills,
94 commands, 68 agent prompts — as a searchable, read-only catalog, wired
through the same permission gate as every other tool:

- **Name one, load one.** "use the tdd-workflow skill", "/code-review", or a
  plain request containing every word of a multi-word skill name plans a
  `load_skill` step; the entry's instructions then arrive in the prompt the way
  recalled memory does.
- **Tools, gated like everything else.** `skill.list`, `skill.search` and
  `skill.read` are `SAFE` and register through the ordinary registry, so an
  operator rule on `skill.*` turns the namespace off. `skill.read` caps injected
  text at `[ecc] max_chars`, because a 400-line skill would otherwise fill a
  2048-token local model's whole context.
- **Eight advisory dev tools.** `ecc.run_tests`, `ecc.lint_check`,
  `ecc.format_code`, `ecc.check_coverage`, `ecc.dependency_analyzer`,
  `ecc.security_audit`, `ecc.git_summary`, `ecc.changed_files` detect, analyse
  and *print* commands — they never execute.
- **Browse it.** `alan skills`, `alan skills <terms>`, `alan skills --read
  <name>`, and `alan doctor` shows what loaded.

Off in code, on in this install's `settings.toml` (`[ecc] enabled = true`) — a
fresh checkout behaves exactly as before until you turn it on. The library lives
in `vendor\ECC` (git-ignored and pinned); if it's missing, `alan doctor` prints
the one-line clone command. ECC's hooks are deliberately *not* ported. Details:
[docs/integrations/ecc.md](docs/integrations/ecc.md).

---

## Layout

```
<home>\Alan\
├─ Alan.cmd                 # double-click launcher
├─ README.md
├─ requirements.txt         # pins for env\python (see the file for what it excludes)
├─ config\
│   ├─ settings.toml        # provider, model, memory, tools, browser, permissions
│   ├─ .env                 # your API keys (git-ignored)
│   └─ .env.example
├─ env\                     # git-ignored: interpreters + installed packages
│   ├─ python\              # Python 3.12.10 + pip + Alan's packages
│   ├─ cognee\python\       # isolated Python for the memory sidecar
│   ├─ browser\python\      # isolated Python for the browser sidecar
│   └─ ollama\              # the model runtime
├─ models\                  # local model files (git-ignored)
├─ src\
│   ├─ browser_sidecar.py   # browser-use sidecar; runs in env\browser only
│   └─ alan\
│       ├─ cli.py           # command-line interface
│       ├─ assistant.py     # conversation state
│       ├─ config.py        # settings + paths
│       ├─ runtime.py       # composition root: builds and wires everything
│       ├─ core\            # orchestrator, planner, events, router, atomic writes
│       ├─ interfaces\      # the seven provider interfaces Alan owns
│       ├─ llm\             # provider backends (OpenAI/Anthropic/Groq/OpenRouter/Ollama)
│       ├─ memory\          # cognee_client + providers/{cognee,json,mem0}
│       ├─ models\          # pydantic schemas
│       ├─ permissions\     # gate, tiers, policy
│       ├─ agents\          # the seven subagents
│       ├─ ecc\             # ECC catalog + skill.*/ecc.* tools (read-only)
│       └─ tools\           # registry, standard set, shell/, filesystem/, browser/
├─ scripts\                 # run / activate / selftest / setup-* / *-serve
│                           # install-command.ps1 -> the `alan` command
├─ docs\                    # ARCHITECTURE.md, decisions/, integrations/
├─ demos\                   # demo_all.py — five runnable scenarios
├─ data\                    # logs, saved sessions, cognee + browser memory/state
└─ tests\                   # unit tests, stdlib unittest only
```

`env\`, `models\`, `data\` and `vendor\` are git-ignored: a fresh checkout is
source only, and `alan doctor` reports exactly which piece is missing and the
command that fetches it.

---

## Performance

**Memory is the constraint on a small machine, not CPU.** On the reference box
(4 logical / 2 physical cores, 3.9 GB RAM) a question went from **34.2 s to
5.5 s** after three fixes, each measured rather than guessed:

| Where | Was | Now | Why |
|---|---|---|---|
| Runtime construction | 7.0 s | 3.0 s | Four backend liveness probes ran on every invocation; results are cached in `data\state\probes.json` |
| Memory recall | 9.1 s | 0.7 s | One question used to cost two graph round trips — `_recall` counted the hits, then `context_block` searched *again* |
| Model call | 17.7 s | 1.7 s | RAM pressure, not the model. Once nothing else was competing, a 1.5B model answered in under 2 s |

The first question after a cold start still pays ~12 s to load the model into
RAM. Nothing in Alan's control changes that.

Two things that look slow but aren't: `max_tokens` doesn't affect a short answer
(verified at 64/128/256/1024 — all 0.46–0.69 s), and the memory sidecar's
first-ever write downloads ~800 MB of extractor models.

**When Alan feels slow on a small machine, check RAM before anything else.**
Ollama's own log is the honest instrument: under memory pressure it reports
~0.2–0.5 tokens/second for a model that does 20–60 tok/s when it has room,
which looks like a slow model and isn't one. Free RAM and it returns.
`num_ctx` defaults to 2048 and `keep_alive` to `30m` for exactly this reason;
both are overridable in `config\settings.toml` under `[ollama]`.

If a backend is flapping and you don't trust the cached answer, `alan doctor`
always probes fresh, and `ALAN_PROBE_CACHE=0` turns the cache off entirely.

---

## Location independence

The folder is found from the code's own location, not from a registry entry or
a hard-coded path:

- `ALAN_HOME` derives from `src\alan\config.py` → move or rename the folder and
  it still works; a test asserts no absolute path is hard-coded anywhere.
- The bundled Ollama listens on `127.0.0.1:11435`, not the usual `11434`, so it
  coexists with any Ollama already installed — and always uses its own copy in
  `env\ollama`, with models redirected to `models\` via `OLLAMA_MODELS`.
- Nothing is written outside the folder except, if you opt in with
  `scripts\install-command.ps1`, a forwarder at `C:\Users\ADMIN\bin\alan.cmd`
  and one user-PATH entry — and `-Uninstall` removes both, restoring PATH
  exactly as it was.
- Python bytecode is compiled into host temp (`PYTHONPYCACHEPREFIX`) so the
  source tree stays clean.

```powershell
scripts\install-command.ps1 -Status     # is the `alan` command wired up?
setx ALAN_HOME "X:\Alan"                # point the forwarder somewhere new
scripts\install-command.ps1 -Uninstall  # restore PATH exactly as it was
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No usable AI backend was found` | Add a key in `config\.env`, or pull a model |
| `model ... is not pulled` | `scripts\pull-model.ps1 qwen2.5:1.5b` |
| Ollama slow to answer first time | Model is loading into RAM; later turns are faster |
| Memory seems empty / first turn slow | First-ever memory write downloads ~800 MB of extractor models into `data\cognee\cache`. Warm writes are ~1.4 s |
| `memory sidecar: unreachable` | Start it with `scripts\cognee-serve.ps1` (or run via `run.ps1`). It takes 3–4 minutes to boot, and the script waits for it rather than giving up |
| `browser sidecar: unreachable` / `needs a BrowserProvider` | Start it with `scripts\browser-serve.ps1`. Ready in under a second — it launches no browser until you actually call a tool |
| `web.search` reports no trustworthy results | The engine blocked this network or its markup moved. Try `engine = "bing"`: Alan refuses to return results that don't match your query |
| `web.open` denied, `risk=high_risk` | Working as intended. Raise the ceiling in `config\settings.toml` under `[permissions]`, or ask in an interactive session where you can confirm |
| Answers feel "hallucinated" from memory | Memory is only injected when it genuinely matches your question — zero word overlap means zero score. `alan forget --list` shows what's actually stored; see `docs\integrations\repository-audit.md` §7 for a known cognee defect that leaks across datasets |
| Alan gets slower over hours, RAM climbs | An orphaned browser from the sidecar. `scripts\browser-serve.ps1 -Stop` closes it properly; `alan doctor` shows the sidecar state. Confirm with `Get-Process chrome` |
| `code.test` takes a minute, or "collects 0 tests" | Fixed by `pytest.ini`, which pins `testpaths = tests`. Without it `pytest` walked `env\python` and `env\browser\python`, burned 73 s collecting, and collected none of Alan's own tests |
| `alan` is not recognised | Open a **new** command prompt — PATH is only read when a window starts. Check with `scripts\install-command.ps1 -Status` |
| `alan: cannot find Alan at "..."` | `ALAN_HOME` points somewhere without `Alan.cmd`. Run `scripts\install-command.ps1` from Alan's real folder. A stray trailing space or backslash also causes this |
| PowerShell blocks scripts | Use `Alan.cmd` (it sets the bypass flag) |
| `browser-serve.ps1 > out.txt` leaves the file locked | Expected: `Start-Process` inherits handles, so the sidecar holds a copy until it exits |

---

## Docs

| Doc | What's in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The shape of the system: orchestrator, planner, memory gate, tool registry |
| [docs/decisions/architecture-decisions.md](docs/decisions/architecture-decisions.md) | Every ADR — the decision, the rejected alternatives, when to revisit |
| [docs/integrations/ecc.md](docs/integrations/ecc.md) | How the skills catalog is wired and gated |
| [docs/integrations/dependencies.md](docs/integrations/dependencies.md) | Pinned manifest, ports, and the constraints it respects |
| [docs/integrations/repository-audit.md](docs/integrations/repository-audit.md) | The audit of six external projects; nothing was vendored |

Tests: `python -m pytest -q` from the repo root (668 tests, stdlib unittest
under the hood), or `scripts\selftest.ps1` for the offline smoke tests.

## License

MIT
