# Decisions

What was decided during the external-repository acquisition, and why. Every
entry is either a rule this workspace operates under, or a claim it was checked
against. Corrections to the original brief are recorded as **CORRECTION**,
because a brief that quietly disagrees with reality is worse than no brief.

---

## Process decisions

### D1 — Shallow clones, pinned by commit

`git clone --depth 1 --single-branch --no-tags --no-recurse-submodules`.

These repositories are read, not built. Full history would cost gigabytes to
answer questions about architecture. The exact commit is recorded in
`clone-results.json` and in `REPOSITORIES.md`, so any claim in the audit can be
re-checked against a known state rather than "whatever main was today".

Submodules are not followed: a submodule pointer is code nobody has evaluated,
and following it would defeat the isolation this workspace is built on.

### D2 — Failure is data

A repository that 404s, is renamed, is private, or is an archive stub is
recorded with its error and counted in the audit. Acquisition does not abort on
the first failure, because "this project is not at that URL" is a finding.

### D3 — Read, never execute

Third-party code in this workspace is never installed, built or run. `acquire.py`
performs `git clone` and `git rev-parse`; `inspect_repos.py` reads text files.
No `pip install`, no `npm install`, no build script, no test suite. The
research workspace must not be able to affect Alan, and the cheapest way to
guarantee that is to never execute the thing.

`GIT_TERMINAL_PROMPT=0` on every git invocation, so a moved or private
repository fails immediately instead of hanging on a credential prompt.

### D4 — Research runs on Alan's own interpreter, stdlib only

Both scripts run on `..\env\python\python.exe` and import nothing outside the
standard library. Installing a research dependency into Alan's environment —
even to read a repo's tests — would let a research project perturb the product.

### D5 — No cloud, no credentials, no telemetry

No API keys created, no service authenticated, no telemetry enabled, nothing
uploaded. Repositories with cloud-only features have those features
documented as unavailable rather than enabled. Per-repository privacy findings
are in `REPOSITORIES.md`.

### D6 — Mechanical facts by script, judgements by hand

`inspect_repos.py` harvests what can be read off disk: commit, licence,
language mix, declared dependencies, and *string-level* signals for telemetry
and cloud SDKs. It emits no verdict.

Integration verdicts are written by hand in `REPOSITORIES.md`, because they
depend on Alan's architecture and on the brief's four tests, and a script that
emitted them would be making unreviewed architecture decisions.

This is why signal hits are reported as "the string `posthog` appears in
`src/telemetry.py`", not as "sends telemetry". The first is evidence; the
second is a claim that needs reading the code.

### D7 — BrowserGym and WindowsAgentArena live in `evaluation/`

Both are benchmarks. The brief lists them under computer-use but the
integration map places them under evaluation, and the map is right: a benchmark
that ships as infrastructure is exactly how a research dependency ends up on a
production path.

### D8 — Three deletions from Alan's own tree are now guarded

Files in `D:\Alan` were deleted three times during this work by something
outside Alan's control — `src/alan/tools/coding.py`, `src/alan/core/orchestrator.py`,
`src/alan/interfaces/types.py` — each of which stops Alan importing. Cause never
identified: not OneDrive (signed in to nothing), not volume health, not disk
space, and not anything in Alan's own code (its only `shutil.rmtree` is scoped
to its own browser profile directory).

Mitigation, since the cause is unknown:

```powershell
python scripts\verify_tree.py            # checksum every source file, exit 1 if los
python scripts\verify_tree.py --restore  # repair from the mirror
```

Verified working: a deliberately deleted file is detected in under a second and
restored byte-identically.

This is recorded here rather than only in the changelog because it constrains
how any future work in this tree must be done: **write, then verify, then test**
— in that order, every time.

---

## CORRECTION — the historical Alan used ChromaDB + Graphify, not LanceDB

**Brief said:** "Alan's previous ingestion pipeline already used LanceDB."

**Actually:** the prior OpenCode-based Alan at
`C:\Users\ADMIN\projects\opencode-memory` used three stores, none of them
LanceDB:

| Layer | Store | Evidence |
|---|---|---|
| Episodic | SQLite — `sessions`, `decisions`, `tasks` | `episodic.db`, `episodic.py` |
| Semantic | **ChromaDB** v1.4.4 | `chroma/` (12 collections), `MEMORY.md` |
| Relational | **Graphify** v0.9.27 | `graph/graph.json` — 131 nodes, 126 edges |
| Daily logs | Markdown | `daily/` — 19 files |

`DIGITAL_TWIN_ARCHITECTURE.md` states it outright: "Persistent memory:
ChromaDB (semantic) + Graphify (relationships) + Versioned updates."

