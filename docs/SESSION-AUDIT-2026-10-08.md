# Alan Overnight Upgrade — Complete Audit Report

- **Session**: 2026-10-08, started **00:48:27** (right after the operator said good night)
- **Duration**: ~8-hour autonomous agent session, no operator questions, every decision logged
- **Goal**: turn Alan (`D:\Alan`, portable USB AI assistant) into an **always-on local-first agent** — the explicit bar: *better than OpenAI Dots and Grok Bot* — while keeping the friendly `you>` chat surface and the offline-first stick
- **Result**: 5 phases delivered across ADR-008…012; test suite grew **394 → 535 passed** (130 → 132 subtests), green at every boundary; work published to GitHub as the `python-core` branch (commit `d91fd7c`)

---

## 1. Executive summary

| Metric | Start | End |
|---|---|---|
| Tests passing | 394 | **535** (+141) |
| Subtests | 130 | **132** |
| Full-suite run time | 54.5 s | ~43 s |
| ADRs | 001–007 | 001–012 (+5) |
| New source modules | — | `permissions/rules.py`, `permissions/queue.py`, `core/scheduler.py`, `tools/internet.py`, `tools/schedule.py`, `telegram.py`, `chatfeed.py` |
| New test files | — | `test_rules.py`, `test_scheduler.py`, `test_internet.py`, `test_telegram.py`, `test_chatfeed.py`, `test_serve.py`, `test_vibecoder.py` |

Five deliverables, in order:

1. **Always-on scheduler + approval queue + operator rules** (Phase 1, ADR-008)
2. **Internet backends** — Exa search + Jina Reader (Phase 2, ADR-009)
3. **Vibecoder hardening** (Phase 3, ADR-010)
4. **Phone approvals** via Telegram long-poll bridge + richer TUI feed (Phase 4, ADR-011)
5. **Phone results + headless always-on**, plus a research gap pass against Dots/Grok-Bot/Muse (Phase 5, ADR-012)

Boundaries held throughout: no permission-gate removal, no license/DRM/auth bypass, no safety-gate weakening, no scope drift past the planned gap pass.

---

## 2. Milestones

| Time (2026-10-08) | Event | Suite |
|---|---|---|
| 00:48:27 | Session start, baseline | 394 / 130 |
| — | Phase 1 complete — scheduler + approvals + rules (ADR-008) | **461** / 130 |
| — | Phase 2 complete — Exa + Jina internet backends (ADR-009) | **490** / 130 |
| — | Phase 3 complete — Vibecoder hardening (ADR-010) | **495** / 132 |
| — | Phase 4 complete — Telegram bridge + richer TUI (ADR-011) | **525** / 132 |
| — | Gap pass — Dots / Grok Bot / Muse research (no code) | — |
| — | Phase 5 complete — `notify_tasks` + `alan serve` (ADR-012) | **535** / 132 |
| 07:58:58 | Final audit written; root commit `d91fd7c` pushed (`python-core`) | 535 / 132 |
| 2026-10-08 (follow-up) | Audit report authored; suite re-run **live: 535 passed, 132 subtests, 43.09 s** | 535 / 132 |

---

## 3. Phase 1 — Scheduler + approval queue + operator rules (ADR-008)

### What was built
- **Permission rules overlay** (`allow`/`ask`/`block`) — `permissions/rules.py`, `permissions/gate.py::_evaluate`, config `[[permissions.rules]]` parsing with eager validation (unknown effect → loud `ValueError`).
  - Matching: exact capability, `ns.*`, bare `ns`, `*`; **last-match-wins**.
  - Runs after FORBIDDEN and the capability ceiling, so a rule can never un-forbid or lift above the tier.
  - Rules come only from `settings.toml`.
- **Approval queue** — `permissions/queue.py`:
  - Fingerprint-keyed approvals; **approve → cache** (an identical re-run auto-passes); **reject → no cache**, task pauses.
  - No expiry, dedupe of identical pending actions, action history.
  - The `confirmer` returns True only for a cached fingerprint; `set_on_decide` callback wiring.
