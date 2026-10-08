# Dependency and integration manifest

Every external repository and package Alan relies on, with an exact pin.
Generated 2026-10-04 (ECC row added 2026-10-08). See
[repository-audit.md](repository-audit.md) for the evidence behind each
verdict, [ecc.md](ecc.md) for the ECC integration, and
[../decisions/architecture-decisions.md](../decisions/architecture-decisions.md)
for the decisions.

## Pinned source repositories

Pinned because DeepSeek Harness is a developer preview that states it will make
compatibility-breaking changes, and because reproducibility requires an exact
revision for every source Alan learns from.

| Repository | URL | Commit SHA | Date | License | Integration type | Alan adapter | Local modifications | Reason | Known limitations |
|---|---|---|---|---|---|---|---|---|---|
| deepseek-ai/deepseek-harness | https://github.com/deepseek-ai/deepseek-harness | `5badb15009ae` | 2026-10-03 | MIT | C — architecture port | `src/alan/core/` | None (independent Python implementation) | Primary reference for plugin registry, event bus, session/job lifecycle, subagents | Alpha (`0.2.1-alpha.1`); its API will change; Alan pins the commit only as a design reference |
| mem0ai/mem0 | https://github.com/mem0ai/mem0 | `abb81c88e1f7` | 2026-10-01 | Apache-2.0 | A — PyPI dependency | `src/alan/memory/providers/mem0_provider.py` | None | L4/L5 semantic memory with mature retrieval | Ships `posthog` analytics as a hard dep (disabled); pulls `qdrant-client` (~35 MB) |
| letta-ai/letta-code | https://github.com/letta-ai/letta-code | `77faf36e3437` | 2026-10-03 | Apache-2.0 | D — reference | `src/alan/memory/blocks.py`, `memfs.py` | None | Memory blocks, MemFS git-tracked context, skill/subagent organisation | TypeScript/Bun; concepts only, no code reusable |
| browser-use/browser-use | https://github.com/browser-use/browser-use | `5c892e013a73` (tag `0.13.10`) | 2026-09-04 | MIT | A — isolated sidecar | `src/browser_sidecar.py`, `src/alan/tools/browser/` + `scripts/browser-serve.ps1` | None | Mature CDP browser automation, driven but not delegated to | Exact `==` pins conflict with Alan's main env; its agent loop and `extract_content` are deliberately unused; sidecar must be started separately |
| openinterpreter/open-interpreter | https://github.com/openinterpreter/open-interpreter | `2767e5f20d69` | 2026-10-02 | Apache-2.0 | D — reference | `src/alan/tools/shell/`, `filesystem/` | None | Execution/harness-abstraction patterns | Now Rust (Codex fork); installs via `curl \| sh`; rejected as untrusted |
| SWE-agent/SWE-agent | https://github.com/SWE-agent/SWE-agent | `3ea751c087f3` | 2026-07-16 | MIT | D — reference | `src/alan/agents/coding/` | None | Coding-agent decomposition and narrow tool set | Research-oriented deps (datasets/pandas/flask) unused by Alan |
| affaan-m/ECC | https://github.com/affaan-m/ECC | `ef648e01` | 2026-10-08 | MIT | E — local content library | `src/alan/ecc/` | None (markdown read as data; tools ported independently) | 293 skills, 94 commands, 68 agent prompts as a gated read-only catalog; 8 advisory dev tools | The one vendored checkout (`vendor\ECC`); hooks deliberately not ported — its permission auto-approve has no path into Alan (ADR-006); see [ecc.md](ecc.md) |

**No third-party source was modified.** No repository was forked. The one
vendored checkout is ECC, cloned to `vendor\ECC` and read as data at runtime
— its files are never edited, and the adapter is Alan's own code. `vendor/`
is git-ignored like `env/`: the clone (pinned at `ef648e01`, 55 MB with its
assets) stays local, and a fresh clone restores it with the single `git
clone` command in [ecc.md](ecc.md).

## Packages in the main environment (`env\python`)

