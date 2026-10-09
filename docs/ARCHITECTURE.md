# Alan architecture

## What Alan is

One folder, zero host installs. Everything needed -- interpreter, libraries,
model runtime, memory store, code -- sits inside the `Alan\` directory on
whatever drive it happens to be plugged into.

Two properties drive every decision below:

1. **Portable.** `D:\Alan` and `F:\Alan` must behave identically. Nothing may
   depend on a drive letter, on `PATH`, or on something being installed.
2. **Bounded.** A portable assistant runs on an unknown machine, unattended. It
   may not be able to do arbitrary things, it may not retry forever, and it
   may not claim to remember things it never stored.

## Shape

```
                      user
                        |
        +---------------+----------------+
        |                                |
   ask / stream                      CLI (typer)
        |                                |
        +----------> Orchestrator <------+
                        |
     +---------+--------+---------+-----------+------------+
     |         |        |         |           |            |
  Planner   Memory   Router   Registry     Agents      Events
  (intent)  (L0-L5) (models)  + Gate    (7 least-    (jsonl
                                |       privilege)    trace)
                                |
                    Execution / Coding / Browser providers
                                |
             cognee sidecar | json store | mem0 | Ollama
```

`Orchestrator` implements `AgentRuntime` directly. It is the only thing that
decides what happens; every other component is a specialist it calls.

## The request flow

One method, `Orchestrator.run()`, and the order is the design:

| # | Step | Fails how |
|---|------|-----------|
| 1 | Open a trace, emit `request.started` | -- |
| 2 | Plan the request (`HeuristicPlanner`) | never; falls back to a single-step plan |
| 3 | Recall memory for the query | degrades to no context |
| 4 | Route a model for the plan's category | `NoModelAvailable` -> honest failure |
| 5 | Execute each step through the tool registry | per-step failure, recorded |
| 6 | Delegate to subagents if the plan asks | per-agent failure, recorded |
| 7 | Compose the answer from observations | -- |
| 8 | Decide what, if anything, becomes memory | never writes silently |
| 9 | Emit `request.completed` with the outcome | -- |

Three properties are load-bearing:

- **Recall is retrieval, not dumping.** `memory.search()` returns ranked
  `MemoryItem`s that pass into a bounded context block. An item with zero
  lexical overlap scores zero. Alan can answer "I don't know".
- **The capability checked is the one a tool *declares*, not its name.** The
  registry looks the gate up by `ToolSpec.permissions`, and falls back to the
  tool's own name only for specs that declare none. This distinction is
  load-bearing rather than cosmetic: `system.open` declares `app.launch`,
  `web.open` declares `browser.open`, `web.search` declares `browser.search`.
  Looking those up by tool name matched nothing, fell through to a default
  derived from the spec's own declared risk, and so silently ignored every rule
  an operator had written for those capabilities — including which of them need
  approval. The gate was, in effect, asking about capabilities nobody had ever
  configured.
- **A capability needing approval fails closed when there is nobody to ask.**
  `confirmation required` used to be a dead end: the gate returned it, and the
  one caller that could have answered it never passed anything, so any such
  capability was permanently unusable. Approval is now an explicit per-call
  `confirmer`, supplied only by a surface that can genuinely put the question to
  a person (the interactive CLI). The refusal names the setting that would
  change it, rather than a `confirm=True` flag no caller ever set.
- **The approval channel is on the orchestrator config, never on the gate.**
  Every subagent invokes through the same registry and the same gate, so a
  confirmer stored there would be inherited by agents that have nobody to ask —
  an agent could raise its own approval rate by prompting on the operator's
  behalf. A subagent needing approval fails closed instead. And a declining or
  broken prompt is a refusal: a capability that acts without a clear "yes" has
  not been approved, it has merely not been stopped.
- **A subagent's tool access is enforced, not promised.** Every agent received the
  *whole* registry and was trusted to consult its own allow-list first — one
  forgotten check, or one new agent written in a hurry, from any tool in the
  system. Each subagent now receives a `ScopedTools` view that cannot invoke
  outside its allow-list, refuses a tool above its declared `ceiling`, refuses to
  register or unregister anything, and drops `confirm`/`confirmer` rather than
  forwarding them so an agent cannot approve itself. Scopes nest, so delegation
  composes and only ever narrows.
- **A hung tool cannot take the others down with it.** Tool calls used to share a
  four-thread pool. Python cannot interrupt a running thread, so a tool that
  ignored its timeout held one forever; four of those and every later tool timed
  out waiting for a thread that was never going to free up — and because those
  workers are non-daemon and joined at interpreter exit, the process could no
  longer exit either. Each call now runs on its own daemon thread, bounded by a
  semaphore. Abandoning a call leaks only that thread, and one tool leaking
  `_MAX_HUNG` threads is quarantined *by name*, so the rest of Alan keeps working
  and the tool recovers by itself once those threads drain.
- **Only http and https reach Windows.** Any explicit scheme used to count as a
  web address, which made `file://` a way to open a local path while skipping the
  extension allow-list an explicit path must pass, and `smb://` a way to make
  Windows connect to a remote host. Both are refused by name now.
