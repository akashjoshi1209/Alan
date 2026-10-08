# Repository audit

Audit date: 2026-10-04. Method: repository metadata + full file-tree listing via the
GitHub API at a **pinned commit**, plus README and package-manifest inspection.
No repository was cloned into the product; nothing was forked or renamed.

Section 7 is an **addendum (2026-10-05)**: cognee was not one of the six scoped
sources, but it became the default memory backend, and one of its defects can
only be found by running it — see "datasets are not isolated". The six-source
verdict above is unchanged.

## Verdict summary

| # | Repository | SHA | Lang | License | Files | Verdict |
|---|---|---|---|---|---|---|
| 1 | deepseek-ai/deepseek-harness | `5badb15009ae` | TypeScript | MIT | 14,208 | **Adapt (C)** — architecture ported to Python |
| 2 | mem0ai/mem0 | `abb81c88e1f7` | Python | Apache-2.0 | 1,835 | **Dependency (A)** — L4/L5 semantic memory |
| 3 | letta-ai/letta-code | `77faf36e3437` | TypeScript | Apache-2.0 | 2,456 | **Reference (D)** — memory-block concepts |
| 4 | browser-use/browser-use | `5c892e013a73` | Python | MIT | 536 | **Dependency (A), isolated sidecar** |
| 5 | openinterpreter/open-interpreter | `2767e5f20d69` | Rust | Apache-2.0 | 8,682 | **Reference (D)** — no code taken |
| 6 | SWE-agent/SWE-agent | `3ea751c087f3` | Python | MIT | 409 | **Reference (D)** — agent decomposition |

Classification key: **A** dependency · **B** vendored library · **C** adapter ·
**D** reference implementation · **E** discarded.

### Headline finding

Three of the six sources are **not Python**. DeepSeek Harness and Letta Code are
Node/TypeScript agent harnesses; Open Interpreter has been rewritten in Rust as a
fork of OpenAI Codex. None of them can be imported into a Python product. Only
mem0, browser-use and SWE-agent are Python.

