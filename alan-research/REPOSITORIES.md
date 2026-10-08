# Repositories

Thirty repositories, thirty of thirty cloned, zero failures. **2.15 GB**,
shallow, pinned.

Every entry below records the clone URL, the exact commit, the licence as read
from the file (not as advertised in a README), and a verdict. Verdicts are
judgements and live in this file because they need to be reviewable by a human;
the mechanical facts they rest on were harvested by
`scripts/inspect_repos.py` into `repo-facts.json`.

**Acquired 2026-10-05.** `--depth 1 --single-branch --no-tags --no-recurse-submodules`.
No repository was modified, built, installed or executed.

---

## Summary

| Category | Repository | Commit | Licence | Size | Verdict |
|---|---|---|---|---|---|
| inference | llama.cpp | `d89651a7b205` | MIT | 169.0 MB | `ADAPT` |
| inference | ollama | `42e911bc3d05` | MIT | 46.1 MB | `IN PLACE` |
| agents | pydantic-ai | `721c78d6014f` | MIT | 282.0 MB | `REFERENCE_ONLY` |
| agents | langgraph | `9a0394d88b22` | MIT | 13.6 MB | `REFERENCE_ONLY` |
| agents | agent-framework | `e9857bd75a21` | MIT | 56.7 MB | `REFERENCE_ONLY` |
| agents | autogen | `027ecf0a379b` | MIT (code) / CC-BY-4.0 (content) | 55.4 MB | `REJECT` |
| memory | letta-code | `f898fda60932` | Apache-2.0 | 51.1 MB | `REFERENCE_ONLY` |
| memory | letta | `5bcdd177d70f` | Apache-2.0 | 0.1 MB | `REFERENCE_ONLY` (no source) |
| memory | mem0 | `abb81c88e1f7` | Apache-2.0 | 34.6 MB | `ADAPT` |
| memory | graphiti | `b7fc30f2a1e2` | Apache-2.0 | 21.7 MB | `ADAPT` |
| memory | supermemory | `ac2180498223` | MIT | 75.0 MB | `REJECT` |
| memory | memlocal_core | `d46d09c54b30` | Apache-2.0 | 1.3 MB | `ADAPT` |
| knowledge | lancedb | `7f5933594f42` | Apache-2.0 | 21.5 MB | `ADAPT` |
| knowledge | local-rag | `3777b94616f4` | MIT | 2.2 MB | `REJECT` |
| browser | browser-use | `7be96ed8bafa` | MIT | 9.3 MB | `IN PLACE` |
| browser | open-browser-use | `7765002ac880` | MIT | 7.4 MB | `REJECT` (host) |
| computer-use | OmniParser | `354021201345` | CC-BY-4.0 | 20.6 MB | `REFERENCE_ONLY` |
| android | scrcpy | `19c1261d2e2c` | Apache-2.0 | 1.8 MB | `REFERENCE_ONLY` |
| android | termux-api | `fc26ce17e3ba` | **none found** | 1.1 MB | `REJECT` (licence) |
| android | vesta | `0c98470eb5c1` | MIT | 3.0 MB | `REFERENCE_ONLY` |
| android | LocalMind | `e85b5304d4a1` | MIT | 138.4 MB | `REFERENCE_ONLY` |
| android | offline-ai-assistant | `55f29a33851b` | MIT | 155.0 MB | `REFERENCE_ONLY` |
| protocols | mcp-python-sdk | `2118f14f8a19` | MIT | 15.0 MB | `ADAPT` |
| routing | litellm | `be4481779ee8` | MIT | 209.1 MB | `REFERENCE_ONLY` |
| workflows | crewAI | `738c8e19e35c` | MIT | 399.2 MB | `REJECT` |
| workflows | prefect | `2338265f6588` | Apache-2.0 | 85.8 MB | `REFERENCE_ONLY` |
| workflows | AutoGPT | `f8b0e0a87c38` | Polyform-Shield / MIT | 225.1 MB | `REJECT` |
| utilities | uv | `46b84fd0bfec` | MIT | 39.1 MB | `ADAPT` |
| evaluation | BrowserGym | `c05d7f38f4f7` | Apache-2.0 | 2.7 MB | `BENCHMARK_ONLY` |
| evaluation | WindowsAgentArena | `6d39ed88c545` | MIT | 5.9 MB | `BENCHMARK_ONLY` |

Three licences need human review before any code moves: **termux-api** (no
licence file), **OmniParser** (CC-BY-4.0 applied to code), **AutoGPT**
(Polyform Shield on its platform folder).

---

# INFERENCE

## llama.cpp

**Clone URL** `https://github.com/ggml-org/llama.cpp.git`
**Category** inference · **Language** C/C++ (784 `.c`/`.cpp`) · **Licence** MIT
**Commit** `d89651a7b205` (2026-10-05) · **Size** 169.0 MB, 3,685 files

**Purpose.** The reference local LLM inference engine: GGUF loading,
quantisation, CPU and GPU backends, multimodal input, and an HTTP server.

**Why Alan Cares.** This is the floor under Alan's inference promise. Every
answer Alan gives locally ultimately runs through something like it — currently
via Ollama. It is the only item in this workspace that could remove the Go
daemon from Alan's critical path.

**Important components**
- `tools/server` — the OpenAI-compatible HTTP server (`examples/server` no longer
  exists; it moved here)
- `tools/mtmd` — multimodal input handling
- `ggml/src/ggml-cuda` — GPU backend; also Vulkan, Metal, and others
- No root `pyproject.toml` or `Cargo.toml`: it is a C++ project built with CMake,
  with `requirements.txt` present only for the Python bindings

**Potential Alan Integration.** Behind `ModelProvider`. Direct GGUF loading would
remove one process from the request path.

**Integration Type** `ADAPT`

**Dependencies** None at the language level. Self-contained C/C++.

**Privacy Concerns** None. Fully local. No telemetry, no account, no network
egress beyond the ports you bind.

**Offline Compatibility** YES
**Mobile Compatibility** PARTIAL — and weaker than expected. This checkout
contains **zero `.java` files and no `android/` directory**; only 11 `.kt` files.
It ships the native library, not an Android app. Any mobile build means
integrating NDK/CMake by hand.

**Security Considerations** A server mode means an inference endpoint. Binding it
to loopback and keeping it behind Alan's permission tiers is mandatory — it is a
model that will execute tool calls if told to.

**License Compatibility** MIT. No obligation on Alan.

**Recommendation** Keep studying. Do not integrate yet — Alan's bottleneck was
never inference speed, it was recall discipline and prompt assembly. Revisit if
the Ollama hop ever proves to be the cost.

---

## ollama

**Clone URL** `https://github.com/ollama/ollama.git`
**Category** inference · **Language** Go · **Licence** MIT
**Commit** `42e911bc3d05` (2026-10-02) · **Size** 46.1 MB, 1,335 files

**Purpose.** Local model lifecycle: download, quantise, load, serve, discover.

**Why Alan Cares.** It is Alan's current `ModelProvider` implementation, running
on the bundled port 11435 alongside a host instance on 11434.

**Important components**
- Go service: model store, scheduler, `llama-server` as a subprocess
- Client library consumed by `OllamaProvider`
- Modelfile-based model construction

**Potential Alan Integration** Already integrated, behind `ModelProvider`.

**Integration Type** `IN PLACE` — with the standing rule that it stays behind
the interface so it can be replaced.