- **A reported open is a claim about the OS, not about the screen.**
  `launched: True` means Windows accepted the request. Several Windows 11
  applications ship as a stub that hands off to a packaged app, so `startfile`
  succeeds and no window appears; the result carries an explicit `confirmed` note
  so the agent reports "started" rather than "is now open".
- **A probe cache entry that is being used keeps being usable.** The cache treated
  an *unchanged* answer as nothing worth recording and returned without touching
  the timestamp, so a healthy backend's entry froze at the moment it was first
  written. The TTL then measured absolute age, not idleness, and Ollama's
  reachability probe — about 3 s on this machine — was paid again every 300 s
  forever, several times per command. Hits now slide the window forward. The
  cache is still bounded: it measures how long a backend has been *unused*, and
  the first real request that fails calls `forget_key`, which deletes the entry
  outright rather than waiting for a clock. `alan doctor` still probes fresh.
- **Startup cost is paid only by the commands that need it.** `cli.py` imported
  `rich.markdown` at module scope, which drags in `markdown_it` and `pygments` —
  ~180 ms — for every command including `version`, which renders no markup at all.
  Those three renderers are now module-level proxies that import on first call and
  then replace themselves with the real class. A PEP 562 module `__getattr__` was
  tried first and does not work here: it serves `cli.Table`, never the global
  lookup inside a function body, which is what every call site does.
- **A secret is not a file to be backed up.** `config/.env` was tracked and copied
  byte-identically into the backup tree — the directory most likely to be zipped,
  uploaded or committed. It held no keys yet, but that was luck, not design: the
  moment one is added it leaks. `verify_tree.py` now excludes it by exact path
  (`SKIP_EXACT`) and purges any existing copy from the mirror on sync.
  `.env.example` stays tracked so the shape and variable names remain protected.
- **Memory writes pass a gate.** Only an explicit "remember" intent, or a
  statement that is already a durable fact about the user, is persisted. A
  transcript is not a memory. `Orchestrator.store_turn()` is the same gate the
  streaming path uses, so there is exactly one rule.
- **Every step is observable.** Each stage emits a structured event carrying the
  trace id and session id, and appends to `data/logs/events.jsonl`.
- **Alan does not write code.** For any request that changes code, Alan plans,
  delegates to `vibecoder` and reports — it never edits a file itself. Its own
  model is a 1.5b one and a plausible-looking edit from a model that small is
  worse than no edit at all, because the user cannot tell the difference by
  reading the diff. The delegation plan ends with a `vibecoder_report` step, so a
  plan that delegates has no way to skip saying what was and was not done.
- **A delegated claim is not proof.** Vibecoder reported "a comprehensive
  JavaScript/TypeScript math library… symbolic computation, units, complex
  numbers, matrices" about a two-line file. File changes are therefore checked
  against the filesystem — mtime and size — before and after the run, and every
  claim is labelled either *verified on disk* or *claimed, not verified*. The
  observation handed to the model leads with a plain-English verdict, because the
  first live run received a raw dict, could not read it, and replied "Vibecoder
  claimed:" about an edit that had demonstrably landed.
- **An exit code is not an outcome.** Vibecoder returns `0` for success, for a
  total no-op, and for "the agent gave up". `_interpret()` never reads
  `returncode`; it reads the transcript and reports `acted=False` for an empty
  log. A run that stops part-way emits no notice at all, so tools with no
  summary and no truncation block is itself reported as unfinished.
- **A positional task is a silent no-op.** Vibecoder's dispatcher accepts a bare
  prompt only when `process.argv.length === 3`; add `--cwd` or `--max-steps` and
  the prompt is discarded — no work, no session, exit 0. Delegation always uses
  `--prompt`.
- **The glyph is not the signal.** Vibecoder's banner decoration changed between
  two runs on this machine (an emoji became a dot, `➡️` became `→`). Tool calls
  are now matched on the ASCII tool name that follows the marker, and only names
  in `_KNOWN_TOOLS` count, so unrelated braces cannot be mistaken for work.
