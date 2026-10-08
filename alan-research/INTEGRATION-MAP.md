# Integration map

External projects placed against Alan's architecture.

The left column is Alan. The right column is what this workspace acquired to
study. A name appearing on the right is **not** a dependency — it is a candidate
source of implementation ideas, pinned to a commit, in
`REPOSITORIES.md`.

---

## Alan's actual shape

Before mapping anything onto it, what Alan is today. All of this is Alan-owned
and none of it came from a repository in this workspace.

```
                        ┌──────────────────────────────┐
   USER ──▶ alan.assistant.Alan ──▶ Orchestrator ──▶ AgentRuntime
                                                     │
             ┌────────────────────────────┬──────────┴───────────┐
             ▼                            ▼                      ▼
        ModelRouter                 MemoryProvider            ToolRegistry
    (FAST/REASONING/CODING/     (L0–L5 + relevance gate)   (8 or 15 specs,
     VISION/LOCAL/REMOTE)                                   risk-tiered)
             │                            │                      │
             ▼                            ▼                      ▼
       ModelProvider              cognee + recall gate    ToolProvider
       (Ollama now)              (lexical overlap ≥ gate) BrowserProvider
                                                          ExecutionProvider
                                                          CodingProvider
                                                         (all Alan-owned
                                                          interfaces)
```

Seven provider interfaces, all defined by Alan in
`src/alan/interfaces/`: `AgentRuntime`, `ModelProvider`, `MemoryProvider`,
`ToolProvider`, `BrowserProvider`, `ExecutionProvider`, `CodingProvider`.

The contract with every external project is that **it implements one of those
seven, or it does not enter Alan.** A repository that wants to be Alan must
speak in Alan's types. This is the single mechanism that keeps acquisition from
becoming a rewrite.

### The invariants any integration must preserve

| Invariant | Concretely |
|---|---|
| One composition root | `src/alan/runtime.py`. No second `build_*()` that assembles an agent. |
| One request path | `Orchestrator.run()` → plan → route → recall → tool → subagent → result. |
| Local first | a local model must always satisfy a request; cloud is never a hard requirement |
| Alan owns permissions | `SAFE < LOW_RISK < MEDIUM_RISK < HIGH_RISK < PRIVILEGED`, no agent bypass, ever |
| Alan owns memory shape | L0–L5, metadata, and the recall gate. A provider stores; it does not decide. |
| Bounded work | retry, timeout, fallback and escalation are all bounded; nothing loops forever |
| Alan can say "I don't know" | zero relevance ⇒ empty context, never a guess |
| Observable | `agent.*`, `tool.*`, `memory.*`, `permission.*`, `model.*`, `subagent.*`, each with a `trace_id` |

---

## The map

```
ALAN
│
├── Inference ────────────────────────────────────────────── study only
│   ├── llama.cpp ................. ADAPT if direct GGUF inference is wanted
│   └── Ollama .................... IN PLACE (ModelProvider impl)
│
├── Agent runtime ───────────────────────────────────────── Alan-native
│   ├── Alan Kernel ............... KEEP — Orchestrator, planner, runner
│   ├── pydantic-ai .............. REFERENCE_ONLY (typed tools, output validation)
│   ├── LangGraph ................. REFERENCE_ONLY (checkpoint/resume)
│   └── agent-framework .......... REFERENCE_ONLY (HITL patterns)
│   └── (autogen) ................ REJECT — maintenance mode
│
├── Memory ──────────────────────────────────────────────── Alan-native shape
│   ├── Alan Memory (L0–L5) ...... KEEP — the model
│   ├── Letta ..................... REFERENCE_ONLY — memory blocks, self-editing
│   ├── Mem0 ...................... ADAPT — extraction + conflict resolution
│   ├── Graphiti .................. ADAPT — temporal edges, bi-temporal validity
│   ├── Supermemory ............... REJECT as dependency; study its OpenCode plugin
│   └── memlocal_core ............. ADAPT — local-first retrieval design
│
├── Knowledge ─────────────────────────────────────────────
│   ├── cognee (in place) ........ KEEP
│   ├── LanceDB ................... ADAPT — embedded vector store
│   └── local-rag ................. REJECT — 2 MB, nothing to take
│
├── Tools ─────────────────────────────────────────────────
│   ├── MCP python-sdk ............ ADAPT — external tool protocol, behind Alan's
│   │                                   permission layer
│   ├── browser-use ............... IN PLACE (BrowserProvider impl)
│   ├── open-browser-use .......... REJECT on this host — macOS/Linux only
│   ├── Termux API ................ ADAPT — Android capability adapter
│   └── Alan's own 15 tools ....... KEEP
│
├── Computer use ─────────────────────────────────────────
│   └── OmniParser ................ REFERENCE_ONLY — perception layer, needs GPU
│
├── Android ──────────────────────────────────────────────
│   ├── llama.cpp native lib ..... ADAPT — the inference engine on device
│   ├── scrcpy .................... REFERENCE_ONLY — dev/test infrastructure
│   ├── Termux API ................ ADAPT — device capabilities
│   ├── Vesta ..................... REFERENCE_ONLY — offline-first UX reference
│   ├── LocalMind ................. REFERENCE_ONLY — Kotlin/Compose + JNI patterns
│   └── offline-ai-assistant ...... REFERENCE_ONLY — JNI model-session patterns
│
├── Automation ───────────────────────────────────────────
│   ├── Alan scheduler ............ KEEP — Alan-owned
│   ├── CrewAI .................... REFERENCE_ONLY
│   └── Prefect ................... REFERENCE_ONLY — reliability patterns only
│
├── Routing ──────────────────────────────────────────────
│   └── LiteLLM ................... REFERENCE_ONLY — Alan's router is sufficient
│
└── Evaluation ───────────────────────────────────────────
    ├── BrowserGym ................ BENCHMARK_ONLY
    └── Windows Agent Arena ....... BENCHMARK_ONLY
```