**Dependencies** 20, mostly the web UI (`@headlessui/react`, `framer-motion`,
`katex`). The Go server itself is light.

**Privacy Concerns** Local by default. Pulls models from a registry on first use —
that is a network operation, and it should stay an explicit user action.

**Offline Compatibility** YES after first model pull.
**Mobile Compatibility** NO — server application.

**Security Considerations** The host Ollama on 11434 must not be disturbed; it is
user infrastructure, not Alan's.

**License Compatibility** MIT.

**Recommendation** Keep as an interchangeable backend. The interface, not this
implementation, is the asset.

---

# AGENTS

## pydantic-ai

**Clone URL** `https://github.com/pydantic/pydantic-ai.git`
**Category** agents · **Language** Python · **Licence** MIT
**Commit** `721c78d6014f` (2026-10-04) · **Size** 282.0 MB, 4,102 files

**Purpose.** A typed Python agent framework: agent abstraction, dependency
injection, structured output, validated tools.

**Why Alan Cares.** The brief calls it CRITICAL because Alan's preferred restart
foundation is Python. It is also the best-engineered example of the thing Alan
already does by hand.

**Important components**
- `pydantic_ai_slim/pydantic_ai/providers/` — **38 provider adapters**
- `pydantic_graph` — its own graph module, i.e. a mini orchestration engine
- `pydantic_evals` — evaluation harness
- Manifest declares **zero required dependencies**; everything is extras-gated

**Potential Alan Integration** As a quality bar for `ToolSpec` schema generation
and typed tool results.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** Zero required; every provider is an extra. That is good
packaging discipline.

**Privacy Concerns** **36 of 38 provider adapters are cloud services** —
Anthropic, Azure, Bedrock, Google, Groq, Fireworks, Together, OpenRouter, xAI,
Cohere, Mistral, Snowflake, Vercel, Heroku, DeepSeek, Alibaba, Cerebras, Crusoe,
Moonshot, Nebius, OVH, SambaNova, Vercel. The only local adapters are
`ollama.py` (5 KB) and `vllm.py` (8.4 KB). For Alan, that ratio makes it a
reference rather than a foundation.

**Offline Compatibility** PARTIAL — via `ollama.py` only.
**Mobile Compatibility** NO.

**Security Considerations** None found in the code read. The risk is
architectural: a framework whose value is its provider catalogue encourages
sending data outward.

**License Compatibility** MIT.

**Recommendation** Read for tool ergonomics. Do not adopt. Note that
`pydantic_graph` means "adopt the framework" arrives with a second orchestration
engine attached.

---

## langgraph

**Clone URL** `https://github.com/langchain-ai/langgraph.git`
**Category** agents · **Language** Python · **Licence** MIT
**Commit** `9a0394d88b22` (2026-10-03) · **Size** 13.6 MB, 681 files

**Purpose.** Stateful agent orchestration as an explicit graph, with durable
checkpointing.

**Why Alan Cares.** Alan runs multi-step work that must survive interruption.
LangGraph's checkpoint layer is the most concrete reusable idea in this category.

**Important components**
- `libs/checkpoint` — the durable-state abstraction
- `libs/checkpoint-sqlite` — **the local-first backend**
- `libs/checkpoint-postgres` — the server backend Alan does not want
- `libs/langgraph`, `libs/prebuilt`, `sdk-py`, `sdk-js`

**Potential Alan Integration** Alan's scheduler could checkpoint long-running
tasks so they resume rather than restart.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** **4**: `langgraph-checkpoint`, `orjson`, `psycopg`,
`psycopg-pool`. Genuinely light.

**Privacy Concerns** None inherent. `checkpoint-postgres` would mean a server, but
`checkpoint-sqlite` does not.

**Offline Compatibility** YES
**Mobile Compatibility** PARTIAL — `checkpoint-sqlite` is portable; the graph
engine is heavy for a phone.

**Security Considerations** Checkpoint state is agent state. Persisting it means
persisting whatever the agent was told, so it belongs inside Alan's memory privacy
model, not beside it.

**License Compatibility** MIT.

**Recommendation** Read the SQLite checkpointer. Do not adopt the engine — it wants
to own the loop, and the loop is Alan.

---

## agent-framework

**Clone URL** `https://github.com/microsoft/agent-framework.git`
**Category** agents · **Language C#** · **Licence** MIT
**Commit** `e9857bd75a21` (2026-10-02) · **Size** 56.7 MB, 5,479 files

**Purpose.** Microsoft's agent orchestration framework — workflows, multi-agent
patterns, human-in-the-loop, MCP/A2A interoperability.

**Why Alan Cares.** The brief prioritises it MEDIUM/HIGH for architectural
research. Two things make it worth a look: it is the most current statement of
enterprise HITL design, and it treats MCP/A2A as first-class.

**Important components**
- `agent-framework-core` (the only declared dependency — the rest is this package)
- Workflow and graph constructs, middleware pipeline
- A2A and MCP integration surfaces

**Potential Alan Integration** None directly. Patterns only.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** `agent-framework-core` only, at the manifest level.

**Privacy Concerns** Runs on .NET. No mandatory cloud in the core.

**Offline Compatibility** YES in principle — .NET has a self-contained publish mode.
**Mobile Compatibility** NO.

**Security Considerations** A middleware pipeline is the right shape for Alan's
permission tiers; worth reading for that alone.

**License Compatibility** MIT.

**Recommendation** Architectural research only, as the brief intends. A .NET
runtime beside Alan's Python one is not a trade Alan should make.

---

## autogen

**Clone URL** `https://github.com/microsoft/autogen.git`
**Category** agents · **Language** Python · **Licence** **MIT (code) / CC-BY-4.0
(content)** — `LICENSE-CODE` is MIT, `LICENSE` is CC-BY-4.0
**Commit** `027ecf0a379b` (2026-04-06) · **Size** 55.4 MB, 1,837 files

**Purpose.** Historical multi-agent orchestration. **Upstream is in maintenance
mode** — the brief says so, and the commit date (2026-04-06, six months behind
every other agents repo here) is consistent with it.

**Why Alan Cares** Low. The brief rates it LOW/MEDIUM and says study it only for
historical patterns.