- **Scheduler** — `core/scheduler.py`:
  - One sequential tick thread (structural — **no "max tasks" setting**, the Grok-Bot lesson).
  - Daemon worker per task with `join(task_timeout)`; alive/overdue workers skipped (tasks never overlap).
  - Runs via the `TaskRunner` protocol (only `LocalRunner` now → `orchestrator.run` with `session_id=f"sched-{id}"`).
  - Summaries clipped; atomic state file `data/state/schedule.json`; a corrupt file is preserved as `.corrupt-<ts>` and the store starts empty — **never bricks Alan**.
- **`parse_when` plain-English timing** — `every N units`, named times, `once a day`, `twice a day`, `every morning|night`, `daily`/`weekly`, `at HH:MM`, `today/tomorrow at`, `in N units`. Refuses with the accepted forms rather than guessing. `at_time` anchor honored only for a daily cadence (`every_seconds == 86400`); weekly/once-a-day/twice-a-day never re-anchor daily.
- **Planner + tools** — `schedule` intent; `schedule.add/list/remove` tools (LOW_RISK gate); remove by id or request-substring.
- **Runtime wiring** — `_route_confirmer` (session_id prefixes), scheduler + approvals built in `build_runtime` when `[scheduler] enabled`, started only by chat; `activity_text`, `decide_approval`; `Alan` passthroughs; CLI slash commands `/activity /approve /reject /schedule`; CLI `alan activity` and `alan schedule add|list|show|remove|pause|resume|run|clear-done`.
- **Docs** — ADR-008 (alternatives rejected: configurable concurrency, Dots-style broad allow, approval expiry, retry-on-reject, goal tasks, weekday-anchored weekly deferred), README "Always-on scheduling" section, `[scheduler]` settings table + commented `[[permissions.rules]]` examples.

### Decisions worth re-reading
- **Concurrency is structural**, not configurable.
- **Approve→cache + reject→pause** settles prompts instead of generating them; rejections can never nag.
- **Only chat owns the loop** — `ask`/`doctor`/`alan schedule …` never spawn background threads.
- The scheduled runner has **no confirmer** (fail-closed: HIGH_RISK from a scheduled task returns `ToolResult(ok=False, error="needs your approval")` — by design).

### Bugs fixed in-phase
- `[scheduler]` tick windup: per-run `now` wasn't propagated from `tick(now)` into run recording (`_record` used `self._now()`), making `tick(now=…)` non-deterministic. Threaded `now` through `_execute`/`_record`.
- `task_timeout` minimum lowered 1.0 s → 0.1 s because the 1.0 s floor hid the abandoned-worker path from tests — a guard that hides a code path is a misplaced guard.
- `_route_confirmer` now gives every runtime-built orchestrator a confirmer that **fails closed outside `sched-` sessions**; `test_a_subagent_gets_no_approval_channel` updated to assert the real safety property.

### Tests added
- `tests/test_rules.py` — matching, last-match-wins, ceiling/FORBIDDEN immutability, ask-forces-confirmation, validation errors, `[scheduler]` table parsing round-trip.
- `tests/test_scheduler.py` — `parse_when` forms & refusals, next-occurrence, atomic store round-trip / corrupt-safe / bad-row isolation, add validation, once/interval execution, failure paths, timeout path, pause/resume/run_now, RUN_HISTORY bound, waiting-approval flow (approve→requeue, reject→pause), LocalRunner clipping / AgentResult duck-typing / exception→outcome, ApprovalQueue cache / dedupe / decide-by-task / no-cache-on-reject, live-loop events.
- Updated `tests/test_core.py` (2 tests) for the new event namespaces (`scheduler.*`, `task.*`, `approval.*`, `activity.*`).

