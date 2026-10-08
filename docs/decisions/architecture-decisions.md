# Architecture decisions

Each entry records a decision, the alternatives that were rejected, and what
would make us revisit it. Context for the source evidence is in
[../integrations/repository-audit.md](../integrations/repository-audit.md).

---

## ADR-001 — Port DeepSeek Harness's architecture into Python; do not ship Node

**Status:** accepted · **Date:** 2026-10-04

### Context
DeepSeek Harness (`5badb15009ae`) is the brief's designated primary reference
for Alan's agent runtime. It is a **TypeScript** monorepo on Node
`^22.19.0 || >=24.0.0` with pnpm, built on the Cordis composition framework,
14,208 files, MIT licensed, and self-described as a developer preview with
guaranteed breaking changes.

### Decision
Reimplement the harness *architecture* in Python inside `src/alan/core/`:
a service/plugin registry, a typed event bus, session lifecycle, job execution
separation, and subagent delegation. Pin the DSH commit as a design reference
only. Ship no Node runtime, no pnpm workspace and no DSH process.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Fork DSH and rename to Alan | Explicitly forbidden by the brief; creates a Node product, not a Python one, and inherits alpha churn |
| Bundle portable Node + DSH sidecar | Adds ~80 MB Node plus a 263 MB monorepo and a pnpm build; creates a second competing runtime (forbidden by brief §6/§21); breaks "one folder, Python-only"; adds a third supervised process next to Ollama and cognee |
| Ignore DSH | Discards the best available source for plugin/event/session design |

### Consequences
Alan owns its orchestration layer outright, which is exactly what the brief
requires. The cost is that DSH's Cordis-specific ideas must be re-derived rather
than imported, and Alan's plugin system will not be bug-compatible with DSH's.
An optional adapter that talks to a running `dsh web` server (`:3080`) remains
possible later as a non-shipped power tool.

---

## ADR-002 — Two memory backends behind one `MemoryProvider`

**Status:** accepted · **Date:** 2026-10-04

### Context
Alan's existing persistent memory is a **cognee sidecar** (REST on `:8012`,
keyless local mode: GLiNER + fastembed, kuzu + lancedb + sqlite). It works
offline and is verified. The brief also nominates **mem0** for long-term
semantic memory. mem0 is Apache-2.0 and Python, with compatible `>=` pins, but
needs an LLM for extraction.

### Decision
Define a single Alan-owned `MemoryProvider` interface. Provide two
implementations — `cognee` (default, already proven) and `mem0` — selectable by
configuration. mem0 is pointed at the **local Ollama** server so Alan stays
keyless and offline. No memory code outside the provider implementations may
import either library.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Replace cognee with mem0 | Throws away verified working functionality and the offline keyless path, violating "remove working functionality without a migration path" |
| Run both unconditionally | Duplicate retrieval paths, doubled write latency (~15 s per cognify), no benefit |
| Keep cognee only | Leaves the brief's semantic-memory requirement unmet |

### Consequences
A swappable backend, at the cost of one interface to maintain. mem0's
`posthog` analytics dependency is a hard install requirement, so telemetry is
disabled explicitly at construction. mem0 adds ~40 MB to the main environment.

---

## ADR-003 — browser-use runs as an isolated sidecar

**Status:** accepted, **amended 2026-10-04** (Playwright premise no longer holds;
default flipped on) · **Date:** 2026-10-04

### Context
browser-use is Python and MIT, and is the strongest available browser
automation. But it pins dependencies with `==`, and several of those pins
conflict with Alan's main environment: `openai==2.26.0` (Alan has 3.24.0),
`rich==14.3.3` (Alan has 15.0.0), `requests==2.33.0` (Alan has 2.34.2),
`posthog==7.7.0` (Alan has 7.62.1). Installing it in place would downgrade
Alan's OpenAI provider and change CLI rendering.

The original ADR also assumed browser-use needed a Playwright Chromium binary
(~150 MB), which breaks "zero host installs". **That premise is now false.**
browser-use 0.13.10 dropped Playwright entirely; its `pyproject.toml` at commit
`5c892e013a73` depends on `cdp-use==1.4.5` and `browser-harness==0.1.13` with no
Playwright anywhere. There is no driver to fetch and no bundled browser. The
sidecar launches the host's own installed Chrome (or Edge) headless and drives
it over CDP. The portability objection evaporated on its own, which is why this
ADR was amended rather than left to rot.