- **A coding agent may not be pointed at its supervisor.** Vibecoder holds the
  operator's keys and a shell, so nothing is a sandbox — but `delegate()` refuses
  a drive root, a system directory, the home directory, and Alan's own source
  tree *before* launching. The last one matters most: an agent allowed to edit
  the code that decides what to delegate to it, and that verifies the result,
  can silence this entire contract by editing the file that writes the report.
- **Writing costs a prompt; reading does not.** `vibecoder.delegate` declares
  `code.delegate_write`, a `MEDIUM_RISK` capability whose policy sets
  `requires_confirmation` — so a write asks first, while `vibecoder.inspect`
  runs without asking. One tool with a `read_only` flag was rejected: if
  inspection also demanded confirmation, every read would cost a prompt, and a
  prompt a user answers reflexively is worse than no prompt at all.

## The permission gate

There is exactly one way to reach a tool: `ToolRegistry.invoke()`. It builds a
`PermissionRequest` and asks `PermissionGate.check()` before the executor runs.

```
SAFE < LOW_RISK < MEDIUM_RISK < HIGH_RISK < PRIVILEGED
```

- `ceiling` in `settings.toml` is the operator's maximum. Agents cannot raise it;
  `delegate()` intersects a child's allow-list with its parent's and takes the
  lower of the two ceilings.
- `FORBIDDEN` capabilities are refused regardless of ceiling.
- `code.delegate_write` is `MEDIUM_RISK` but sets `requires_confirmation`, so it
  asks a person before handing a coding task to Vibecoder. It is medium rather
  than high deliberately: the point is the question, not the refusal, and a
  capability above `ceiling` would simply never run. `vibecoder.inspect` carries
  no such capability and runs unasked.
- `auto_confirm = false` means a `CONFIRMATION_REQUIRED` decision stops the
  tool and tells you, instead of Alan deciding for you.
- Local execution stays jailed: `[tools] execution_root` is the one root the
  executor will touch, and a path outside it is refused, not clamped. Empty
  falls back to `ALAN_HOME`.

Nothing above the gate can be talked out of it. Not a prompt, not a plan, not a
model's own suggestion.

## Capability providers

External systems sit behind Alan-owned interfaces. Swapping one is a
composition-root edit, not a rewrite.

| Interface | Implementations shipped |
|---|---|
| `AgentRuntime` | `Orchestrator` |
| `ModelProvider` | OpenAI, Anthropic, Groq, OpenRouter (the last two through `OpenAIProvider`, which takes the provider name rather than a key), Ollama, `UnavailableModel` |
| `MemoryProvider` | cognee, mem0, `JsonMemory` |
| `ToolProvider` | `ToolRegistry` over the standard tool set; `SkillTools` and `EccDevTools` add the gated, read-only ECC tools when `[ecc]` is on |
| `ExecutionProvider` | `LocalExecution` (jailed to `[tools] execution_root`, argv-only, timeouts) |
| `CodingProvider` | `LocalCoding` (analyse / edit / test / review) — registered when `[tools] coding = true`, but the planner no longer emits these steps |
| `BrowserProvider` | `SidecarBrowser` (browser-use over loopback HTTP, port 8022) |
| `Vibecoder` | `VibecoderTools` (`inspect` / `delegate` / `report`) |

### The browser sidecar

`SidecarBrowser` is the only `BrowserProvider` implementation, and it talks to
nothing but `127.0.0.1:8022`. It imports no part of browser-use, so Alan's main
environment stays free of browser-use's exact pins (ADR-003).

The sidecar runs in `env\browser\python`, a separate environment whose `._pth`
has Alan's `src` stripped out, so it cannot `import alan`. It launches the
host's installed Chrome or Edge headless and drives it over CDP — no bundled
browser binary, no driver download.

Three behaviours are worth knowing before debugging it:

- **Search is unreliable by nature and is treated as such.** Search engines are
  scraped HTML pages. DuckDuckGo and Mojeek block this network outright; Bing
  sometimes returns cached results for an unrelated query while reporting the
  right URL and title. So the default engine is `marginalia` with a `bing`
  fallback, every engine gets a retry, and every result is relevance-filtered —
  zero lexical overlap with the query means the result is discarded, not
  returned. An honest "no trustworthy results" beats a confident wrong answer.
- **The capability is wired on but starts nothing.** `[tools] browser = true`
  builds the provider; no browser launches until a tool is called and the
  permission gate approves it. With the sidecar stopped, `alan doctor` says so
  and the tools return an actionable message naming `browser-serve.ps1` and
  port 8022 — never a traceback.
