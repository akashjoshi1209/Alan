"""Learning from failure: what is remembered, and what is shown again.

Offline by construction. The store is exercised against a scratch file and the
orchestrator against stub models, because the behaviour under test is "does the
next attempt get told about the last one" -- which needs no backend to answer.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from alan.core.events import EventBus, Events
from alan.core.lessons import (
    MAX_SIGNATURE_TOKENS,
    Lesson,
    LessonStore,
    render_hint,
    signature,
    similarity,
    tokens,
)
from alan.core.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    _outstanding,
    _reasons,
)
from alan.interfaces.model import ModelProvider
from alan.interfaces.types import RiskLevel
from alan.models.router import ModelRouter
from alan.permissions.gate import default_gate
from alan.tools.registry import ToolRegistry


class StubModel(ModelProvider):
    """Records the system prompt of every completion it is asked for."""

    def __init__(self, name: str = "ollama", reply: str = "stub reply") -> None:
        self.name = name
        self.model = f"{name}:stub"
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    @property
    def local(self) -> bool:
        return True

    def chat(self, messages, system=None):
        self.prompts.append((system or "", messages[0]["content"]))
        return self.reply

    @property
    def last_system(self) -> str:
        return self.prompts[-1][0] if self.prompts else ""


class ExplodingStore(LessonStore):
    """A store that cannot read or write: read-only home, locked file, worst day."""

    def recall(self, request, *, top_k=2):
        raise RuntimeError("store is down")

    def note_failure(self, request, *, stage, cause, actions=()):
        raise RuntimeError("store is down")

    def note_success(self, request, *, actions=(), model=""):
        raise RuntimeError("store is down")


RESEARCH = "search the web for the latest news about local AI"


class LessonTestCase(unittest.TestCase):
    """A scratch directory per test, and registries closed on the way out."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="alan-lessons-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "lessons.json"
        self.store = LessonStore(self.path)

    def registry(self, bus: EventBus | None = None, *, searchable: bool = False):
        registry = ToolRegistry(default_gate(RiskLevel.MEDIUM_RISK), bus=bus or EventBus())
        self.addCleanup(registry.close)
        if searchable:
            registry.register_tool(
                "web.search",
                "search the web",
                lambda **_: "found it",
                risk=RiskLevel.SAFE,
            )
        return registry

    def orchestrator(self, store, *, model=None, registry=None, bus=None):
        router = ModelRouter()
        router.add(model or StubModel())
        return Orchestrator(
            router,
            registry if registry is not None else self.registry(bus),
            bus=bus,
            lessons=store,
        )


# --------------------------------------------------------------------------
# Identity: what counts as "the same thing"
# --------------------------------------------------------------------------
class TestSignatures(LessonTestCase):
    def test_stopwords_do_not_shape_a_task(self) -> None:
        self.assertEqual(tokens("the bug in the parser"), {"bug", "parser"})

    def test_word_order_does_not_change_the_task(self) -> None:
        self.assertEqual(signature("fix the login bug"), signature("login bug, fix it"))

    def test_signatures_are_case_and_punctuation_insensitive(self) -> None:
        self.assertEqual(signature("Read math.js!"), signature("read math js"))

    def test_a_signature_is_bounded(self) -> None:
        long = " ".join(f"word{i:02d}" for i in range(60))
        self.assertEqual(len(signature(long).split()), MAX_SIGNATURE_TOKENS)

    def test_a_paraphrase_matches_the_original(self) -> None:
        score = similarity(
            signature("fix the bug in math.js"),
            signature("fix the bug in math.js so that add returns the sum"),
        )
        self.assertGreaterEqual(score, 0.6, "the short form is the same job")

    def test_a_different_job_does_not_match(self) -> None:
        score = similarity(
            signature("fix the bug in math.js"),
            signature("what is the weather in paris today"),
        )
        self.assertEqual(score, 0.0)

    def test_an_empty_signature_matches_nothing(self) -> None:
        self.assertEqual(similarity("", "fix the bug"), 0.0)
        self.assertEqual(signature("   "), "")