**Important components**
- Agent/group-chat abstractions
- AutoGen Studio — a React/xyflow web UI (note: the dependency manifest the
  harvester picked up belongs to the **studio**, listing `@headlessui/react`,
  `antd`, `@xyflow/react`; the core's dependencies are different)

**Potential Alan Integration** None.

**Integration Type** `REJECT`

**Dependencies** The detected manifest is the Studio UI's. Do not read that list
as AutoGen's core dependency surface.

**Privacy Concerns** Out of scope for a maintenance-mode project.

**Offline Compatibility** PARTIAL
**Mobile Compatibility** NO

**Security Considerations** A multi-agent framework that lets agents call agents
needs a permission model more than most. Alan's five tiers would have to wrap
everything, which defeats the point.

**License Compatibility** Code is MIT and usable. Documentation and assets are
CC-BY-4.0, so attribution is required for anything taken from the docs.

**Recommendation** Reject as a foundation, as instructed. Read the agent-loop
history if curious. Nothing here justifies adopting a project its own maintainers
have stepped back from.

---

# MEMORY

## letta

**Clone URL** `https://github.com/letta-ai/letta.git`
**Category** memory · **Language** none · **Licence** Apache-2.0
**Commit** `5bcdd177d70f` (2026-09-10) · **Size** 0.1 MB, **12 files**

**Purpose.** Was Letta (f.k.a. MemGPT). **It no longer contains source code.**

**Why Alan Cares** The brief asks to "inspect the historical repository". The
honest finding is that there is nothing left to inspect.

**Important components** `README.md`, `LICENSE`, `CITATION.cff`, and five
governance documents: `AGENTS.md`, `AI_POLICY.md`, `PRIVACY.md` (21 KB),
`SECURITY.md`, `TERMS.md`, `CONTRIBUTING.md`. That is the whole repository.

Its README states it directly: *"The current source code lives in
`letta-ai/letta-code`, which includes the agent harness, interactive terminal UI,
App Server, channels, and the runtime used by the desktop and web apps."*

**Potential Alan Integration** None from code. Its `PRIVACY.md` and `AI_POLICY.md`
are worth reading as templates for Alan's own policy documents.

**Integration Type** `REFERENCE_ONLY` — policy documents only.

**Dependencies** None declared. Nothing detected at any depth.

**Privacy Concerns** The project is explicit about self-hosting and has a real
privacy document. Its runtime, however, defaults to `chat.letta.com`.

**Offline Compatibility** N/A — no code.
**Mobile Compatibility** N/A

**Security Considerations** `SECURITY.md` and `AI_POLICY.md` are unusually thorough
for a project of this size.

**License Compatibility** Apache-2.0.

**Recommendation** Record it as a documentation stub. Inspect `letta-code` instead.

---

## letta-code

**Clone URL** `https://github.com/letta-ai/letta-code.git`
**Category** memory · **Language** **TypeScript** · **Licence** Apache-2.0
**Commit** `f898fda60932` (2026-10-04) · **Size** 51.1 MB, 2,445 files

**Purpose.** Stateful agents with persistent, self-editing memory. Memory
*blocks* the agent rewrites; sleep-time "dreaming" consolidation; background
memory maintenance.

**Why Alan Cares** The strongest idea set in this category for a digital twin:
memory as something the agent maintains rather than something it reads.

**Important components**
- `src/agent/` — agent machinery, `src/agent/subagents/`
- `src/skills/builtin/` — memory management expressed as skills:
  `managing-shared-memory`, `migrating-memory`, `syncing-memory-filesystem`,
  `initializing-memory`, `curating-memory-palace`, `finding-agents`
- `python/` — 2 Python files, essentially a stub

**Potential Alan Integration** Design vocabulary only: self-editing memory blocks,
and memory management as agent-visible skills.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 21, including `@modelcontextprotocol/sdk`, `@letta-ai/letta-client`,
`@earendil-works/pi-ai`, `@scarf/scarf` (telemetry), `cron-parser`.

**Privacy Concerns** `chat.letta.com` is offered as the hosted path. Self-hosting
is supported via `letta server`.

**Offline Compatibility** YES when self-hosted.
**Mobile Compatibility** NO.

**Security Considerations** `@scarf/scarf` is a download-analytics service. Not
enabled by Alan, but worth naming.

**License Compatibility** Apache-2.0. Permissive.

**Recommendation** **The language mix decides this: 2,023 `.ts` + 131 `.tsx`
against 7 `.py` files.** Adopting Letta means a Node runtime beside Alan's Python
one, or a rewrite of a very large codebase. Take the concepts; take no code.

---

## mem0

**Clone URL** `https://github.com/mem0ai/mem0.git`
**Category** memory · **Language** Python + TypeScript · **Licence** Apache-2.0
**Commit** `abb81c88e1f7` (2026-10-01) · **Size** 34.6 MB, 1,835 files

**Purpose.** Persistent memory: extract facts from conversation, reconcile them
against existing memory, retrieve semantically.

**Why Alan Cares** The single most directly relevant project here. Alan's memory
stores items; it does not decide what a new conversation *supersedes*. Mem0's
extract → compare → ADD/UPDATE/DELETE/NOOP loop is exactly that missing decision.

**Important components**
- The memory core and its ADD/UPDATE/DELETE decision logic
- `mem0/` (Python) and `mem0-ts` (TypeScript) — genuinely dual-language
- `server/`, `cli/`, `skills/`

**Potential Alan Integration** The extraction and conflict-resolution decision,
inside `MemoryProvider`, **after** Alan's relevance gate. The gate stays Alan-side:
mem0 supplies candidates, Alan decides what may be asserted.

**Integration Type** `ADAPT`

**Dependencies** 40+, spanning a very wide backend surface: `chromadb`,
`faiss-cpu`, `elasticsearch`, `azure-search-documents`, `databricks-sdk`,
`cassandra-driver`, `boto3`, `pymongo`. That breadth is a cost — most of it
inert for Alan, all of it in the dependency graph.

**Privacy Concerns** Cloud backends are optional. The local configuration path
exists.

**Offline Compatibility** YES with a local backend.
**Mobile Compatibility** PARTIAL — the backends are not phone-sized, but the
decision logic is portable.

**Security Considerations** Repository quality note: its 98,929-byte
`memory_core.py` is **duplicated verbatim seven times** across
`integrations/{agent-plugin,codex-plugin,antigravity-plugin,cursor-plugin,claude-code-plugin,mem0-agent-plugin,kimi-plugin}/core/`.
Seven copies of a 99 KB file is a maintenance liability in the project itself, and
a sign that the repo's centre of gravity has shifted toward coding-agent plugins
rather than the memory library.

**License Compatibility** Apache-2.0. Permissive.

**Recommendation** **Adapt the decision, not the architecture.** Read the conflict
resolution carefully; reimplement it against Alan's types; keep Alan's gate in
front of it.

---

## graphiti

**Clone URL** `https://github.com/getzep/graphiti.git`
**Category** memory · **Language** Python · **Licence** Apache-2.0
**Commit** `b7fc30f2a1e2` (2026-10-04) · **Size** 21.7 MB, 384 files

**Purpose.** A temporal knowledge graph: entities, relationships, and facts
attached to the interval during which they were true.

**Why Alan Cares** This is the best single idea in the entire acquisition. Alan's
`MemoryItem` has a boolean `expires`, which cannot say *"this was true from March
until May"*. Bi-temporal edges can, and that is the difference between a memory
system that asserts and one that is honest.

**Important components**
- `graphiti_core/graphiti.py` — the main module (76 KB)
- Edge semantics with `valid_at` / `invalid_at`
- **`graphiti_core/driver/kuzu`** — an embedded graph database
- `driver/neo4j`, `driver/neptune`, `driver/falkordb` — server and cloud drivers

**Potential Alan Integration** Temporal validity semantics, re-expressed in
Alan's `MemoryItem`. Not the graph engine, not on day one.

**Integration Type** `ADAPT` — highest priority in this document.

**Dependencies** 34, including **`kuzu`**, `falkordb`, `falkordblite`, `gliner2`,
`anthropic`, `boto3`, `google-genai`, `groq`.

**Privacy Concerns** Cloud drivers and LLM providers are optional but prominent;
examples reference `azure_openai_neo4j` and OpenTelemetry exporters. Entity
extraction requires an LLM, which on this host means a local one.

**Offline Compatibility** **YES with the Kuzu driver** — no server, no API key.
That is what makes this project viable for Alan at all.

**Mobile Compatibility** PARTIAL — Kuzu is embeddable, but the Python package and
its extraction model are heavy for a phone.

**Security Considerations** Knowledge-graph poisoning: if extracted entities come
from untrusted input, the graph accumulates attacker-chosen edges. Alan's gate and
risk tiers must apply before anything is written.

**License Compatibility** Apache-2.0. Permissive.

**Recommendation** **Adopt the temporal model, not the engine.** Prove it against
Alan's own recall set before it lands. The Kuzu driver is the enabler to remember
if a graph is ever wanted.

---

## supermemory

**Clone URL** `https://github.com/supermemoryai/supermemory.git`
**Category** memory · **Language** TypeScript · **Licence** MIT
**Commit** `ac2180498223` (2026-10-05) · **Size** 75.0 MB, 914 files

**Purpose.** Memory + context engine + RAG, with multimodal and code/document
ingestion.

**Why Alan Cares** The brief singles it out for study because it has an OpenCode
integration and claims self-hosting, and Alan has OpenCode history. Studied. The
answer is no.

**Important components**
- `packages/memory-graph`, `packages/hooks`, `packages/tools`, `packages/validation`
- `apps/docs/integrations/opencode.mdx` — the OpenCode integration documentation
- The plugin itself is **not in this repository**; it lives in
  `supermemoryai/opencode-supermemory`, which was not part of this acquisition

**Potential Alan Integration** None as a dependency. Its multimodal ingestion
approach is worth reading.

**Integration Type** `REJECT`

**Dependencies** 41 Node packages including `@ai-sdk/anthropic`,
`@ai-sdk/openai`, `@ai-sdk/google`, `@ai-sdk/cerebras`, `@anthropic-ai/sdk`,
`@google/genai`. The `packages/` tree also carries `openai-sdk-python`,
`pipecat-sdk-python` (voice), `livekit-sdk-python`, `cartesia-sdk-python`.

**Privacy Concerns** **This is the disqualifying finding.** The documented default
is `bunx opencode-supermemory@latest install` followed by
`bunx opencode-supermemory@latest login` — a **browser login to
`supermemory.ai`**, with `SUPERMEMORY_API_KEY` written into shell profiles.
Self-hosting (`npx supermemory local`) exists but is presented as an opt-in tip.
For a product whose premise is that data does not leave the machine, a memory
service whose default path is a browser login is the wrong shape.

**Offline Compatibility** PARTIAL — only by running its own TypeScript server.
**Mobile Compatibility** NO.

**Security Considerations** Cloud-first by construction. Two Node runtimes
(AI SDK, Anthropic SDK) plus voice stacks is a large supply-chain surface.

**License Compatibility** MIT for the code, but the architecture is wrong for Alan
regardless of licence.

**Recommendation** **Reject.** The strongest possible disqualifier for a
local-first product is a dependency that authenticates to a vendor by default.

---

## memlocal_core

**Clone URL** `https://github.com/memlocal/memlocal_core.git`
**Category** memory · **Language** **Rust** · **Licence** Apache-2.0
**Commit** `d46d09c54b30` (2026-06-02) · **Size** 1.3 MB, 272 files

**Purpose.** A local-first memory engine: local database, vector retrieval,
full-text search, graph memory, reranking, local inference, mobile SDKs, MCP
integration.

**Why Alan Cares** It is the only project in the set whose *design goal* matches
Alan's, including the mobile-and-offline part of the vision. And at 1.3 MB it can
be read end to end, which is itself the recommendation.

**Important components**
- Embedded graph storage via **`cozo`** (a Rust graph database)
- `flutter_rust_bridge` — mobile SDK exposure
- Full-text + vector retrieval, local reranking, MCP integration

**Potential Alan Integration** Retrieval design and the mobile story. Its commit
is from 2026-06-02, four months before acquisition, so treat it as a snapshot.

**Integration Type** `ADAPT`

**Dependencies** 12, all Rust and all light: `chrono`, `cozo`, `flutter_rust_bridge`,
`hex`, `log`, `rayon`, `reqwest`, `serde`, `serde_json`, `sha2`.

**Privacy Concerns** None found. Local-first by construction.

**Offline Compatibility** YES
**Mobile Compatibility** **YES** — the only project here that can claim it, via
`flutter_rust_bridge`. (Flutter, not Android-native, but it is a mobile target.)

**Security Considerations** Rust means memory safety in the parser paths, which is
a real advantage for anything that ingests untrusted documents.

**License Compatibility** Apache-2.0.

**Recommendation** **Read fully.** Small, aligned, mobile-capable, and cheap in
dependencies. The best-value project per megabyte in the set.

---

# KNOWLEDGE

## lancedb

**Clone URL** `https://github.com/lancedb/lancedb.git`
**Category** knowledge · **Language** Rust · **Licence** Apache-2.0
**Commit** `7f5933594f42` (2026-10-03) · **Size** 21.5 MB, 791 files

**Purpose.** Embedded local vector and multimodal database.

**Why Alan Cares** Alan's memory needs vector retrieval that works offline on a
4 GB Windows host and later on a phone.

**Important components**
- Rust core, Arrow-based columnar storage
- Vector index, multimodal storage, filtering
- Bindings across Python, Node, Rust

**Potential Alan Integration** As a vector store behind `MemoryProvider`, if and
when Alan's current recall needs one. Note that cognee already provides recall
today — this would be a replacement, not an addition.

**Integration Type** `ADAPT`

**Dependencies** 40+, including `arrow`, `datafusion`, plus optional cloud
integrations (`awscli`, `boto3`, `adlfs`, `cohere`, `colpali-engine`,
`InstructorEmbedding`). Most are extras for cloud paths Alan will not use.

**Privacy Concerns** Cloud connectors are optional. The embedded engine is local.

**Offline Compatibility** YES
**Mobile Compatibility** PARTIAL — a Rust core is portable, but the bindings are
not yet a phone story.

**Security Considerations** Vector stores read untrusted files; LanceDB's Rust core
avoids a class of parser bugs.

**License Compatibility** Apache-2.0.

**Recommendation** **Correct the record first: Alan's prior ingestion pipeline did
not use LanceDB.** It used ChromaDB and Graphify (`DECISIONS.md`, CORRECTION 1).
Evaluate LanceDB on merit, not as a replacement for something Alan never had.

---

## local-rag

**Clone URL** `https://github.com/leestott/local-rag.git`
**Category** knowledge · **Language** JavaScript · **Licence** MIT
**Commit** `3777b94616f4` (2026-03-13) · **Size** 2.2 MB, **48 files**

**Purpose.** A small local RAG reference implementation.

**Why Alan Cares** Only to understand a minimal ingestion → chunk → embed →
retrieve pipeline. It does not.

**Important components** Essentially a tutorial project.

**Potential Alan Integration** None.

**Integration Type** `REJECT`

**Dependencies** 3: `better-sqlite3`, `express`, **`foundry-local-sdk`**. That last
one is Microsoft Foundry — a **cloud** AI platform. A project presented as a
*local* RAG example depends on a cloud SDK for its model calls.

**Privacy Concerns** Its "local" retrieval is local; its model calls are not.

**Offline Compatibility** NO in practice.
**Mobile Compatibility** NO

**Security Considerations** None notable.

**License Compatibility** MIT.

**Recommendation** **Reject.** 48 files teach nothing Alan does not already do,
and the brief's own guidance — do not integrate wholesale, priority LOW/MEDIUM —
is confirmed by the contents rather than assumed.

---

# BROWSER

## browser-use

**Clone URL** `https://github.com/browser-use/browser-use.git`
**Category** browser · **Language** Python · **Licence** MIT
**Commit** `7be96ed8bafa` (2026-10-02) · **Size** 9.3 MB, 536 files

**Purpose.** AI-controlled browser automation.

**Why Alan Cares** It is Alan's `BrowserProvider` implementation, already running
in `env\browser` behind the `:8022` sidecar.

**Important components**
- Agent loop over a DOM-extraction abstraction
- `browser-use-core` split, `browser-harness`
- `BrowserProfile` and its profile-copy behaviour

**Potential Alan Integration** Already integrated.

**Integration Type** `IN PLACE`

**Dependencies** 40, including `anthropic`, `boto3`, `agentmail`, `InquirerPy`.

**Privacy Concerns** Operates a real local Chrome instance with the user's
profile. That is inherently sensitive, which is why Alan's sidecar uses a dedicated
profile directory rather than the user's own.

**Offline Compatibility** YES for local browsing; the LLM can be local.
**Mobile Compatibility** NO.

**Security Considerations** The known trap, and the reason Alan's sidecar code is
written the way it is: `BrowserProfile._copy_profile` **always** creates a new
random temp directory; only the literal marker `browser-use-user-data-dir-` in the
path causes the copy to be skipped. Alan's profile is therefore deliberately named
`data\browser-profile\browser-use-user-data-dir-sidecar-8022`. **Do not rename
it** — the odd name is load-bearing for orphan reaping. Verified: a full cycle
takes Chrome 0 → 9 → 0 processes and leaves no directories behind.

**License Compatibility** MIT.

**Recommendation** Keep, pinned. The cloned HEAD is `0.13.10`, which **exactly
matches** Alan's installed `browser_use-0.13.10` — no version drift, so no upgrade
work is pending. The profile-naming constraint must survive any upgrade.

---

## open-browser-use

**Clone URL** `https://github.com/open-browser-use/open-browser-use.git`
**Category** browser · **Language** TypeScript + Rust · **Licence** MIT
**Commit** `7765002ac880` (2026-06-02) · **Size** 7.4 MB, 490 files

**Purpose.** Control the user's *existing* browser and sessions via a Chrome
extension, rather than launching an automated one.

**Why Alan Cares** Privacy-wise this is the more interesting model — it reuses the
browser the user already trusts instead of a fresh automated profile.

**Important components**
- Chrome extension (TypeScript)
- A Rust native-messaging host for the agent connection

**Potential Alan Integration** Would be Alan's alternative `BrowserProvider`
*implementation*, behind the same interface — which is exactly the point of having
the interface.

**Integration Type** `REJECT` **on this host.**

**Dependencies** 2 Node (dev-only), 38 Rust.

**Privacy Concerns** The design is privacy-preserving: existing sessions, no
profile duplication, no credentials exported.

**Offline Compatibility** YES
**Mobile Compatibility** NO

**Security Considerations** Native messaging gives an extension host access to the
real session. Powerful, and a much larger blast radius than Alan's isolated
profile. Any future adoption needs the permission tiers wrapped around it.

**License Compatibility** MIT.

**Recommendation** **Rejected for now on a factual, not architectural, basis:**
its own README says *"open-browser-use is a **macOS / Linux public preview** — the
Chrome Web Store listing isn't live yet."* It cannot run on this Windows host.
Re-evaluate when Windows ships.

---

# COMPUTER USE

## OmniParser

**Clone URL** `https://github.com/microsoft/OmniParser.git`
**Category** computer-use · **Language** Python · **Licence` **CC-BY-4.0** ⚠
**Commit** `354021201345` (2026-07-19) · **Size** 20.6 MB, 78 files

**Purpose.** Convert screenshots into structured UI element descriptions with
bounding boxes, so a computer-use agent can act on coordinates.

**Why Alan Cares** It is the missing perception layer. Alan has tools; it has no
way to see the screen.

**Important components**
- `omnitool/`, `util/`, `eval/`
- Detection + captioning models

**Potential Alan Integration** As a perception capability behind a new Alan-owned
interface, if computer use is ever built.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 33, heavy and cloud-adjacent: `accelerate`, `anthropic`,
`azure-identity`, `boto3`, `dashscope`, `easyocr`, `gradio`, `einops`. Inference
expects a GPU.

**Privacy Concerns** Screenshots are the most sensitive input Alan could ever
process. Cloud model options in the default dependency set are disqualifying for a
local-first design unless explicitly avoided.

**Offline Compatibility** PARTIAL — models can run locally, but a GPU is expected.
**Mobile Compatibility** NO.

**Security Considerations** A screenshot channel into an agent that can click is a
prompt-injection surface: text rendered in an image can instruct the agent. Alan's
risk tiers would need to cover it explicitly.

**License Compatibility** ⚠ **Requires review.** The repository's only licence file
is `LICENSE` = Creative Commons **Attribution 4.0 International**, and there is no
separate code licence anywhere in the tree. CC-BY-4.0 is a *content* licence
applied to a code repository — unusual, and not an OSI-approved software licence.
Reusing this code needs a legal decision, not a preference.

**Recommendation** Do not reuse code. Read the architecture if computer use is ever
prioritised, and raise the licence question before that conversation goes further.

---

# ANDROID

## scrcpy

**Clone URL** `https://github.com/Genymobile/scrcpy.git`
**Category** android · **Language** Java + native C · **Licence** Apache-2.0
**Commit** `19c1261d2e2c` (2026-09-05) · **Size** 1.8 MB, 371 files

**Purpose.** Display and control Android devices over USB or network, via ADB.

**Why Alan Cares** Development and device-testing infrastructure for the Android
future. Not a runtime component.

**Important components** Client (`app/`), server (`server/`), ADB protocol
implementation, HW encoder support.

**Potential Alan Integration** Development and CI infrastructure only.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** Gradle/Android SDK. No runtime dependencies beyond the Android
platform.

**Privacy Concerns** None — it is a local device bridge.

**Offline Compatibility** YES over USB.
**Mobile Compatibility** N/A — it controls mobile, it does not run on it.

**Security Considerations** ADB access is full device control. Keep it in
development scope; it must never become an Alan capability exposed to a model
without permission tiers.

**License Compatibility** Apache-2.0.

**Recommendation** Use as test infrastructure. Not a product dependency.

---

## termux-api

**Clone URL** `https://github.com/termux/termux-api.git`
**Category** android · **Language** Java · **Licence** ⚠ **NONE FOUND**
**Commit** `fc26ce17e3ba` (2026-09-17) · **Size** 1.1 MB, 1,048 files

**Purpose.** Expose Android device functions — sensors, notifications, clipboard,
camera, battery, location — to command-line programs as a Termux package.

**Why Alan Cares** It is the most direct route to real device capabilities for an
Android Alan.

**Important components** `app/src/main/java/.../Termux*` API entry points, a Gradle
build, and the `termux-api` package wrapper.

**Potential Alan Integration** As an Android capability adapter — each API mapped
to an Alan-owned tool with its own risk tier.

**Integration Type** `REJECT` **as it stands, on licence grounds.**

**Dependencies** Gradle/Android SDK.

**Privacy Concerns** Device sensors, camera and location are the highest-sensitivity
capabilities in the entire brief. Whatever adopts these must be `HIGH_RISK` or
`PRIVILEGED` by default, never `LOW_RISK`.

**Offline Compatibility** YES on-device.
**Mobile Compatibility** YES — Android-native.

**Security Considerations** The broadest capability surface in this workspace. It
should be adopted last, if ever, and only behind explicit per-capability consent.

**License Compatibility** ⚠ **Requires review — and this one is disqualifying as
it stands.** There is **no LICENSE file anywhere in the repository.** The README
says *"Released under the GPLv3 license"* and links to gnu.org, but a README
sentence is not a licence grant. Two problems: absent a licence file, default
copyright applies and there is no permission to use; and **GPL-3.0 is copyleft**,
which would impose obligations on anything linking it.

**Recommendation** **Reject.** Not because the technology is wrong — it is exactly
right for the goal — but because Alan cannot link unlicensed or copyleft code
into a project intended to stay private. If a permissive licence appears upstream,
revisit. Meanwhile implement the capability adapters against Alan's own interfaces.

---

## vesta

**Clone URL** `https://github.com/RisorseArtificiali/vesta.git`
**Category** android · **Language** **TypeScript (React Native/Expo)** ·
**Licence** MIT · **Commit** `0c98470eb5c1` (2026-07-08) · **Size** 3.0 MB, 180 files

**Purpose.** *"Intelligence that never leaves home."* An offline-first AI
assistant that runs entirely on the phone — chat, alarms, events, reminders, with
no internet, no accounts, and no data leaving the device.

**Why Alan Cares** The brief ranks it CRITICAL and says study it extremely
carefully, because it is a real-world reference for Alan's Android direction. Its
positioning is nearly identical to Alan's.

**Important components**
- `apps/mobile` — Expo app (88 `.ts`, 13 `.tsx`, 9 `.kt`, 11 `.xml`)
- Local SQLite via `expo-sqlite`
- Device features via `expo-calendar`, `expo-contacts`, `expo-notifications`,
  `expo-document-picker`, `expo-file-system`, `expo-linking`
- `expo-router` for navigation

**Potential Alan Integration** Interaction and packaging reference for an Android
Alan. Not code.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 22, all Expo/React Native.

**Privacy Concerns** The stance matches Alan's exactly: no accounts, no
egress. That is a design constraint to adopt, not a library.

**Offline Compatibility** YES — that is the project's premise.
**Mobile Compatibility** YES

**Security Considerations** Note the licence claim in the README badge versus the
actual `LICENSE` file: the file governs, and the harvester confirmed MIT.

**License Compatibility** MIT.

**Recommendation** **Study the UX and the offline promise.** Do not copy the
architecture, per the brief. Two caveats worth recording: it is **React
Native/Expo, not Android-native Kotlin**, so it is a reference for interaction
design rather than for a native Alan; and at 180 files with a 2026-07-08 commit it
is a snapshot, not a mature system.

---

## LocalMind

**Clone URL** `https://github.com/tk85457/LocalMind.git`
**Category** android · **Language** Kotlin/Java + C++ · **Licence** MIT
**Commit** `e85b5304d4a1` (2026-08-14) · **Size** 138.4 MB, 2,526 files

**Purpose.** A local-LLM Android assistant. Kotlin, Compose, llama.cpp via JNI,
GGUF models.

**Why Alan Cares** The brief rates it HIGH as the native-Kotlin reference for
running a local model on Android — the thing `vesta` is not.

**Important components**
- Android app (Kotlin, Compose)
- Native inference binding to llama.cpp
- GGUF model loading and lifecycle

**Potential Alan Integration** Patterns for on-device inference: model session
management, GGUF loading, and the JNI boundary.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 12 detected at the manifest the harvester selected — mostly
JavaScript documentation tooling (`highlight.js`, `remark`, `pdfjs-dist`); the
real Android dependency set is in the Gradle build.

**Privacy Concerns** Fully local. No cloud SDK surfaced.

**Offline Compatibility** YES
**Mobile Compatibility** YES — native Android.

**Security Considerations** JNI is the sharp edge: a native boundary means
`GGUF` files are parsed in C/C++. Treat downloaded models as untrusted input.

**License Compatibility** MIT.

**Recommendation** **The most relevant Android reference for the engine half of
the problem** — native, on-device, GGUF. Read for the JNI and session-management
patterns. Do not adopt.

---

## offline-ai-assistant

**Clone URL** `https://github.com/its-hazratbilal/offline-ai-assistant.git`
**Category** android · **Language** Kotlin + C++ · **Licence** MIT
**Commit** `55f29a33851b` (2026-08-17) · **Size** 155.0 MB, 2,958 files

**Purpose.** Another offline Android/local-LLM implementation.

**Why Alan Cares** A second implementation reference for the same problem, which
is how you tell a pattern from a one-off.

**Important components**
- Android app with a Compose UI
- **`app/src/main/cpp/llama.cpp/` — it vendors the entire llama.cpp source tree
  inside the app**
- JNI glue, `build.gradle.kts`

**Potential Alan Integration** Confirmation of the standard approach: bundle
llama.cpp's native library, bind it, manage the model session.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** No manifest at the app root; manifests are inside the vendored
llama.cpp (`requirements.txt`, `tools/ui/package.json`) plus `build.gradle.kts`.

**Privacy Concerns** None found. Local inference.

**Offline Compatibility** YES
**Mobile Compatibility** YES

**Security Considerations** The 155 MB checkout is mostly vendored third-party C++
inside the app directory — a supply-chain shape worth noting. Alan's equivalent
should keep the native engine as a pinned submodule or a proper dependency, not as
files copied into the app tree.

**License Compatibility** MIT at the top level, but it contains vendored
llama.cpp (MIT) whose licence obligations must travel with it.

**Recommendation** Read alongside `LocalMind`. The most useful finding is
negative and architectural: **both** Android projects solve local inference by
bundling llama.cpp, which confirms that as the standard path and argues for
`llama.cpp` staying `ADAPT` rather than being replaced by Ollama permanently.

---

# PROTOCOLS

## mcp-python-sdk

**Clone URL** `https://github.com/modelcontextprotocol/python-sdk.git`
**Category** protocols · **Language** Python · **Licence** MIT
**Commit** `2118f14f8a19` (2026-10-02) · **Size** 15.0 MB, 1,717 files

**Purpose.** The official Python SDK for the Model Context Protocol: servers,
clients, tools, resources, prompts, and transports.

**Why Alan Cares** The cleanest way to give Alan external tools without letting
external code into the process. The brief calls it CRITICAL.

**Important components**
- Client transports: `stdio.py`, `sse.py`, `streamable_http.py`, plus `auth/`,
  `caching.py`, `session_group.py`, `extension.py`
- Server: `mcp/server/lowlevel/{server,helper_types}.py`, `mcp/types`,
  `mcp/shared`

**Potential Alan Integration** A tool *source*. Discovered MCP tools become
Alan's `ToolSpec` entries with an assigned `RiskLevel`, `timeout`, `permissions`
and `logging_policy` — never a bypass.

**Integration Type** `ADAPT`

**Dependencies** **3**: `python-dotenv`, `rich`, `typer`. Remarkably light — the
SDK itself contributes almost nothing to a dependency tree.

**Privacy Concerns** Neutral. A stdio MCP server is a local subprocess; HTTP
transports are Alan's choice to expose or not.

**Offline Compatibility** YES over stdio.
**Mobile Compatibility** PARTIAL — stdio and HTTP are portable; the SDK targets
CPython.

**Security Considerations** **The decisive finding: MCP has no permission model.**
Searching the SDK for `human_in_the_loop`, `require_approval` or any tool-approval
gate returns nothing equivalent. What exists is **elicitation** — `ctx.elicit(...)`
— which is the *server asking the client to collect input from a human*, and a
`roots` capability for hinting at filesystem boundaries. Neither is authorization.

By design, an MCP tool call is something the host executes on trust. That is
exactly the gap the brief anticipated, now confirmed in code rather than assumed.

**License Compatibility** MIT.

**Recommendation** **Adopt, permission-wrapped.** Start read-only, stdio only.
An agent's allow-list must not become irrelevant because a tool arrived over MCP —
that is the test to write first.

---

# ROUTING

## litellm

**Clone URL** `https://github.com/BerriAI/litellm.git`
**Category** routing · **Language** Python · **Licence** MIT
**Commit** `be4481779ee8` (2026-10-04) · **Size** 209.1 MB, **13,524 files**

**Purpose.** LLM abstraction, routing, fallback, retries, load balancing, cost
tracking.

**Why Alan Cares** The brief asks for a direct comparison against Alan's
`ModelRouter.calculateScore()`.

**Important components**
- A unified client across a very large provider catalogue
- Router, fallbacks, retries, budget tracking
- A web UI and proxy server (the Node/React dependency set)

**Potential Alan Integration** None. Alan's router already covers the local-first
path, and a cloud-shaped router is the wrong default here.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 40+, including `anthropic`, `PyJWT`, `RestrictedPython`,
`aws-sdk-bedrock-runtime`, `apscheduler`.

**Privacy Concerns** Structurally cloud-first. Its centre of gravity is routing
between *paid providers*, which is the opposite of Alan's premise. Proxy mode
means a long-lived service holding credentials and prompts.

**Offline Compatibility** PARTIAL — can front a local model, but nothing about it
is local-first.
**Mobile Compatibility** NO

**Security Considerations** LiteLLM's proxy stores API keys and request/response
bodies. A large attack surface by design.

**License Compatibility** MIT.

**Recommendation** **Reference only.** Alan's `ModelRouter` (six categories,
prefer-local) plus `ModelProvider` is the right shape for a local-first product.
Do not add a 13,524-file cloud routing layer to satisfy a capability Alan has.