### Decision
Run browser-use in its **own sidecar environment** — the same isolation pattern
the cognee sidecar already proves — reached only through Alan's `BrowserProvider`
over a loopback HTTP boundary on port 8022. Neither sidecar environment has
`..\..\src` on its `._pth`, so the sidecar physically cannot `import alan`.

Three further decisions follow from what the library actually offers:

1. **browser-use's agent loop is not used.** Alan already has a planner;
   running a second one inside the browser is the "two competing brains" failure
   mode. Alan decides *what* to do; browser-use performs the mechanical clicking
   and typing.
2. **`extract_content` is not used.** It requires an LLM. Reading
   `document.body.innerText` is both cheaper and more predictable than asking a
   1.5B model to summarise a page.
3. **browser-use 0.13 is async**, so the sidecar runs a private asyncio loop on a
   daemon thread, which keeps one browser session alive across the synchronous
   HTTP requests Alan's tools make.

The capability is now **wired on by default** (`[tools] browser = true`) but
still starts no browser process on its own. Nothing is fetched until a tool is
called and the permission gate has approved it, `alan doctor` shows whether the
sidecar is reachable, and a stopped sidecar produces an actionable message
rather than a traceback.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Install into the main environment | Downgrades `openai` 3→2 and breaks the provider layer — dependency hell, explicitly forbidden |
| Vendor a patched browser-use | Modifying third-party source is a last resort; the conflict is upstream's exact pins, not a bug |
| Bundle a browser binary | Not needed: 0.13 drives an installed Chrome over CDP. Kept as a rejected option because it remains the wrong trade on an exFAT stick |
| Use browser-use's agent loop | A second planner. Alan must remain the only thing deciding what happens next |
| Leave the capability off by default | Reserving it for a capability that now has no setup cost other than starting a script means shipping a working feature that reports itself broken |

### Consequences
Browser automation is real and needs its own setup script, but it no longer
needs a browser download, so the capability is cheap to enable. In exchange,
Alan's provider layer and CLI stay intact, and the highest-risk capability sits
behind a process boundary plus a permission gate — the correct shape for it
regardless.

Two library quirks are pinned by tests because they look like Alan bugs if you
meet them cold: `Element.evaluate` receives no element reference (so `el` is
undefined and any expression touching it throws — `Page.evaluate` with explicit
`*args` is used instead), and `page.screenshot()` returns a base64 **string**
rather than bytes.

A third quirk is Alan's own, and it is the reason long-lived sessions need a
recovery path. The session is cached for the life of the sidecar, which is what
lets `click` act on the page `open` left behind. But a cached session can die —
the browser crashes, the watchdog resets it, someone closes the window — and
`get_current_page()` then returns `None`. The first version surfaced that as
`'NoneType' object has no attribute 'goto'`: an internal detail leaked to the
user, and the capability stayed broken until a human restarted the sidecar.
`_current_page()` now retries once with a fresh session and, failing that, says
so in words. A capability that is temporarily slow beats one that is
permanently dead until someone notices.

Search is a special case. Search engines are scraped HTML pages, so they break
silently: measured from this machine, DuckDuckGo serves a captcha wall, Mojeek
returns 403, and Bing sometimes returns cached results for a *different query*
while reporting the correct URL and title. Alan's default is therefore
`marginalia` with `bing` fallback, and every result is relevance-filtered —
anything with zero lexical overlap with the query is discarded rather than
returned. This mirrors the rule already applied to memory recall.

---

## ADR-004 — Implement execution natively; Open Interpreter is reference only

**Status:** accepted · **Date:** 2026-10-04

### Context
Open Interpreter is now a **Rust** fork of OpenAI Codex (4,813 `.rs` files,
Bazel, ~287 MB) that installs by piping a remote script into a shell
(`curl … | sh`, `irm … | iex`).

### Decision
Write Alan's own `ExecutionProvider` in Python: `ShellExecutor`,
`PythonExecutor`, `FilesystemExecutor`, `ProcessExecutor`. Take only the
*concepts* (harness abstraction, cheap-model-first design) from Open
Interpreter.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Depend on Open Interpreter | Rust toolchain, 287 MB, untrusted remote-script install, and a terminal agent with broad local authority — for capability that is ~200 lines of `subprocess` |
| Shell out to `interpreter` | Requires the host to have it installed; breaks portability |