Installed only after the conflict analysis in the audit.

| Package | Pin | Purpose | Notes |
|---|---|---|---|
| `openai` | `3.24.0` | cloud provider | pin preserved through the mem0 conflict analysis |
| `anthropic` | `1.11.0` | cloud provider | |
| `pydantic` | `2.13.5` | provider request/response models | |
| `rich` | `15.0.0` | CLI tables | |
| `requests` | `2.34.2` | cognee REST client | |
| `httpx` | `0.28.1` | provider transport | |
| `typer` | `0.27.2` | CLI framework | |
| `ollama` | `0.6.3` | local provider client | |
| `pytest` | `9.1.1` | runs third-party test suites via `code.test` | added after `LocalCoding.run_tests` proved a bare `pytest` can never work — the bundled `Scripts` dir is not on `PATH` |
| `mem0ai` | `2.2.1` | L4/L5 semantic memory | installed and verified: `alan backends` reports `ready (llm=qwen2.5:1.5b via http://127.0.0.1:11435)`. Telemetry forced off via `MEM0_TELEMETRY=False` before import. Added only after a `pip install --dry-run` confirmed no existing pin would be downgraded. |

Transitive additions from `mem0ai`: `qdrant-client`, `numpy`, `sqlalchemy`,
`protobuf`, `posthog`, `pytz`, `portalocker`, `backoff`, `distro`, `grpcio`,
`pywin32`. From `pytest`: `pluggy`, `iniconfig`, `packaging`.

`requests` serves three distinct clients — the cognee REST provider, the browser
sidecar client, and the Ollama provider — which is why its pin is one of the
harder constraints in the sidecar decision below.

## Sidecar environments

Neither is installed into `env\python`. Both are built by a script, and both
strip `..\..\src` from `python*._pth` so that neither can `import alan` — the
sidecars are implementation detail and must not be able to reach into Alan's
package tree.

### `env\browser\python` — browser automation

| Package | Pin | Notes |
|---|---|---|
| `browser-use` | `0.13.10` | commit `5c892e013a73` |
| `cdp-use` | `1.4.5` | browser-use's own Chrome DevTools Protocol client |
| `browser-harness` | `0.1.13` | pinned by browser-use 0.13.10 |

**browser-use 0.13 no longer uses Playwright.** Its `pyproject.toml` at the
pinned commit lists `cdp-use` and `browser-harness` and contains no Playwright
dependency at all. There is therefore no `playwright install` step, no driver
download, and no bundled browser binary: the sidecar launches the host's own
installed Chrome (or Edge) headless and talks to it over CDP. This is a
meaningful change from earlier versions of this manifest, which assumed a
Playwright-driven browser.

Inside this environment browser-use's exact pins downgrade `openai` 3.24→2.26,
`rich` 15.0→14.3, `requests` 2.34→2.33 and `posthog` 7.62.1→7.7.0. That
downgrade is the entire reason the environment exists.

Two API facts that cost real debugging time and are pinned by tests:

- `Element.evaluate` is unusable for reading an element's text. It hands the
  supplied function no element reference, so `el` is undefined and any
  expression touching it throws. `Page.evaluate(page_function, *args)` does
  take arguments and is used instead.
- `page.screenshot(format=...)` returns a base64 **string**, not bytes.

browser-use 0.13 is async, so the sidecar runs a private asyncio loop on a
daemon thread (`_Loop`) in order to keep one browser session alive across
synchronous HTTP requests.

### `env\cognee\python` — local semantic memory

| Package | Pin | Notes |
|---|---|---|
| `cognee` | `1.6.2` | `/health` reports `{"status":"ready","version":"1.6.2"}` |
| `torch` | `2.14.1+cpu` | CPU-only: no CUDA, no host GPU dependency |

#### Known upstream defect: dataset isolation is not enforced

Measured against 1.6.2 on 2026-10-05. `POST /api/v1/recall` is called with
`{"datasets": ["latency_probe"]}`, and the response contains passages that
belong to the `alan` dataset:

```
probe dataset recall -> "cognee latency probe 3: ..."      (correct)
                     -> "no my name is akash"               (from `alan`)
                     -> "Alan keeps its bundled Ollama..."  (from `alan`)
```

So the `datasets` filter is not applied to whatever source answers the query.
Alan's own request is well-formed; the defect is inside cognee.

**Why this matters to Alan.** A memory system that cannot keep datasets apart
can present content that was written for a different purpose as if it were the
user's own memory. Two consequences, both handled:

1. *Correctness.* Cross-dataset passages would be asserted as facts. Alan's
   defence is the relevance gate in `alan.memory.relevance`: a passage with no
   lexical overlap with the question scores zero and is never rendered into the
   prompt. The gate is Alan's own, not cognee's, which is the point of it.
2. *Diagnosis.* `alan forget --list` enumerates Alan's index, which only ever
   contains what Alan wrote. If recall shows something that is not in that
   list, this defect is the explanation.

**Mitigation not attempted.** Filtering client-side by dataset would mean
trusting a field cognee does not filter on. Deleting the offending datasets is
an option but destroys real memory. Alan leaves the data and narrows what may
be asserted from it.

### `env\browser\python` — browser automation

Alan's own test suite deliberately uses **stdlib `unittest` only**, so
verifying Alan never requires pytest. pytest exists so the coding agent can run
*other* projects' suites.

#### browser-use cannot be given a profile directory

`BrowserSession(user_data_dir=...)` does not do what the parameter name
suggests. `BrowserProfile._copy_profile` creates a **new random**
`tempfile.mkdtemp(prefix="browser-use-user-data-dir-")` and copies the supplied
profile into it, so a caller cannot own the path it asked for. The only way to
keep a path is to include the literal marker `browser-use-user-data-dir-` in
the name, which is that function's own "already a temp directory" escape hatch.

Alan needs a stable path, because it identifies and reaps an orphaned browser
by matching that path in the process command line; a random path per launch
cannot be matched. Hence `ALAN/data/browser-profile/browser-use-user-data-dir-sidecar-8022`
— an odd name that is load-bearing. `docs/ARCHITECTURE.md` repeats it, and
`_PROFILE_MARKER` in `src/browser_sidecar.py` says why it must not be tidied.

There is no behavioural downside: `user_data_dir` is only also consulted by a
warning that fires when `storage_state` is *also* set, and Alan never sets it.

## Packages deliberately NOT added

| Package | Why not |
|---|---|
| `browser-use` and its ~40 pinned deps in the **main** env | Would downgrade `openai` 3.24→2.26, `rich` 15.0→14.3, `requests` 2.34→2.33, `posthog` 7.62.1→7.7.0. Isolated sidecar instead. |
| `playwright` | Not a choice any more: browser-use 0.13.10 dropped Playwright entirely in favour of `cdp-use`. Alan never installs it. What would have cost ~150 MB of bundled Chromium is now the host's existing browser, which is the better outcome anyway. |
| `datasets`, `pandas`, `flask`, `litellm`, `swe-rex` | SWE-agent's research/eval stack; unused. |
| Node.js / pnpm / Bun runtimes | DSH and Letta Code are TypeScript. Bundling a Node runtime + a 263 MB monorepo would create a second competing runtime and break portability. |
| Open Interpreter binary | Rust project installed by remote script; a large untrusted surface for capability Alan implements in ~200 lines. |
| browser-use's own agent loop | It is a second planner competing with Alan's. Alan decides *what* to do; browser-use only does the mechanical clicking and typing. Same reason `extract_content` is unused — it requires an LLM, and handing page extraction to a 1.5B model is worse than reading `innerText`. |
| A search API key | Search must work keyless. The engines Alan reads are keyless HTML pages, and their results are relevance-filtered because this network rewrites them. See below. |

## Measured behaviour, not assumed behaviour

Findings that changed the implementation, all recorded because the code looks
arbitrary without them.