---

# WORKFLOWS

## crewAI

**Clone URL** `https://github.com/crewAIInc/crewAI.git`
**Category** workflows · **Language** Python · **Licence** MIT
**Commit** `738c8e19e35c` (2026-10-02) · **Size** 399.2 MB, **31,868 files** — the
largest repository acquired

**Purpose.** Agent orchestration as "crews" of role-playing agents plus
deterministic "flows".

**Why Alan Cares** The brief rates it HIGH for autonomous task execution research.

**Important components**
- Crews (LLM-coordinated agent teams) and Flows (event-driven, deterministic)
- Event bus, task delegation

**Potential Alan Integration** None.

**Integration Type** `REJECT`

**Dependencies** 22: `aiohttp`, `anyio`, `authlib`, `cryptography`, `gitpython`,
and — decisively — **`langchain-core` and `langchain-text-splitters`**. Adopting
crewAI means adopting LangChain, which the brief explicitly says not to do
automatically. Note the manifest the harvester resolved declared no *required*
dependencies, so this set comes from the broader project metadata; either way,
LangChain is in crewAI's stack.

**Privacy Concerns** Cloud LLM providers throughout.

**Offline Compatibility** PARTIAL at best.
**Mobile Compatibility** NO

**Security Considerations** `cryptography` and `authlib` in the dependency tree
means credential-handling code paths to audit.