### Honest scope cuts (recorded in ADR-008, not accidents)
- **Goal tasks** ("back up the drive") need a completion predicate; not faked.
- **Weekday-anchored weekly** ("every Tuesday at 09:00") deferred; the `at_time` re-anchor invariant is daily-only.

---

## 4. Phase 2 — Internet backends: Exa + Jina behind `web.*` (ADR-009)

### What was built
- `tools/internet.py`:
  - **`ExaSearch`** — `mcporter call exa.web_search_exa --output json query=… numResults=…`; parses the text transcript into `{title, url, published, author, snippet}` blocks split by `\n---\n`, snippets clipped to 260 chars. **No shell string ever built.**
  - **`JinaReader`** — `curl -s -L https://r.jina.ai/<url>`; text clipped to 80k chars; http(s)-only URL validation (refuses `javascript:`/`ftp:`/missing-host).
  - **`Internet` facade** — `available()` is a cheap offline probe (`shutil.which` for mcporter/curl, probecached), free for builds/doctor.
- Backends run **through the existing `web.search` (LOW_RISK) / `web.open` (HIGH_RISK) specs** — no new capabilities, no new risk, one gate policy. Internet preferred; the browser sidecar is the fallback and the only interactive path.
- Config: `[tools] internet = true` (default on), `ToolSettings.internet`, settings.toml docs.
- Runtime: builds `Internet`, passes to `StandardTools`, `status()["internet"]`, `internet_note`; `alan doctor` renders it.
- **SSRF closed structurally**: a local-address request (`http://127.0.0.1:9/…`) is fetched on Jina's machines, not the stick's — the local network is unreachable through the proxy.

### Bugs found and fixed while wiring
- **Windows npm `.CMD` shim resolution** — `mcporter` lives as `mcporter.CMD`; bare-name `CreateProcess` fails. `_run` resolves `argv[0]` via `shutil.which` before spawning — one place, hidden from injected test runners.
- **UTF-8 decode** — `subprocess.run(text=True)` decoded with cp1252 and Exa's em-dash/`¶` bytes crashed the reader thread. Fixed to `encoding="utf-8", errors="replace"`.
- **`_validate_url` scheme checks** — plain `https://` prefixing let `javascript:`/`file:`/missing-host through netloc checks; added a `://`-scheme allowlist plus a lone-`name:`-prefix guard that still lets `host:port` through.
- **`count=0` clamp** — `int(count or 5)` silently turned 0 into 5; now 0 means "no opinion" → default 5, documented. Count clamped 1…8.

### Tests added
- `tests/test_internet.py` (**29 tests**): exact argv shapes, transcript parsing, snippet clipping, count clamps, URL validation (incl. localhost-proxy semantics), timeout/nonzero-exit/missing-binary, npm-CMD resolution, facade combining/backends, StandardTools prefer/fallback/opt-out/error paths, format_results, runtime `internet` status key disabled+enabled.
- `tests/test_browser.py`: doctor source-level guard now also asserts `status['internet']['note']`.

### Live verification (not just fixtures)
- Real Exa search → 3 parsed results in ~7 s; real Jina read → page text in ~1 s.
- `read("http://127.0.0.1:9/...")` returns content fetched on Jina's machines (local loopback unreachable through the proxy).
- Gated registry path: `web.search` passes the gate and returns Exa results; `web.open` correctly denied without approval (HIGH_RISK fail-closed).
- `alan doctor` shows `internet: internet - internet ready (Exa search + Jina Reader)`.

---

## 5. Phase 3 — Vibecoder hardening (ADR-010)

### What was built
- **Tail-preserving transcript cap** — the delegation stream keeps the NEWEST `MAX_TRANSCRIPT_CHARS` (200 kB) via a 2×-slack oldest-drop buffer; a clipped run says so in WARNINGS. The verdict (claim / truncation / final tool call) is written at the end, so the tail is the honest part. **Memory stays bounded whatever the agent prints.**
- **Timeout floor in `delegate`** — `timeout = max(1.0, float(timeout))`; a `timeout = 0` (settings typo) can no longer become an instant kill-loop.
- **Typo-proof numeric settings** — `_floor_int` wraps `max_steps`/`timeout` conversions in the constructor AND argv build: `"lots"` costs a documented default, never a dead runtime.
- **Removed the duplicated `_KNOWN_TOOLS` definition** (two identical, confusingly doubled contracts).