**Where a turn's time actually went.** Measured 2026-10-05 on the reference
machine (4 logical / 2 physical cores, 3.9 GB RAM) with `scripts\profile_ask.py`,
which wraps each stage of one `alan ask`:

| Stage | Before | After | Cause |
|---|---|---|---|
| Runtime construction | 7.0 s | 3.0 s | Four backend liveness probes per invocation; ~5 s of it was Ollama's `client.list()` |
| Memory recall | 9.1 s | 0.7 s | `_recall` searched, then `context_block` searched *again* — two graph round trips per question |
| Model call | 17.7 s | 1.7 s | Hardware-bound; fell once RAM pressure stopped evicting the model |
| **Total, one question** | **34.2 s** | **5.5 s** | |

The first-invocation cost is separate and stays: loading `qwen2.5:1.5b` into
RAM costs ~12 s, and nothing in Alan's control changes that.

Probes are now cached in `data\state\probes.json` — 300 s for a success, 3 s
for a failure, because a sidecar that is still starting must be noticed
promptly. `alan doctor` clears the cache first and probes fresh, since a
diagnostic that reported a remembered "ready" would be worse than none. A
provider that fails a real request drops its own entry, so a stale "up" cannot
outlive the backend it described. `ALAN_PROBE_CACHE=0` disables the cache
entirely; `scripts\selftest.ps1` sets it, because tests stub transports and must
not be answered by a probe from an earlier run.

**Memory is the binding constraint, not CPU.** With the browser sidecar's
orphans cleaned up (125 processes, 731 MB — see below) the machine still ran
out of RAM, and Ollama's own timings showed **0.21 prompt tokens/second and
0.46 generation tokens/second** for a 1.5B model that does 20–60 tok/s on a
normal CPU. Those numbers are the signature of a host that is paging, not of a
slow prompt. `num_ctx` therefore defaults to 2048 (from Ollama's 4096) and
`keep_alive` to `30m`: every doubling of the KV window costs memory whether or
not a prompt is that long, and a chat turn pauses while the user reads and
types, which is charged entirely to the next answer.

**The browser sidecar leaked its browser.** Sixteen start/stop cycles of
`scripts\browser-serve.ps1` left **125 Chrome processes and 731 MB resident**,
plus one random profile directory per launch. Two independent causes:

1. `browser-serve.ps1 -Stop` used `Stop-Process -Force`, which on Windows calls
   `TerminateProcess` — no signal handler, no `finally`, no `atexit`. The
   sidecar's own cleanup block ran only on Ctrl+C, which is not how it was
   being stopped.
2. `stop()` was never reached, and even had it been, the profile directory was
   left behind.

Both are fixed and the fix is verified by measurement, not inspection: a full
start → search → stop cycle now goes 0 → 9 → **0** Chrome processes and leaves
**0** directories behind. `POST /shutdown` closes the session before exiting,
`browser-serve.ps1 -Stop` calls it and waits before considering a force-kill,
and a startup reaper terminates any browser still holding *Alan's own* profile
path — matched on that path, so it can never reach the operator's own browser.
The reclamation depends on the profile path being stable, which is what the
section above is about.

**Search engines are not interchangeable.** Measured from this machine:

| Engine | Result |
|---|---|
| DuckDuckGo (HTML endpoint) | Captcha wall: *"Unfortunately, bots use DuckDuckGo too."* |
| Mojeek | `403 Forbidden` — blocks automated traffic |
| Brave, Startpage, searx.be | Anti-bot challenges or empty result sets |
| Marginalia | Real, relevant results — but frequently empty on first fetch, so one retry is required |
| Bing | Real results with snippets, but sometimes empty and sometimes **cached results for an unrelated query** |

`DEFAULT_ENGINE` is therefore `marginalia` with `bing` as fallback, both kept
registered. The Bing case is the reason for `_is_relevant()`: a search page
arrived reporting the correct URL and title while its result rows belonged to a
completely different query. Any result with zero lexical overlap with the query
is discarded rather than returned, on the same principle that governs memory
recall — a zero-overlap "memory" is not a memory, and a zero-overlap "result" is
not evidence.

**The engine default must be empty on the Alan side.** `SidecarBrowser.search()`
defaults to `engine=""`, meaning "choose for me", rather than naming an engine.
This was a real bug, not a theoretical one: with `engine="bing"` as the client
default, Marginalia was never tried at all, the fallback chain was dead code, and
a query that Marginalia could serve failed whenever Bing could not. The demo
that exposed it reported `Tried: bing` — one engine, not a chain. Which engine
works is a property of the network, which only the sidecar can observe, so the
default belongs there. A test now pins the client default to `""`, and a second
one pins that an explicitly named engine is still passed through as an override.
Observed working chain: `marginalia` (2 attempts, self-links only, 0 relevant) →
`bing` (7 results, 7 relevant).

**cognee is slower than the JSON store, by a much smaller margin than it first
appeared.** Measured with `scripts\measure_cognee.py`, on an already-warm
sidecar, three writes of an L3 project fact each:

| Operation | cognee | JSON store |
|---|---|---|
| `remember` (warm) | **1.3–1.5 s** | **0.002–0.02 s** |
| `search` | 0.7–2.0 s | — |
| ratio | ~120–140× slower | — |

The important qualifier is that cognee's cost is overwhelmingly **one-off
startup**, not per-write:

| Stage | Time |
|---|---|
| First-ever write (extractor models not yet loaded) | ~559 s |
| First write after a sidecar restart (models on disk) | ~35 s cold, ~14 s warm |
| Steady state | ~1.4 s warm, no measurable cold penalty |

An earlier version of this file recorded only the 559 s figure and called the
backend's suitability unresolved. That was the wrong conclusion drawn from the
worst possible sample: it measured model download and process warm-up, then
attributed it to writing. Measured properly, a warm write costs about 1.4 s,
which is entirely acceptable for a background memory operation and is the reason
cognee remains the default rather than the JSON fallback. What cognee buys for
those 1.4 s is semantic recall — the probe recalled items by meaning rather than
keyword.

Cognee's *startup* remains slow (3–4 min: relational migrations plus GLiNER and
fastembed loading), which is why `scripts\cognee-serve.ps1` waits up to 600 s for
`/health` and distinguishes a crashed process (`exit 1`) from one still starting
(`exit 2`). An earlier version waited 60 s and reported failure for a process
that was perfectly healthy.