**License Compatibility** MIT. (The harvester initially reported this as unknown
because the canonical MIT text omits the "MIT License" title line; reading the
file gives plain MIT, © 2025 crewAI, Inc.)

**Recommendation** **Reject.** 31,868 files, a second orchestration layer, and a
LangChain dependency, in exchange for agent-team abstractions Alan expresses more
directly with seven least-privilege subagents.

---

## prefect

**Clone URL** `https://github.com/PrefectHQ/prefect.git`
**Category** workflows · **Language** Python + TypeScript · **Licence** Apache-2.0
**Commit** `2338265f6588` (2026-10-02) · **Size** 85.8 MB, 5,438 files

**Purpose.** Reliable workflow orchestration: scheduling, retries, state,
dependencies, monitoring.

**Why Alan Cares** The brief says Alan should learn Prefect's *reliability
patterns* rather than let the LLM control every aspect of execution. That is a
statement about architecture, not about code, and it is the right one.

**Important components**
- Task/flow runner with retries, caching, state
- Scheduling and event triggers
- A server plus a substantial TypeScript UI

**Potential Alan Integration** Patterns for Alan Scheduler's reliability envelope.

**Integration Type** `REFERENCE_ONLY`

**Dependencies** 40+, including **`amplitude-analytics`** (a telemetry vendor),
`aiosqlite`, `alembic`, `asyncpg`, `apprise`, `cloudpickle`.