### Tests added (in `tests/test_vibecoder.py`)
- `test_huge_transcripts_keep_the_tail_and_say_so` — buffer bound + tail survival + cap warning (fake Popen).
- `test_zero_timeout_is_raised_to_one_second` — floor prevents instant-kill on a mis-typed timeout.
- `test_a_mis_typed_vibecoder_table_does_not_crash_construction` — bad `max_steps`/`timeout` fall back to defaults.

---

## 6. Phase 4 — Telegram long-poll bridge + richer TUI (ADR-011)

### What was built
- `telegram.py` — the **approval bridge**:
  - **Long-poll, not webhook**: a poll thread calls `getUpdates` with an advancing offset; Alan dials out, Telegram never calls back in. **No port, no server.**
  - **Fail-closed**: only the configured `[telegram] chat_id` may decide `/approve`/`/reject`; any other sender gets silence. **The token never appears in a repr/log/status note.**
  - **One decision path**: `decide` is wired to the same `runtime.decide_approval` the chat `/approve` uses (id-suffix matching, re-queue/pause replies).
  - **Event-driven outbound**: subscribes to `approval.submitted`/`approval.decided` → announces approvals and outcomes to the configured chat, only while started.
  - Built with the runtime (doctor can report it), **started only by chat** — the same ownership rule as the scheduler. `_TelegramHTTP` imports `requests` lazily.
  - A bot API conflict/network error is counted (`poll_errors`/`last_error`), waited out, never fatal; empty fast polls are paced (`_MIN_CALL_GAP`) so a quirky transport can't spin.
- `chatfeed.py` — richer TUI: `render_event_line` turns `task.*`/`scheduler.*` events into short dim lines; the chat loop **drains them between turns** (never mid-prompt), so the `you>` surface stays clean.
- `config.py` `TelegramSettings` + `[telegram]` table parse; `config/settings.toml` `[telegram]` section (defaults off via blank token).
- `runtime.py`: `telegram` field, `status()["telegram"]`, `start_telegram`/`stop_telegram`, bridge built after the runtime exists; `assistant.py` passthroughs; `cli.py` chat wiring (`/telegram` command, feed drain, doctor panel line).

### Bug fixed in-phase
- **Rich markup swallowed `[telegram]`** in status notes (square brackets parse as style tags); the note now reads "set bot_token and chat_id in settings.toml" (no brackets).

### Tests added
- `tests/test_telegram.py` (**23 tests** + runtime/config/doctor): authorized approve/reject routing, foreign chats ignored entirely, plain text → help not action, empty target → usage, `/activity` view, failing decide survives, textless updates ignored, offset advance (0 → 10 for updates 7,9), 409-conflict counted not fatal, start/stop idempotent, **repr never leaks the token**, announcements only while started + decision outcomes + close detaches, config defaults/apply/missing, runtime builds the bridge only when configured (never started), doctor source guard.
- `tests/test_chatfeed.py` (**7 tests**): per-event rendering, distinct ok/Failed/waiting lines, scheduler bookends, unrelated events skipped, bounded lines, collect-then-drain-once, close detaches.

---

## 7. Gap pass — Dots / Grok Bot / Muse research (2026-10-08)

Gathered via the agent-reach pipeline (Exa via mcporter + Jina Reader). Product slices:

- **OpenAI Dots** (DevDay 2026-09-29, GPT-6 Astra): always-on agent per user on its own cloud computer + browser; 4,000+ apps via plugins; context carries across ChatGPT/Slack/Teams + texting beta; background "proactive research" is **read-only restricted**; scheduled checks ("check X each weekday at 9am… confirm the schedule"); **Custom Rules = per-action allow / pre-approved / ask / hand-off** with auto-review + monitoring that can pause/stop work; sensitive actions (password) always stay with the user; Activity view + Take over; memory + self-notes; background agents in parallel.
- **Grok Bot** (docs.x.ai, Sept 2026): named Bots on a persistent cloud computer (browser+folder+terminal); setup is a message; bots coordinate in parallel, group chats, handoffs; **skills learned from demonstration, rerun on a schedule**; context compounds per bot; approval checkpoints; desktop+mobile+voice.
- **Meta Muse** (2026-09-08, Muse Spark): personal agent on a dedicated cloud VM; **Sentinel is the sole permission authority** for connectors + all network egress; audit trail of everything done/planned; credentials never visible to the model; goals → action plans with proactive nudges; connectors + Skill files; subagent swarms + crons; prompt-injection defense in depth.

**What Alan already covers** (or structurally declines, per ADR): permission-gated tools + rules (Dots Custom Rules ≈ `[[permissions.rules]]` allow/ask/block; approve-once fingerprint ≈ pre-approved); phone approvals (Phase 4 telegram bridge ≈ channels); scheduled recurring work with plain-English times (≈ scheduled checks); activity view + audit traces; memory across sessions; one-task-at-a-time concurrency is a deliberate ADR-008 boundary (the Grok-Bot lesson).