- **browser-use's agent loop is unused on purpose.** Alan already has a planner;
  a second one inside the browser would be two competing brains. Alan decides
  what happens; browser-use only clicks and types.
- **The sidecar must close its own browser.** `Stop-Process -Force` calls
  `TerminateProcess`, which on Windows runs no signal handler, no `finally` and
  no `atexit` — so the cleanup path that existed was on a code path that was
  never taken. Sixteen start/stop cycles left **125 Chrome processes and 731 MB**
  resident on a 4 GB machine, which is what made Alan itself slow. Now
  `POST /shutdown` closes the session before exiting, `browser-serve.ps1 -Stop`
  calls it and waits before considering a force-kill, and a startup reaper
  terminates any browser still holding *Alan's own* profile path. Verified by
  measurement rather than inspection: a full start → search → stop cycle goes
  0 → 9 → **0** Chrome processes and leaves **0** directories behind.
- **The profile path is deliberately ugly, and must stay that way.**
  `data/browser-profile/browser-use-user-data-dir-sidecar-8022`. browser-use's
  `_copy_profile` copies any supplied profile into a *new random temp directory*,
  so a caller cannot own the path it asked for; including the literal
  `browser-use-user-data-dir-` marker is that function's own "already a temp
  directory, do not copy" escape hatch. The path has to be stable because it is
  how an orphaned browser is identified — a random path per launch cannot be
  matched, and the reaping above stops working. **Do not rename it.**
  `_PROFILE_MARKER` in `src/browser_sidecar.py` says the same thing in code.
- **Reaping must happen before the launch, not only at startup.** A browser that
  starts *after* the sidecar did — or that outlives a session rebuild — still
  holds the profile, and the next launch then fails in a way that points
  somewhere else entirely: Windows treats the new Chrome as a second-instance
  message, hands over its command line and exits, so browser-use reports
  "exited before CDP became available" and every reading of that is a broken
  Chrome install. `_get_session()` reaps first and clears the stale profile, and
  the reaper escalates from `terminate` to `kill` for anything that has not
  actually exited.

### Opening applications and websites

"Open YouTube" means *put it on my screen*. `web.open` means *tell me what this
page says*, and drives a headless browser that returns text. Folding the first
meaning into the second would mean raising a HIGH_RISK network capability to
serve a MEDIUM_RISK local one — and would still show the user nothing. So opening
is its own capability, `system.open` → `app.launch`, at `MEDIUM_RISK`: the same
tier as `shell.execute`, and reachable at the default ceiling without the
operator raising anything.

`tools/opening.py` resolves a target through places Windows itself treats as
authoritative — `App Paths` in the registry, Start Menu shortcuts, `PATH`, then
a short table of built-ins that predate `App Paths`. A URL goes straight to the
system's registered handler. A bare word is read as a website only as a last
resort, and the result carries `assumed: true` when it does, so the caller can
say "I could not find an application called X, so I opened X.com" rather than
pretending it was certain. That is the same assumption as a hard-coded list of
well-known sites, minus the staleness and minus the sites nobody remembered.

Four decisions are load-bearing:

- **No shell, ever.** `os.startfile` takes a path or URL and never a command
  string, so there is no interpreter to inject into. The test asserts this
  against the parsed syntax tree rather than by grepping the module, because a
  comment can assert a safety property as fluently as it can violate one.
- **Metacharacters are refused, not escaped.** Escaping would imply we meant to
  honour them.
- **No arguments.** "Open notepad" is a launcher; "run this with these flags" is
  `shell.execute`, gated separately. An explicit path must also carry an
  allow-listed extension, so this cannot become "run this file".
- **Start Menu matching is strict on purpose.** Substring search looks
  attractive and is actively harmful: on a machine with SQL Server and a
  Photoshop codec installed it resolves `server` to *SQL Server Management
  Studio*, `code` to *PSD Codec User Guide* and `word` to *Wordpad*. Opening the
  wrong program in answer to a correct request is worse than reporting nothing.
  Only the whole shortcut name or its **final** word matches, which is the two
  forms Windows actually uses.

The `open` intent is anchored to the start of the request, because "open" is an
ordinary verb mid-sentence. A determiner separates the two readings that spelling
cannot: "open the file explorer" is an application and "run the tests" is not,
so for those the planner asks the operating system what is installed. An
*assumed* answer counts as no answer — a bare word suffixed with `.com` always
parses, so accepting it would confirm every English noun is an application.