**Privacy Concerns** `amplitude-analytics` in the dependency set means a telemetry
vendor is present in the tree. Alan would not enable it, and should note that its
presence is why "the framework is fine" is not sufficient reasoning.

**Offline Compatibility** PARTIAL — `aiosqlite` shows a local path exists, but the
server/UI architecture is service-oriented.
**Mobile Compatibility** NO

**Security Considerations** A workflow engine holds credentials and executes
arbitrary task graphs. It belongs beside Alan's permission model, never inside the
agent's reach.

**License Compatibility** Apache-2.0.

**Recommendation** **Read for reliability patterns.** Deterministic orchestration
should own retries and state — Alan already believes this, and Prefect is the best
available articulation of it. Adopt no code.

---

## AutoGPT

**Clone URL** `https://github.com/Significant-Gravitas/AutoGPT.git`
**Category** workflows · **Language** TypeScript · **Licence` ⚠ **Polyform Shield /
MIT (split)** · **Commit** `f8b0e0a87c38` (2026-10-02) · **Size** 225.1 MB, 8,181 files

**Purpose.** Autonomous agent research platform.

**Why Alan Cares** The brief rates it LOW/MEDIUM and says use it primarily as
historical/reference material. Agreed.

**Important components**
- `autogpt_platform/` — the modern platform
- Legacy agent code
- 40 dependencies at the top level

**Potential Alan Integration** None.

**Integration Type** `REJECT`

**Dependencies** 40+, including `anthropic`, `boto3`, `Pillow`, `beautifulsoup4`,
`aiohttp`, `ddgs`.

**Privacy Concerns** Cloud providers throughout.

**Offline Compatibility** NO in practice.
**Mobile Compatibility** NO

**Security Considerations** Autonomous execution with shell and network access is
a large blast radius by design.

**License Compatibility** ⚠ **Requires review.** The `LICENSE` file is explicit:
*"All portions of this repository are under one of two licenses. Everything inside
the `autogpt_platform` folder is under the **Polyform Shield License**. Everything
outside the `autogpt_platform` folder is under the MIT License."* Polyform Shield
is source-available and **non-commercial** — it permits internal use and forbids
selling the software. That is a live constraint, not a formality.

**Recommendation** **Reject**, as the brief directs. History and architecture are
worth skimming; the licence split is a further reason not to build on it.

---

# UTILITIES

## uv

**Clone URL** `https://github.com/astral-sh/uv.git`
**Category** utilities · **Language** Rust · **Licence` **MIT / Apache-2.0 (dual)**
· **Commit** `46b84fd0bfec` (2026-10-03) · **Size** 39.1 MB, 1,768 files

**Purpose.** Fast Python environment and dependency management.

**Why Alan Cares** The brief asks whether it should manage the new Alan Python
kernel. It is the only project here whose job is making *other* Python projects
manageable.

**Important components**
- Resolver, installer, virtual environment management
- Python version management
- A Rust implementation with a Python wrapper

**Potential Alan Integration** Development tooling for Alan and this research
workspace. Not a runtime dependency.

**Integration Type** `ADAPT` (tooling only)

**Dependencies** 40+ Rust crates. Built and shipped as a standalone binary.

**Privacy Concerns** None.

**Offline Compatibility** YES for local operations; registry access only for
downloads.
**Mobile Compatibility** N/A — a developer tool.

**Security Considerations** As a resolver it sits in the supply chain. Pin and
hash-lock, exactly as Alan already pins `browser_use==0.13.10`.

**License Compatibility** MIT **or** Apache-2.0, at the user's choice. Both
permissive; the most permissive-detection-friendly of any repository here.

**Recommendation** **Evaluate for Alan's Python kernel, not for this workspace.**
It cannot migrate Alan's existing `env` without a rebuild, and a working
environment is worth more than a fast one. Worth it for greenfield.

---

# EVALUATION

## BrowserGym

**Clone URL** `https://github.com/ServiceNow/BrowserGym.git`
**Category** evaluation · **Language** Python · **Licence` Apache-2.0
**Commit** `c05d7f38f4f7` (2026-09-27) · **Size** 2.7 MB, 205 files

**Purpose.** Web-agent environments and benchmarks.

**Why Alan Cares** It can measure whether Alan's browser tool is reliable, and
provide regression tests.

**Important components**
- Environment abstractions over browser backends
- Benchmark task definitions and scoring

**Potential Alan Integration** Test harness only. **Never in the runtime path.**

**Integration Type** `BENCHMARK_ONLY`

**Dependencies** Manifest found, no *required* dependencies declared — they are
extras-gated.

**Privacy Concerns** Benchmarks replay recorded tasks; that is synthetic data by
design, and none of it is Alan's.

**Offline Compatibility** PARTIAL — some environments need network targets.
**Mobile Compatibility** NO

**Security Considerations** Running a benchmark executes a browser agent against
sites. Keep it sandboxed and off any real account.

**License Compatibility** Apache-2.0.

**Recommendation** Use to measure Alan's browser tool. Do not let it become
infrastructure — a benchmark on the production path is how a research dependency
becomes a product dependency.

---

## WindowsAgentArena

**Clone URL** `https://github.com/microsoft/WindowsAgentArena.git`
**Category** evaluation · **Language** Python · **Licence` MIT
**Commit** `6d39ed88c545` (**2024-11-20**) · **Size** 5.9 MB, 514 files

**Purpose.** Benchmarking desktop computer-use agents on Windows.

**Why Alan Cares** It is the only benchmark in the set that targets the actual
host OS.

**Important components**
- Task suites over Windows applications
- An Azure ML-backed evaluation harness

**Potential Alan Integration** Reference methodology only.

**Integration Type** `BENCHMARK_ONLY`

**Dependencies** **5, and all of them are Azure**: `azure-ai-ml`,
`azure-identity`, `azureml-core`, `azureml-dataset-runtime`, plus `pandas`. The
harness requires Azure Machine Learning.

**Privacy Concerns** Its harness sends task data to Azure. Not usable as-is for a
privacy-first evaluation, and not runnable without an Azure account.

**Offline Compatibility** **NO** — Azure ML is a hard requirement.
**Mobile Compatibility** NO

**Security Considerations** Desktop automation on a real Windows host. Isolate it
completely; it drives the same desktop the user works on.

**License Compatibility** MIT.

**Recommendation** **Benchmark only, and not currently runnable.** Two blockers,
both factual: the commit is **2024-11-20**, roughly two years behind every other
repository acquired here and behind current Windows agent work, and the harness
requires Azure. Read the task design if computer use is ever prioritised; do not
plan to run it.

---

## Cloud dependencies — recorded, not enabled

Per the brief, cloud-only features are documented rather than turned on.

| Repository | Cloud dependency | Local alternative | Alan's decision |
|---|---|---|---|
| supermemory | `supermemory.ai` browser login (default) | self-hosted `npx supermemory local` | **avoid** |
| litellm | paid providers, proxy server | Alan's `ModelRouter` + Ollama | **avoid** |
| pydantic-ai | 36 of 38 provider adapters | `ollama.py` | **research only** |
| crewAI | LangChain + cloud models | Alan's own subagents | **reject** |
| AutoGPT | Anthropic, AWS, platform SaaS | — | **reject** |
| WindowsAgentArena | Azure ML (5/5 deps) | — | **cannot run** |
| OmniParser | Azure, AWS, Anthropic in deps | local models + GPU | **avoid** |
| local-rag | `foundry-local-sdk` (Microsoft Foundry) | local embeddings | **reject** |
| prefect | `amplitude-analytics` telemetry | — | **never enable** |
| mem0 | optional cloud vector backends | local `chromaadb` / `faiss-cpu` | optional |
| graphiti | optional Neo4j/Neptune | **`kuzu` embedded** | **prefer local** |
| lancedb | optional S3/Azure connectors | embedded local store | **prefer local** |
| agent-framework | optional cloud channels | self-contained .NET | research only |
| letta-code | `chat.letta.com` | `letta server` self-hosted | research only |

**No API key was created, no service authenticated, no telemetry enabled, and
no Alan data transmitted during this acquisition.**

---

## Security findings from reading the trees

Things worth knowing that are not licence or dependency questions:

1. **browser-use's profile-copy trap.** `BrowserProfile._copy_profile` always
   creates a new random temp directory unless the path contains the literal marker
   `browser-use-user-data-dir-`. This caused 125 leaked Chrome processes and
   731 MB during earlier Alan work. Alan's sidecar profile name is load-bearing —
   do not rename it.
2. **MCP has no permission model.** Elicitation and `roots` are not authorization.
   An MCP tool call is trusted by construction.
3. **mem0 duplicates its own core seven times.** A 98,929-byte `memory_core.py`
   appears verbatim in seven `integrations/*/core/` directories.
4. **local-rag is not local.** Its "local RAG" depends on Microsoft Foundry.
5. **Open browser sessions are a larger blast radius.** `open-browser-use` reaches
   the user's real session through native messaging — far more sensitive than
   Alan's isolated profile, which is the safer default.
6. **Screenshots are an injection channel.** OmniParser-style perception turns
   rendered text into agent input.
7. **Vendored source inside app trees.** `offline-ai-assistant` carries a full
   llama.cpp checkout in `app/src/main/cpp/`, a supply-chain shape to avoid.
8. **`RestrictedPython` in litellm** — sandboxing code is in scope for that
   project, which tells you what kind of features it has.
9. **No `curl | bash` was executed.** Install scripts were read, not run.