Alan therefore treats #1, #3, #5 and #6 as *architectural sources* and ports the
ideas into Python it owns, and takes real dependencies only where a Python
package exists (#2, #4).

---

## 1. DeepSeek Harness — the primary architectural reference

- **Repo:** `deepseek-ai/deepseek-harness` @ `5badb15009ae` (2026-10-03)
- **Version:** `0.2.1-alpha.1` — self-declared *developer preview* with
  "THERE WILL BE COMPATIBILITY-BREAKING CHANGES".
- **License:** MIT. Third-party notices in `THIRD_PARTY_NOTICES.md`.
- **Runtime:** Node `^22.19.0 || >=24.0.0`, pnpm 11.7.0 monorepo
  (workspaces `vendor/*`, `packages/*/*`, `native/system`, `apps/*`).
- **Composition:** 4,704 `.ts`, 611 `.tsx`, 1,788 `.yaml`, 4,104 `.md`.

### Architecture

Built on **Cordis** ("A Programming Paradigm for Spatiotemporal Composability",
arXiv 2608.25512) with an **everything-is-a-plugin** model. Notable package
boundaries visible in the tree:

| Path | Role |
|---|---|
| `packages/acp/acp` | Agent Client Protocol surface |
| `packages/api/session-controller` | session lifecycle + client |
| `packages/api/job-controller` | long-running job execution |
| `packages/api/gateway`, `remotes`, `account-controller` | transport / remote peers |
| `apps/cli`, `apps/web`, `apps/desktop` | three front-ends over one core |
| `native/system/packages/entry` | native entry surface |
| `.agents/`, `AGENTS.md`, `CLAUDE.md` | agent-facing repo conventions |

Entry point: `npx @deepseek-ai/dsh web` → Web UI on `127.0.0.1:3080`.

### Security model

`SAFETY.md` is called out in the README as mandatory reading before running.
20 GitHub workflows. Access control is not part of the core agent path we need.

### Reusable vs. incompatible

**Reusable (ported to Python, Alan's own implementation):** the plugin/service
registry, the event bus with typed events, session lifecycle, job execution
separation, multi-front-end-over-one-core layering, subagent delegation.

**Incompatible:** the entire Cordis runtime, the TypeScript type system, the
pnpm build, the three front-ends, Bazel-free-but-npm-specific native packaging.

### Decision

**Adapt (C).** Port the *architecture* into `src/alan/core/` — a plugin/service
registry, a typed event bus, session and job lifecycles. Do **not** ship Node,
do **not** run DSH as a process. Rationale and the rejected alternative are
recorded in [ADR-001](../decisions/architecture-decisions.md). The commit is
pinned because the project is in preview and breaks compatibility freely.

---

## 2. mem0 — semantic persistent memory

- **Repo:** `mem0ai/mem0` @ `abb81c88e1f7` (2026-10-01)
- **License:** Apache-2.0. Has `SECURITY.md`; 34 workflows.
- **Composition:** 420 `.py`, 455 `.ts`, plus CLIs in `cli/python` and `cli/node`
  and adapters for Vercel AI SDK, Zapier, DeepSeek, OpenClaw, Pi-agent.

### Runtime dependencies (root `pyproject.toml`)

```
qdrant-client>=1.12.0   pydantic>=2.7.3     openai>=1.90.0    httpx>=0.28.0
posthog>=7.14.0        pytz>=2024.1        sqlalchemy>=2.0.31
protobuf>=5.29.6,<7.0.0
```

Optional extras are large and mostly irrelevant to Alan: `vector-stores`
(chromadb, pinecone, weaviate, faiss, milvus, redisvl, databricks, …),
`llms` (groq, together, litellm, …), `nlp` (spacy).

### Compatibility with Alan's environment

| Package | Alan | mem0 requires | Result |
|---|---|---|---|
| pydantic | 2.13.5 | `>=2.7.3` | compatible |
| openai | 3.24.0 | `>=1.90.0` | compatible |
| httpx | 0.28.1 | `>=0.28.0` | compatible |
| sqlalchemy | — | `>=2.0.31` | new, ~3 MB |
| protobuf | — | `>=5.29.6,<7` | new, ~5 MB |
| qdrant-client | — | `>=1.12.0` | new, ~35 MB |
| posthog | — | `>=7.14.0` | new — **telemetry, must be disabled** |

All pins are `>=` ranges, so mem0 coexists with Alan's stack.

### Security

`posthog` is an analytics client shipped as a hard dependency. Alan's
environment policy (§16 of the brief) forbids unsolicited telemetry, so the
provider must be initialised with telemetry disabled and no API key, and the
`MemoryProvider` must never let mem0 reach a cloud endpoint. mem0 is pointed at
the **local Ollama** server for its extraction LLM, keeping Alan keyless.

### Decision

**Dependency (A).** Installed into the main environment as a lazily-imported
optional provider behind `MemoryProvider`. cognee remains the default local
store; mem0 supplies L4/L5 semantic memory. The backend is swappable by config.

---

## 3. Letta Code — stateful-agent and memory concepts

- **Repo:** `letta-ai/letta-code` @ `77faf36e3437` (2026-10-03)
- **License:** Apache-2.0.
- **Runtime:** TypeScript on **Bun** (`bun.lock`); npm package
  `@letta-ai/letta-code`, entry `bin/letta.js`. 2,034 `.ts`, 131 `.tsx`.
- **Front-ends:** CLI (ink/React), desktop app, browser, Telegram, Slack,
  Discord; `src/channels/` holds the integrations.

### Capabilities worth adopting (concepts only)

| Concept | Source path | Alan counterpart |
|---|---|---|
| Memory blocks edited by the agent itself | memory blocks / system-prompt learning | `memory/blocks.py` |
| MemFS — all context tracked in git | MemFS | `memory/memfs.py` (git-backed) |
| Skill learning / self-configuration | `src/skills/builtin/self-configuration` | `agents/` skill loading |
| Subagents incl. forked and recall subagents | `src/agent/subagents` | `agents/subagent.py` |
| `/palace`, `/doctor`, `/sleeptime` introspection | CLI slash commands | `alan doctor`, `alan memory --palace` |
| Permission modes | permissions | `permissions/` |
| Hooks at execution points | `src/hooks` | `core/events` subscribers |

### Decision

**Reference (D).** No code, no dependency, no Node runtime. The memory-block
and MemFS ideas are reimplemented natively because they are small, central to
Alan's identity, and would be distorted by a Node boundary.

---

## 4. browser-use — browser automation

- **Repo:** `browser-use/browser-use` @ `5c892e013a73` (tag `0.13.10`, 2026-09-04)
- **License:** MIT, with `.github/SECURITY.md`.
- **Composition:** 410 `.py`. Entry points `browser_use/__main__.py`,
  `browser_use/cli.py`, `browser_use/mcp/__main__.py`.

### Dependencies — the blocking problem

browser-use pins almost everything with `==`, and drags in roughly 40 packages
(as of `0.13.10`):

```
aiohttp==3.14.3      anyio==4.12.1      click==8.3.3      rich==14.3.3
openai==2.26.0       pydantic==2.13.5   httpx==0.28.1     requests==2.33.0
anthropic>=0.76      groq==1.0.0        ollama==0.6.1     posthog==7.7.0
google-genai==1.65.0 google-api-core==2.29.0   mcp==2.1.1     pillow==12.3.0
pypdf==6.16.2 reportlab markdownify python-docx psutil screeninfo uuid7
browser-use-sdk==3.4.2   browser-harness==0.1.13   cdp-use==1.4.5
pydantic-settings==2.15.0
```

### Conflict matrix against Alan's main environment

| Package | Alan | browser-use | Result |
|---|---|---|---|
| `openai` | 3.24.0 | `==2.26.0` | **hard conflict** — would downgrade Alan's cloud provider |
| `rich` | 15.0.0 | `==14.3.3` | **hard conflict** — CLI rendering |
| `requests` | 2.34.2 | `==2.33.0` | **hard conflict** |
| `posthog` | 7.62.1 | `==7.7.0` | **hard conflict** — added by mem0, downgraded by browser-use |
| `pydantic` | 2.13.5 | `==2.13.5` | match |
| `httpx` | 0.28.1 | `==0.28.1` | match |
| `anthropic` | 1.11.0 | `>=0.76.0,<2.0.0` | compatible |

Installing browser-use into the main environment would downgrade `openai` from
3.x to 2.x and break Alan's OpenAI provider. Four hard conflicts, not three.

**Correction to the original audit: there is no Playwright dependency.**
`pyproject.toml` at the pinned commit lists `cdp-use==1.4.5` and
`browser-harness==0.1.13` and contains no Playwright reference. The earlier
finding that browser-use "needs a Chromium binary (Playwright, ~150 MB+)" was
true of earlier versions and is now obsolete. Consequence: there is no
`playwright install` step, no driver to fetch, and no bundled browser. The
sidecar launches the host's installed Chrome or Edge headless and drives it over
CDP — which removes the "violates one folder, zero host installs" objection
entirely and is why ADR-003 was amended rather than left describing a problem
that no longer exists.

### Security

A browser agent is the highest-risk capability in the system: it can reach
authenticated sessions. browser-use is therefore never exposed to a model
directly. Every action passes Alan's permission gate, domain policy and
confirmation flow, and results are logged. Telemetry (`posthog`) is disabled.

Its agent loop is additionally **not used at all**. `browser-use` can either be
driven directly or given a task and left to plan it; the second mode would put a
second planner in the loop beside Alan's. The `retry_with_browser_use_agent` MCP
tool shipped upstream is not exposed. `extract_content` is also unused because it
requires an LLM, and delegating page extraction to a 1.5B model is worse than
reading `innerText` directly.

### Search is inherently untrustworthy

Search engines are scraped HTML pages, and scraping them fails silently rather
than loudly. Measured from this machine:

| Engine | Behaviour |
|---|---|
| DuckDuckGo `html.duckduckgo.com` | Empty shell, captcha wall ("bots use DuckDuckGo too") |
| `lite.duckduckgo.com` | Same captcha wall |
| Mojeek | `403 Forbidden` |
| Brave, Startpage, `searx.be` | Anti-bot challenge or empty |
| Marginalia | Real relevant results; often empty on the first fetch (retry required) |
| Bing | Real results with snippets; sometimes empty, and sometimes **cached results for an unrelated query** while the page reports the correct URL and title |

The Bing case is the important one: a page was observed whose `location.href`
and `document.title` both matched the query while its `li.b_algo` rows were a
complete SERP for "yt jeffsy" on Pinkbike. The HTML being served is rewritten in
transit — the same network also serves a modified `example.com` with no `<h1>`.

Alan's response is to treat a non-empty result set as unproven: every result
must share at least one meaningful (non-stopword) token with the query or it is
discarded, and if nothing survives the tools report that no *trustworthy*
results were available rather than returning the page's contents. The default
engine is `marginalia` with `bing` fallback, both measured; DuckDuckGo stays
registered because it works from other networks.

### Decision

**Dependency (A) in an isolated sidecar environment**, reached only through
Alan's `BrowserProvider` over a loopback HTTP boundary on port 8022 — the same
isolation pattern already proven by the cognee sidecar. browser-use 0.13 is
async, so the sidecar bridges it with a private asyncio loop on a daemon thread
to keep one session alive across synchronous requests. Wired on by default, but
it launches no browser until a tool is called and the gate approves it, and the
test suite mocks the provider so no browser is needed to test.

Two library quirks are pinned by tests, because both look like Alan bugs when
met cold: `Element.evaluate` receives no element reference (`el` is undefined,
so any expression touching it throws — `Page.evaluate` with explicit `*args` is
used instead), and `page.screenshot()` returns a base64 **string**, not bytes.

---

## 5. Open Interpreter — local execution

- **Repo:** `openinterpreter/open-interpreter` @ `2767e5f20d69` (2026-10-02)
- **License:** Apache-2.0, with `SECURITY.md`.
- **Runtime:** **Rust** — 4,813 `.rs` files, Bazel build, `codex-rs/*` crates
  (`agent-graph-store`, `agent-identity`, `agent-roles`, `apply-patch`,
  `bwrap`, …), a thin `codex-cli` npm shim, and 1,343 `.snap` desktop files.
  It is a fork of OpenAI Codex. Legacy Python (209 `.py`) is vestigial.

### Security

The documented install path is `curl -fsSL … | sh` (macOS/Linux) and
`irm … | iex` (Windows) — remote-script execution, exactly the pattern the
brief's §16 forbids for untrusted sources. It is also a terminal coding agent
with broad local authority.

### Reusable vs. not

Conceptually useful: harness abstraction (`/harness` swaps the agent loop),
low-cost-model-first design. Not reusable: the Rust implementation, the Codex
lineage, the self-installing installer.

### Decision

**Reference (D), no dependency.** Alan's `ExecutionProvider` is a small native
Python implementation (`ShellExecutor`, `PythonExecutor`, `FilesystemExecutor`,
`ProcessExecutor`) where Alan owns the permission decision outright. Depending on
a 287 MB Rust project to run `subprocess` would add risk, not capability.

---

## 6. SWE-agent — specialised coding agents

- **Repo:** `SWE-agent/SWE-agent` @ `3ea751c087f3` (2026-07-16)
- **License:** MIT, `SECURITY.md` present.
- **Composition:** 100 `.py`, 51 YAML configs, and a notable `tools/` directory
  of ~12 standalone CLI tools (`filemap`, `str_replace_editor`, `_state_diff_state`,
  `view_image`, `search/find_file`, `registry/_read_env`, `submit`, …).
- **Entry point:** `sweagent = "sweagent.run.run:main"`.

### Dependencies

`datasets`, `pandas`, `numpy`, `flask`, `flask-socketio`, `litellm`, `GitPython`,
`ghapi`, `swe-rex`, `textual`, `tenacity`, `unidiff`, `simple-parsing`,
`rich-argparse`, `tabulate`. Heavy research/eval surface (datasets + pandas +
flask) that Alan has no use for.

### Valuable architecture

The decomposition of a coding agent into a narrow tool set plus a controlled
loop — inspect, edit, run, submit — with each tool a separate process and the
diff as the unit of review. This is what Alan's CodingAgent reproduces natively.

### Decision

**Reference (D).** No dependency: the dependency set is research-oriented and
would bloat the portable environment. Alan implements
`RepositoryAnalyzer → CodeEditor → TestRunner → DiffReviewer → PermissionGate`
natively, and may later drive SWE-agent as an isolated subprocess for very hard
issues.

---

## 7. cognee - the default memory backend

Not one of the six sources this audit was scoped to, but it became Alan's
default `MemoryProvider` (ADR-002/ADR-003) and so earned the same treatment.
Pinned `cognee==1.6.2`, `torch 2.14.1+cpu`, in its own environment
`env\cognee\python`, reached only over loopback HTTP on port 8012.

### Defect found in use: datasets are not isolated

`POST /api/v1/recall` accepts a `datasets` filter. Alan sends it correctly:

```json
{"query": "cognee latency probe browser sidecar port",
 "datasets": ["latency_probe"], "topK": 5, "searchType": "CHUNKS"}
```

The response still contains passages from the `alan` dataset:

```
cognee latency probe 3: Alan's browser sidecar listens on port 8025   (asked for)
cognee latency probe 1: Alan's browser sidecar listens on port 8023   (asked for)
cognee latency probe 2: Alan's browser sidecar listens on port 8024   (asked for)
no my name is akash                                                    (not asked for)
Alan keeps its bundled Ollama on port 11435, not the default 11434.   (not asked for)
```

The filter is not applied to whichever source answers the query. Alan's request
is well-formed; the defect is upstream.

A second symptom turned out to be the same root cause. Recalling
`what is the capital of France?` from the `alan` dataset returned three
unrelated passages, and `my name is Sam` returned *the same three* — cognee's
ranking was not discriminating at all on a dataset this small. So one defect
showed up twice, as leakage and as irrelevance, and a single mechanism had to
answer both.

### Decision

**Keep the dependency; refuse to trust its ranking.** Alan does not filter
client-side on a field the backend demonstrably ignores, and does not delete
datasets to hide the symptom — that destroys real memory. Instead:

* `alan.memory.relevance` decides what may be **asserted**, and it is Alan's own
  code. Zero lexical overlap scores zero, and a zero-scoring passage is never
  rendered into a prompt. cognee keeps the job it is good at — proposing
  candidates — and loses the job it is not: deciding what Alan claims to know.
  This restores the one property the product promises and the backend could not:
  **Alan can say "I don't know."**
* `alan forget --list` enumerates Alan's own index, which only ever contains
  what Alan wrote. Recall showing anything outside that list is this defect, and
  the list is therefore also the diagnostic.

Both are covered by `tests/test_relevance.py`, and the gate is applied in
`MemoryProvider.context_block`, so a future backend inherits the guarantee
without having to be taught it.

---

## Rejected: cloning whole repositories

Cloning all six would add ~820 MB (mostly `.git`) to a portable product for
zero runtime capability, since three of them cannot execute Python and the rest
have incompatible or oversized dependency sets. Source inspection was performed
against the GitHub API at pinned commits instead; see
[dependencies.md](dependencies.md) for the pin table and
[ADR-001](../decisions/architecture-decisions.md) for the rationale.