### Whole-device filesystem access

`filesystem.read` / `filesystem.list` / `filesystem.write` / `filesystem.delete`
are ordinary tool specs like any other, but for a long time nothing reached
them: the planner mapped a request like "read D:/work/notes.txt" onto
`vibecoder_inspect` (or plain chat), so the tools were registered and
unreachable at the same time. The planner now has a filesystem branch.

Routing is gated on something concrete, because the failure that matters here
is the destructive one — a chat request about deleting things must not become a
delete:

- **read** needs a read verb (`read`/`cat`/`type`/`print`) *and* a path that
  carries a separator or a drive letter. A path with no extension names a
  directory, so it lists instead. A bare filename ("read math.js") carries no
  separator and keeps its old `vibecoder_inspect` intent.
- **list** is checked before read, so "read the files in D:/work" lists. It
  needs a listing verb and one of the words *files/directories/folders/contents*.
- **write** needs a path *and* content ("write D:/a.txt with content hello",
  "write hello to D:/a.txt"). Prose without a path — "write a test for the
  cache" — keeps its coding intent.
- **delete** needs the *tool* to be named (`filesystem.delete`/`fs.delete`) and a
  path after it. The bare sentence "delete the file notes.txt" stays chat.

"Open" keeps precedence at the top of the request, because `open` already means
launch: only an *absolute* source path (`open D:/work/notes.txt`) is read where
it lies; a relative one (`open src/core/planner.py`) stays the Vibecoder reader.

The tool mapping lives in `_CAPABILITY_HANDLERS` (`fs_read → filesystem.read`,
and so on) and the intent allow-list in `_allowed_for`, so a filesystem plan
escalates through the same `requires_capability()` path as every other command
rather than being answered conversationally.

### The execution jail