## Existing runtime (unchanged)

| Component | Version | Notes |
|---|---|---|
| CPython (embeddable) | 3.12.10 | Windows, `python312._pth` isolated mode, exFAT-safe |
| cognee sidecar | 1.6.2 | separate env `env\cognee\python`, torch 2.14.1+cpu, port 8012 |
| cognee model cache | — | ~811 MB under `data\cognee\cache` (GLiNER + fastembed `BAAI/bge-small-en-v1.5`) |
| browser sidecar | browser-use 0.13.10 | separate env `env\browser\python`, port 8022, drives host Chrome/Edge over CDP |
| bundled Ollama | — | port 11435, models in `models/` |
| primary local model | `qwen2.5:1.5b` | |

## Ports

Chosen to be distinct from anything a developer machine is likely to be running,
because a collision with a host service is indistinguishable from a bug in
Alan.

| Port | Service |
|---|---|
| `8012` | cognee sidecar |
| `8022` | browser sidecar |
| `11435` | Alan's bundled Ollama (`OLLAMA_HOST=127.0.0.1:11435`) |
| `11434` | *not* Alan's — a host Ollama default that must not be touched |

## Portability constraints this manifest must respect

1. **Python only.** No Node/Bun/Rust runtime is shipped.
2. **One folder.** Every path resolves from `ALAN_HOME`; no host installs.
3. **exFAT.** No symlinks, no POSIX permissions, single-file clusters are 128 KB
   — new dependencies are kept small and few.
4. **Lean main env.** Heavy or conflicting stacks become isolated sidecars.
5. **Keyless by default.** No integration may require a cloud key to function.
