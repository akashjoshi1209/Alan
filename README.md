# Alan

A portable, self-contained AI assistant that lives entirely on this USB drive.
Plug it into any Windows PC and chat — Python, Ollama, and the code all travel
with the stick. Nothing needs to be installed on the host machine.

```
        _
       / \    _      _  __ _ _ __
      / _ \  | |    / \/ _` | '_ \
     / ___ \ | |___| | (_| | | | |
    /_/   \_\|_____|_|\__,_|_| |_|
```

---

## Quick start (3 steps)

1. **Plug the USB into a Windows PC** (drive letter doesn't matter).
2. **Double-click `Alan.cmd`** in the root of the drive.
   *(Or run `scripts\run.ps1` from PowerShell.)*
   *(Or, on a drive you use often, run `scripts\install-command.ps1` once and
   then just type `alan` — see below.)*
3. Start chatting at the `you>` prompt.

That's it. On first launch Alan starts its own bundled Ollama server and uses
the local model in `models\`.

### One-shot questions

```powershell
scripts\run.ps1 ask "What is the capital of France?"
scripts\run.ps1 ask "Summarize this: ..." --no-stream
```

### Other commands

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
| `scripts\install-command.ps1` | Make `alan` runnable from any command prompt |
| `scripts\cognee-serve.ps1` | Start/stop the memory sidecar |
| `scripts\browser-serve.ps1` | Start/stop the browser sidecar |

---

## Typing `alan` instead of hunting for a script

The steps above work on any PC with no setup at all. But if Alan lives on a
drive you use often, you can have a plain `alan` command:

```powershell
scripts\install-command.ps1
```

Then **open a new command prompt** (PATH is read when a window launches, so the
current one will not see it) and:

```
alan                              start a chat
alan ask "What is 2 + 2?"         one question, then exit
alan doctor                       check every part of the install
alan --help                       everything else
```

This works in cmd and in PowerShell.

### What it actually changes

Two independent routes are installed, because they fail differently:

| Route | What it is | Why keep it |
|---|---|---|
| `C:\Users\ADMIN\bin\alan.cmd` | A forwarder that locates Alan and calls `Alan.cmd` | Exposes exactly one command, can be removed without touching PATH, and follows `ALAN_HOME` if Alan moves |
| `D:\Alan` on your **user** PATH | Lets `alan` resolve `Alan.cmd` directly | No moving parts, and `Alan.cmd` is drive-relative so it survives a change of drive letter |

The forwarder sits earlier in PATH, so it is the one that runs. Check with:

```powershell
scripts\install-command.ps1 -Status
```

Your **machine** PATH is never touched, and no files are written outside
`C:\Users\ADMIN\bin` and your user PATH entry.

### If Alan moves to another drive

Set `ALAN_HOME` and the forwarder follows — no reinstall:

```powershell
setx ALAN_HOME "X:\Alan"
```

The installer sets this for you when it runs. Re-running
`scripts\install-command.ps1` from the new location updates it.

### Undo

```powershell
scripts\install-command.ps1 -Uninstall
```

Removes the forwarder and the PATH entry, restoring PATH exactly as it was. It
only clears `ALAN_HOME` if that still points at Alan, so a value you set for
some other reason is left alone.

---

## Choosing a brain

Alan supports two kinds of backend and picks between them automatically
(`provider = "auto"` in `config\settings.toml`):

1. **Cloud API** — OpenAI, Anthropic, Groq or OpenRouter. Fast and powerful;
   needs internet and a key.
2. **Local model** — runs on the host PC via the bundled Ollama. Private and
   offline; needs a reasonably capable machine.

The choice is made by `priority` in `settings.toml` — lower wins — and every
cloud provider ships at `0` against the local model's `10`. That is what makes
**adding a key actually change which model answers**. A request that touches
your memory stays on this machine no matter what the priorities say.

### Use a cloud model

Edit `config\.env` and paste a key (or copy `.env.example` over it):

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

Already set up: the `qwen2.5:1.5b` model lives in `models\`.
Want a different/smaller/larger one?

```powershell
scripts\pull-model.ps1 qwen2.5:3b
```

Then set the model name in `config\settings.toml` under `[ollama]`.

---

## Memory

Alan remembers across conversations. A small local **cognee** sidecar (started
automatically for `chat`/`ask`) stores each turn and pulls relevant memories
back into the system prompt before Alan answers. It runs keyless and offline:
GLiNER extracts entities/relations and fastembed builds the vectors — no cloud
LLM is involved, so your memory never leaves the machine.

- **Automatic** — every chat turn is remembered in the background after the
  reply; relevant past memories are recalled before the next reply.
- **Manual** — in `chat`, use `/remember TEXT`, `/recall QUERY`, `/memory`
  (status) and `/forget` (erase the memory dataset).
- **CLI** — `scripts\run.ps1 ingest "..."` or `scripts\run.ps1 memory "query"`.

Memory lives in `data\cognee\`. The **first** write ever downloads the extractor
models (~800 MB) into that folder, so that first write takes minutes. Later
start-ups are slower than a normal app (~3–4 min while cognee migrates and loads
its models), but a warm write is only about **1.4 s**, which is fine for
something that happens in the background after a reply.

To start Alan without memory, set `ALAN_NO_MEMORY=1`, or set `enabled = false`
under `[memory]` in `config\settings.toml`.

---

## Learning from failure

Alan does not make the same mistake twice. When an attempt does not come off —
a tool that is not registered, a delegation that lists outstanding work, a model
that was not there — the reason and the approach used are written to
`data\state\lessons.json`. The next request that looks like that one is shown the
lesson in its system prompt *before* anything runs.

- **It stops when the failure stops.** A lesson is only shown while that task
  has more failures than successes. The first attempt that works closes it, and
  a later relapse reopens it.
- **It says what was tried, and what worked.** Until something works, the hint
  names the approach already burned so Alan does not simply repeat it. Once
  something does, the hint names that instead.
- **It quotes the task it is about.** Matching is by content words rather than
  exact text, so a reworded retry still finds the lesson — and the wording makes
  clear which task is being described when the match is close but not identical.
- **It never breaks anything.** If the file cannot be read or written, Alan
  forgets the lesson and answers anyway. Reasons are redacted before they are
  stored (they come from backend errors), and the file is capped at 200 entries.

`alan lessons` shows what is held — what is still open, and what has since been
solved — and `alan lessons --clear` starts over.

---

## Always-on scheduling

Alan can hold a recurring intent while you are not typing: say it in chat and
it remembers, runs it on its own, and tells you in `/activity` what happened.

```text
you> remind me tomorrow at 09:00 to call the dentist
you> /schedule add check the news every morning
alan  Scheduled queued: check the news
you> /activity
```

- **The loop only runs on an explicit surface** — an `alan chat` session or
  `alan serve` owns it. One-shot commands like `alan ask` never spawn
  background threads; nothing starts loops by itself (ADR-008/012).
- **One task at a time, always.** That is structural, not a setting. A task
  that stalls (waiting on a question) or overruns (`task_timeout`) is recorded
  as-is and the loop moves on; a timed-out worker is abandoned, never
  overlapped.
- **Tasks stop at the permission gate like anything else.** If a run needs an
  approval, it becomes `waiting_approval`, shows up in `/activity`,
  `/approve ID` re-queues it (and the *identical* re-run later passes without
  asking), and `/reject ID` pauses it — so "no" cannot turn into a nag loop.
  Approvals and their decisions are visible to you and are not forgotten on
  restart.
- **Plain-English timing** (`every morning`, `every day at 07:30`, `in 2
  hours`, `weekly at 09:00`) is parsed or refused — Alan never guesses.
- **Tasks survive restarts.** `data\state\schedule.json` is written atomically;
  a corrupt file is preserved beside it and never bricks Alan.

Everything is also available outside chat:

| Command | What it does |
|---|---|
| `alan activity` | Tasks, approvals awaiting a decision, recent runs (`--history`) |
| `alan schedule list` | Every scheduled task |
| `alan schedule add "TASK" --when "TIMING"` | Add a task deterministically (no planner) |
| `alan schedule show <id>` | One task, full detail |
| `alan schedule remove <id-or-phrase>` | Cancel by id or text |
| `alan schedule pause\|resume\|run <id>` | Pause, resume, or run one task now |
| `alan schedule clear-done` | Drop finished tasks |
| `alan serve` | Headless always-on: run scheduler + phone bridge with no chat surface (Ctrl+C stops; `--seconds N` for scripts) |

In chat the same control is `/schedule …` plus `/approve ID` and `/reject ID`.
Enable or disarm the whole thing in `[scheduler]` in `config\settings.toml`.

**Approvals can also be answered from a phone.** With a `[telegram]` bot token
and `chat_id` in `config\settings.toml`, every new approval is sent to that
chat and `/approve ID` / `/reject ID` replies work exactly like the chat loop's
commands (same id-suffix matching, same re-queue/pause behaviour — ADR-011).
The bridge *long-polls* the Telegram Bot API: Alan dials out, nothing opens a
port, and only the configured chat may decide. A blank `bot_token` disables it.
Run `/telegram` in chat or `alan doctor` for its state.

With `notify_tasks = true` (the default) the bridge also sends a short line for
each task that starts, finishes, or fails — the phone carries *results*, not
just decisions. A task stopped at the permission gate is only announced once
(by its approval line, which carries the `/approve` instruction).

**Tasks in the background announce themselves.** `alan chat` prints a dim line
for each task that starts, waits for approval, or finishes — at the safe moment
after your reply, never over the `you>` prompt.

Operator rules (`allow` / `ask` / `block`, matching `capability.*` patterns)
live in `[[permissions.rules]]` in the same file — see
[ADR-008](docs/decisions/architecture-decisions.md) for the design and its
deliberately rejected alternatives.

---

## Browsing

Alan can search the web and read pages, through two layers that run behind the
same two tools (`web.search` and `web.open`):

- **The internet backends (default, zero-config).** `web.search` uses Exa — a
  hosted semantic search — via `mcporter`, and `web.open` reads a page's text
  through Jina Reader (`curl https://r.jina.ai/<url>`). Nothing is installed
  beyond tools the agent-reach setup already provides, and there is nothing to
  start: if mcporter and curl exist, this works.