**Consequence:** the brief's instruction "do not replace LanceDB without a
technical reason" protects something that was never there. LanceDB is
evaluated on its merits in `REPOSITORIES.md`, not as a replacement for a
component Alan already had. Nothing is lost by this correction — but a plan
that assumed LanceDB continuity would have been planning against a fiction.

---

## CORRECTION — ToolSpec exposes `risk`, not `risk_level`

**Brief said:** registry entries expose `name, description, input_schema,
output_schema, permissions, risk_level, executor, timeout, logging_policy`.

**Actually:** `alan.interfaces.tools.ToolSpec` has `risk`, not `risk_level`:

```
name, description, input_schema, output_schema, risk, permissions,
timeout, logging_policy, executor, tags
```

Also worth recording: `doctor` reports **8** tools, not 15, because
`execution` and `coding` are `false` in `config/settings.toml`. That is correct
configuration, not a broken registry — and it is a good illustration of why the
brief's tool count and Alan's actual tool count should not be compared
directly.

---

## CORRECTION — the named historical Alan repositories are not on this machine

The brief asks for comparison against `alan-memory`, `alan-os-platform`,
`alan-opencode-runtime`, `alan-os-kotlin`, `alan-python-kernel`.

None of those directories exists on this host. What does exist is
`C:\Users\ADMIN\projects\opencode-memory`, which is the OpenCode-based Alan
runtime and its memory store, plus one design document.

So the section-20 comparison is performed against:

1. **Current Alan** (`D:\Alan`) — the implementation, with a working end-to-end path.
2. **OpenCode-based Alan** (`opencode-memory`) — memory, identity, episodic
   SQLite, knowledge graph, OpenCode hooks.
3. **`DIGITAL_TWIN_ARCHITECTURE.md`** — the vision, which is mostly unimplemented.

Which repositories are missing is itself a finding, recorded here so the audit
is not read as having compared against five codebases when it compared against
one and a half.

---

## Findings from the prior Alan worth preserving

### F1 — The vision document is 1/6 implemented; memory is the only finished pillar

`DIGITAL_TWIN_ARCHITECTURE.md` checks off exactly one item — "Persistent memory
with versioning" — and leaves executor, planner, configuration, Slack, Discord,
email, GitHub, RLHF, dashboard and scheduler unchecked. Its configuration
schema asks for `GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `DISCORD_TOKEN`, IMAP/SMTP
credentials and a Docker sandbox.

Read against current Alan, this is the clearest possible evidence for the
brief's closing lesson. The prior Alan was a design document with one working
subsystem and a cloud-shaped wish list. Current Alan has a working end-to-end
path and no mandatory cloud dependency. **The comparison must not run
backwards.**

### F2 — Episodic schema is good; two of its features are unproven

`episodic.db` is genuinely well designed and worth keeping in mind:

- `sessions` — `status` in `active|idle|closed`, plus `parent_id` for nesting,
  and per-session code stats (`additions`, `deletions`, `files_changed`)
- `decisions` — `status` in `active|superseded|reverted` **with a
  `superseded_by` foreign key**, so a decision is retired by pointing at its
  replacement rather than by deletion
- `tasks` — `pending|in_progress|done|cancelled`
- 5 indexes, all on the columns actually queried

The supersession idea is worth carrying into Alan's memory model: current Alan's
`MemoryItem` has `importance`, `confidence`, `expires` and `privacy`, but
**nothing that links a memory to the memory that replaced it.** That is a real
gap.

But both of its headline features are unexercised, and reporting them as
working would be dishonest:

- **All 11 `decisions` rows are `status='active'` with `superseded_by=NULL`.**
  The supersession path has never run.
- **28 of 59 `sessions` rows are still `active`**; 28 are `idle`, 3 `closed`.
  Session close-out is not happening.

So: adopt the *idea*, do not assume the *implementation*.

### F3 — The knowledge graph is cheap and local, which is the point

`graph/graph.json` is `{directed, nodes, edges}`, 131 nodes and 126 edges, and
every node is derived from a local markdown file:

```json
{"id": "IDENTITY.md:Name", "label": "Name", "type": "attribute",
 "source": "IDENTITY.md", "properties": {"value": "OpenCode Assistant"}}
{"source": "file:IDENTITY.md", "target": "IDENTITY.md:Name",
 "type": "contains", "label": "has_attribute"}
