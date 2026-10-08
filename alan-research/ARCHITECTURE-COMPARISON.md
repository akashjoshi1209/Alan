# Architecture comparison

Alan against the serious alternatives, axis by axis.

Scope: the eight systems the comparison is actually useful for — **Alan**,
**Letta**, **Mem0**, **Graphiti**, **Supermemory**, **Pydantic AI**, **LangGraph**,
**MCP**.

Every claim below is checkable against a pinned commit in
`clone-results.json`. Where a project has changed shape recently, that is called
out rather than glossed — one of the acquired repositories turned out to have no
source code left at all.

---

## 1. The short version

**They are not competitors to Alan. They are components, and Alan is the
system.**

- Alan owns the request path, the permission tiers, the memory shape, the tool
  registry and the events. None of the seven does any of that.
- The five memory projects each own one idea that Alan lacks, and each would
  replace five things Alan has if adopted wholesale.
- LangGraph, CrewAI and Prefect each want to own the loop. The loop *is* Alan.
- Pydantic AI is a well-made reference for tool schemas that Alan has already
  learned to generate for itself.
- MCP is the one protocol genuinely worth adopting, and it has no security model
  at all — which is precisely why it needs Alan wrapped around it.

The historical lesson applies directly: unwired is a bug list, not a reason to
rewrite Alan.

---

## 2. Axis comparison

`●` strong · `○` adequate · `△` partial · `—` absent · `n/a` not its job

| | Alan | Letta | Mem0 | Graphiti | Supermemory | Pydantic AI | LangGraph | MCP |
|---|---|---|---|---|---|---|---|---|
| **Architecture** | | | | | | | | |
| Single composition root | ● | ○ | ○ | ○ | △ | ● | ● | — |
| Python-first | ● | — | ● | ● | — | ● | ● | ● |
| Second runtime required | — | ● TS | — | — | ● TS | — | — | — |
| **Memory** | | | | | | | | |
| Hierarchical levels | ● L0–L5 | ○ blocks | △ | △ | ○ | — | — | — |
| Extracted from conversation | ● | ● | ● | ● | ● | — | — | — |
| Conflict resolution | △ | ○ | ● | ● | ○ | — | — | — |
| Temporal validity | — | ○ | ○ | ● bi-temporal | ○ | — | ● checkpoint | — |
| Supersession links | △ planned | ○ | ○ | ● edges | ○ | — | — | — |
| Recall is gated, not dumped | ● | ○ | ○ | △ | △ | — | — | — |
| **Local / privacy** | | | | | | | | |
| Fully local by default | ● | △ | ○ | ● w/ Kuzu | — cloud | — | ● | ● |
| Server-free option | ● | ● | ● | ● Kuzu | △ | ● | ● | ● stdio |
| Telemetry present | — | ? | ? | ? | ● | ? | ? | ? |
| **Tools & execution** | | | | | | | | |
| Typed tool schemas | ● | ○ | ○ | — | ○ | ● best | ○ | ● |
| Risk tiers / permissions | ● 5 tiers | △ | — | — | — | — | — | — |
| Tool approval gate | ● | ○ | — | — | — | — | ● HITL | △ elicitation |
| Long-running resumable | ● scheduler | ○ | — | — | — | ○ | ● checkpoints | — |
| Bounded retry/timeout | ● | ○ | ○ | ○ | ○ | ○ | ○ | — |
| **Observability** | | | | | | | | |
| Structured events | ● 6 families | △ | △ | △ | △ | ● | ● | △ |
| Trace id per request | ● | △ | — | — | — | ● | ● | — |
| **Agent runtime** | | | | | | | | |
| Subagent allow-lists | ● 7 agents | ○ | — | — | ○ | ○ | ● | — |
| Model routing | ● 6 categories | ○ | ○ | — | ○ | ○ | — | — |
| Prefer-local policy | ● | △ | ○ | ○ | — | — | — | — |

Read the table by column, not by row. Alan's column is the only one with
entries in *every* row, and that is the whole argument for keeping Alan's
architecture: the others are deeper in one or two rows and empty in the rest.

