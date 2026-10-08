"""The ECC integration: catalog, gated tools, planner wiring, settings.

What is pinned here, in behaviour terms:

* frontmatter parses with no PyYAML and survives the shapes ECC ships --
  quoted values containing colons, block scalars, nested ``metadata``;
* matching is deterministic: explicit invocation (verbatim hyphenated name,
  ``<name> skill``, leading ``/name``) and, for multi-word names only,
  all-tokens overlap on otherwise-plain chat -- a lone common word never
  matches, and memory/schedule intents keep precedence;
* every tool reaches execution through the registry, so the permission gate
  sees it; an operator rule on ``skill.*`` blocks the namespace outright
  (ADR-006 gains no new door);
* ``skill.read`` caps injected content, because the local model's whole
  context is smaller than the larger skills;
* the eight ``ecc.*`` tools advise: they detect and print commands, they
  never execute, and git goes through an argv list with a validated ref;
* catalog and tools switch on and off together -- ``register_tools=False``
  never leaves a planner naming entries the registry cannot load;
* disabled in code, enabled in this install's settings.toml.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from alan.config import EccSettings, Settings, _apply_tables
from alan.core.orchestrator import _CAPABILITY_HANDLERS, _allowed_for
from alan.core.planner import HeuristicPlanner, ModelPlanner
from alan.ecc import EccCatalog, EccDevTools, Skill, SkillTools, find_ecc_root
from alan.ecc.catalog import parse_frontmatter
from alan.ecc.devtools import _safe_ref
from alan.core.events import EventBus, Events
from alan.interfaces.types import PermissionDecision, RiskLevel
from alan.permissions.gate import default_gate
from alan.permissions.rules import parse_rules
from alan.tools.registry import ToolRegistry

# -- fixtures ---------------------------------------------------------------

SKILL_MD = """\
---
name: tdd-workflow
description: "Test-driven development: write a failing test first. Note the colon: it stays inside the quotes."
argument-hint: <path/to/*.plan.md>
metadata:
  origin: ECC
---

# TDD

Write the test first. Then make it pass.
"""

PLAIN_SKILL_MD = """\
---
name: no-description
---

# Heading that becomes the description

Body text here.
"""

COMMAND_MD = """\
---
description: Code review — local uncommitted changes (an em dash and no name field).
argument-hint: [pr-number | blank]
---

# Code Review

Checklist follows.
"""

AGENT_MD = """\
---
name: architect
description: Software architecture specialist.
tools: Read, Grep
model: opus
---

You are the architect.
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_root() -> Path:
    """A miniature ECC tree: one entry per family, plus junk to be ignored."""
    root = Path(tempfile.mkdtemp(prefix="ecc-fixture-"))
    _write(root / "skills" / "tdd-workflow" / "SKILL.md", SKILL_MD)
    _write(root / "skills" / "no-description" / "SKILL.md", PLAIN_SKILL_MD)
    _write(root / "skills" / "security-review" / "SKILL.md",
           "---\nname: security-review\ndescription: "
           "Use this skill when handling user input or secrets.\n---\n\n"
           "# Security\n\nChecklist.\n")
    _write(root / "skills" / "loose.md", "not inside a skill dir")
    _write(root / "commands" / "code-review.md", COMMAND_MD)
    _write(root / "agents" / "architect.md", AGENT_MD)
    return root


def _catalog() -> EccCatalog:
    """Catalog built in memory -- matching tests need no disk at all."""
    entries = [
        Skill("skill", "tdd-workflow", "Test-driven development workflow.",
              "# TDD\n" + "content line\n" * 60, Path("skills/tdd-workflow/SKILL.md")),
        Skill("skill", "security-review", "Security checklist for input handling.",
              "# Security\ncontent", Path("skills/security-review/SKILL.md")),
        Skill("skill", "council", "Four-voice decision council.",
              "# Council\ncontent", Path("skills/council/SKILL.md")),
        Skill("skill", "seo", "Search engine optimisation audit.",
              "# SEO\ncontent", Path("skills/seo/SKILL.md")),
        Skill("command", "code-review", "Review local changes.",
              "# Review\ncontent", Path("commands/code-review.md")),
    ]
    return EccCatalog(Path("."), entries)


# -- frontmatter ------------------------------------------------------------

class TestFrontmatter(unittest.TestCase):
    def test_quoted_value_keeps_its_colon(self) -> None:
        fields, body = parse_frontmatter(SKILL_MD)
        self.assertIn("colon: it stays", fields["description"])
        self.assertEqual(fields["name"], "tdd-workflow")
        self.assertEqual(fields["argument-hint"], "<path/to/*.plan.md>")
        self.assertIn("# TDD", body)

    def test_nested_metadata_survives_without_yaml(self) -> None:
        fields, _ = parse_frontmatter(SKILL_MD)
        self.assertEqual(fields["metadata"], {"origin": "ECC"})

    def test_unquoted_value_without_name_field(self) -> None:
        fields, _ = parse_frontmatter(COMMAND_MD)
        self.assertNotIn("name", fields)
        self.assertTrue(fields["description"].startswith("Code review"))

    def test_block_scalar(self) -> None:
        fields, _ = parse_frontmatter(
            "---\ndescription: >\n  folded over\n  two lines\n---\nbody\n"
        )
        self.assertEqual(fields["description"], "folded over\ntwo lines")

    def test_quote_left_open_by_a_wrapped_line_joins(self) -> None:
        fields, _ = parse_frontmatter(
            '---\ndescription: "wrapped first line\ncontinues on the second"\n---\n'
        )
        self.assertIn("continues on the second", fields["description"])
        self.assertNotIn('"', fields["description"])

    def test_no_fence_means_no_frontmatter(self) -> None:
        fields, body = parse_frontmatter("# just markdown\n")
        self.assertEqual(fields, {})
        self.assertEqual(body, "# just markdown\n")

    def test_unclosed_fence_is_not_frontmatter(self) -> None:
        fields, _ = parse_frontmatter("---\nname: stuck\n")
        self.assertEqual(fields, {})


# -- catalog loading ---------------------------------------------------------

class TestCatalogLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _fixture_root()
        self.catalog = EccCatalog.load(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_counts_one_per_family_plus_ignored_junk(self) -> None:
        # loose.md sits directly in skills/ and must not be counted.
        self.assertEqual(
            self.catalog.counts(), {"skill": 3, "command": 1, "agent": 1}
        )

    def test_name_from_frontmatter_directory_or_stem(self) -> None:
        skill = self.catalog.get("skill", "tdd-workflow")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.metadata.get("origin"), "ECC")
        # No name field -> directory / stem name.
        self.assertIsNotNone(self.catalog.get("command", "code-review"))
        self.assertIsNotNone(self.catalog.get("agent", "architect"))

    def test_description_falls_back_to_the_first_line(self) -> None:
        entry = self.catalog.get("skill", "no-description")
        self.assertEqual(entry.description, "Heading that becomes the description")

    def test_lookup_is_case_insensitive_and_kind_preferring(self) -> None:
        self.assertEqual(self.catalog.get("SKILL", "TDD-Workflow").name,
                         "tdd-workflow")
        # find() with a bare name walks skill -> command -> agent.
        self.assertEqual(self.catalog.find("code-review").kind, "command")
        self.assertEqual(self.catalog.find("command:code-review").kind, "command")
        self.assertIsNone(self.catalog.find("nothing-here"))

    def test_find_ecc_root_prefers_the_explicit_setting(self) -> None:
        with mock.patch("alan.ecc.paths.HOME", self.root):
            found = find_ecc_root(str(self.root))
        self.assertEqual(found, self.root)


# -- search ------------------------------------------------------------------

class TestSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()

    def test_ranks_name_hits_over_description_hits(self) -> None:
        hits = self.catalog.search("security review")
        self.assertEqual(hits[0].name, "security-review")

    def test_kind_filter_and_limit(self) -> None:
        hits = self.catalog.search("review", kind="command", limit=5)
        self.assertTrue(hits)
        self.assertTrue(all(h.kind == "command" for h in hits))

    def test_no_terms_and_no_hits_are_empty_not_errors(self) -> None:
        self.assertEqual(self.catalog.search(""), [])
        self.assertEqual(self.catalog.search("zzz-unknown-term"), [])


# -- planner matching ---------------------------------------------------------

class TestMatching(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()

    def test_explicit_verbatim_hyphenated_name(self) -> None:
        hit = self.catalog.match_explicit("use the tdd-workflow skill please")
        self.assertEqual(hit.name, "tdd-workflow")

    def test_explicit_framed_space_form(self) -> None:
        hit = self.catalog.match_explicit("follow the security review skill")
        self.assertEqual(hit.name, "security-review")

    def test_explicit_single_word_needs_framing(self) -> None:
        self.assertEqual(
            self.catalog.match_explicit("run the council skill").name, "council"
        )
        # Bare word, no framing: not an invocation.
        self.assertIsNone(self.catalog.match_explicit("i went to the council meet"))

    def test_explicit_leading_slash(self) -> None:
        self.assertEqual(
            self.catalog.match_explicit("/code-review").name, "code-review"
        )
        # A path is not a command.
        self.assertIsNone(self.catalog.match_explicit("/etc/hosts is a file"))

    def test_implicit_requires_every_word_of_a_multiword_name(self) -> None:
        self.assertEqual(
            self.catalog.match_implicit("what is the tdd workflow here?").name,
            "tdd-workflow",
        )
        self.assertIsNone(self.catalog.match_implicit("review this email for me"))
        # Single-word names never match implicitly.
        self.assertIsNone(self.catalog.match_implicit("tell me about the council"))
        self.assertIsNone(self.catalog.match_implicit("hello there"))

    def test_implicit_prefers_the_longer_overlap(self) -> None:
        hit = self.catalog.match_implicit(
            "should we do a security review and also a code review?"
        )
        self.assertIsNotNone(hit)
        self.assertIn(hit.name, ("security-review", "code-review"))


# -- gated skill tools --------------------------------------------------------

class TestSkillTools(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.registry = ToolRegistry(
            default_gate(RiskLevel.MEDIUM_RISK), bus=self.bus
        )
        self.registry.register_provider(SkillTools(_catalog(), max_chars=500))

    def tearDown(self) -> None:
        self.registry.close()

    def test_read_goes_through_the_gate_and_says_so_in_events(self) -> None:
        result = self.registry.invoke(
            "skill.read", {"kind": "skill", "name": "tdd-workflow"}
        )
        self.assertTrue(result.ok)
        self.assertIn("TDD", str(result.output))
        names = [event.name for event in self.bus.history()]
        self.assertIn(Events.PERMISSION_REQUESTED, names)
        self.assertIn(Events.PERMISSION_GRANTED, names)

    def test_read_caps_content_and_points_at_the_full_text(self) -> None:
        result = self.registry.invoke(
            "skill.read", {"kind": "skill", "name": "tdd-workflow"}
        )
        self.assertTrue(result.ok)
        output = str(result.output)
        # The tool floors max_chars at 500; content is longer than that.
        self.assertLessEqual(len(output), 500 + 200)  # cap plus header/notice
        self.assertIn("alan skills --read", output)

    def test_read_of_an_unknown_name_suggests_close_matches(self) -> None:
        result = self.registry.invoke(
            "skill.read", {"kind": "skill", "name": "tdd-wrkflow"}
        )
        self.assertTrue(result.ok)  # a miss is a value, not an exception
        self.assertIn("no ECC entry", str(result.output))

    def test_search_and_list_are_readable_text(self) -> None:
        found = self.registry.invoke("skill.search", {"query": "security"})
        self.assertTrue(found.ok)
        self.assertIn("skill:security-review", str(found.output))
        listing = self.registry.invoke("skill.list", {"kind": "skill"})
        self.assertTrue(listing.ok)
        self.assertIn("tdd-workflow", str(listing.output))

    def test_an_operator_rule_can_block_the_whole_namespace(self) -> None:
        blocked = ToolRegistry(
            default_gate(
                RiskLevel.PRIVILEGED,
                parse_rules([{"capability": "skill.*", "effect": "block"}]),
            ),
            bus=EventBus(),
        )
        try:
            blocked.register_provider(SkillTools(_catalog()))
            result = blocked.invoke(
                "skill.read", {"kind": "skill", "name": "council"}
            )
            self.assertFalse(result.ok)
            self.assertEqual(
                blocked.gate.decisions[-1], PermissionDecision.DENIED
            )
        finally:
            blocked.close()


# -- advisory dev tools --------------------------------------------------------

class TestDevTools(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ecc-dev-"))
        self.tools = EccDevTools()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_tests_detects_python_and_only_prints_a_command(self) -> None:
        _write(self.root / "pyproject.toml", "[project]\nname = 'x'\n")
        out = self.tools.run_tests(cwd=str(self.root))
        self.assertIn("pytest", out)
        self.assertIn("rather than running it", out)

    def test_run_tests_detects_js_with_coverage_flag(self) -> None:
        _write(self.root / "package.json",
               json.dumps({"devDependencies": {"jest": "^29"}}))
        out = self.tools.run_tests(coverage=True, cwd=str(self.root))
        self.assertIn("npm test", out)
        self.assertIn("--coverage", out)

    def test_run_tests_on_an_empty_directory_explains_the_fix(self) -> None:
        out = self.tools.run_tests(cwd=str(self.root))
        self.assertIn("no test framework detected", out)

    def test_lint_and_format_detect_the_toolchain(self) -> None:
        _write(self.root / "pyproject.toml", "[tool.black]\nline-length = 88\n")
        self.assertIn("black", self.tools.format_code(cwd=str(self.root)))
        lint = self.tools.lint_check(cwd=str(self.root))
        self.assertIn("no linter detected", lint)  # black is not a linter
        _write(self.root / ".ruff.toml", "")
        out = self.tools.lint_check(cwd=str(self.root))
        self.assertIn("ruff check", out)

    def test_format_fix_flag_is_printed_not_run(self) -> None:
        _write(self.root / ".ruff.toml", "")
        out = self.tools.format_code(file_path="a.py", cwd=str(self.root))
        self.assertIn("NOT executed", out)

    def test_coverage_summary_json_against_threshold(self) -> None:
        _write(
            self.root / "coverage" / "coverage-summary.json",
            json.dumps({
                "total": {"lines": {"pct": 91.5}},
                "src/a.py": {"lines": {"pct": 100.0}},
                "src/b.py": {"lines": {"pct": 40.0}},
            }),
        )
        passing = self.tools.check_coverage(threshold=80, cwd=str(self.root))
        self.assertIn("PASS", passing)
        self.assertIn("src/b.py", passing)
        failing = self.tools.check_coverage(threshold=95, cwd=str(self.root))
        self.assertIn("FAIL", failing)

    def test_coverage_without_a_report_names_how_to_make_one(self) -> None:
        out = self.tools.check_coverage(cwd=str(self.root))
        self.assertIn("no coverage report found", out)

    def test_dependency_analyzer_flags_pins_and_missing_lockfile(self) -> None:
        _write(self.root / "package.json", json.dumps({
            "dependencies": {"left-pad": "1.3.0", "react": "^18.0.0"},
        }))
        out = self.tools.dependency_analyzer(cwd=str(self.root))
        self.assertIn("exact pins (1)", out)
        self.assertIn("MISSING", out)
        self.assertIn("audit", out)  # the network command is only suggested

    def test_security_audit_finds_a_planted_secret(self) -> None:
        _write(self.root / "config.py",
               'AWS_KEY = "AKIA" + "EXAMPLE" + "1234567890ABCD"\n')
        # The concatenation hides nothing from a real key; plant a literal one.
        _write(self.root / "settings.py", 'TOKEN = "AKIAABCDEFGHIJKLMNOP"\n')
        out = self.tools.security_audit(type="secrets", cwd=str(self.root))
        self.assertIn("AWS access key id", out)
        self.assertIn("secrets:", out)

    def test_security_audit_type_filter_and_clean_tree(self) -> None:
        _write(self.root / "main.py", "print('hello')\n")
        clean = self.tools.security_audit(cwd=str(self.root))
        self.assertIn("no findings", clean)
        _write(self.root / "risky.py", "subprocess.run(cmd, shell=True)\n")
        only_code = self.tools.security_audit(type="code", cwd=str(self.root))
        self.assertIn("shell=True", only_code)
        only_secrets = self.tools.security_audit(type="secrets", cwd=str(self.root))
        self.assertIn("no secrets", only_secrets)

    def test_dependencies_type_is_the_advisory_not_a_scanner(self) -> None:
        _write(self.root / "package.json", json.dumps({"dependencies": {}}))
        out = self.tools.security_audit(type="dependencies", cwd=str(self.root))
        self.assertIn("package.json", out)

    def test_bad_audit_type_is_refused(self) -> None:
        out = self.tools.security_audit(type="everything", cwd=str(self.root))
        self.assertIn("type must be one of", out)

    def test_safe_ref_rejects_anything_craftable(self) -> None:
        self.assertTrue(_safe_ref("main"))
        self.assertTrue(_safe_ref("release/1.2"))
        self.assertTrue(_safe_ref("v1.0.0"))
        self.assertFalse(_safe_ref("--upload-pack=touch"))
        self.assertFalse(_safe_ref("-x"))
        self.assertFalse(_safe_ref("a b"))
        self.assertFalse(_safe_ref("a;rm -rf"))
        self.assertFalse(_safe_ref(""))

    def test_git_tools_on_a_real_repository(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        subprocess.run(
            ["git", "init"], cwd=self.root, capture_output=True, check=False
        )
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "--allow-empty", "-m", "first"],
            cwd=self.root, capture_output=True, check=False,
        )
        summary = self.tools.git_summary(depth=2, cwd=str(self.root))
        self.assertIn("branch:", summary)
        self.assertIn("log (last 2):", summary)

        _write(self.root / "new.txt", "hi\n")
        changed = self.tools.changed_files(cwd=str(self.root))
        self.assertIn("untracked", changed)
        self.assertIn("new.txt", changed)

    def test_git_summary_rejects_a_crafted_base_ref(self) -> None:
        out = self.tools.git_summary(
            base_ref="--upload-pack=touch", cwd=str(self.root)
        )
        self.assertIn("rejected base_ref", out)

    def test_git_on_a_non_repository_says_so(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        out = self.tools.git_summary(cwd=str(self.root))
        self.assertIn("not a git repository", out)


# -- planner wiring ------------------------------------------------------------

class TestPlannerSkillPlans(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = HeuristicPlanner(skills=_catalog())

    def test_explicit_invocation_becomes_a_load_then_answer(self) -> None:
        plan = self.planner.plan("use the tdd-workflow skill to add a feature")
        self.assertEqual(plan.intent, "use_skill")
        self.assertEqual(plan.actions(), ["load_skill", "answer"])
        step = plan.steps[0]
        self.assertEqual(step.arguments["kind"], "skill")
        self.assertEqual(step.arguments["name"], "tdd-workflow")
        self.assertEqual(step.risk, RiskLevel.SAFE)

    def test_explicit_beats_the_coding_intent(self) -> None:
        # "review" alone would be chat or coding; naming the entry must win.
        plan = self.planner.plan("run /code-review for me")
        self.assertEqual(plan.intent, "use_skill")

    def test_implicit_multiword_match_only_on_plain_chat(self) -> None:
        plan = self.planner.plan("what is the tdd workflow here?")
        self.assertEqual(plan.intent, "use_skill")
        self.assertIn("overlaps", plan.rationale)

    def test_memory_verb_keeps_precedence_over_skill_loading(self) -> None:
        plan = self.planner.plan(
            "note that I always follow the security-review skill"
        )
        self.assertEqual(plan.intent, "memory")

    def test_no_match_stays_chat(self) -> None:
        self.assertEqual(self.planner.plan("hello there").intent, "chat")

    def test_without_a_catalog_the_planner_is_unchanged(self) -> None:
        bare = HeuristicPlanner()
        self.assertIsNone(bare.skills)
        # What used to be a plain chat request is still a plain chat request.
        self.assertEqual(bare.plan("use the council skill").intent, "chat")

    def test_model_planner_cannot_stomp_the_skill_intent(self) -> None:
        model = SimpleNamespace(chat=lambda messages, system=None: "chat")
        planner = ModelPlanner(model, fallback=self.planner)
        plan = planner.plan("use the council skill")
        self.assertEqual(plan.intent, "use_skill")

    def test_the_capability_mapping_and_catalog_are_wired(self) -> None:
        self.assertEqual(_CAPABILITY_HANDLERS["load_skill"], "skill.read")
        plan = self.planner.plan("use the council skill")
        self.assertEqual(
            _allowed_for(plan), ["skill.read", "skill.search", "skill.list"]
        )


# -- settings -------------------------------------------------------------------

class TestEccSettings(unittest.TestCase):
    def test_defaults_are_off(self) -> None:
        settings = Settings().ecc
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.devtools)
        self.assertEqual(settings.root, "")
        self.assertEqual(settings.max_chars, 4000)

    def test_table_is_parsed_and_clamped(self) -> None:
        settings = Settings()
        _apply_tables(settings, {
            "ecc": {
                "enabled": "true",
                "devtools": "false",
                "root": "/somewhere/ECC",
                "max_chars": 10,
            }
        })
        self.assertTrue(settings.ecc.enabled)
        self.assertFalse(settings.ecc.devtools)
        self.assertEqual(settings.ecc.root, "/somewhere/ECC")
        self.assertEqual(settings.ecc.max_chars, 500)  # clamped up from a silly 10

    def test_absent_table_leaves_the_defaults_alone(self) -> None:
        settings = Settings()
        _apply_tables(settings, {})
        self.assertFalse(settings.ecc.enabled)

    def test_non_boolean_is_rejected_visibly(self) -> None:
        with self.assertRaises(ValueError):
            _apply_tables(Settings(), {"ecc": {"enabled": "maybe"}})


# -- runtime wiring -------------------------------------------------------------

class TestRuntimeWiring(unittest.TestCase):
    def setUp(self) -> None:
        from alan.runtime import build_runtime

        self.root = _fixture_root()
        self.settings = Settings()
        self.settings.ecc = EccSettings(
            enabled=True, devtools=True, root=str(self.root)
        )
        self.build_runtime = build_runtime

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _tools(self, runtime) -> list[str]:
        return [spec.name for spec in runtime.tools()]

    def test_enabled_registers_both_families_and_counts_them(self) -> None:
        runtime = self.build_runtime(self.settings)
        try:
            names = self._tools(runtime)
            for expected in ("skill.read", "skill.search", "skill.list",
                             "ecc.git_summary", "ecc.security_audit"):
                self.assertIn(expected, names)
            note = runtime.status()["ecc"]
            self.assertIn("3 skills", note)
            self.assertIn("8 dev tools", note)
            self.assertIsNotNone(runtime.orchestrator.planner.skills)
        finally:
            runtime.close()

    def test_disabled_leaves_no_trace(self) -> None:
        self.settings.ecc.enabled = False
        runtime = self.build_runtime(self.settings)
        try:
            names = self._tools(runtime)
            self.assertFalse(any(n.startswith("skill.") for n in names))
            self.assertFalse(any(n.startswith("ecc.") for n in names))
            self.assertEqual(runtime.status()["ecc"], "disabled")
            plan = runtime.orchestrator.planner.plan("use the council skill")
            self.assertEqual(plan.intent, "chat")
        finally:
            runtime.close()

    def test_devtools_off_keeps_skills_only(self) -> None:
        self.settings.ecc.devtools = False
        runtime = self.build_runtime(self.settings)
        try:
            names = self._tools(runtime)
            self.assertIn("skill.read", names)
            self.assertFalse(any(n.startswith("ecc.") for n in names))
        finally:
            runtime.close()

    def test_register_tools_false_keeps_the_contract_whole(self) -> None:
        runtime = self.build_runtime(self.settings, register_tools=False)
        try:
            self.assertIsNone(runtime.orchestrator.planner.skills)
            self.assertEqual(runtime.status()["ecc"], "not registered")
            # A skill-shaped request degrades to chat rather than planning a
            # step no registry can execute.
            plan = runtime.orchestrator.planner.plan("use the council skill")
            self.assertEqual(plan.intent, "chat")
        finally:
            runtime.close()

    def test_missing_root_reports_instead_of_raising(self) -> None:
        self.settings.ecc.root = str(self.root / "nope")
        with mock.patch("alan.runtime.find_ecc_root", return_value=None):
            runtime = self.build_runtime(self.settings)
        try:
            self.assertIn("no ECC found", runtime.status()["ecc"])
            self.assertIsNone(runtime.orchestrator.planner.skills)
        finally:
            runtime.close()

    def test_system_prompt_carries_the_library_hint(self) -> None:
        runtime = self.build_runtime(self.settings)
        try:
            prompt = runtime.orchestrator.config.system_prompt
            self.assertIn("ECC library loaded", prompt)
            self.assertIn("3 skills", prompt)
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