- **The browser sidecar** (started with `scripts\browser-serve.ps1`, listening
  on `127.0.0.1:8022`) running browser-use in its own isolated Python at
  `env\browser\python`. It drives the Chrome or Edge already installed on your
  machine — nothing is downloaded. Alan prefers it for *interacting* with a
  page (clicking, screenshots), and it is the fallback for search/read when the
  internet backends are unavailable. Naming an engine in `web.search`
  (`marginalia`/`bing`/`duckduckgo`) explicitly opts into the sidecar's
  scraped engines.

The internet backends are wired on by default (`[tools] internet = true`).
`alan doctor` reports their state; if mcporter or curl are missing it says
exactly that.

Two things worth knowing before you rely on `web.search`:

- **Jina fetches on its own servers.** `web.open` through the internet layer
  cannot be pointed at a machine on your own network — a local address is but a
  string to Jina — whereas the browser path can, so the browser path's guard
  stays: **every fetch is `high_risk`.** Fetching a URL is the capability most
  able to reach something private, so `web.open` needs a permission decision
  before it runs; an agent cannot authorise itself. See
  [ADR-003](docs/decisions/architecture-decisions.md).
- **Search results are for a reading model.** Exa returns semantically-ranked
  results with snippet highlights; when it has nothing trustworthy it returns
  nothing, and Alan says so instead of showing you a confident wrong answer.

