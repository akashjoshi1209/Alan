# ALAN EXTERNAL REPOSITORY AUDIT

**Date** 2026-10-05 · **Workspace** `D:\Alan\alan-research` ·
**Acquired** 30 of 30 repositories, 0 failures, 2.15 GB · **Built nothing**

Companion documents: `REPOSITORIES.md` (per-repository record),
`INTEGRATION-MAP.md` (placement against Alan's architecture),
`ARCHITECTURE-COMPARISON.md` (axis-by-axis comparison and verdicts),
`DECISIONS.md` (decision record, including three corrections to the brief).

---

## 1. Repositories successfully cloned

**All 30.** No URL in the brief failed, including the six that were likely
candidates for being wrong — `memlocal/memlocal_core`,
`open-browser-use/open-browser-use`, `RisorseArtificiali/vesta`,
`tk85457/LocalMind`, `its-hazratbilal/offline-ai-assistant`, and
`leestott/local-rag`. All six exist.

Shallow (`--depth 1 --single-branch --no-tags`), submodules not followed, exact
commit pinned per repository in `clone-results.json` and `REPOSITORIES.md`.
Nothing was modified, built, installed or executed.

Two repositories turned out to be **not what the brief expected**, which is a
finding rather than a failure:

- **`letta` contains no source code.** 12 files, 0.1 MB. Its README states the
  source moved to `letta-code`. What remains is governance: `PRIVACY.md` (21 KB),
  `AI_POLICY.md`, `SECURITY.md`, `TERMS.md`. The brief asked to inspect the
  historical repository; the honest answer is there is none left to inspect.
- **`WindowsAgentArena`'s newest commit is 2024-11-20** — two years behind every
  other repository acquired, and its five declared dependencies are all `azure-*`.

## 2. Repositories that failed to clone

**None.** Zero failures, zero timeouts, zero renames.

## 3. Repositories requiring special dependencies

Three, and each blocks something rather than merely complicating it.

| Repository | Requirement | Consequence |
|---|---|---|
| `WindowsAgentArena` | `azure-ai-ml`, `azure-identity`, `azureml-core`, `azureml-dataset-runtime` — **5 of 5 deps are Azure** | **Cannot be run locally at all.** Requires an Azure ML account. |
| `OmniParser` | `accelerate`, `easyocr`, `gradio` + a GPU; cloud SDKs (`anthropic`, `boto3`, `azure-identity`) in the default set | Runs only on a GPU, and the cloud options must be actively avoided. |
| `pydantic-ai` | 38 provider adapters, **~36 of them cloud**; only `ollama.py` and `vllm.py` are local | Adoptable for local use only, and then only two of 38 adapters are relevant. |

Not blockers but worth recording: `litellm`, `crewAI`, `AutoGPT` and
`prefect` are all cloud-first by structure; `supermemory` additionally depends on
`@scarf/scarf` download analytics; `prefect` pulls `amplitude-analytics`.

## 4. Best implementation discovered, per Alan capability

| Capability | Best implementation | Why it wins |
|---|---|---|
| **Local inference** | `llama.cpp` | GGUF, quantisation, CPU/GPU, server mode. The floor everything else stands on. |
| **Model serving / lifecycle** | `ollama` (already in place) | Model store, scheduler, discover. Small Go service, 20 deps. |
| **Memory conflict resolution** | `mem0` | The extract → compare → ADD/UPDATE/DELETE/NOOP decision Alan does not have. |
| **Temporal correctness** | `graphiti` | `valid_at`/`invalid_at`. A boolean `expires` cannot express "true from March until May". |
| **Local-first memory design** | `memlocal_core` | Local DB + graph + rerank + mobile SDK, in 12 light Rust deps. |
| **Durable resumption** | `langgraph` `checkpoint-sqlite` | Pause and resume long-running work, locally, in 4 dependencies. |
| **Tool schema ergonomics** | `pydantic-ai` | Typed tools, validated results, zero required dependencies. |
| **External tool protocol** | `mcp-python-sdk` | 3 dependencies, stdio/HTTP, official. |
| **Embedded vector store** | `lancedb` | Rust/Arrow, embedded, no server. |
| **Embedded graph store** | `graphiti`'s **Kuzu** driver | The only server-free, key-free temporal graph in the set. |
| **Android on-device inference** | `LocalMind` + `offline-ai-assistant` | Both bundle llama.cpp via JNI and manage GGUF sessions. Confirms the standard path. |
| **Android offline UX** | `vesta` | "Intelligence that never leaves home" — no accounts, no egress. |
| **Agent privacy model** | **Alan's own** | Five risk tiers, per-subagent allow-lists, no bypass. Nothing in 30 repositories has any equivalent. |
| **Recall gating** | **Alan's own** | Zero lexical overlap ⇒ zero, unrescuable by bonuses. No counterpart anywhere. |
| **Workflow reliability patterns** | `prefect` | Deterministic orchestration owning retries and state. |

The last three rows are Alan's, and that is the finding.

## 5. Recommended components to integrate

Seven, in priority order. None is a repository; all are specific behaviours.

1. **Temporal validity (from `graphiti`).** Add `valid_from` / `valid_to` to
   `MemoryItem`; teach the gate to refuse an item whose interval has closed and
   report it as history rather than fact. *Justified on schema grounds, and it is
   the single biggest correctness gap in Alan's memory model.*
2. **Supersession links (from Alan's own prior `decisions` table).**
   `superseded_by` — retire a memory by pointing at its replacement rather than
   deleting it. Note the prior implementation declared it and never exercised it,
   so adopt the idea and write the tests this time.
3. **Extraction and conflict resolution (from `mem0`).** The decision only,
   re-expressed behind `MemoryProvider`, **after** Alan's gate.
4. **Durable checkpointing (from `langgraph` `checkpoint-sqlite`).** So long
   Alan's scheduler work resumes instead of restarting.
5. **Read-only MCP tool source (from `mcp-python-sdk`).** Every imported tool
   mapped to an existing `RiskLevel`, `timeout` and `permissions`. stdio only,
   read-only first.
6. **Retrieval design (from `memlocal_core`).** Only if recall measurement shows
   a need.
7. **`uv` for a greenfield Python kernel.** Not for the working `env`.

**Explicitly not recommended: replacing Alan's memory store, router, orchestrator,
permission model, tool registry or browser tool.** All of them work, all of them
are tested, and none is outperformed by anything acquired.

## 6. Components that should remain Alan-native

Permanently, not provisionally:

- **The composition root.** One `build_runtime()` in `src/alan/runtime.py`. One
  request path: `request.started → memory.retrieved → plan.created →
  model.started → model.finished → request.finished`.
- **The permission tiers.** `SAFE < LOW_RISK < MEDIUM_RISK < HIGH_RISK <
  PRIVILEGED`, with per-subagent allow-lists and risk ceilings. Agents cannot
  bypass them, including via MCP.
- **The memory model and the relevance gate.** L0–L5 plus `memory/relevance.py`.
  Providers generate candidates; Alan decides what may be asserted. Every one of
  the five memory projects returns *more* candidates, which makes the gate more
  important, not less.
- **The model router.** Six categories with a prefer-local policy. LiteLLM is a
  cloud router; replacing Alan's with it would invert the product's premise.
- **The tool registry.** `ToolSpec` with `name`, `description`, `input_schema`,
  `output_schema`, `risk`, `permissions`, `timeout`, `logging_policy`,
  `executor`, `tags`.
- **The seven subagents** and their least-privilege allow-lists.
- **"I don't know."** Zero relevance ⇒ empty context. Verified: an unrelated
  query returns 0 hits and a 0-character block.
- **The event schema.** Six families with a per-request `trace_id`.

## 7. Components that should only be references

Thirteen: `pydantic-ai`, `langgraph`, `agent-framework`, `letta`,
`letta-code`, `scrcpy`, `vesta`, `LocalMind`, `offline-ai-assistant`, `litellm`,
`prefect`, `OmniParser`, and (as a policy-document reference only) `letta`.

For each, the reference is one or two specific ideas — a pattern, an interface, a
failure mode — not an import. `letta-code` in particular is
**2,023 TypeScript files against 7 Python files**; the concepts are worth having,
the code is unreachable for a Python Alan.

## 8. Security and privacy concerns

**Disqualifying, and acted on:**

- **`supermemory`'s documented default is a browser login to `supermemory.ai`**,
  with `SUPERMEMORY_API_KEY` written into shell profiles. For a product whose
  premise is that data does not leave the machine, this is the wrong shape.
  Self-hosting exists but is an opt-in tip. → `REJECT`.
- **`termux-api` has no `LICENSE` file at any depth.** A README sentence claiming
  GPLv3 is not a grant; absent one, default copyright applies. GPL-3.0 would be
  copyleft besides. → `REJECT` on licence grounds, not technical ones.
- **`MCP has no permission model.**** Searching the SDK for `human_in_the_loop`,
  `require_approval` or an approval gate returns nothing equivalent. What exists is
  *elicitation* — the server asking the client to collect human input — and
  `roots` for filesystem hints. Neither is authorization. An MCP tool call is
  trusted by construction, which is why Alan's tiers must wrap it.
- **`OmniParser` is licensed CC-BY-4.0** — a content licence applied to code, not
  an OSI software licence. → blocked for reuse.
- **`AutoGPT`'s `autogpt_platform/` is Polyform Shield** — source-available and
  non-commercial.

**Operational risks found by reading code:**

- **browser-use profile-copy trap.** `BrowserProfile._copy_profile` *always*
  creates a new random temp directory; only the literal marker
  `browser-use-user-data-dir-` skips the copy. This leaked 125 Chrome processes
  and 731 MB in earlier Alan work. Alan's profile is
  `data\browser-profile\browser-use-user-data-dir-sidecar-8022` and **must not be
  renamed** — the name is load-bearing for orphan reaping. Verified 0 → 9 → 0.
- **mem0 duplicates its own core seven times** — a 98,929-byte `memory_core.py`
  appears verbatim in seven `integrations/*/core/` directories.
- **`local-rag` is not local** — it depends on Microsoft Foundry.
- **Screenshots are a prompt-injection channel.** Rendered text becomes agent
  input for any computer-use perception layer.
- **Knowledge-graph poisoning** — extracted entities from untrusted input become
  attacker-chosen graph edges unless gated before write.
- **`open-browser-use` reaches the user's real session** via native messaging — a
  much larger blast radius than Alan's isolated profile. Also macOS/Linux only, so
  unavailable on this host regardless.

**Process guarantees held:** no API key created, no service authenticated, no
telemetry enabled, no Alan data transmitted, no `curl | bash` executed, no install
script run, no third-party code executed at all. `GIT_TERMINAL_PROMPT=0` on every
git call so nothing could block on a credential prompt.

## 9. Licence concerns

29 of 30 are permissively licensed: 14 MIT, 13 Apache-2.0, `uv` dual
MIT/Apache-2.0, `autogen` dual MIT (code) / CC-BY-4.0 (content).

Three require a human decision before any code moves — `termux-api` (no licence),
`OmniParser` (CC-BY-4.0 on code), `AutoGPT` (Polyform Shield on its platform
folder). None is a blocker for `REFERENCE_ONLY` study; all three block reuse.

Two corrections to the audit's own tooling are recorded in `DECISIONS.md` D9:
MIT was initially misreported as unknown (matching a title line that canonical MIT
files omit), and `pyproject.toml` dependencies were regex-parsed into garbage
(`"."`, `"3.10"`, `"E4"`) before being replaced with `tomllib`. A false negative
on a permissive licence reads as "unusable", which would have wrongly condemned
about half the set.

## 10. Mobile and offline compatibility

| | Offline | Mobile | Note |
|---|---|---|---|
| llama.cpp | YES | PARTIAL | **Zero `.java` files, no `android/` dir** — ships the native library, not an app. |
| ollama | YES | NO | Server app |
| memlocal_core | YES | **YES** | Only project that can claim both. `flutter_rust_bridge` + `cozo` |
| graphiti | YES (Kuzu) | PARTIAL | Kuzu is embeddable; the Python package is not phone-sized |
| vesta | YES | YES | React Native/Expo, not native Kotlin |
| LocalMind | YES | YES | Native Kotlin + JNI |
| offline-ai-assistant | YES | YES | Vendors llama.cpp into the app tree |
| lancedb | YES | PARTIAL | Rust core portable; bindings are not yet a phone story |
| mem0 | YES | PARTIAL | Backends are not phone-sized; decision logic is portable |
| browser-use | YES | NO | Desktop |
| pydantic-ai | PARTIAL | NO | `ollama.py` only |
| supermemory | PARTIAL | NO | Only by running its own TS server |
| litellm / crewAI / AutoGPT / prefect | NO–PARTIAL | NO | Cloud-first by structure |
| WindowsAgentArena | **NO** | NO | Azure ML required |

The **both-offline-and-mobile** column has exactly one entry: `memlocal_core`.
That is the project's strongest argument, and the reason it is worth reading in
full at 1.3 MB.

## 11. Recommended final architecture

**Unchanged in shape, sharper in four places.** And that is the conclusion the
acquisition was for.

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

Four changes, each traceable to a specific finding:

1. **Memory validity becomes an interval, not a boolean** (Graphiti). A superseded
   fact stays retrievable as history but cannot be asserted as current.
2. **Memory gains a supersession edge** (Alan's own prior `decisions` table).
3. **MCP arrives as a permission-wrapped tool source** — because the protocol
   itself has no permission model.
4. **The gate gets stronger, not weaker.** Five external memory projects all
   return more candidates. More candidates is a reason for more gating.

Unchanged: one composition root, one request path, one permission model, one
memory shape, local-first, "I don't know".

Thirty repositories were read so that this could be said **with evidence rather
than faith**. The final goal was never "Alan uses 30 GitHub projects". It is "Alan
uses the best proven ideas available, while keeping one coherent, local-first,
secure, maintainable architecture." The audit says: seven components in, zero
architectures replaced.

## 12. Next implementation milestone

**Temporal correctness in Alan's memory model, then external tools behind the
gate.**

Ordered, with tests first:

1. Add `valid_from` / `valid_to` / `superseded_by` to `MemoryItem`.
2. Teach `memory/relevance.py` to refuse an item whose validity interval has
   closed, and to surface it as history rather than fact.
3. Tests: an item valid only until a date must not be asserted after it; a
   superseded item must not be asserted as current; history must still be
   retrievable when asked for.
4. Measure recall on Alan's own set. Adopt only if it beats the boolean model.
5. Then a **read-only, stdio-only MCP tool source**, every imported tool mapped to
   an existing `RiskLevel`, with a test proving an agent cannot escalate through
   MCP.

Do not start any of the other 25. Unwired is a bug list, not a reason to rewrite
Alan — and the cheapest way to keep that true is to keep importing nothing until
a measurement says otherwise.

---

## State of Alan at audit close

Everything below was run **after** the acquisition and after every fix, not before:

| Check | Result |
|---|---|
| `python -m unittest discover -s tests` | **176 tests, OK** (19.4 s) |
| `python -m pytest -q` — bare checkout, no env vars | **176 passed, 6 subtests** (17.3 s) |
| `python -m alan doctor` | green — `ollama:qwen2.5:1.5b`, cognee ready, browser-sidecar ready (browser-use 0.13.10), 8 tools, 7 agents, ceiling `medium_risk` |
| `demos/demo_all.py` | **5/5 PASS** |
| `scripts/selftest.ps1` | **SELFTEST PASSED** |
| `scripts/lint_syntax.py` | **0 defects** across 59 Alan-owned files |
| `scripts/verify_tree.py` | **77 files checked, 77 expected: healthy**, exit 0 |
| mirror `D:\alan-src-backup-20261005-050950` | 77 files, **byte-identical**, `--sync` writes 0 |
| orphan-`.pyc` sweep | clean |

Three defects were found and fixed by the closing scan, all in Alan's own tooling:

1. **`SyntaxWarning: invalid escape sequence '\c'`** in
   `scripts/measure_cognee.py:29` — a docstring quoting a Windows path
   (`data\cognee\latency_probe`). A warning today, a `SyntaxError` in a future
   Python. Fixed by making the docstring raw. `scripts/lint_syntax.py` now exists
   to keep this class from recurring, and promotes `SyntaxWarning` to an error.

2. **16 swallowed exceptions** — all in `close()` teardown, best-effort tracing,
   startup reaping, or `except KeyboardInterrupt` around `serve_forever()`.
   Every one reviewed individually and deliberately left alone. The linter reports
   them as advisories and exits 0; `--strict` makes them fatal.

3. **`ModelRouter` was unprotected** — `verify_tree.py` matched its skip-list
   against every path component, so the entry `"models"` (meant for the top-level
   GGUF store) also excluded `src/alan/models/`. Two files had never been hashed
   and the mirror had no copy of either, so a deletion would have been invisible
   *and* unrecoverable, while the guard reported "healthy". Split into
   `SKIP_TOP` / `SKIP_ANY`; the same bug in `lint_syntax.py` is why it had been
   checking 57 files instead of 59. Proven fixed by deleting `router.py`:
   detected → restored → SHA-256 byte-identical → 77/77 healthy.

**Note on this session.** Three files in `D:\Alan` were deleted by something
outside Alan's control while this work was in progress —
`src/alan/tools/coding.py`, `src/alan/core/orchestrator.py`,
`src/alan/interfaces/types.py`. Each one stops Alan importing. Cause never
identified. The verified mirror caught all three, and `verify_tree.py` now detects
and repairs such a loss in under a second.

I also destroyed the mirror myself, while refreshing it: a PowerShell `$HOME`
assignment silently failed (`$HOME` is read-only), so a path filter came out empty
and every directory in the mirror was deleted. Recoverable, because the mirror is a
copy — but the mistake was mine, and the reason `--sync` now exists is that mirror
maintenance had been a second, hand-maintained file list rather than a function of
the manifest. One list, no path arithmetic.

**Write, then verify, then test — in that order, every time.**

**`F:` was never read, written or listed at any point in this session.**