### Consequences
Alan owns every execution permission decision outright, with no subprocess to
sandbox and nothing untrusted to audit. Reconsider only if Alan later needs
Open Interpreter's model-specific harness tuning.

---

## ADR-005 — CodingAgent is Alan's own, decomposed like SWE-agent

**Status:** accepted · **Date:** 2026-10-04

### Context
SWE-agent (`3ea751c087f3`, MIT) shows a disciplined coding agent: a narrow set
of single-purpose tools, a controlled loop, and the diff as the unit of review.
Its dependencies are research-oriented (`datasets`, `pandas`, `flask`,
`swe-rex`) and unused by Alan.

### Decision
Implement `RepositoryAnalyzer → CodeEditor → TestRunner → DiffReviewer →
PermissionGate` natively, with approval requested before risky actions.
SWE-agent may later be invoked as an isolated subprocess for very hard issues,
behind the same `CodingProvider` interface.

### Consequences
A small, testable coding agent with no research baggage. No SWE-agent
dependency and therefore no `datasets`/`pandas` bloat on the stick.

---

## ADR-006 — Permission gate is mandatory and non-bypassable

**Status:** accepted · **Date:** 2026-10-04

### Context
Brief §12 mandates risk tiers, and §16 requires treating every external
integration as untrusted. Several integrations can act on the machine
(shell, filesystem, browser, package installs).

### Decision
Five tiers — `SAFE`, `LOW_RISK`, `MEDIUM_RISK`, `HIGH_RISK`, `PRIVILEGED` —
enforced in one place, inside the tool registry's invocation path. No agent,
subagent or tool may request an execution path that skips the gate. Every
invocation is attributed to `(request, agent, tool, session, timestamp)` and
logged with secrets redacted.

### Consequences
Adds a check on every tool call and forces every new tool to declare its risk.
In exchange there is exactly one place to audit for privilege escalation.

---

## ADR-007 — Develop on `D:`, distribute to the USB last

**Status:** accepted · **Date:** 2026-10-04

### Context
The USB volume is exFAT with **128 KB clusters**, so every small file consumes
a full cluster. Copying the build to it ran at ~4 files/s and then degraded to
~0.5 files/s with `ERROR 1006` ("volume externally altered"), leaving 6,233
files (64 MB) uncopied. The stick is degrading.

### Decision
Develop, test and demo on `D:\Alan`. Treat the USB as a final distribution step,
and reformat it with a smaller allocation unit (32 KB) before the next full sync.

---

## ADR-008 — Always-on operation: a task scheduler, an approval queue, operator rules

**Status:** accepted · **Date:** 2026-10-08

### Context
Phase 1 makes Alan "better than OpenAI Dots / Grok Bot" on the one axis those
audits (see [../integrations/repository-audit.md](../integrations/repository-audit.md))
flag hardest: a background loop that reliably does the things a person asked
for, without starting loops nobody can stop. Alan's surface stays the friendly
`you>` chat; the new machinery is invisible plumbing behind it.

Three constraints shaped the design. **Concurrency must be structural.** Grok
Bot's lesson is that a configurable "max_task_attemps" re-applies a task nobody
can stop; so Alan runs one task at a time, and that is not a setting. **A "no"
must not become a nag.** Rejecting a scheduled task pauses it; it cannot retry
until a human resumes it. **Rules exist at the configuration file, not in code.**
The tiered gate (ADR-006) is coarse; operators tighten or loosen it per action
class in `settings.toml`.

### Decision
A `Scheduler` (one daemon tick thread, `[scheduler]` in `settings.toml`) runs
due tasks sequentially through a `TaskRunner` protocol — `LocalRunner` today,
orchestrator-backed, remote later. State lives in an atomically written
`data/state/schedule.json`; a corrupt file is preserved beside the store and
the store starts empty rather than bricking Alan. Timing phrases are parsed
from plain English ("every morning", "at 07:30 daily", "in 2 hours") or refused
with the accepted forms — never guessed at.

Approvability unchanged in direction (ADR-006): a run that stops at the
permission gate becomes `waiting_approval` and files an `Approval` in the queue
keyed by an exact action fingerprint. **Approving re-queues the task and caches
the fingerprint** so the identical re-run passes without asking. **Rejecting
pauses the task** and leaves no cache. Decisions have no expiry. The queue's
confirmer routes by `session_id`: `sched-*` sessions file approvals, everything
else fails closed — no approval channel leaks to subagents or unattended runs.