---

## 3. Memory, one at a time

Memory is where the temptation is strongest and where the brief's warning is
most needed. So: does the project replace Alan's memory model, or does it fill a
hole in it?

### Alan — L0–L5 with a recall gate

```
L0_CONTEXT   the prompt being answered
L1_WORKING   this turn's scratch
L2_SESSION   this conversation
L3_PROJECT   one project, isolated
L4_USER      who the user is, slow-changing
L5_ARCHIVE   everything, cold
```

`MemoryItem` carries `content`, `level`, `type`, `source`, `confidence`,
`importance`, `privacy`, `last_accessed`, `expires`, `session_id`, `project`,
`metadata`.

The distinguishing feature is not the enum — it is the **gate**. In
`memory/relevance.py`, zero lexical overlap scores zero, and no combination of
level, importance or confidence can rescue it. `context_block()` then gates
generically before anything reaches a prompt.

That exists because retrieval was dumping cognee's whole candidate set into the
prompt. cognee remains the candidate *generator*; the gate decides what may be
*asserted*. Verified behaviour: an unrelated query returns 0 hits and a 0-character
context block. That is what makes "I don't know" reachable — and it is the single
most important thing in Alan's memory subsystem, with no counterpart in any of the
five projects below.

**Gap, honestly stated:** `MemoryItem` has `expires`, but nothing links a memory
to the memory that replaced it. The prior OpenCode-era Alan had
`decisions.superseded_by` — a foreign key pointing a retired decision at its
replacement. The schema existed and was never exercised (all 11 rows are `active`
with `superseded_by` NULL), but the *idea* is right and Alan should adopt it.

### Letta — memory blocks and self-editing

Letta's contribution is the notion that memory is an **addressable block the
agent itself rewrites**, plus sleep-time "dreaming" consolidation.

Two facts decide its verdict here:

- **`letta` no longer contains source.** Its README states plainly: "The current
  source code lives in letta-ai/letta-code." What is left is governance —
  `PRIVACY.md` (21 KB), `AI_POLICY.md`, `SECURITY.md`, `TERMS.md`, `LICENSE`,
  `CITATION.cff`. **The brief asked to inspect the historical repository; the
  honest answer is that there is no historical source to inspect.**
- **`letta-code` is TypeScript-first: 2,023 `.ts` and 131 `.tsx` against 7 `.py`
  files.** Its memory machinery lives in `src/agent/` and `src/skills/builtin/`
  (`managing-shared-memory`, `migrating-memory`, `syncing-memory-filesystem`).

So Letta's ideas are worth reading and its code is not adoptable by a Python
Alan without a Node runtime or a rewrite.

**Verdict: `REFERENCE_ONLY`.** Take the concepts — self-editing memory blocks,
sleep-time consolidation — into Alan's design vocabulary. Take no code.

### Mem0 — extraction and conflict resolution

Mem0 is the most directly relevant project in the entire set, because the one
thing Alan's memory lacks is exactly what mem0 is for: turning a conversation
into discrete memories, and deciding what to do when two disagree.

Its machinery is LLM-driven — extract candidate facts, compare against existing
memory, then `ADD` / `UPDATE` / `DELETE` / `NOOP`. That is the right shape for a
digital twin, and it is a genuine hole in Alan's design.

But quality caveats found in the repository:

- Its 98,929-byte `memory_core.py` is **duplicated verbatim seven times** across
  `integrations/{agent-plugin,codex-plugin,antigravity-plugin,cursor-plugin,
  claude-code-plugin,mem0-agent-plugin,kimi-plugin}/core/`. Seven copies of a
  99 KB file is a maintenance liability in the project itself.
- The repository's centre of gravity has shifted toward **coding-agent
  integrations** — `.claude-plugin`, `.codex-plugin`, `.cursor-plugin`,
  `.kimi-plugin`, `skills`, `server`, `mem0-ts` — rather than toward being a
  standalone memory library.