# --------------------------------------------------------------------------
# The store itself
# --------------------------------------------------------------------------
class TestStore(LessonTestCase):
    def test_a_first_failure_is_recorded_with_its_reason(self) -> None:
        lesson = self.store.note_failure(
            RESEARCH, stage="step:search_web", cause="the web tool is not registered"
        )
        self.assertEqual(lesson.failures, 1)
        self.assertEqual(lesson.last_stage, "step:search_web")
        self.assertIn("not registered", lesson.last_cause)
        self.assertTrue(lesson.open)

    def test_repeated_failures_accumulate_on_one_row(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.store.note_failure(RESEARCH, stage="run", cause="boom again")
        self.assertEqual(len(self.store), 1)
        self.assertEqual(self.store.lessons()[0].failures, 2)

    def test_the_store_survives_the_process(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        reopened = LessonStore(self.path)
        self.assertEqual(len(reopened), 1)
        self.assertEqual(reopened.lessons()[0].failures, 1)

    def test_a_task_that_never_failed_is_not_written(self) -> None:
        self.assertIsNone(
            self.store.note_success("hello there", actions=("answer",)),
            "recording every success would bury the failures that matter",
        )
        self.assertFalse(self.path.exists())

    def test_a_success_closes_the_lesson_and_remembers_the_way(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        won = self.store.note_success(
            RESEARCH, actions=("search_web", "answer"), model="groq"
        )
        self.assertIsNotNone(won)
        self.assertEqual(won.successes, 1)
        self.assertEqual(won.winner, "search_web answer")
        self.assertEqual(won.winner_model, "groq")
        self.assertFalse(won.open)
        self.assertEqual(self.store.recall(RESEARCH), [], "closed lessons are not advice")

    def test_a_success_on_a_task_that_never_failed_changes_nothing(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.store.note_success("a completely different errand")
        self.assertEqual(self.store.lessons()[0].failures, 1)
        self.assertEqual(self.store.lessons()[0].successes, 0)

    def test_attempts_are_remembered_not_just_the_last_one(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="x", actions=("code",))
        self.store.note_failure(
            RESEARCH, stage="run", cause="y", actions=("vibecoder_delegate",)
        )
        self.assertEqual(
            self.store.lessons()[0].tried, ["code", "vibecoder_delegate"]
        )

    def test_a_relapse_after_a_success_reopens_the_lesson(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.store.note_success(RESEARCH, actions=("answer",))
        self.store.note_failure(RESEARCH, stage="run", cause="boom again")
        lesson = self.store.lessons()[0]
        self.assertEqual(lesson.failures, 2)
        self.assertEqual(lesson.successes, 1)
        self.assertTrue(lesson.open, "a relapse is worth warning about again")

    def test_a_truncated_file_costs_the_history_not_the_run(self) -> None:
        self.path.write_text('{"version": 1, "lessons": {"a b"', encoding="utf-8")
        store = LessonStore(self.path)
        self.assertEqual(len(store), 0)
        store.note_failure(RESEARCH, stage="run", cause="boom")
        self.assertEqual(len(store), 1)

    def test_a_row_that_is_not_a_dict_is_skipped(self) -> None:
        self.path.write_text(
            '{"version": 1, "lessons": {"good one": {"signature": "good one"}, "bad": 7}}',
            encoding="utf-8",
        )
        store = LessonStore(self.path)
        self.assertEqual([lesson.signature for lesson in store.lessons()], ["good one"])

    def test_the_store_evicts_rather_than_growing_forever(self) -> None:
        store = LessonStore(self.path, max_lessons=3)
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        for word in words:
            store.note_failure(f"{word} task needs work", stage="run", cause="x")
        self.assertLessEqual(len(store), 3)

    def test_a_reason_is_redacted_before_it_is_written(self) -> None:
        self.store.note_failure(
            "some task", stage="run", cause="upstream said gsk_abcdef1234567890"
        )
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("gsk_abcdef1234567890", raw)
        self.assertIn("***", raw)

    def test_clear_forgets_everything(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.store.clear()
        self.assertEqual(len(self.store), 0)


# --------------------------------------------------------------------------
# What is shown to the next attempt
# --------------------------------------------------------------------------
class TestRecallAndHints(LessonTestCase):
    def test_only_a_matching_task_is_recalled(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.assertEqual(
            self.store.recall("tell me a joke about penguins"),
            [],
            "a hint about web search has no business next to a joke",
        )

    def test_a_close_paraphrase_is_recalled(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        found = self.store.recall("search the web for latest local AI news")
        self.assertEqual(len(found), 1)

    def test_the_worst_mistake_comes_first(self) -> None:
        # Three near-identical tasks, so all three match the query, and only the
        # failure count separates them.
        self.store.note_failure("alpha bravo charlie task one", stage="run", cause="once")
        self.store.note_failure("alpha bravo charlie task two", stage="run", cause="twice")
        self.store.note_failure("alpha bravo charlie task two", stage="run", cause="twice")
        self.store.note_failure("alpha bravo charlie task two", stage="run", cause="twice")
        found = self.store.recall("alpha bravo charlie task three")
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].failures, 3)
        self.assertEqual(found[1].failures, 1)

    def test_top_k_bounds_the_advice(self) -> None:
        # Words absent from the shared phrase, so each of these is a task of
        # its own rather than six spellings of one.
        for word in ("kilo", "lima", "mike", "november", "oscar", "papa"):
            self.store.note_failure(
                f"{word} alpha bravo charlie variant", stage="run", cause="x"
            )
        self.assertEqual(len(self.store), 6, "each of these is a different task")
        self.assertEqual(
            len(self.store.recall("alpha bravo charlie variant", top_k=2)), 2
        )

    def test_top_k_of_zero_is_no_advice(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.assertEqual(self.store.recall(RESEARCH, top_k=0), [])

    def test_a_hint_quotes_the_task_it_belongs_to(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="model unavailable")
        text = self.store.hint(RESEARCH)
        self.assertIn("Lessons from earlier attempts", text)
        self.assertIn(RESEARCH, text)
        self.assertIn("model unavailable", text)

    def test_a_hint_names_the_approach_already_burned(self) -> None:
        self.store.note_failure(
            RESEARCH, stage="run", cause="boom", actions=("vibecoder_delegate",)
        )
        self.assertIn("vibecoder_delegate", self.store.hint(RESEARCH))

    def test_a_hint_prefers_the_approach_that_worked(self) -> None:
        self.store.note_failure(RESEARCH, stage="run", cause="boom")
        self.store.note_success(RESEARCH, actions=("search_web",), model="groq")
        self.store.note_failure(RESEARCH, stage="run", cause="boom again")
        text = self.store.hint(RESEARCH)
        self.assertIn("search_web", text)
        self.assertIn("groq", text)

    def test_an_empty_recall_renders_nothing(self) -> None:
        self.assertEqual(render_hint([]), "")

    def test_a_hint_cannot_run_away(self) -> None:
        huge = Lesson(
            signature="alpha bravo",
            task="x" * 400,
            failures=9,
            last_cause="y" * 400,
            last_stage="run",
        )
        text = render_hint([huge], max_chars=120)
        self.assertLessEqual(len(text), 120)
        self.assertTrue(text.endswith("..."))


# --------------------------------------------------------------------------
# The helpers the orchestrator uses
# --------------------------------------------------------------------------
class TestHelpers(LessonTestCase):
    def test_one_reason_is_not_repeated_once_per_step(self) -> None:
        broken = [
            ("step:search_web", "the web tool is not registered"),
            ("step:run_tests", "the web tool is not registered"),
            ("step:code", "something else"),
        ]
        self.assertEqual(
            _reasons(broken),
            "the web tool is not registered; something else",
        )

    def test_outstanding_work_is_read_from_the_report_not_the_prose(self) -> None:
        self.assertEqual(
            _outstanding({"not_done": ["path is not a directory"]}),
            "path is not a directory",
        )
        self.assertEqual(_outstanding({"not_done": []}), "")
        self.assertEqual(_outstanding({"report": "all done"}), "")
        self.assertEqual(_outstanding("not a report"), "")

    def test_the_shipped_config_does_not_write_shared_state(self) -> None:
        self.assertFalse(OrchestratorConfig().learn)

    def test_an_explicit_store_is_honoured_even_when_learn_is_off(self) -> None:
        orch = self.orchestrator(self.store)
        self.assertIs(orch.lessons, self.store)

    def test_learning_switches_on_when_asked_for(self) -> None:
        registry = self.registry()
        orch = Orchestrator(
            ModelRouter(),
            registry,
            config=OrchestratorConfig(learn=True),
        )
        self.assertIsInstance(orch.lessons, LessonStore)

    def test_a_hand_built_orchestrator_learns_nothing(self) -> None:
        registry = self.registry()
        orch = Orchestrator(ModelRouter(), registry)
        self.assertIsNone(orch.lessons)
        self.assertEqual(orch.lesson_hint(RESEARCH), "")


# --------------------------------------------------------------------------
# End to end: the next attempt knows about the last one
# --------------------------------------------------------------------------
class TestOrchestratorLearns(LessonTestCase):
    def test_a_step_that_does_not_deliver_is_remembered(self) -> None:
        bus = EventBus()
        orch = self.orchestrator(self.store, bus=bus)
        result = orch.run(RESEARCH)
        self.assertTrue(result.success, result.error)
        rows = self.store.lessons()
        self.assertEqual(len(rows), 1, "one run must produce exactly one record")
        self.assertEqual(rows[0].last_stage, "step:search_web")
        self.assertEqual(rows[0].successes, 0)
        self.assertEqual(rows[0].failures, 1)

    def test_the_trace_says_a_lesson_was_taken_and_recorded(self) -> None:
        bus = EventBus()
        orch = self.orchestrator(self.store, bus=bus)
        orch.run(RESEARCH)
        orch.run(RESEARCH)
        names = [event.name for event in bus.history()]
        self.assertIn(Events.LESSON_RECORDED, names)
        self.assertIn(Events.LESSON_RECALLED, names)

    def test_the_next_attempt_is_told_what_happened_last_time(self) -> None:
        self.orchestrator(self.store).run(RESEARCH)

        model = StubModel()
        self.orchestrator(self.store, model=model).run(RESEARCH)
        system = model.last_system
        self.assertIn("Lessons from earlier attempts", system)
        self.assertIn(RESEARCH, system, "the hint must name the task it is about")
        self.assertIn("search_web", system, "the hint must say what was tried")

    def test_a_request_nothing_failed_at_is_left_alone(self) -> None:
        model = StubModel()
        self.orchestrator(self.store, model=model).run("hello")
        self.assertNotIn("Lessons from earlier attempts", model.last_system)
        self.assertEqual(len(self.store), 0)

    def test_beating_the_failure_stops_the_warning(self) -> None:
        self.orchestrator(self.store).run(RESEARCH)
        self.assertTrue(self.store.lessons()[0].open)

        # The capability exists this time, so the step delivers.
        orch = self.orchestrator(self.store, registry=self.registry(searchable=True))
        result = orch.run(RESEARCH)
        self.assertTrue(result.success, result.error)
        self.assertFalse(self.store.lessons()[0].open, "the task is no longer failing")

        model = StubModel()
        self.orchestrator(self.store, model=model).run(RESEARCH)
        self.assertNotIn("Lessons from earlier attempts", model.last_system)

    def test_a_delegation_with_work_outstanding_is_not_a_success(self) -> None:
        """The reply is fine; the task is not done, and only one of those counts."""
        registry = self.registry()
        # Either intent is acceptable -- what matters is that the capability the
        # plan picks reports outstanding work, which is what a real delegation
        # does when Vibecoder stops half-way.
        for tool in ("vibecoder.delegate", "vibecoder.inspect"):
            registry.register_tool(
                tool,
                "delegate coding work",
                lambda **_: {
                    "report": "DONE: nothing\nNOT DONE: the file was never opened",
                    "verified": [],
                    "not_done": ["the file was never opened"],
                },
                risk=RiskLevel.SAFE,
            )
        request = "ask vibecoder to fix the bug in math.js"
        result = self.orchestrator(self.store, registry=registry).run(request)
        self.assertTrue(result.success, result.error)

        rows = self.store.lessons()
        self.assertEqual(len(rows), 1, "the run answered, but the task did not land")
        self.assertTrue(rows[0].last_stage.startswith("step:vibecoder_"))
        self.assertEqual(rows[0].successes, 0)
        self.assertIn("outstanding", rows[0].last_cause)
        self.assertTrue(
            any("NOT DONE" in note for note in result.artifacts["observations"]),
            "the outstanding work must still reach the reply",
        )

    def test_a_store_that_is_down_never_costs_the_answer(self) -> None:
        orch = self.orchestrator(ExplodingStore(self.path))
        result = orch.run(RESEARCH)
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(self.store), 0)

    def test_a_failing_run_still_records_what_went_wrong(self) -> None:
        registry = self.registry()
        orch = Orchestrator(
            ModelRouter(),  # no backends: the run cannot produce an answer
            registry,
            lessons=self.store,
        )
        result = orch.run(RESEARCH)
        self.assertFalse(result.success)
        rows = self.store.lessons()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].last_stage, "run")
        self.assertIn("model", rows[0].last_cause)


if __name__ == "__main__":
    unittest.main()