---

## Coding

Alan does not write code. You talk to it, it delegates the change to Vibecoder —
a separate headless coding agent that runs a large model behind a real tool
loop — then it reports what actually happened. Vibecoder is installed on its
own (`vibecoder` on PATH); Alan only ever launches it.

That split exists because Alan's bundled model is a 1.5b one, and a
plausible-looking edit from a model that small is worse than no edit at all: you
cannot tell the difference by reading the diff.

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

**Verified, not claimed.** Vibecoder once described a two-line file as "a
comprehensive math library providing symbolic computation, units, complex
numbers, matrices". So Alan checks the filesystem before and after each run and
labels every item *verified on disk* or *claimed, not verified*. `NOT DONE` is
always shown — that report is attached to the reply rather than left for the
model to restate, because a 1.5b model restating it drops the parts it finds
unflattering.

**Writes ask first.** A change needs your confirmation; a read does not. If you
decline, nothing runs. See `[vibecoder]` in `config\settings.toml`.

**Two things to know:**

- Which model writes code is **not** decided by `vibecoder.provider`. That flag
  only reaches Vibecoder's *chat* route; its hybrid router classifies a coding
  task as *heavy* and serves it from `routing.heavyProvider`, which the built-in
  defaults pin to NVIDIA. The real pin lives in `~/.vibecoder/config.json`,
  which Vibecoder deep-merges over those defaults — delete it to go back. Both
  are set to Groq here: free, and reliable where the 550B NVIDIA route failed
  3 of 4 attempts with a silent exit-0 no-op. See `[vibecoder]` for timings.