```

Node identity is `SOURCE:Attribute`; edges are typed. There is no LLM in the
build path — the graph is built by parsing local files. That is the property
worth stealing: it is auditable, it is free, and it cannot hallucinate. Any
replacement has to beat *that*, not beat a vector store.

### F4 — `USER.md` / `IDENTITY.md` / `MEMORY.md` is the right split

Three files, three lifetimes: who the agent is (`IDENTITY.md`, stable),
who the user is (`USER.md`, slow-changing), what has been learned
(`MEMORY.md`, plus `daily/` for per-day detail). Alan's `MemoryLevel` L0–L5
encodes the same idea as an enum, which is more queryable — worth keeping the
enum and borrowing the *separation*.

### F5 — Paths inside the migrated memory are stale

The memory was authored on a Linux host (`/root/.opencode-memory/...`,
`/root/projects/todo-x-routine`, `/usr/bin/sqlite3`, `Asia/Kolkata`) and now
runs on Windows. `MEMORY.md` records that `alan` was **deleted 2026-09-28** with
knowledge preserved at `/root/.opencode/ALAN-ARCHIVE.md` — and that archive is
**not on this machine**. It also flags `vibecoder` as known-stale.

Portable data, non-portable references. Anything adopted from this store needs
a migration story before it is trusted.

---

# Per-repository decisions

Verdicts are argued in `REPOSITORIES.md`. What follows is the decision record:
what was decided, and the one fact that decided it.

## Adopt as a source (ADAPT) — 7

| Repository | Decided by |
|---|---|
| `llama.cpp` | Only item that could remove the Go daemon from the request path. Not yet: the bottleneck was never inference. |
| `mem0` | Extract/compare/ADD-UPDATE-DELETE is precisely the decision Alan's memory lacks. Take the decision, not the plugin architecture. |
| `graphiti` | `valid_at`/`invalid_at` cannot be expressed by Alan's boolean `expires`. Kuzu makes it server-free. Highest priority in the set. |
| `memlocal_core` | Only project that is local-first *by design* and mobile-capable. 1.3 MB, 12 light Rust deps. |
| `lancedb` | Embedded local vector store, if recall ever needs one. On merit — see CORRECTION 1. |
| `mcp-python-sdk` | 3 dependencies, stdio transport, official protocol. Adopt read-only and permission-wrapped. |
| `uv` | Tooling only, for a greenfield Python kernel. Cannot migrate a working `env` without a rebuild. |

## Study, do not depend on (REFERENCE_ONLY) — 13

`pydantic-ai` · `langgraph` · `agent-framework` · `letta` · `letta-code` ·
`scrcpy` · `vesta` · `LocalMind` · `offline-ai-assistant` · `litellm` ·
`prefect` · `OmniParser` · `agent-framework`

## Already in place (IN PLACE) — 2

`ollama` (ModelProvider) · `browser-use` (BrowserProvider)

## Reject (REJECT) — 6

| Repository | Decided by |
|---|---|
| `supermemory` | Default install is a **browser login to `supermemory.ai`**. A memory service that authenticates to a vendor by default is the wrong shape for Alan. |
| `termux-api` | **No LICENSE file anywhere.** A README sentence claiming GPLv3 is not a grant; default copyright applies. |
| `open-browser-use` | Its own README: *"macOS / Linux public preview — the Chrome Web Store listing isn't live yet."* Cannot run on this Windows host. |
| `AutoGPT` | Historical per the brief; and `autogpt_platform/` is Polyform Shield (non-commercial). |
| `autogen` | Upstream maintenance mode per the brief; last commit 6 months behind every peer. |
| `local-rag` | 48 files, and its "local" RAG depends on Microsoft Foundry. |

## Measure, never ship (BENCHMARK_ONLY) — 2

`BrowserGym` · `WindowsAgentArena` — and note the second is not currently
runnable: its five declared dependencies are all `azure-*`, and its commit is
**2024-11-20**, roughly two years stale.

---

# Licence blockers — three repositories, human decision required

| Repository | Finding | Status |
|---|---|---|
| `termux-api` | No `LICENSE` file at any depth. README claims GPLv3. | **Blocked** — no permission to use; GPL-3.0 is copyleft besides. |
| `OmniParser` | Sole licence is CC-BY-4.0; no separate code licence. | **Blocked for reuse** — a content licence applied to code is not an OSI software licence. |
| `AutoGPT` | Split: `autogpt_platform/` Polyform Shield, rest MIT. | **Blocked for the platform folder** — source-available, non-commercial. |

`autogen` is dual (`LICENSE` = CC-BY-4.0 for content, `LICENSE-CODE` = MIT for
code) but the **code** licence is permissive, so it is not a blocker — recorded
because reading only the first file would have got this backwards.

---

# D9 — Two harvester defects found and fixed

The audit's licence column is load-bearing, so a false negative there is worse
than a gap. Both were found by noticing an *implausible* result, not by a failing
test, and both are worth recording as a caution about trusting mechanical output.

**D9a — MIT was misreported as unknown.** Matching only the string `"MIT License"`
fails on ordinary MIT licence files, because the canonical MIT text opens with the
copyright line and goes straight to *"Permission is hereby granted"*; the title
line is often absent. `crewAI` — plainly MIT, © 2025 crewAI, Inc. — came out
`UNKNOWN`. Now matched on the grant sentence.

This one mattered more than it looks. A false negative on a permissive licence
reads as *"this project cannot be used"*, and that is exactly the conclusion a
reader would wrongly draw about 15 of the 30 repositories.

**D9b — `pyproject.toml` dependencies were parsed by regex.** The identifier
`dependencies` also occurs in ruff and mypy configuration and in classifier lists,
and a non-greedy `\[.*?]` captured whatever came first. Real output included
`"."`, `"3.10"`, `"E4"`, `"--ignore"` as dependencies of `graphiti`, `mem0`,
`litellm` and `prefect`. Replaced with `tomllib` and PEP 621 parsing, plus
recursive manifest discovery for monorepos — `langgraph` has no root manifest at
all, and `agent-framework` is `.NET` throughout.

Related: a *display* bug briefly reported `uv`'s dual MIT/Apache licence as
"DMIT", because the dual-licence flag was printed flush against the SPDX string.
The data was correct; the column was not.

The lesson generalises past this script: **a suspicious mechanical result is a bug
report about the script, not a fact about the world.** `DMIT` looked like a
third licence. It was two columns touching.

---

# D11 — The integrity guard was blind to `ModelRouter`

Found during the closing scan, and worth stating plainly because it is a failure
of the tool I built to detect exactly this class of loss.

`scripts/verify_tree.py` excluded generated directories with a single name-based
set, matched against **every** path component:

```python
if SKIP_DIRS & set(p.relative_to(HOME).parts):
    continue