`[tools] execution = true` builds a `LocalExecution`, and `execution_root`
decides how much of the machine it may touch. Any resolved path outside that
root is refused by the executor itself, so "whole-device" access is a scoped
privilege, not an unrestricted one: the shipped configuration points at `D:\`,
which leaves `C:\Windows` and the rest of the system unreachable even while the
D: drive is fully usable. An empty root falls back to Alan's own folder — the
conservative default. The permission gate is unchanged and still sits in front
of every call: turning execution on widens *what the executor will allow*, not
what the gate will auto-approve.

### Streaming is not tool-less

`assistant.stream()` calls the routed model directly, skipping the plan,
because token-by-token output is worth more than a plan and a plan costs
seconds. The exception is a request that needs a capability. Told to "open
youtube", a tool-less path cannot open anything, so the model answers the only
way it can and says it cannot help — a correct response to a request the user
believed was being carried out, which is exactly why the bug was invisible from
the output. `Orchestrator.requires_capability()` consults the deterministic plan
(regex only, no model call, microseconds) and escalates to the full pipeline
when a tool is needed. Conversation pays one regex pass; a command is never
answered with small talk.

### The tool registry

12 tools by default — 9 standard (`filesystem.*`, `memory.*`, `system.*`,
`web.*`) plus `vibecoder.inspect` / `vibecoder.delegate` / `vibecoder.report`.
Adding `[tools] coding = true` brings in four more — `code.analyze` /
`code.edit` / `code.test` / `code.review` — though the planner no longer emits
those steps, so nothing reaches them in normal use. Each entry declares
`name`, `description`, `input_schema`, `output_schema`, `permissions`, `risk`,
`executor`, `timeout`, `logging_policy` and `tags`. The orchestrator filters plan
arguments against `input_schema` before invoking, so a plan can never pass a key
the tool does not accept.

`alan doctor` counts what is *reachable*, not what exists: `shell.execute`,
`shell.python` and `filesystem.delete` are registered only when
`[tools] execution = true`, and the four `code.*` tools only when `coding =
true`. The ECC providers (`skill.*`, `ecc.*`) add their own entries when
`[ecc]` is on. A low count here is configuration being reported honestly, not a
missing tool.

Namespaces: `filesystem`, `shell`, `system`, `memory`, `web`, `code`.

### The coding provider

`LocalCoding` answers four mechanical questions about a repository - `analyze`,
`edit`, `test`, `review` - and nothing else. Deciding *what* to change is the
CodingAgent's job; keeping this layer testable without a model is the point.
The diff is the unit of review: `edit` writes files and returns a real unified
diff, read before the write so a caller still gets the diff when the write
fails.

`analyze` counts source files by suffix to pick a dominant language, prunes
vendored and hidden directories in place while walking, and matches test files
by name to decide whether a runner exists. Pruning is not an optimisation
detail: walking `node_modules` to count source files turns a one-second
question into a thirty-second one. An unlisted language degrades to "no test
command" rather than to a wrong one.

Only languages with a recognisable runner get one, and Python deliberately runs
through *this* interpreter (`python -m pytest -q`, not a bare `pytest`) so a
portable install on removable media uses the Python it shipped with.

The inferred command is only as good as the repository's own test configuration,
so Alan ships one: `pytest.ini` pins `testpaths = tests` and excludes `env`.
That exclusion is load-bearing rather than tidy. Alan bundles two full
interpreters under `env\`, and unbounded `pytest` walks both, imports test
modules out of their `site-packages`, and buries the real suite. Measured on
this repository before the fix: 73 s of collection, **zero** of Alan's own
tests collected, and a crash on a `win32comext` test file. `conftest.py` adds
`src` to the path, so the inferred command works from a bare checkout with no
`PYTHONPATH` - which matters because that command is reachable through
`code.test`, not just by hand.

`run_tests` distinguishes *the suite failed* from *the runner is missing*: exit
127 means the process never launched, while `python -m pytest` starts fine and
exits 1 with "No module named". Both mean the runner is absent, and collapsing
them sends the coding agent to fix tests that were never run.

## The model router

Categories (`ModelCategory`): `fast`, `reasoning`, `coding`, `vision`,
`embedding`, `general`, alongside each provider's `local` flag.

`route()` ranks every eligible backend and takes the top one; failover walks the
same ordered list. Failover is bounded: no backend is retried twice, and
`NoModelAvailable` is raised rather than looping.

Ranking is a tuple compared left to right, so **slot order *is* order of
precedence**:

| Slot | Signal | Applies when |
|---|---|---|
| 0 | `prefer` | the caller named a backend (`-100`) |
| 1 | category | the backend claims the task (`+10` if it does not) |
| 2 | `priority` | always; configured in `settings.toml`, lower wins |
| 3 | locality | `-1` for local — only a complete tie decides this |

Two things were wrong here until recently, and neither failed a test:

- **Priority used to sit below locality, and only at `complexity >= 4`.**
  Alan's plans score 1–3, so priority never applied at all and local won every
  time — adding an API key changed nothing, which is indistinguishable from an
  invalid key. Both gates are removed; `complexity` now only reaches the trace
  and the progress line.
- **Categories were guessed from the model name**, and `gpt-4o-mini` and
  `qwen2.5:1.5b` guess identically, so the category slot could not tell a coding
  specialist from a chat model. `settings.toml` now takes `categories` per
  provider, with the guess kept as the fallback.

Provider names come from one list, `config.PROVIDER_NAMES`, shared by
`llm._NAMES` and `runtime.build_router`. Those two used to keep separate copies,
so a provider added to one was silently unknown to the other — reached by one
code path but not the router, which again reads as "my key does nothing".

Privacy is a **filter, not a score**: `local_only` and `allow_remote = false`
remove remote models from the candidate list rather than ranking them lower.
When no local model is running, Alan refuses and says how to fix it instead of
quietly sending your memory to a cloud API.

Measured here with a Groq key configured: **3.5 s vs 26.7 s** for the same
prompt routed to local Ollama. One prompt on a memory-pressured host, not a
benchmark — but it is the difference between an answer and a wait.

## Subagents

Seven, each with an explicit tool allow-list, a risk ceiling, and a
`needs_model` flag:

| Agent | May use | Needs a model |
|---|---|---|
| Planner | -- | no |
| Research | `web.*`, `memory.*` | yes |
| Coding | `code.*`, `filesystem.*` | no |
| Browser | `web.*` | no |
| Memory | `memory.*` | yes |
| System | `system.*` | no |
| Reviewer | -- | yes |

Agents with `needs_model = False` run with an `UnavailableModel` stub, so
tool-and-state work still functions with no model backend at all.

## Memory hierarchy

`L0` transient context -> `L1` session -> `L2` working -> `L3` project ->
`L4` user -> `L5` long-term.

Every item carries `id`, `type`, `source`, `timestamp`, `confidence`,
`importance`, `scope`, `privacy`, `last_accessed`, `expiration`. Promotion
between levels is explicit; `allow_autonomous_promotion` is off, so model
output cannot write itself into long-term memory.

All persistence goes through `core/atomic.py::write_atomic` -- a unique temp
name per write plus a bounded `PermissionError` retry. A shared temp file
produces a real `WinError 5` race on Windows when several threads persist at
once; this is why that helper exists.

Backends degrade rather than fail: `cognee -> mem0 -> json`, chosen by
`build_memory_provider`, with the reason reported by `alan doctor`.

### The relevance gate

A backend decides *which candidates to consider*. `memory/relevance.py` decides
*which of those may be asserted as something Alan knows*. Those are different
jobs, and `alan.memory.relevance.gate` is the boundary between them.

The rule is one line: **zero lexical overlap scores zero.** An item sharing no
meaningful term with the question is not a weak match, it is not a match, and
rendering it under "Relevant memory" is worse than rendering nothing -- it
looks like knowledge Alan has when it does not. That failure mode is the entire
reason the module exists: measured against cognee 1.6.2, asking "what is the
capital of France?" and asking the user's own name returned *the same three
passages*, all about a browser sidecar port, and all of them were being
injected into the prompt. See `repository-audit.md` §7.

The gate sits in `MemoryProvider.context_block`, so every backend inherits it
without being taught it, and in `_recall`, so the `memory.retrieved` hit count
in the trace is a count of what actually reached the prompt rather than of what
a backend proposed. Ranking bonuses (level, importance, confidence) are only
ever applied *after* a real overlap is required -- if a bonus could raise a
zero, the loudest unrelated note in the store would get asserted.

Semantic recall is kept as the candidate generator. What is removed is the
backend's authority to decide what Alan claims to know, which is the one thing
this product promised and the one thing it could not previously deliver:
**Alan can say "I don't know."**

## Startup cost

Every invocation used to re-ask every backend whether it was up: a cognee
health check, an Ollama model list, a browser sidecar ping. ~5 s before the
first token, for answers that had not changed since the previous command.
`core/probecache.py` caches them in `data/state/probes.json`.

The TTLs are asymmetric on purpose, and so is the bypass:

* **300 s for a success.** A model that is pulled does not un-pull itself.
* **3 s for a failure.** A sidecar that is still booting must be picked up as
  soon as it is listening; caching failures for long would be worse than not
  caching at all.
* **`alan doctor` clears the cache and probes fresh.** A diagnostic that
  reported a remembered "ready" is worse than no diagnostic.
* **A provider that fails a real request drops its own entry.** A stale "up"
  must not outlive the backend that produced it.
* **`ALAN_PROBE_CACHE=0` disables it** -- what the test suite uses, since tests
  stub transports and must not be answered by a probe from an earlier run.

## Learning from failure

Alan used to report a failure and forget it: the same request asked again took
the same route, met the same obstacle and failed the same way, and the only
thing that changed was how quickly it got there. `core/lessons.py` is the record
that outlives the run, in `data/state/lessons.json`.

* **Consulted before planning, not after.** A hint arriving with the
  observations is advice about work already done; this is advice about work not
  to start. It reaches the model through the system prompt -- the heuristic
  planner discards `context`, so the prompt is the channel that actually carries
  it -- and `assistant.stream()` asks for it directly, so the one path that skips
  planning is not also the one path that cannot learn.
* **Open only while it is still failing.** `failures > successes` is the test.
  The same task succeeding closes the lesson, and a later relapse reopens it.
* **Carries what was tried, not just what broke.** The remedy Alan hands itself
  is the approach that eventually worked; until one exists, the approaches
  already burned are what it gets.
* **Exactly one record per run.** Steps that degraded are collected and written
  once at the end, so a plan with three broken steps is one failed attempt, not
  three -- otherwise `failures` would count steps and every lesson would look
  worse than it is.
* **A run that only half works is not a success.** A reply produced while a step
  came back empty -- a delegation listing outstanding work, a tool that is not
  registered -- never closes the lesson it opened, because closing it would
  leave the next attempt *less* informed than this one.
* **Matching is by content words.** Overlap of the non-stopword tokens, so a
  terse retry still finds the long request it came from, and two different jobs
  that share only scaffolding do not. Every hint quotes the task it belongs to:
  the match is approximate, and the reader has to be able to tell which task is
  being talked about.
* **Never a dependency.** Every entrypoint catches -- a store that cannot be
  read or written costs the hint, never the answer. Reasons are redacted on the
  way in (they come from backend errors), the file is capped at 200 lessons, and
  `alan lessons` shows what is held while `alan lessons --clear` forgets it.

Learning is off in `OrchestratorConfig` by default, so an orchestrator built by
hand writes nothing shared, and `build_runtime` switches it on: that is the
wiring with an operator behind it. Supplying a `LessonStore` directly enables it
too, since passing one is the caller saying "learn into this".

## Module map

| Path | Responsibility |
|---|---|
| `runtime.py` | Composition root. `build_runtime`, `build_gate`, `build_router` |
| `interfaces/` | Types and ABCs. No behaviour, no dependencies |
| `core/orchestrator.py` | The request flow, delegation, memory decision |
| `core/planner.py` | `HeuristicPlanner`: deterministic intent classification |
| `core/events.py` | `EventBus`, `Events`, jsonl trace writer |
| `core/lessons.py` | What failed before, and what eventually worked |
| `core/atomic.py` | Crash- and collision-safe file writes |
| `core/probecache.py` | Short-lived backend liveness cache |
| `memory/relevance.py` | The gate: what may be asserted as memory |
| `permissions/gate.py` | `PermissionGate`, `Policy`, `default_gate`, `FORBIDDEN` |
| `tools/registry.py` | Specs, the gate call, timeouts, execution |
| `tools/standard.py` | The 12 built-in tools |
| `tools/opening.py` | Resolving "open X" to a program or a website |
| `tools/execution.py` | `LocalExecution`: jailed, argv-only, timeouts |
| `tools/coding.py` | `LocalCoding`: analyse / edit / test / review |
| `ecc/` | ECC catalog (skills, commands, agents) and the gated `skill.*` / advisory `ecc.*` tools |
| `models/router.py` | Category routing and bounded failover |
| `memory/providers/` | cognee, mem0, json, and the fallback factory |
| `agents/standard.py` | The seven subagents |
| `assistant.py` | Interactive chat only. Delegates everything else |
| `cli.py` | Typer commands |

`assistant.py` owns conversation state and nothing else. `ask()` runs the full
pipeline; `stream()` calls the routed model directly for latency and then
applies the orchestrator's memory gate. It used to remember every turn
unconditionally -- that is precisely what the gate exists to prevent.

## Portability mechanics

- `ALAN_HOME` is derived from `config.py`'s own path
  (`<home>/src/alan/config.py` -> `<home>`), so a changing drive letter is
  irrelevant. A test asserts no drive letter is hard-coded anywhere.
- The embeddable Python build uses `python312._pth`; `..\..\src` is listed
  there so `import alan` works without `PYTHONPATH`, which isolated mode
  ignores.
- `OLLAMA_MODELS` points at `<home>/models`, keeping model blobs on the stick.
- Bundled Ollama binds `127.0.0.1:11435` so it never collides with a host
  Ollama on `11434`.
- The cognee sidecar runs on `127.0.0.1:8012` from a **second** embeddable
  Python at `env\cognee\python`, so cognee's heavy native dependencies cannot
  break the assistant. Keyless local mode: `GRAPH_EXTRACTOR=gliner_demo`,
  `EMBEDDING_PROVIDER=fastembed`, kuzu + sqlite + lancedb, all under
  `data\cognee`. Cold start is 3-4 minutes (relational migrations plus GLiNER
  and fastembed loading), so `cognee-serve.ps1` waits up to 600 s for `/health`
  and distinguishes a crashed process from one still starting.
- The browser sidecar runs on `127.0.0.1:8022` from a **third** embeddable
  Python at `env\browser\python`. browser-use pins `openai`, `rich`, `requests`
  and `posthog` with `==`; installing it into `env\python` would downgrade the
  provider layer and the CLI. Both sidecar environments have `..\..\src`
  stripped from their `._pth`, so neither can `import alan`.
- Test runners use `sys.executable -m pytest`, never a bare `pytest`. The bundled
  `Scripts` directory is deliberately not on `PATH`, so a bare command cannot
  work -- and a test asserts the suite genuinely ran rather than silently
  doing nothing.
- `scripts/run.ps1` wires it together; `scripts/selftest.ps1` verifies it
  offline.

Two Windows gotchas the setup scripts handle: `cognee[gliner]` drags in CUDA
PyTorch (the CPU wheel is pre-installed), and ladybug needs the OpenSSL 3 DLLs
copied into `<python>\DLLs`.

## Why embeddable Python?

The drive is exFAT: no permissions, no symlinks. A normal `venv` and many
installers assume both. The official *embeddable* CPython zip is
self-contained, needs no installer, and sidesteps permission issues. Its main
quirk -- isolated mode, ignoring `PYTHONPATH` -- is handled via `._pth`.

## Where the design came from

Six external projects were audited and none of them is vendored. What was
taken is architectural, and every decision is recorded in
`docs/decisions/architecture-decisions.md` with the reason. See
`docs/integrations/dependencies.md` for the per-project record.