Operator rules layer onto the gate: `allow` / `ask` / `block` per capability
pattern (`shell.*`, `shell`, `*`), last match wins, only from `settings.toml`,
and never able to override the FORBIDDEN set or raise anything above the
configured `ceiling`.

Only an interactive chat owns the loop: `Alan.start_scheduler()` runs in the
chat loop, and one-shot commands (`ask`, `doctor`, `alan schedule …`) never
start background threads. `alan schedule add` and `/schedule add` exercise the
same tool path the planner uses, so scheduling stays permission-gated.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Configurable concurrency ("max tasks" like Grok Bot's `max_task_attemps`) | The exact anti-pattern the audit flags; concurrency is structural, one at a time, period |
| Goal-style tasks ("back up the drive") without a completion predicate | Honest scope cut: goal tasks built with a made-up predicate would be a lie. Deferred until predicates are real |
| Weekday-anchored weekly tasks (`every Tuesday at 09:00`) | Deferred: the `at_time` re-anchor invariant is daily-only, and a weekly-by-weekday rule has its own timezone/edge cases |
| Bidirectional "every Monday **and** Thursday" lists | Same deferred family; one-anchor semantics first, lists later |
| Approval expiry | A sleeping operator approving "yesterday's" backup is fine; expiry would re-prompt autonomously for the same thing — closer to a nag than an answer |
| Retry-on-reject | Rejecting pauses the task. "Remind me if it's still not done" must be said as a fresh schedule request, not implied forever |
| Start the scheduler from one-shot commands / scripts | Background threads nobody asked for — scripts and `doctor` would silently spawn loops |
| Rules as a Python API / CLI flag | Rules are a security setting; they belong in the one file an operator can review, `settings.toml` |
| Dots-style broad `allow` for everything | The exact over-broad-permission anti-pattern the audit flags; rules can only carve, never lift ceiling or forbidden |
| Fingerprint with no cache | Every cadence tick would re-ask for the identical action forever — the queue would *generate* prompts, not settle them |
| `at_time` re-anchor for weekly/twice-a-day cadences | Forces every run to today's clock blindly; the anchor is only meaningful on a daily rhythm |

### Consequences
Alan can now be handed a recurring intent and act on it without the operator
behind the screen, while every interesting action still runs past the gate.
The `sched-<taskid>` session namespace means approval routing is decided by a
string prefix in one place — trivial to audit, invisible to users. The cost is
the usual machinery tax: a state file, a thread, slash commands, and the
scheduler's own event namespace (`scheduler.*`, `task.*`, `approval.*`,
`activity.*`). Revisit if a task type ever genuinely needs parallelism *that a
human has explicitly asked for by name* — not as a setting that defaults on.

Notable follow-ups parked by this ADR: goal tasks (need a completion
predicate), weekday-anchored recurrences, a Telegram long-polling channel so
approvals can be decided from a phone (Phase 4), and richer terminal UI in
chat (Phase 4).

---

## ADR-009 — `web.*` gains zero-config internet backends; the sidecar stays for interaction

**Status:** accepted · **Date:** 2026-10-08

### Context
Phase 2's gap is the one the Dots/Grok-Bot comparisons measure most bluntly:
does it have real web? Alan's only web path was the browser sidecar (ADR-003) —
a separate process that has to be started and a browser attached. On a fresh
stick boot it is *not* running, so `web.search` and `web.open` both returned
"start the sidecar". An assistant whose only web path is a sidecar nobody
started has effectively no web.

Meanwhile the agent-reach setup had already made the two genuinely
zero-config backends available (`agent-reach doctor`): **Exa** semantic search
through `mcporter`, and **Jina Reader** (`curl https://r.jina.ai/<url>`), which
fetches a page *on Jina's servers* and returns its readable text.

### Decision
Add `tools/internet.py`, which owns those two backends and *nothing else*:

* `ExaSearch.search(query, count)` — one exact argv list
  (`mcporter call exa.web_search_exa --output json query=… numResults=…`), no
  shell string is ever built. The human-readable transcript is parsed into
  `{title, url, published, author, snippet}` with snippet clips.