```

`"models"` is in that set, chosen to exclude the top-level GGUF store
(`D:\Alan\models`). But `src/alan/models/` is a real source package. So the check
silently excluded it, and with it:

- `src/alan/models/router.py` (7,405 b) — `ModelRouter` at line 61
- `src/alan/models/__init__.py` (434 b)

Two consequences, both bad. Deleting `router.py` would have been **invisible** to
the guard, and because the mirror was synced from the same truncated file list,
the mirror had **no copy to restore from**. The tool reported "healthy" over a tree
missing one of Alan's load-bearing components. On a machine that has already
deleted three files unprompted, that is exactly the wrong failure mode: silent.

**Fixed** by splitting the set by depth. `SKIP_TOP` applies only at the top level
(`env`, `data`, `models`, `logs`, `build`, `dist`, …); `SKIP_ANY` applies at any
depth (`__pycache__`, `.git`, …). The same bug was present in `scripts/lint_syntax.py`,
which is why it reported 57 files when there are 59 — it had never checked those
two either. Both now 59, both clean.

**Proven, not assumed:** deleted `router.py` deliberately → guard reported
`MISSING src/alan/models/router.py`, exit 1 → `--restore` brought it back →
SHA-256 byte-identical to the original → 77/77 healthy, exit 0.

The general lesson: **a name-based exclusion must record where it applies.**
Matching a bare word against a whole path assumes the word is unambiguous
everywhere, and `models` shows how that fails. `data`, `build`, `dist`, `coverage`
and `test` are all waiting to fail the same way.

Note also that this is the *third* defect found in my own tooling this session
(D9a, D9b, D11). The pattern is consistent: hand-written path logic, unexercised
by a test, found only by an implausible result. Each is now covered by the tree
manifest or by a deliberate failure.

---

# D10 — Two assumptions in the brief that the evidence contradicted

Recorded in full as CORRECTIONs above; collected here so they are not missed:

1. **"Alan's previous ingestion pipeline already used LanceDB."** It did not. The
   prior Alan used **ChromaDB + Graphify + SQLite**. The instruction "do not
   replace LanceDB without a technical reason" therefore protects nothing, and
   LanceDB must be judged on merit alone.
2. **The five named historical repositories** (`alan-memory`, `alan-os-platform`,
   `alan-opencode-runtime`, `alan-os-kotlin`, `alan-python-kernel`) **are not on
   this machine.** The section-20 comparison ran against current Alan, the
   OpenCode-based Alan at `C:\Users\ADMIN\projects\opencode-memory`, and its
   `DIGITAL_TWIN_ARCHITECTURE.md`.

Both corrections reduce the brief's authority rather than extending it. That is the
correct direction for a document whose subject is "do not rewrite Alan".

---

# Final rule, honoured

Nothing in this workspace was built, installed, merged, or wired. No Alan source
file was modified. The deliverable is 30 pinned repositories and five documents.

The next step is a decision, not an import: **temporal validity in Alan's memory
model**, then a read-only MCP tool source behind the permission tiers. Both are
described in `ARCHITECTURE-COMPARISON.md` §7, and both are small.
