# ECC integration

What Alan takes from [ECC](https://github.com/affaan-m/ECC) (pinned at
`ef648e01`, MIT, cloned to `vendor\ECC`), how it is wired, and what was
deliberately left behind. Companion to the manifest in
[dependencies.md](dependencies.md).

## Shape: a read-only library behind the ordinary gate

ECC ships three families of local markdown — `skills/<name>/SKILL.md`,
`commands/<name>.md`, `agents/<name>.md` — eight TypeScript tools, and a set
of hooks. Alan's integration takes the first as **data**, ports the second as
**advisory Python**, and refuses the third entirely.

```
vendor\ECC (or ~/.config/opencode)
   │  frontmatter + body, parsed with a stdlib subset (no PyYAML)
   ▼
EccCatalog ──┬─ search / match_explicit / match_implicit ── HeuristicPlanner
             │                                            (intent: use_skill,
             │                                             step: load_skill)
             └─ SkillTools (skill.list/search/read)  ┐
             └─ EccDevTools  (ecc.* × 8)             ┴── ToolRegistry → gate
```

Every path ends at `ToolRegistry.invoke`, which checks the permission gate
before anything runs. There is no second door: the catalog feeds a *plan
step*, and the plan step executes a *tool*, and the tool is `SAFE` and
rule-governed exactly like `filesystem.read` (ADR-006). An operator rule of
`{capability = "skill.*", effect = "block"}` disables the whole integration
without touching code, proven by a test.

## How a skill reaches the model

Alan's model layer has no function-calling: tools run as planner-driven plan
steps before a single final completion, and observations land in the system
prompt. Skill use therefore has exactly one shape:

1. **Match** (deterministic, `EccCatalog`):
   - *Explicit*: a leading `/name`; a hyphenated name appearing verbatim
     (`tdd-workflow`); or any name framed by an invocation word
     ("the council skill", "skill called report"). Checked after the
     memory and schedule intents so "note that I always follow the X skill"
     stays a memory write, but before coding/chat so an explicitly named
     entry always wins.
   - *Implicit*: every word of a **multi-word** name appears as its own word
     in a request that would otherwise be plain chat ("what is the tdd
     workflow here?"). Single-word names never match implicitly — a skill
     called `report` must not fire on "report on the budget". Runs last,
     before the chat fallback, so it can never hijack an intent that had
     work of its own.
2. **Plan**: `intent="use_skill"`, steps `[load_skill, answer]`. The
   `ModelPlanner` guard refuses to reclassify the intent (same treatment as
   `schedule*`), because relabelling would hide `skill.read` from the tool
   catalog the step runs against.
3. **Execute**: `_CAPABILITY_HANDLERS["load_skill"] = "skill.read"` —
   an explicit mapping, so an unmapped action stays a visible bug.
4. **Cap**: `skill.read` truncates at `[ecc] max_chars` (default 4000
   chars) with a pointer to `alan skills --read <name>`; the local model's
   whole context is 2048 tokens, and a 400-line skill would evict the
   question.

`requires_capability` counts `load_skill`, so `alan ask` escalates a
skill-named request to the full pipeline instead of answering it
conversationally with a promise it never loaded anything.

## The eight dev tools — advisory, on purpose

| Tool | Does | Does not |
|---|---|---|
| `ecc.run_tests` | detects framework (pytest/jest/vitest/cargo/go), prints the command | run tests |
| `ecc.lint_check` | detects ruff/biome/eslint/pylint/…, prints the check (and `--fix` only when asked) | fix anything |
| `ecc.format_code` | detects black/prettier/biome/gofmt/rustfmt, prints the write command | format anything |
| `ecc.check_coverage` | reads `coverage-summary.json` / `lcov.info` / `coverage.xml`, compares to threshold | generate coverage |
| `ecc.dependency_analyzer` | reads `package.json` / `requirements.txt`: counts, exact pins, lockfile status | install or update |
| `ecc.security_audit` | local pattern scan for hardcoded secrets and risky patterns | claim to replace `npm audit` |
| `ecc.git_summary` | branch, status, log, `--stat` diffs via argv list + validated ref | write, fetch, or shell out on a string |
| `ecc.changed_files` | groups `git status --porcelain` into a tree | stage or commit |

ECC's TypeScript originals *execute* (`npm audit fix`, formatter writes).
Alan keeps the knowledge and drops the execution: the permission gate must
stay the single path to side effects, and an advisory that prints the command
gives the operator the same decision at the same moment, visibly.

## What was deliberately not ported

- **Hooks — none, ever.** ECC's "Permission Auto-Approve" hook would approve
  capabilities without a human. It has no path into Alan; the gate has no
  auto-approve mode for this integration (ADR-006).
- **Subagent-per-skill.** Registering 293 agents would flood `available()`
  and violate least privilege; agents stay as catalog entries (`agent:…`
  refs) readable like any other entry.
- **Fuzzy description matching.** The planner only matches names
  deterministically; description overlap is for `skill.search`, not for
  deciding what a request means.

## Configuration and CLI

```toml
[ecc]
enabled = true       # catalog + skill.* tools + planner matching
devtools = true      # also register the eight ecc.* tools
root = ""            # auto: vendor\ECC, then ~/.config/opencode
max_chars = 4000     # skill text cap per run (clamped 500..20000)
```

Code defaults are all-off; this install's `settings.toml` turns them on.
`register_tools=False` surfaces (one-shot helpers) build **no catalog at
all**, so the planner can never name an entry the registry cannot load.

```
alan skills                    # counts + names
alan skills tdd workflow       # ranked search table
alan skills --kind command     # one family
alan skills --read council     # one entry, in full
alan doctor                    # "ecc: 293 skills, 94 commands, ..."
```

`alan skills` builds the catalog directly and needs no model provider.

## Maintenance

- ECC is pinned at `ef648e01`. `vendor\` is git-ignored (the clone is 55 MB
  with its assets; only the markdown is read), so a fresh checkout restores it
  with:

  ```
  git clone https://github.com/affaan-m/ECC vendor/ECC
  git -C vendor/ECC checkout ef648e01
  npm --prefix vendor/ECC install --ignore-scripts   # only for ECC's own tooling
  ```

  Until then `alan doctor` reports `enabled but no ECC found` with the same
  instruction, and the `~/.config/opencode` install (ECC's official OpenCode
  adapter) remains a second source. Update the pin by re-cloning, then
  re-check `alan skills` + `pytest tests/test_ecc.py`; the loader tolerates
  unknown frontmatter (unparseable lines are skipped, not raised) so a new
  ECC revision degrades per-file rather than fatally.
- Tests: `tests/test_ecc.py` (60 tests) — frontmatter shapes, matching
  positives *and* negatives, the gate path, the block rule, ref validation,
  runtime wiring for all three switches.