**Gaps selected for this session** (fit Alan's offline-first stick + remaining time):
1. **Task results on the phone** — the bridge announced only *approvals*; "always-on" per Dots/Grok-Bot means it *reaches out with results*. → opt-in `[telegram] notify_tasks`.
2. **Headless always-on** — Dots/Grok/Muse keep working with no app open. The stick can't run while the laptop sleeps, but it *can* run with the terminal closed. → explicit `alan serve`.

**Declined this session** (recorded): goals store (needs a model that plans; the scheduler re-expresses most of it), skill-from-demonstration (a phase of its own), proactive read-only research (needs app connectors the stick doesn't have), parallel agents (ADR-008 boundary stands).

---

## 8. Phase 5 — Phone results + headless always-on (ADR-012)

### What was built
- **`[telegram] notify_tasks = true`** — the bridge now subscribes to `task.*` and sends a short **start / done / FAILED** line per scheduled task to the operator's chat (the phone carries *results*, matching Dots "reaches out with results" / Grok Bot "keeps you updated" / Muse nudges). A task *waiting* at the gate is deliberately not repeated — the `approval.submitted` line already carries the id + `/approve` instruction. Subscribed at construction (the flag is a constructor decision, not a runtime poke).
- **`alan serve`** — explicit headless always-on: build the runtime, start the scheduler + (if configured) the telegram bridge, print what is serving, block until Ctrl+C (or `--seconds N`), then `finally` **stops both loops** and closes the runtime. Nothing configured → says so and **exits code 1**. Safety invariant preserved: the scheduler runner still has no confirmer, approvals wait in the queue, nothing proceeds unasked — only the reachability of the human changed (ADR-011).
- `_start_always_on(alan)` helper returns `(scheduler_on, telegram_on)` so tests assert the start/stop contract without a terminal or network.

### Decisions
- Loop ownership amended (ADR-008 → ADR-012): scheduler/bridge threads are owned by **chat or an explicit `serve`**, never by builds or one-shot commands.
- `notify_tasks` defaults true (the bridge is opt-in anyway); waiting stays on the approval line.
- No auto-start/service wrapper — "explicit, not implied" is the stick's rule.

### Tests added
- `tests/test_serve.py` (fake `_QuietTransport`, source guards): starts both loops, nothing-to-serve exits 1, stops both in `finally`.
- `tests/test_telegram.py` additions: notify_tasks started/finished/failed announce, waiting stays silent, `notify_tasks=false` silences task lines, pre-start quiet, config default + parse of the flag.

---

## 9. Cross-phase: bugs found and fixed

| # | Bug | Phase | Fix |
|---|---|---|---|
| 1 | `tick(now=...)` non-deterministic (run recording re-read clock) | 1 | Threaded `now` through `_execute`/`_record` |
| 2 | 1.0 s `task_timeout` floor hid the abandoned-worker path | 1 | Floor lowered to 0.1 s |
| 3 | Orchestrator confirmer reachable outside `sched-` sessions | 1 | `_route_confirmer` fails closed |
| 4 | `mcporter.CMD` npm shim not spawnable by name | 2 | `_run` resolves argv[0] via `shutil.which` |
| 5 | cp1252 decode crashed Exa reader on em-dash/`¶` | 2 | `encoding="utf-8", errors="replace"` |
| 6 | `javascript:`/`file:`/missing-host slipped URL checks | 2 | `://` scheme allowlist + lone-`name:` guard |
| 7 | `count=0` silently became 5 | 2 | 0 = "no opinion" → default 5, documented |
| 8 | Transcript buffer unbounded with a verbose agent | 3 | 200 kB tail-preserving cap |
| 9 | `timeout=0` → instant kill-loop | 3 | `max(1.0, float(timeout))` floor |
| 10 | `max_steps`/`timeout` typos crashed construction | 3 | `_floor_int` in constructor + argv build |
| 11 | Duplicated `_KNOWN_TOOLS` definition | 3 | Removed copy |
| 12 | Rich markup ate `[telegram]` in status notes | 4 | Brackets removed from note text |
| 13 | (structural) Approvals same path on phone + chat | 4 | Both call `runtime.decide_approval` |

---

## 10. Test growth and final verification

Suite green at every phase boundary: **461 → 490 → 495 → 525 → 535**, subtests 130 → 132.

**Live verifications performed:**
- Real Exa search → parsed results (~7 s) and real Jina read (~1 s) — Phase 2.
- `read("http://127.0.0.1:9/…")` resolved on Jina's machines — local SSRF closed structurally.
- `web.search` gate-passes; `web.open` correctly HIGH_RISK-denied without approval.
- `alan doctor` renders all panels incl. `internet` and `telegram` lines.
- `alan serve --seconds 2` starts/stops the scheduler cleanly (smoke).
- `alan version` exit 0.
- **Final re-run for this audit (2026-10-08): `535 passed, 132 subtests passed in 43.09s`.**

**Testing hygiene:** no network in tests — transports/backends are injected; real HTTP classes are never constructed by the suite. Transports built for the Telegram bridge (`_TelegramHTTP`) import `requests` lazily; tests all run offline.

---

## 11. Boundaries and scope (safety)

- **Permission gate untouched and non-bypassable** (ADR-006 stands): the scheduled runner keeps `confirmer=None`, so HIGH_RISK fails closed with "needs your approval"; no `allow` was broadened.
- **No license / DRM / auth bypass**: Telegram is opt-in (`[telegram]`, defaults off), a local-first bridge — Alan dials out, nothing listens; secrets never logged.
- **No auto-start, no implied background**: only `alan chat` or an explicit `alan serve` own the loops (ADR-012).
- **Concurrency invariant kept**: one task at a time — the Grok-Bot loop lesson was *applied*, not relaxed.

---

## 12. Publication to GitHub

- The account's "alan" repo is **`akashjoshi1209/Alan`** (≡ `alan`): `master` = Android app, `python-core` = stale old Python layout. Neither matched today's `src/alan/` project.
- **Per operator decision**: replaced `python-core` with the `D:\Alan` project.
  - `git init -b python-core` in `D:\Alan`; remote `origin` = the private repo.
  - Author identity: `akashjoshi1209 <200609181+akashjoshi1209@users.noreply.github.com>`.
- `.gitignore` extended: `.ua/` (runtime knowledge graph), `.pytest_cache/`, `cognee-install.log`, session scratch files (`a.txt`, `test_localsystem.txt`); `env/`, `data/`, `models/`, `config/.env` already covered.
- Root commit **`d91fd7c`** — 139 files, 35,320 insertions; pushed with `--force-with-lease`: `209c253...d91fd7c python-core -> python-core`.
- `master` untouched (`95a4076`). Working tree clean. Pre-push **secret scan clean** (only fake fixture strings in `tests/test_core.py`).

---

## Appendix A — Committed file inventory (139 files, excluding `alan-research/`)

**Root / config / docs / scripts / demos**
```
.gitignore  Alan.cmd  README.md  conftest.py  pytest.ini  requirements.txt
config/.env.example  config/settings.toml
docs/ARCHITECTURE.md  docs/decisions/architecture-decisions.md
docs/integrations/dependencies.md  docs/integrations/repository-audit.md
demos/demo_all.py
scripts/activate.ps1  browser-serve.ps1  cognee-serve.ps1  dump_pyc.py
install-command.ps1  lint_syntax.py  measure_cognee.py  profile_ask.py
pull-model.ps1  run.ps1  scan_tree.py  selftest.ps1  setup-browser.ps1
setup-cognee.ps1  verify_rebuild.py  verify_tree.py
```

**`src/alan/`** (core package)
```
__init__.py  __main__.py  assistant.py  chatfeed.py  cli.py  config.py
runtime.py  telegram.py
agents/__init__.py  agents/standard.py
core/__init__.py  atomic.py  events.py  lessons.py  orchestrator.py
planner.py  probecache.py  scheduler.py
interfaces/__init__.py  agent_runtime.py  capabilities.py  memory.py
model.py  tools.py  types.py
llm/__init__.py  base.py  cloud.py  local.py
memory/__init__.py  cognee_client.py  relevance.py
memory/providers/__init__.py  cognee_provider.py  json_store.py  mem0_provider.py
permissions/__init__.py  gate.py  queue.py  rules.py
tools/__init__.py  coding.py  execution.py  internet.py  opening.py
registry.py  schedule.py  standard.py  vibecoder.py
tools/browser/__init__.py  tools/browser/sidecar.py
```
`src/browser_sidecar.py` (sidecar process entry, ADR-003)

**`tests/`**
```
test_browser.py  test_chatfeed.py  test_core.py  test_internet.py
test_lessons.py  test_memory.py  test_opening.py  test_probes.py
test_providers.py  test_relevance.py  test_rules.py  test_scheduler.py
test_serve.py  test_smoke.py  test_telegram.py  test_vibecoder.py
```

## Appendix B — ADR index (docs/decisions/architecture-decisions.md)

| ADR | Title | Session |
|---|---|---|
| 001 | Port DeepSeek Harness's architecture into Python; do not ship Node | prior |
| 002 | Two memory backends behind one `MemoryProvider` | prior |
| 003 | browser-use runs as an isolated sidecar | prior |
| 004 | Implement execution natively; Open Interpreter is reference only | prior |
| 005 | CodingAgent is Alan's own, decomposed like SWE-agent | prior |
| 006 | Permission gate is mandatory and non-bypassable | prior |
| 007 | Develop on `D:`, distribute to the USB last | prior |
| 008 | Always-on operation: a task scheduler, an approval queue, operator rules | **this session** |
| 009 | `web.*` gains zero-config internet backends; the sidecar stays for interaction | **this session** |
| 010 | Vibecoder hardening: bounded transcripts, floored timeouts, typo-proof settings | **this session** |
| 011 | The approval bridge long-polls Telegram; chat prints the background feed | **this session** |
| 012 | The phone is for results too (`notify_tasks`), and `alan serve` runs the loops without chat | **this session** |

## Appendix C — Durable session record
- `C:\Users\ADMIN\AppData\Local\Temp\opencode\worklog.md` — the hourly decision/action record behind this audit (phases, gap-pass findings, milestones, final audit).