* `JinaReader.read(url)` — `curl -s -L https://r.jina.ai/<url>`, text clipped to
  80k chars for the model. URLs are validated to http(s) only; schemes like
  `javascript:` are refused before anything runs.
* `Internet` facade — `available()` is **cheap and offline** (`shutil.which`
  for mcporter and curl, cached via probecache), so a runtime build and
  `alan doctor` can ask every time without a network round-trip.

The backends are **not** new capability names and not new risk levels: they run
*through* the existing `web.search` (LOW_RISK) and `web.open` (HIGH_RISK)
specs, so the permission gate treats them exactly as it always treated the
sidecar path. The Internet provider is preferred when available; the sidecar
remains the fallback and the only path for interactive browsing (clicking,
screenshots, named engines). `[tools] internet = true` is the default.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| New capability namespace (`internet.search` / `internet.read`) | Two tools doing the same job with different names is exactly the model-confusion the design avoids; the agent has to guess which one is "the" web tool, and operators have to block two names. One tool, two backends, one gate policy |
| Replace `web.*` entirely with the internet backends | The sidecar is still the only thing that can *interact* with a page, and its scraped engines are a real fallback. ADR-003's reasons to keep it stand; the failure mode was that nothing better existed first |
| Directory-aware strict URL parser | A strict parser turns `"youtube.com"` into a failed fetch whose error message blames the network rather than the missing scheme. The existing permissive `normalize_url` was reused, with a guard added for non-http(s) schemes |
| Shell string construction (`"mcporter call … $query"`) | The exact injection bug small models find by accident; query text with spaces is already a valid argv element |
| A "feed anything through Jina" tool | Out of scope; the audit's own lesson (no scope drift) applies. Search + read is what web.* ever promised |

