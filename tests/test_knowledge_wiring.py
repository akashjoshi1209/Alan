"""Tests for the second brain's wiring: planner intents, capability map, runtime.

These assert the seams rather than the engine: that a request aimed at the user's
own corpus becomes a ``kb_search`` plan (and a web request does not), that the
capability map points at the real tool names, and that ``build_runtime`` exposes
gated ``knowledge.*`` tools backed by a working store.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alan.config import Settings
from alan.core.orchestrator import (
    _CAPABILITY_HANDLERS,
    _SOFT_STEPS,
    _allowed_for,
)
from alan.core.planner import HeuristicPlanner
from alan.runtime import build_runtime


class PlannerIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = HeuristicPlanner()

    def test_notes_query_becomes_a_knowledge_search(self) -> None:
        plan = self.planner.plan("search my notes for the pricing decision")
        self.assertEqual(plan.intent, "knowledge_search")
        self.assertEqual(plan.actions(), ["kb_search"])
        self.assertIn("pricing", plan.steps[0].arguments["query"])

    def test_what_do_i_know_becomes_a_knowledge_search(self) -> None:
        plan = self.planner.plan("what do I know about churn")
        self.assertEqual(plan.intent, "knowledge_search")

    def test_ask_my_brain_strips_scaffolding(self) -> None:
        plan = self.planner.plan("ask my brain about onboarding")
        self.assertEqual(plan.intent, "knowledge_search")
        self.assertEqual(plan.steps[0].arguments["query"], "onboarding")

    def test_ingest_with_a_path(self) -> None:
        plan = self.planner.plan("index D:/notes")
        self.assertEqual(plan.intent, "knowledge_ingest")
        self.assertEqual(plan.steps[0].arguments["paths"], ["D:/notes"])

    def test_reindex_sets_the_flag(self) -> None:
        plan = self.planner.plan("reindex D:/notes")
        self.assertEqual(plan.intent, "knowledge_ingest")
        self.assertTrue(plan.steps[0].arguments["reindex"])

    def test_web_search_is_not_hijacked(self) -> None:
        plan = self.planner.plan("search the web for the latest release notes")
        self.assertEqual(plan.intent, "research")

    def test_memory_write_is_not_hijacked(self) -> None:
        plan = self.planner.plan("remember that I prefer dark mode")
        self.assertEqual(plan.intent, "memory")

    def test_opening_an_index_file_is_not_an_ingest(self) -> None:
        plan = self.planner.plan("open docs/index.html")
        self.assertNotEqual(plan.intent, "knowledge_ingest")


class CapabilityMapTests(unittest.TestCase):
    def test_search_and_ingest_map_to_their_tools(self) -> None:
        self.assertEqual(_CAPABILITY_HANDLERS["kb_search"], "knowledge.search")
        self.assertEqual(_CAPABILITY_HANDLERS["kb_ingest"], "knowledge.ingest")

    def test_no_plan_can_reach_a_destructive_or_browsing_knowledge_tool(self) -> None:
        """`knowledge.forget` is CLI-only, and it takes two layers to say so.

        `_allowed_for` builds the *informational* catalogue in the system
        prompt, not an execution gate -- `_execute_step` reads the tool
        straight from `_CAPABILITY_HANDLERS`. So the mapping is the only
        thing between a future planner rule and an erased corpus, and it is
        the layer this pins. Reaching these tools must stay a deliberate act
        (`alan brain forget`), never a sentence someone happened to type.
        """
        self.assertNotIn("kb_list", _CAPABILITY_HANDLERS)
        self.assertNotIn("kb_forget", _CAPABILITY_HANDLERS)

        planner = HeuristicPlanner()
        for request in (
            "forget my notes about pricing",
            "delete my notes",
            "remove everything from my second brain",
            "list my notes",
            "show my notes",
        ):
            plan = planner.plan(request)
            actions = [step.action for step in plan.steps]
            with self.subTest(request=request, intent=plan.intent):
                self.assertNotIn("kb_forget", actions)
                self.assertNotIn("kb_list", actions)
                self.assertNotIn("knowledge.forget", _allowed_for(plan))

    def test_unmapped_knowledge_actions_fail_visibly_instead_of_running(self) -> None:
        """The design contract the removal rests on.

        `_execute_step` reports an action with no mapping rather than no-op'ing
        it, so a planner rule that did start emitting `kb_forget` stops with an
        explanation instead of quietly invoking the tool.
        """
        self.assertNotIn("kb_forget", _SOFT_STEPS)

    def test_allowed_tools_are_scoped_to_the_intent(self) -> None:
        search_plan = HeuristicPlanner().plan("search my notes for x")
        self.assertEqual(_allowed_for(search_plan), ["knowledge.search"])
        ingest_plan = HeuristicPlanner().plan("index D:/notes")
        self.assertEqual(_allowed_for(ingest_plan), ["knowledge.ingest"])


class RuntimeWiringTests(unittest.TestCase):
    def test_knowledge_tools_register_and_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kb_wire_") as tmp:
            settings = Settings()
            # Keep the build hermetic and fast: no memory sidecar, no scheduler.
            settings.memory.enabled = False
            settings.scheduler.enabled = False
            settings.knowledge.store_path = str(Path(tmp) / "kb.db")

            runtime = build_runtime(settings, register_tools=True)
            try:
                specs = {spec.name: spec for spec in runtime.tools()}
                self.assertIn("knowledge.search", specs)
                self.assertIn("knowledge.forget", specs)
                # Searching your own corpus is safe; erasing it is not.
                self.assertEqual(str(specs["knowledge.search"].risk), "safe")
                self.assertEqual(str(specs["knowledge.forget"].risk), "high_risk")
                self.assertIsNotNone(runtime.knowledge)

                doc = Path(tmp) / "notes.md"
                doc.write_text(
                    "# Pricing\n\nThe Pro tier is $29 per month.\n", encoding="utf-8"
                )
                ingested = runtime.registry.invoke(
                    "knowledge.ingest", {"paths": [str(doc)]}, agent="test"
                )
                self.assertTrue(ingested.ok, ingested.error)

                found = runtime.registry.invoke(
                    "knowledge.search", {"query": "pricing tier"}, agent="test"
                )
                self.assertTrue(found.ok, found.error)
                self.assertGreaterEqual(found.output["count"], 1)
                self.assertIn("notes.md", found.output["hits"][0]["citation"])

                self.assertIn("knowledge", runtime.status())
                self.assertTrue(runtime.status()["knowledge"]["enabled"])
            finally:
                runtime.close()

    def test_disabled_knowledge_registers_no_tools(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kb_off_") as tmp:
            settings = Settings()
            settings.memory.enabled = False
            settings.scheduler.enabled = False
            settings.knowledge.enabled = False
            settings.knowledge.store_path = str(Path(tmp) / "kb.db")

            runtime = build_runtime(settings, register_tools=True)
            try:
                names = {spec.name for spec in runtime.tools()}
                self.assertFalse([name for name in names if name.startswith("knowledge.")])
                self.assertIsNone(runtime.knowledge)
            finally:
                runtime.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