`IN PLACE` means Alan already depends on it. `ADAPT` means take a specific,
identified implementation and re-express it behind an Alan interface.
`REFERENCE_ONLY` means read it, do not depend on it. `BENCHMARK_ONLY` means run
it against Alan, never ship it. `REJECT` means do not use.

Nothing above is decided yet — `DECISIONS.md` carries the reasoning and this map
is the short form.

---

## The three real questions

Mapping thirty projects onto a diagram is the easy part. Three questions decide
almost everything.

### 1. Does the project have a local path at all?

Alan's product promise is local-first. A project that is architecturally
cloud — not merely cloud-capable — can be studied but not depended on, however
good its algorithms are.

This is the single biggest separator in the acquired set:

| Project | Local path | Verdict follows |
|---|---|---|
| llama.cpp | native, first-class | ADAPT-able |
| Ollama | native | already in place |
| graphiti | **Kuzu embedded driver** | ADAPT-able |
| LanceDB | embedded | ADAPT-able |
| memlocal_core | local-first by design | ADAPT-able |
| browser-use | local Chrome | already in place |
| pydantic-ai | 36 of 38 providers are cloud | reference only |
| supermemory | cloud login is the *default* | reject as dependency |
| mem0 | cloud-optional, local-capable | adapt |

Graphiti's embedded Kuzu driver is the most interesting find in the set: it is
the only temporal knowledge graph in the list that can run with no server and no
API key.

### 2. Does it force a second architecture?

A dependency that needs its own agent loop, its own memory model, or its own
runtime does not get to be a dependency.

This is where the sizes stop matching Alan's:

- **letta-code: 2,023 TypeScript files, 7 Python files.** Integrating Letta means
  a Node runtime beside Alan's Python one, or a rewrite.
- **supermemory: 210 `.ts` + 127 `.tsx`, 53 `.py`**, in a bun/turbo monorepo
  whose packages include `openai-sdk-python`, `pipecat-sdk-python` and
  `livekit-sdk-python`.
- **scrcpy** is Java plus native C — infrastructure, not a library.

A second runtime in a local-first privacy product is a permanent tax: two
dependency trees to patch, two sets of network policy, two things to audit. The
brief's "unwired is a bug list, not a reason to rewrite Alan" cuts both ways —
do not wire in a project whose price is a second Alan.

### 3. Is it better on the axis that matters, measurably?

"Proven better" has to mean measured, on Alan's workload, on this hardware.
Two constraints make most published comparisons inapplicable here:

- **This host is memory-starved.** 4 GB RAM, nonpaged pool ~729 MB, available
  memory ~0 MB at measurement time. A 1.5 B model ran at 0.2 prompt tok/s and
  0.46 gen tok/s — a paging signature, not a model-quality result. Any
  benchmark run in that state measures the pager.
- **Alan's bottleneck is not model quality.** It is prompt assembly and recall
  discipline. The measured wins were unglamorous: 34.2 s → 5.5 s by caching
  probes (7.0 → 3.0 s), searching memory once instead of twice (9.1 → 0.7 s),
  and right-sizing `num_ctx` (17.7 → 1.7 s). Nothing in these thirty
  repositories addresses that, because nothing in them is about the problem Alan
  actually had.

So the honest integration list is short, and the reference list is long. That is
the expected outcome, not a disappointing one.

---

## What is deliberately not in the map

- **No second orchestration engine.** LangGraph, CrewAI and Prefect all want to
  own the loop. Alan's loop is the product.
- **No cloud memory service.** Supermemory's default is a browser login to
  `supermemory.ai`. Not for a product whose premise is that data does not leave.
- **No benchmark in the runtime path.** BrowserGym and WindowsAgentArena measure
  Alan; they are never imported by it.
- **No automatic upgrade path into `src/`.** Nothing moves from this workspace
  into Alan without a decision record naming the file, the interface it
  implements, and the measurement that justified it.