**Verdict: `ADAPT`.** Port the extraction and conflict-resolution *decision*, not
the plugin architecture. Alan's `MemoryProvider` is the seam; mem0 supplies a
candidate generator, and Alan's gate still decides what may be asserted.

### Graphiti — bi-temporal knowledge

Graphiti is the strongest single idea available, and it has a decisive enabler
that is easy to miss: its driver layer supports **Kuzu**, an embedded graph
database, alongside Neo4j, FalkorDB and Neptune.

That matters enormously. Neo4j means a server; Neptune means AWS and a bill. Kuzu
means a directory. For a 4 GB Windows host and a future phone, that is the
difference between a viable dependency and an unusable one.

Its edge model carries `valid_at` / `invalid_at` — a fact is true *during an
interval*, not forever. That is a strictly better model than Alan's boolean
`expires`, and it is the correct answer to "what changed my mind about X".

**Verdict: `ADAPT`**, and the highest-priority one in this document. Not for the
graph engine — for the temporal edge semantics, re-expressed in Alan's own
`MemoryItem` and `MemoryLevel`. Alan does not need a graph database on day one;
it needs to stop asserting that a superseded fact is still true.

### Supermemory — reject as a dependency, study its OpenCode plugin

Supermemory is memory + context engine + RAG, and it has an OpenCode integration,
which made it look like a natural fit for a project with OpenCode history. The
audit says otherwise, on evidence:

- **210 `.ts` + 127 `.tsx` against 53 `.py`**, in a bun/turbo monorepo.
- The package list includes `openai-sdk-python`, `pipecat-sdk-python` (voice),
  `livekit-sdk-python`, `cartesia-sdk-python`. That is a cloud-and-voice product.
- **Cloud is the default path.** The documented install is
  `bunx opencode-supermemory@latest install`, then
  `bunx opencode-supermemory@latest login` — a **browser login** to
  `supermemory.ai`. Self-hosting is an opt-in tip (`npx supermemory local`), and
  the plugin lives in a **separate repository** (`supermemoryai/opencode-supermemory`)
  that was not part of this acquisition.
- Even self-hosted, it is a TypeScript server Alan would have to run.

**Verdict: `REJECT` as a dependency.** For a product whose premise is that data
does not leave the machine, a memory service whose default is a browser login is
the wrong shape. Its multimodal ingestion is worth reading; its architecture is not.

### memlocal_core — local-first retrieval

Small (1.3 MB) and explicitly local-first: local database, vector retrieval,
full-text search, graph memory, reranking, local inference, mobile SDKs, MCP
integration. Apache-2.0.

Its mobile-and-offline emphasis is the most directly relevant *design* in the set
for Alan's stated Android future.

**Verdict: `ADAPT`** for retrieval design and the mobile story. Small enough to
read end to end, which is itself a recommendation — the projects worth learning
from are the ones that fit in context.

---

## 4. Agent runtime

### LangGraph — checkpointing, and why not the loop

LangGraph's genuinely valuable contribution here is durable checkpointing:
`libs/checkpoint`, `checkpoint-sqlite`, `checkpoint-postgres`. Long-running work
that can be paused and resumed is a real capability, and `checkpoint-sqlite` is
the local-first half of it.

But LangGraph is a graph engine that wants to own the orchestration. Alan's
`Orchestrator` already is that, and it is wired: 176 tests, five passing demos,
one request path with six phase events
(`request.started → memory.retrieved → plan.created → model.started →
model.finished → request.finished`).

**Verdict: `REFERENCE_ONLY`.** Read the SQLite checkpointer. Do not adopt the
engine.

### Pydantic AI — the tool-schema reference

Pydantic AI is the best-engineered Python agent framework in the set, and it is
the clearest case for `REFERENCE_ONLY` in the whole audit: Alan already does the
things it is best at.

Its `providers/` directory holds **38 adapters, of which roughly 36 are cloud**
— Anthropic, Azure, Bedrock, Google, Groq, Fireworks, Together, OpenRouter, xAI,
Cohere, Mistral, SambaNova, Snowflake, Vercel, Heroku, DeepSeek, and more. The
only local ones are `ollama.py` (5 KB) and `vllm.py` (8.4 KB).