- The working directory defaults to *the directory Alan is running in*. Run it
  from inside a project. It is refused from Alan's own source tree, your home
  directory and any drive root.

Vibecoder is not installed by default; `alan doctor` reports it either way, and
without it Alan can still chat, browse and search.

---

## Skills (ECC)

Alan reads the [ECC](https://github.com/affaan-m/ECC) library — 293 skills,
94 commands and 68 agent prompts — as a searchable, read-only catalog, wired
through the same permission gate as every other tool:

- **Name one, load one.** "use the tdd-workflow skill", "/code-review", or a
  plain request containing every word of a multi-word skill name ("what is the
  tdd workflow?") plans a `load_skill` step; the entry's instructions then
  arrive in the prompt the way recalled memory does, and the answer follows
  them. A lone common word never matches, and memory/schedule requests keep
  precedence over skill loading.
- **Tools, gated like everything else.** `skill.list`, `skill.search` and
  `skill.read` are `SAFE` and register through the ordinary registry, so an
  operator rule on `skill.*` turns the namespace off — ADR-006 gets no new
  door. `skill.read` caps injected text at `[ecc] max_chars`, because a
  400-line skill would otherwise fill a 2048-token local model's whole
  context; the full text stays reachable with `alan skills --read`.
- **Eight advisory dev tools.** `ecc.run_tests`, `ecc.lint_check`,
  `ecc.format_code`, `ecc.check_coverage`, `ecc.dependency_analyzer`,
  `ecc.security_audit`, `ecc.git_summary` and `ecc.changed_files` detect,
  analyse and *print* commands — they never execute. Git goes through an argv
  list with a validated ref, never a shell string.
- **Browse it.** `alan skills` lists the library, `alan skills <terms>`
  searches it, `alan skills --read <name>` prints one entry in full, and
  `alan doctor` shows what loaded.

On in this install's `settings.toml` (`[ecc] enabled = true`); off in code, so
a fresh checkout behaves exactly as before until you turn it on. The library
itself lives in `vendor\ECC` (git-ignored and pinned; if it is missing,
`alan doctor` prints the one-line clone command). ECC's hooks
are deliberately *not* ported — its permission auto-approve hook has no path
into Alan. Details: [docs/integrations/ecc.md](docs/integrations/ecc.md).

---

## Layout

```
<drive>:\Alan\
├─ Alan.cmd                 # double-click launcher
├─ README.md
├─ requirements.txt         # pins for env\python (see the file for what it excludes)
├─ config\
│   ├─ settings.toml        # provider, model, memory, tools, browser, permissions
│   ├─ .env                 # your API keys (git-ignored)
│   └─ .env.example
├─ env\
│   ├─ python\              # portable Python 3.12.10 + pip + Alan's packages
│   ├─ cognee\python\       # isolated Python for the memory sidecar
│   ├─ browser\python\      # isolated Python for the browser sidecar
│   └─ ollama\              # portable Ollama server
├─ models\                  # local model files (gguf/blobs)
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
└─ tests\                   # 128 tests, stdlib unittest only
```

---

## Portability notes

- **Drive letter free.** Alan detects its own home from the code location, so
  it works whether the USB mounts as `F:`, `G:`, etc.
- **No host installs.** Python and Ollama run straight from `env\`.
- **Ollama models** are redirected to `models\` via `OLLAMA_MODELS`, so nothing
  lands on the host.
- **No port clashes.** The bundled Ollama listens on `127.0.0.1:11435` (not the
  usual `11434`), so Alan coexists peacefully with any Ollama already installed
  on the PC — and always uses its own copy in `env\ollama`.
- **exFAT caveat:** the drive has no Unix permissions or symlinks. That's fine
  for this project; it's why Alan uses the embeddable Python build.

## Performance note

Running a local model directly from a USB stick is slower than from an internal
SSD and adds wear. If speed matters, use a cloud API key, or copy `models\` to
an internal disk and point `OLLAMA_MODELS` there. The code and config still
stay portable on the stick either way.

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

Two things that look slow but are not: `max_tokens` does not affect a short
answer (verified at 64/128/256/1024 — all 0.46–0.69 s), and the memory
sidecar's first-ever write downloads ~800 MB of extractor models.

**When Alan feels slow on a small machine, check RAM before anything else.**
Ollama's own log is the honest instrument: under memory pressure it reports
~0.2–0.5 tokens/second for a model that does 20–60 tok/s when it has room,
which looks like a slow model and is not one. Free RAM and it returns.
`num_ctx` defaults to 2048 and `keep_alive` to `30m` for exactly this reason;
both are overridable in `config\settings.toml` under `[ollama]`.

If a backend is flapping and you do not trust the cached answer, `alan doctor`
always probes fresh, and `ALAN_PROBE_CACHE=0` turns the cache off entirely.

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
| `web.search` reports no trustworthy results | The engine blocked this network or its markup moved. Try `engine = "bing"`, or accept it: Alan refuses to return results that do not match your query |
| `web.open` denied, `risk=high_risk` | Working as intended. Raise the ceiling in `config\settings.toml` under `[permissions]`, or ask in an interactive session where you can confirm |
| Answers feel "hallucinated" from memory | Memory is only injected when it genuinely matches your question — zero word overlap means zero score, so it is dropped. If Alan cites something unrelated, `alan forget --list` shows what is actually stored; see `docs\integrations\repository-audit.md` §7 for a known cognee defect that leaks across datasets |
| Alan gets slower over hours, RAM climbs | An orphaned browser from the sidecar. `scripts\browser-serve.ps1 -Stop` now closes it properly; `alan doctor` shows the sidecar state. Confirm nothing is left with `Get-Process chrome` |
| `code.test` takes a minute, or "collects 0 tests" | Fixed by `pytest.ini`, which pins `testpaths = tests`. Without it `pytest` walked `env\python` and `env\browser\python` - two bundled interpreters full of site-packages test files - burned 73 s collecting, imported third-party test modules, and collected none of Alan's own. `conftest.py` also puts `src` on the path, so `python -m pytest -q` now works from a bare checkout with no `PYTHONPATH` |
| Wrong drive letter in messages | Harmless — paths are derived, not hard-coded |
| PowerShell blocks scripts | Use `Alan.cmd` (it sets the bypass flag) |
| `alan` is not recognised | Open a **new** command prompt — PATH is only read when a window starts. Check with `scripts\install-command.ps1 -Status` |
| `alan: cannot find Alan at "..."` | `ALAN_HOME` points somewhere without `Alan.cmd`. Run `scripts\install-command.ps1` from Alan's real folder, or `setx ALAN_HOME "X:\Alan"`. A stray trailing space or backslash in the value also causes this |
| `browser-serve.ps1 > out.txt` leaves the file locked | Expected: `Start-Process` inherits handles, so the sidecar holds a copy until it exits. See the note in the script |
