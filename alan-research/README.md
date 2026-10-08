# alan-research

A research, reference and benchmarking library for Alan. **Not** part of the
Alan product.

> Your AI, your rules.

This directory exists so that Alan can be built from the best *proven*
implementations available, without becoming thirty GitHub projects in a trench
coat. Every repository here is an implementation source. None of them is
architecture. Alan's architecture is in `..\src\alan`, it is Alan-owned, and
nothing in here changes that by existing.

---

## The one rule

```
ACQUIRE → INSPECT → UNDERSTAND → COMPARE → DOCUMENT → DECIDE
```

Building starts **after** the audit, not during it. The failure mode this
workspace is built to prevent is a rewrite: twenty new subsystems arriving at
once, none of them wired, and a working product replaced by a larger unwired
one. Unwired is a bug list, not a reason to rewrite Alan.

Concretely, that means while working here you do **not**:

- copy a repository into `..\src`
- edit a repository to make it fit Alan's interfaces
- `pip install` a research dependency into `..\env`
- enable a cloud service, authenticate anything, or set an API key
- run a repository's install script, build, or test suite

You may read all of it. You may measure it. You may copy a *specific file or
function* into `DECISIONS.md` as evidence — that is research output, not
integration.

---

## Layout

```
alan-research/
├── README.md                 this file
├── AUDIT.md                  the final report: 12 findings + verdict
├── REPOSITORIES.md           per-repository record: URL, commit, licence,
│                             language, purpose, privacy, verdict
├── INTEGRATION-MAP.md         external projects mapped onto Alan's architecture
├── ARCHITECTURE-COMPARISON.md  Alan vs the serious alternatives, axis by axis
├── DECISIONS.md              what was decided, and why — including reversals
├── repos.json                the acquisition manifest (input)
├── clone-results.json        clone outcome + pinned commit per repository
├── repo-facts.json           harvested facts: licence, languages, deps, signals
├── acquire.log               transcript of the clone run
│
├── inference/     llama.cpp, Ollama
├── agents/        pydantic-ai, langgraph, agent-framework, autogen
├── memory/        letta-code, letta, mem0, graphiti, supermemory, memlocal_core
├── knowledge/     lancedb, local-rag
├── browser/       browser-use, open-browser-use
├── computer-use/  OmniParser
├── android/       scrcpy, termux-api, vesta, LocalMind, offline-ai-assistant
├── protocols/     mcp-python-sdk
├── routing/       litellm
├── workflows/     crewAI, prefect, AutoGPT
├── evaluation/    BrowserGym, WindowsAgentArena
├── utilities/     uv
└── scripts/       acquire.py, inspect_repos.py  (Alan's own, safe to run)
```

**30 of 30 cloned, 0 failures, 2.15 GB.** Shallow, one directory each, no
submodules, no monorepo. Each repository is independent: deleting one must not
affect another, and nothing in this tree may affect `..\src`, `..\env` or
`..\tests`.

BrowserGym and WindowsAgentArena sit in `evaluation/` rather than
`computer-use/` because they are benchmarks, and a benchmark that ships as
infrastructure is how a research dependency ends up on the production path.

### Where to start reading

`AUDIT.md` is the summary — twelve findings and the verdict.
`DECISIONS.md` is the honest part: it records three corrections to the brief and
two defects in the audit's own tooling. Read that one before trusting anything
here.

---

## Reproducing

```powershell
# clone (or resume) - shallow, isolated, failure-tolerant
python scripts\acquire.py --timeout 900

# re-clone just one
python scripts\acquire.py --only mem0,graphiti

# harvest licence / language / dependency / privacy facts
python scripts\inspect_repos.py
```

`acquire.py` records a failure as data rather than aborting, because "this
project does not exist under that name" is a finding the audit needs. Both
scripts read only; neither builds, installs or executes third-party code.

Both run on Alan's own interpreter (`..\env\python\python.exe`) and import
nothing outside the standard library, so the research workspace cannot perturb
the product environment.

---

## What counts as a reason to integrate

A repository earns its way into Alan only if it survives all four:

1. **It solves a problem Alan actually has.** Not a problem Alan could imagine.
2. **It does not force a second architecture.** One composition root, one
   request path. A repository that needs its own runtime, its own agent loop or
   its own memory model is a reference, not a dependency.
3. **It is local-first and license-compatible.** No mandatory cloud call, and a
   licence that does not oblige Alan to open-source its own work.
4. **It is provably better on the axis that matters** — measured, not assumed.

Anything short of that is `REFERENCE_ONLY` or `BENCHMARK_ONLY`, which are
successful outcomes. Adopting less is the point.

---

## Safety posture

- Third-party code is read, never executed. Cloning is the only operation.
- No API keys, tokens, cookies, SSH keys or personal documents are exposed to
  any repository here. None of them receives Alan's data.
- No telemetry is enabled. Where a repository ships telemetry, the audit
  records *where the switch is* so it can be confirmed off rather than assumed.
- Cloud-only features are documented, never enabled. See `DECISIONS.md`.
- Repositories with unknown or incompatible licences are candidates for
  `REJECT` regardless of technical merit.