For a local-first product, that ratio is the finding. Alan's own
`ModelProvider` + `ModelRouter` (six categories: FAST, REASONING, CODING, VISION,
LOCAL, REMOTE, with a local-first preference) covers the local path that 36 of
these adapters are not about.

Note also that pydantic-ai ships its own `pydantic_graph` — a graph module
alongside LangGraph. The "just use the framework" reflex would arrive with two
orchestration engines attached.

**Verdict: `REFERENCE_ONLY`.** Take the tool-declaration ergonomics — schema
generation, typed results, validation — as a quality bar. Alan's `ToolSpec` and
registry already meet it.

### MCP — the one thing genuinely worth adopting

The Python SDK is a small, well-scoped, dependency-light protocol implementation.
Transports on the client side: `stdio`, `sse`, `streamable_http`, plus `auth`,
`caching`, `session_group`, and a client `extension` module. Server side:
`mcp/server/lowlevel/{server,helper_types}.py`, `mcp/types`, `mcp/shared`.

The finding that matters: **MCP has no permission model.** Searching the SDK for
`human_in_the_loop`, `require_approval` or an approval gate returns nothing
comparable. What it has is **elicitation** — `ctx.elicit(...)`, the server asking
the *client* to collect input from the human — and a `roots` capability for
hinting at filesystem boundaries. Elicitation is not authorization.

So an MCP tool call is, by design, something the host executes on trust. That is
exactly the gap the brief flagged, now confirmed by reading the code rather than
by assumption.

**Verdict: `ADAPT`, behind Alan's permission layer.** MCP becomes a *source of
tool specs*, each mapped into Alan's `ToolRegistry` with a `RiskLevel`,
`timeout`, `permissions` and `logging_policy` — never an escape hatch around it.
An agent's allow-list must not become irrelevant because a tool arrived over MCP.

### CrewAI, Prefect, AutoGPT — reliability patterns only

- **Prefect** teaches the right lesson for an agent system: let deterministic
  code own scheduling, retries and state rather than a model. Alan already
  believes this — `Alan Scheduler` owns recurrence, and retry/timeout/fallback
  are bounded in Alan's own code.
- **CrewAI** (399 MB, the largest repository acquired) is agent-orchestration
  built on agent-orchestration. It would be a second Alan.
- **AutoGPT** is explicitly historical in the brief, and correctly so.

None is a dependency. Prefect's reliability patterns are worth reading; the rest
is architecture Alan should not acquire.

---

## 5. Verdicts

### KEEP FROM ALAN

The list is short because Alan is already coherent, and coherence is the
asset.

1. **The composition root.** One `build_runtime()`. The single most valuable
   property in the codebase, and the one most easily destroyed by adding a
   second assembly path.
2. **The relevance gate.** Zero lexical overlap ⇒ zero, unrescuable by bonuses.
   This is what makes "I don't know" real, and no acquired project has an
   equivalent.
3. **Five permission tiers agents cannot bypass**, with per-subagent allow-lists
   and risk ceilings. More sophisticated than anything in this workspace.
4. **Six structured event families with a per-request `trace_id`.** The acquired
   frameworks trace well; none has a permission-tier model at all.
5. **A local-first model router** with six categories and a prefer-local policy.
6. **Bounded retry/timeout/fallback/escalation**, plus a probe cache so
   health checks do not pay full latency (34.2 s → 5.5 s overall).
7. **Seven owned provider interfaces** as the only door into Alan.
8. **The graceful browser-sidecar shutdown** — profile directory named
   `browser-use-user-data-dir-sidecar-8022` specifically so orphan reaping can
   recognise it. Verified 0 → 9 → 0 Chrome processes across a full cycle. That
   is the level of operational care the acquired projects do not have.

### BORROW FROM EXTERNAL PROJECTS