### Consequences
The single most common failure of the browser path — "sidecar not started" — is
no longer the default experience. `web.search` works with zero setup, and
`web.open` still needs the operator's permission (HIGH_RISK) but then returns
the page. Because Jina fetches remotely, the internet `web.open` path is
*structurally incapable* of the local-network fetch the browser path can do —
that hole is closed by the proxy itself, not by a policy. The cost is a third
tool namespace's module (`tools/internet.py`) and a probe line in `alan
doctor`. Revisit if mcporter/Jina ever stop answering on the stick's host: the
browser fallback is still wired behind the same specs with no code change
needed.

---

## ADR-010 — Vibecoder hardening: bounded transcripts, floored timeouts, typo-proof settings

**Status:** accepted · **Date:** 2026-10-08

### Context
Phase 3 is the "hardening" pass on `tools/vibecoder.py`. The module already
carried the expensive lessons (verified-vs-claimed, `--prompt` not positional,
exit-0-is-meaningless, refusal of Alan's own tree) from real CLI runs; what
remained were the *unattended* failure modes — the ones that only bite when
nothing human is watching the screen. Three were real:

* A verbose agent can print hundreds of kilobytes. The transcript goes into
  the run record, and the *verdict* of a run is written at its **end** (the
  claim, the max-steps notice, the last tool call). Truncating the head would
  have been fatal to honesty; doing nothing lets a runaway stream grow without
  bound.
* `timeout = 0` for Vibecoder is a plausible settings typo (the field is a
  number; 0 trips the "falsy means default" convention). The old deadline math
  turned it into an instant kill-loop: every delegation stopped before its
  first line.
* `[vibecoder] max_steps = "lots"` (a string in a numeric field) raised inside
  the provider constructor and took the whole runtime down with it. One typo
  in one file killing the assistant entirely is not a configuration failure,
  it is a bug in us.

Also found in reading: the `_KNOWN_TOOLS` table was defined **twice**
verbatim — a duplicated cross-module contract that any future edit to one copy
would silently diverge from the other.

### Decision
Four bounded changes:

* **Tail-preserving transcript cap.** The stream keeps the *newest*
  `MAX_TRANSCRIPT_CHARS` (200 kB) with a 2x slack buffer that drops oldest
  lines; a run that was clipped says so in `WARNINGS`. The verdict always
  survives because the cap keeps what was written last.
* **Timeout floor in `delegate`.** `timeout = max(1.0, float(timeout))` before
  the deadline is computed, so `0`, a negative number, or a non-numeric value
  can never produce an instant kill or a negative deadline.
* **Typo-proof numeric settings.** `_floor_int` wraps the `max_steps` and
  `timeout` conversions in both the provider constructor and `delegate`'s argv
  build: a bad value costs a documented default, never a dead runtime. (The
  runtime floor still applies defensively when the value parses but is
  absurd.)
* **One `_KNOWN_TOOLS`.** The duplicate definition is removed; the set is the
  contract an unrelated JSON fragment is measured against, and there is now
  exactly one place it can drift.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Cap the head instead of the tail | The verdict (claim, truncation notice, final tool call) is written at the end. Keeping the head and losing the outcome recreates the exact "stopped part-way looks clean" bug the module already guards against |
| Reject bad settings at load | The gate and scheduler settings already refuse loudly at startup for *security-relevant* values. Spending the same treatment on a step-count knob chokes on a collaborator's valid older settings file; a default is the right failure for a non-security knob |
| Keep the buffer, drop the warning | A clipped report without a note reads as a complete transcript; the warning is what lets a reader trust the tail |

### Consequences
A runaway Vibecoder process can no longer drown the run record or the model's
context, a mis-typed `timeout` cannot stop every delegation immediately, and a
mis-typed numeric setting falls back instead of bricking the runtime. The
verdict-is-last invariant is now enforced by the buffer's shape rather than by
hope. Revisit the 200 kB figure if a task type ever legitimately needs to wrap
a context window larger than that in a single delegation.

---

## ADR-011 — the approval bridge long-polls Telegram; chat prints the background feed

**Status:** accepted · **Date:** 2026-10-08

### Context
Phase 4's two remaining gaps were both about the *operator experience* of an
always-on assistant, and both follow from a fact already true on day one of
this redesign: **chat owns the loops.** The scheduler (ADR-008) and now the
approval bridge only live while an `alan chat` session is open.

Two hard truths stood out while wiring Phase 1:

* The console is the *only* place approvals can be decided. A task that stops
  at the permission gate waits forever when nobody is at the terminal. An
  always-on assistant whose approvals can only be answered from the one seat
  it is supposed to free is not always-on.
* Background tasks finish while a reply streams or a prompt waits. The
  obvious fix — a thread printing into the terminal — garbles exactly the
  `you>` surface this project refuses to touch.

### Decision
Two bounded additions, one per problem:

**Telegram long-poll bridge (`src/alan/telegram.py`).** Subscribes to
`approval.*` bus events and *announces* new approvals to the operator's chat;
a poll thread reads `getUpdates` and routes `/approve` / `/reject` from the
configured chat through the *same* `decide_approval` call the chat loop's
`/approve` uses — one decision path, two surfaces. Design constraints:

* Long-polling, not webhooks: Alan dials out of the network on a schedule;
  Telegram never calls back in. No port, no server — the stick's offline-first
  posture is structural again.
* Fail-closed chat: `[telegram] chat_id` is the only chat that may decide.
  Other senders get no action, no echo, no reply — a leaked token reaches
  nothing but silence.
* Built, never started: the bridge is constructed with the runtime (so
  `alan doctor` can report it) but a chat session must start it, exactly like
  the scheduler.
* `bot_token` never appears in a message, log line, repr, or status note.

**Live background feed (`src/alan/chatfeed.py`).** The chat loop collects
rendered `task.*` / `scheduler.*` lines in a thread-safe buffer and prints them
at the safe moment — after a reply, before the next prompt. Background events
still reach the human; the `you>` prompt is never drawn over.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Telegram webhook (Alan listens on a port) | The stick lives on unknown networks. A webhook means forwarding, a public endpoint, or a tunnel — all of them a server to censor and a port to explain. `getUpdates` is the zero-exposure option: Telegram holds the request, Alan holds the schedule |
| Any sender may decide, with a per-message PIN | The chat id is already a shared secret between operator and bot; a second secret doubles the failure surface for no extra boundary. One identity, fully fail-closed, is simpler to reason about |
| A thread prints feed lines straight into `console.input` | Rich's input is a blocking read; a concurrent print interleaves mid-prompt and corrupts the `you>` surface. Collect-and-drain is the same output with a safe moment |
| Put the poll loop in the scheduler's tick | A scheduling tick would only poll Telegram accidentally; the bridge needs one long-poll-owned offset and its own error cadence. Separate loop, same ownership rule |
| Reuse `Executor`/`subprocess` for Telegram | The Bot API is one authenticated JSON exchange; a session with a timeout is the honest minimum, and a fake transport keeps it fully testable offline |

### Consequences
Approvals can be answered from a phone while the stick sits behind closed
doors, and a task finishing in the background now tells the operator about
itself instead of waiting to be asked. The cost is one opt-in module, one
config table, and a chat-loop drain that costs nothing when there are no
events; scheduled-task semantics are unchanged. The bridge is disabled by
default — a blank `bot_token` — so nothing phones home until the operator
configures it. Revisit the single-chat rule if a project ever needs
multi-operator approvals; nothing here assumes one decision authority.

---

## ADR-012 — the phone is for results too (`notify_tasks`), and `alan serve` runs the loops without chat

**Status:** accepted · **Date:** 2026-10-08

### Context
Two findings from the September 2026 competitive pass (Dots, Grok Bot, Muse)
survived the "what do they have that Alan does not" filter:

* Their agents *reach out with results*, not just decisions. A scheduled check
  finishes and the operator is told — Dots messages you, Grok Bot keeps you
  updated, Muse nudges you. Phase 4 left Alan announcing only approvals on the
  phone.
* They keep working while no app is open. Alan's loop only ran while an
  `alan chat` session was open (ADR-008). A USB stick cannot run when the
  laptop sleeps, but it *can* run with the terminal closed — and insisting on
  chat-only meant "always-on" still quietly meant
  "always-on-if-you-are-sitting-here".

### Decision
Two bounded additions:

**`[telegram] notify_tasks = true` (`src/alan/telegram.py`).** The bridge also
subscribes to `task.*` and sends short start / done / FAILED lines for each
scheduled task to the operator's chat. A task *waiting* at the permission gate
is deliberately not repeated — the approval announcement already carries the id
and the `/approve` instruction, and two messages for one stop is noise. Off by
turning the flag off; the bridge as a whole stays opt-in.

**`alan serve` (`src/alan/cli.py`).** An explicit, headless command: build the
runtime, start the scheduler, start the Telegram bridge if configured, print
what is serving, and block until Ctrl+C (or `--seconds N`, for scripts), then
stop both loops and close the runtime in a `finally`. If nothing is configured
it says so and exits rather than pretending to serve.

### Why headless is safe now when ADR-008 said chat-only
ADR-008's invariant was: *an unattended run must not stop to ask, and must not
proceed unasked either.* That invariant holds unchanged. What changed is the
*reachability* of the human: the permission gate still stops every task that
needs a decision, and now the operator can answer from a phone (ADR-011)
instead of only from a chat prompt. `alan serve` does not remove, bypass, or
soften a single gate — the scheduler's runner still has no `confirmer` (the
runtime keeps `confirmer=None` for the scheduled path), approvals wait in the
queue, and nothing proceeds unasked. It only promises that the waiting window
is vertical, and that the person who owns the stick asked for exactly this
command. Background threads still never launch from builds or one-shot
commands: chat and `serve` are the only owners.

### Alternatives rejected
| Alternative | Why rejected |
|---|---|
| Make `notify_tasks` the default for every channel including local chat | The chat feed already prints task lines to anyone watching; phoning every short interval tick would bury the decisions. One line added to a phone that chose to be on is welcome; a console that is already on is not a notification surface |
| Announce `task.waiting_approval` on the phone too | Duplicate of the approval line for the same event — the approval line is the one with the actionable id and the /approve instruction |
| Serve mode auto-starts on boot / as a Windows service | Turns "the operator explicitly ran serve" into "something runs forever that nobody asked for". The stick's rule is *explicit, not implied*; a service wrapper can come later as its own decision |
| `serve` without Telegram, printing into a detached console | Exactly what "terminal closed" forbids; serve with nothing to do must say so and exit |
| Keep chat-only and call it final | Then "always-on" only meant "while you sit here", which is the exact Dots/Grok Bot gap this session exists to close |

### Consequences
The phone now carries results *and* decisions; a morning check scheduled in
chat reports itself to the operator's pocket. `alan serve` gives the always-on
claim a real headless home while the laptop is awake and the terminal is
closed. The cost: one config flag, one subscription per bridge, and one CLI
command plus its source-guard tests. ADR-008's ownership rule is amended by
this ADR: the loops are owned by *chat or an explicit `serve`*, nothing else.
Revisit when a service-style bootstrap is requested; that should be its own
ADR, not a silent extension of this one.