| Take | From | As |
|---|---|---|
| Bi-temporal edge validity (`valid_at`/`invalid_at`) | Graphiti | `MemoryItem` validity interval |
| Supersession links | prior Alan `decisions.superseded_by` | `MemoryItem.superseded_by` |
| Extract / UPDATE / DELETE conflict decisions | Mem0 | inside Alan's `MemoryProvider`, after Alan's gate |
| Self-editing memory blocks | Letta | memory *behaviour*, not a new store |
| Durable checkpointing (SQLite flavour) | LangGraph | Alan scheduler resume |
| Tool declaration ergonomics | Pydantic AI | quality bar for `ToolSpec` |
| MCP transports + tool discovery | MCP SDK | a tool *source*, permission-wrapped |
| Local-first retrieval + mobile story | memlocal_core | retrieval design |
| Offline-first assistant UX | Vesta | Android interaction reference |
| JNI/GGUF model session handling | LocalMind, offline-ai-assistant | Android inference patterns |
| Embedded graph storage | Graphiti's Kuzu driver | local, serverless graph when needed |

### REPLACE ONLY IF PROVEN BETTER

Exactly one candidate, and it is not a whole system:

- **Alan's memory validity model, replaced by Graphiti's interval semantics.**
  Justified by a schema-level argument (a boolean `expires` cannot express
  "true from March until May"), not by a benchmark. If implemented, it must
  measure better on Alan's own recall set before it lands.

Everything else in the acquired set either has no counterpart in Alan to replace,
or would replace something that already works.

### DO NOT USE

- **supermemory** — cloud-login default, TS runtime, voice/LiveKit packages.
- **AutoGPT** — historical, per the brief.
- **autogen** — upstream maintenance mode.
- **open-browser-use** — macOS/Linux public preview; the Chrome Web Store listing
  is not live, so it does not run on this Windows host at all.
- **local-rag** — 2.2 MB, a tutorial-scale implementation with nothing Alan lacks.
- **BrowserGym / WindowsAgentArena** as infrastructure — benchmarks that must
  measure Alan, never ship inside it.
- **Direct Ollama dependency as the long-term answer** — Alan should keep
  `ModelProvider` as the contract and treat any single backend as replaceable,
  which is exactly why `llama.cpp` remains worth studying.

---

## 6. Recommended final architecture

Unchanged in shape. Sharper in four places.

```
USER → ALAN → ORCHESTRATOR → memory recall (gated) → plan → route (local first)
                              ↓                                 ↓
                          TOOL REGISTRY                    MODEL
                              ↓
                        permission tier ──▶ TOOL ──▶ SUBAGENT (allow-list)
                              ↓
                          event + trace_id
                              ↓
                            REMEMBER
```

What changes, on evidence from this audit:

1. **Memory validity becomes an interval, not a boolean.** From Graphiti. A
   superseded memory is still retrievable as history but cannot be asserted as
   current — which also gives Alan's "I don't know" a way to be *temporally*
   honest.
2. **Memory gains a supersession edge.** From Alan's own prior `decisions` table.
3. **MCP arrives as a tool source, permission-wrapped.** An external tool gets a
   `RiskLevel` and a timeout like any other, or it does not run.
4. **The gate stays Alan-native, and gets stronger.** Every one of the five
   memory projects returns *more* candidates, not better assertions. More
   candidates makes the gate more important, not less. Nothing here replaces it.

And what does not change, stated plainly: one composition root, one request
path, one permission model, one memory shape. Thirty repositories were read so
that this sentence could be made with evidence instead of faith.

---

## 7. Next milestone

**Temporal correctness in Alan's memory model, then external tools behind the
gate.**

1. Add `valid_from` / `valid_to` and `superseded_by` to `MemoryItem`.
2. Teach the gate to refuse an item whose validity interval has closed, and to
   report it as history rather than fact.
3. Tests first: an item valid only until a date must not be asserted after it.
4. Then a read-only MCP tool source, every imported tool mapped to an existing
   `RiskLevel`, with a test proving an agent cannot escalate through it.

Measure before adopting anything else. The bottleneck found so far was never
model quality — it was recall discipline and prompt assembly. That is where the
next win is too.