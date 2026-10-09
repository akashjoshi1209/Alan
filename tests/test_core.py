"""Alan's core behaviour tests.

Offline by construction: no network, no model server, no browser. Everything a
real backend would provide is stubbed at the Alan-owned interface boundary
(:class:`ModelProvider`, :class:`MemoryProvider`, :class:`BrowserProvider`),
which is itself part of what these tests verify.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from alan import cli
from alan.config import MemorySettings, PermissionSettings, Settings
from alan.core.events import EventBus, Events, new_trace
from alan.core.orchestrator import Orchestrator, OrchestratorConfig
from alan.core.planner import HeuristicPlanner, Plan
from alan.interfaces.agent_runtime import Agent, AgentContext
from alan.interfaces.memory import MemoryProvider
from alan.interfaces.model import ModelCategory, ModelProvider
from alan.interfaces.capabilities import DiffSummary
from alan.interfaces.types import (
    AgentResult,
    MemoryItem,
    MemoryLevel,
    MemoryType,
    PermissionDecision,
    PermissionRequest,
    Privacy,
    RiskLevel,
    ToolResult,
    ToolSpec,
    redact,
)
from alan.memory.providers.json_store import JsonMemory
from alan.models.router import ModelRouter, NoModelAvailable, RoutingRequest
from alan.permissions.gate import PermissionGate, Policy, default_gate
from alan.tools.coding import LocalCoding
from alan.tools.execution import LocalExecution
from alan.tools.registry import ScopedTools, ToolRegistry


# --------------------------------------------------------------------------
# Stubs (each one proves the interface is actually an interface)
# --------------------------------------------------------------------------
class StubModel(ModelProvider):
    """Records every prompt it is given, so tests can assert on them."""

    def __init__(self, name: str = "ollama", reply: str = "stub reply",
                 local: bool = True, fail: bool = False) -> None:
        self.name = name
        self.model = f"{name}:stub"
        self.reply = reply
        self._local = local
        self.fail = fail
        self.prompts: list[tuple[str, str]] = []

    @property
    def local(self) -> bool:
        return self._local

    def chat(self, messages, system=None):
        self.prompts.append((system or "", messages[0]["content"]))
        if self.fail:
            raise RuntimeError("stub failure")
        return self.reply


class BrokenMemory(MemoryProvider):
    """A backend that is down. Every call fails, as a dead sidecar would."""

    def available(self):
        return False, "backend down"

    def remember(self, item):
        raise RuntimeError("backend down")

    def update(self, item_id, item):
        raise RuntimeError("backend down")

    def forget(self, item_id=None, *, everything=False):
        raise RuntimeError("backend down")

    def search(self, query, *, top_k=5, levels=None, min_confidence=0.0):
        raise RuntimeError("backend down")

    def retrieve(self, item_id):
        raise RuntimeError("backend down")

    def summarize(self, query="", *, top_k=20):
        raise RuntimeError("backend down")

    def promote_to_long_term(self, item):
        raise RuntimeError("backend down")


class RecordingAgent(Agent):
    """A subagent used to prove delegation narrowing."""

    name = "recorder"
    description = "records what it was allowed to use"
    tools = frozenset({"filesystem.read"})
    ceiling = RiskLevel.LOW_RISK
    # It only inspects its context, so it must run with no model backend at all.
    needs_model = False

    def __init__(self) -> None:
        self.seen_allowlist: frozenset[str] = frozenset()
        self.seen_ceiling: RiskLevel = RiskLevel.PRIVILEGED

    def run(self, ctx: AgentContext) -> AgentResult:
        self.seen_allowlist = ctx.allowed_tools
        self.seen_ceiling = ctx.ceiling
        return AgentResult(output="recorded", agent=self.name)


class TempStoreMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="alan-test-")
        self.root = Path(self._tmp.name)
        self.store = JsonMemory(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# --------------------------------------------------------------------------
# 1. Configuration
# --------------------------------------------------------------------------
class TestConfiguration(unittest.TestCase):
    def test_settings_sections_have_safe_defaults(self) -> None:
        settings = Settings()
        self.assertEqual(settings.provider, "auto")
        # A portable product must not open up privileged actions by default.
        self.assertEqual(settings.permissions.ceiling, "medium_risk")
        self.assertFalse(settings.tools.execution)
        self.assertFalse(settings.tools.browser)
        self.assertFalse(settings.permissions.auto_confirm)

    def test_missing_tables_keep_defaults(self) -> None:
        from alan.config import _apply_tables

        # Only the [alan] table exists; the new sections must keep their defaults.
        settings = _apply_tables(Settings(), {"alan": {"name": "X"}})
        self.assertEqual(settings.memory.backend, "auto")
        self.assertEqual(settings.memory.top_k, 5)
        self.assertEqual(settings.permissions.ceiling, "medium_risk")
        self.assertFalse(settings.tools.browser)

    def test_tables_are_applied(self) -> None:
        from alan.config import _apply_tables

        settings = _apply_tables(
            Settings(),
            {
                "memory": {"backend": "mem0", "top_k": 9},
                "permissions": {"ceiling": "high_risk", "deny": ["shell.execute"]},
                "tools": {"execution": True, "include": ["filesystem"]},
            },
        )
        self.assertEqual(settings.memory.backend, "mem0")
        self.assertEqual(settings.memory.top_k, 9)
        self.assertEqual(settings.permissions.ceiling, "high_risk")
        self.assertIn("shell.execute", settings.permissions.deny)
        self.assertTrue(settings.tools.execution)

    def test_execution_root_is_read_from_config(self) -> None:
        """The jail root is configuration, not a hard-coded folder."""
        from alan.config import _apply_tables

        # Default: empty, which the runtime reads as "Alan's own folder".
        self.assertEqual(Settings().tools.execution_root, "")
        settings = _apply_tables(
            Settings(), {"tools": {"execution": True, "execution_root": "Z:\\"}}
        )
        self.assertEqual(settings.tools.execution_root, "Z:\\")

    def test_quoted_boolean_strings_do_not_invert(self) -> None:
        from alan.config import _as_bool

        # ``bool("false")`` is True; an accidentally quoted setting used to
        # silently turn a security switch on. A string is now interpreted.
        self.assertFalse(_as_bool("false", "x"))
        self.assertFalse(_as_bool("off", "x"))
        self.assertTrue(_as_bool("true", "x"))
        self.assertTrue(_as_bool("yes", "x"))

    def test_a_bad_boolean_refuses_to_load(self) -> None:
        from alan.config import _as_bool

        with self.assertRaises(ValueError):
            _as_bool("maybe", "permissions.auto_confirm")

    def test_bad_boolean_in_a_table_is_loud_not_silent(self) -> None:
        from alan.config import _apply_tables

        with self.assertRaises(ValueError):
            _apply_tables(Settings(), {"permissions": {"auto_confirm": "maybe"}})


# --------------------------------------------------------------------------
# 2. Memory hierarchy, retrieval and honesty
# --------------------------------------------------------------------------
class TestMemory(TempStoreMixin):
    def test_hierarchy_levels_round_trip(self) -> None:
        for level in MemoryLevel:
            item = MemoryItem(content=f"content at {level}", level=level)
            self.store.remember(item)
            found = self.store.retrieve(item.id)
            self.assertIsNotNone(found, f"{level} was not persisted")
            self.assertEqual(found.level, level, "level must survive a round trip")
            self.assertEqual(str(found.level), level.name)

    def test_metadata_is_required_and_preserved(self) -> None:
        item = MemoryItem(
            content="Alan prefers dark mode",
            level=MemoryLevel.L4_USER,
            type=MemoryType.PREFERENCE,
            source="user",
            confidence=0.9,
            importance=0.8,
            privacy=Privacy.SENSITIVE,
        )
        self.store.remember(item)
        loaded = self.store.retrieve(item.id)
        for field in ("id", "type", "source", "confidence", "importance", "privacy"):
            self.assertTrue(hasattr(loaded, field), f"missing metadata: {field}")
        self.assertEqual(loaded.privacy, Privacy.SENSITIVE)
        self.assertEqual(loaded.type, MemoryType.PREFERENCE)
        self.assertIsNotNone(loaded.timestamp)
        self.assertIsNone(loaded.expires)

    def test_retrieval_is_not_a_dump(self) -> None:
        for index in range(40):
            self.store.remember(
                MemoryItem(content=f"unrelated note number {index} about weather")
            )
        self.store.remember(MemoryItem(content="Alan uses the Atlantic timezone"))

        hits = self.store.search("which timezone does Alan use?", top_k=3)
        self.assertEqual(len(hits), 1, "top_k must be honoured, not ignored")
        self.assertIn("Atlantic", hits[0].content)
        # Only relevant material reaches the prompt.
        block = self.store.context_block("timezone", max_chars=500)
        self.assertNotIn("weather", block)

    def test_unknown_query_returns_nothing(self) -> None:
        """Alan must be able to say 'I don't know' rather than guess."""
        self.store.remember(MemoryItem(content="Alan prefers dark mode"))
        self.assertEqual(self.store.search("who won the 1966 world cup?"), [])

    def test_expiry_and_confidence_filters(self) -> None:
        from datetime import timedelta

        from alan.interfaces.types import utcnow

        self.store.remember(
            MemoryItem(content="expired secret", expires=utcnow() - timedelta(days=1))
        )
        self.store.remember(MemoryItem(content="sure fact", confidence=0.9))
        self.store.remember(MemoryItem(content="unsure fact", confidence=0.2))

        self.assertEqual(self.store.search("expired secret"), [])
        strong = self.store.search("fact", min_confidence=0.5)
        self.assertEqual([item.content for item in strong], ["sure fact"])

    def test_promotion_moves_item_between_levels(self) -> None:
        item = MemoryItem(content="promote me", level=MemoryLevel.L2_SESSION)
        self.store.remember(item)
        self.assertEqual(self.store.stats()["L2_SESSION"], 1)

        self.store.promote_to_long_term(item)
        self.assertEqual(self.store.stats()["L2_SESSION"], 0)
        self.assertEqual(self.store.stats()["L4_USER"], 1)
        self.assertEqual(self.store.retrieve(item.id).level, MemoryLevel.L4_USER)

    def test_forget_removes_the_item(self) -> None:
        first = self.store.remember(MemoryItem(content="delete me"))
        self.store.remember(MemoryItem(content="keep me"))
        self.assertEqual(self.store.forget(first.id), 1)
        self.assertIsNone(self.store.retrieve(first.id))
        self.assertEqual(len(self.store.search("keep")), 1)

    def test_concurrent_writes_do_not_corrupt_the_store(self) -> None:
        """A background writer and a foreground reader must not collide."""
        import threading

        errors: list[BaseException] = []

        def writer(worker: int) -> None:
            try:
                for index in range(20):
                    self.store.remember(
                        MemoryItem(content=f"worker {worker} note {index}")
                    )
            except BaseException as exc:  # noqa: BLE001 - recorded and re-raised below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [], f"concurrent writes failed: {errors[:1]}")
        # Every write must have survived: 4 workers x 20 notes.
        self.assertEqual(len(self.store.search("worker note", top_k=500)), 80)
        # And the store must still be readable by a fresh instance.
        reopened = JsonMemory(self.root)
        self.assertEqual(len(reopened.search("worker note", top_k=500)), 80)

    def test_dead_backend_degrades_instead_of_failing(self) -> None:
        provider = BrokenMemory()
        self.assertFalse(provider.available()[0])
        orchestrator = _orchestrator(memory=provider, model=StubModel())
        result = orchestrator.run("what do you remember about me?")
        self.assertTrue(
            result.success,
            "a dead memory backend must degrade to a normal answer, not an error",
        )


# --------------------------------------------------------------------------
# 3. Permissions
# --------------------------------------------------------------------------
class TestPermissions(unittest.TestCase):
    def test_every_risk_tier_exists_and_is_ordered(self) -> None:
        self.assertEqual(
            [level.name for level in RiskLevel],
            ["SAFE", "LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "PRIVILEGED"],
        )
        self.assertLess(RiskLevel.SAFE, RiskLevel.LOW_RISK)
        self.assertLess(RiskLevel.MEDIUM_RISK, RiskLevel.HIGH_RISK)
        self.assertLess(RiskLevel.HIGH_RISK, RiskLevel.PRIVILEGED)

    def test_denied_above_ceiling(self) -> None:
        gate = default_gate(RiskLevel.MEDIUM_RISK)
        self.assertEqual(
            gate.check(PermissionRequest("x", RiskLevel.SAFE)),
            PermissionDecision.ALLOWED,
        )
        self.assertEqual(
            gate.check(PermissionRequest("x", RiskLevel.HIGH_RISK)),
            PermissionDecision.DENIED,
        )
        self.assertEqual(
            gate.check(PermissionRequest("x", RiskLevel.PRIVILEGED)),
            PermissionDecision.DENIED,
        )

    def test_destructive_actions_need_confirmation(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED)
        decision = gate.check(PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK))
        self.assertEqual(decision, PermissionDecision.CONFIRMATION_REQUIRED)

        gate.auto_confirm = True
        self.assertEqual(
            gate.check(PermissionRequest("filesystem.delete", RiskLevel.HIGH_RISK)),
            PermissionDecision.ALLOWED,
        )

    def test_forbidden_capabilities_are_never_allowed(self) -> None:
        gate = PermissionGate(ceiling=RiskLevel.PRIVILEGED, auto_confirm=True)
        for capability in ("shell.run_privileged", "permissions.elevate"):
            self.assertEqual(
                gate.check(PermissionRequest(capability, RiskLevel.PRIVILEGED)),
                PermissionDecision.DENIED,
                f"{capability} must be impossible even at the highest ceiling",
            )

    def test_the_declared_permission_is_what_gets_checked(self) -> None:
        """A tool's policy must be found under the capability it declares.

        The registry used to ask the gate about the *tool name*. For
        ``system.open`` -- which declares ``app.launch`` -- that matched no
        policy at all, so every rule an operator wrote for ``app.launch`` was
        silently ignored and the tool fell back to a default derived from its own
        declared risk. The gate was, in effect, asking about a capability nobody
        had ever configured.
        """
        gate = default_gate(RiskLevel.MEDIUM_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)

        seen: list[str] = []
        original = gate.check

        def spy(request, **kwargs):
            seen.append(request.capability)
            return original(request, **kwargs)

        registry.register(
            ToolSpec(
                name="system.open",
                description="open something",
                permissions=("app.launch",),
                risk=RiskLevel.MEDIUM_RISK,
                executor=lambda **_: True,
            )
        )
        gate.check = spy  # type: ignore[method-assign]
        registry.invoke("system.open", {})
        self.assertEqual(seen, ["app.launch"],
                         "the gate must be asked about the declared capability")

    def test_an_operator_denial_of_the_declared_capability_is_honoured(self) -> None:
        """The end-to-end consequence of the lookup being right.

        Denying ``app.launch`` while leaving the ceiling alone must stop
        ``system.open``. Before the lookup was fixed this could not happen, and
        it still would not if the policy were only consulted by accident.
        """
        gate = default_gate(RiskLevel.MEDIUM_RISK)
        gate.set_policy(Policy(capability="app.launch", allow=False,
                               reason="operator said no"))
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="system.open",
                description="open something",
                permissions=("app.launch",),
                risk=RiskLevel.MEDIUM_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke("system.open", {})
        self.assertFalse(result.ok)
        self.assertIn("denied", result.error)
        self.assertEqual(ran, [], "the executor must not run")

    def test_a_capability_needing_approval_fails_closed_without_one(self) -> None:
        """No one to ask means it does not run -- and says what to change.

        This is the case that used to be unreachable and therefore untested: the
        policy table missed every capability whose name differed from its tool's,
        so nothing that needed approval was ever actually asked for.
        """
        gate = default_gate(RiskLevel.HIGH_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="web.open",
                description="fetch a page",
                permissions=("browser.open",),
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke("web.open", {"url": "https://example.com"})
        self.assertFalse(result.ok)
        self.assertEqual(ran, [], "an unapproved capability must not run")
        self.assertIn("auto_confirm", result.error,
                      "the error must name the setting that changes this")
        self.assertNotIn("confirm=True", result.error,
                         "no caller ever set that flag; naming it is useless")

    def test_approval_runs_the_capability(self) -> None:
        gate = default_gate(RiskLevel.HIGH_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="web.open",
                description="fetch a page",
                permissions=("browser.open",),
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke(
            "web.open", {"url": "https://example.com"}, confirmer=lambda _r: True
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(ran, [True])

    def test_declining_approval_refuses(self) -> None:
        gate = default_gate(RiskLevel.HIGH_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="web.open",
                description="fetch a page",
                permissions=("browser.open",),
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke(
            "web.open", {"url": "https://example.com"}, confirmer=lambda _r: False
        )
        self.assertFalse(result.ok)
        self.assertEqual(ran, [], "a declined capability must not run")

    def test_a_broken_confirmer_is_a_refusal_not_a_grant(self) -> None:
        """Failing open would turn a prompt bug into a permission bypass."""
        gate = default_gate(RiskLevel.HIGH_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []

        def explode(_request):
            raise RuntimeError("the terminal went away")

        registry.register(
            ToolSpec(
                name="web.open",
                description="fetch a page",
                permissions=("browser.open",),
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke("web.open", {"url": "https://x.com"}, confirmer=explode)
        self.assertFalse(result.ok)
        self.assertEqual(ran, [])

    def test_a_denial_outranks_an_approval_on_another_capability(self) -> None:
        """A multi-capability tool is all-or-nothing, never partly run."""
        gate = default_gate(RiskLevel.MEDIUM_RISK)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="system.dangerous",
                description="two capabilities, one refused",
                permissions=("app.launch", "shell.run_privileged"),
                risk=RiskLevel.MEDIUM_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke("system.dangerous", {}, confirmer=lambda _r: True)
        self.assertFalse(result.ok)
        self.assertEqual(ran, [], "approval of one capability cannot excuse another")

    def test_auto_confirm_is_the_operators_opt_out_of_being_asked(self) -> None:
        gate = default_gate(RiskLevel.HIGH_RISK)
        gate.auto_confirm = True
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(
                name="web.open",
                description="fetch a page",
                permissions=("browser.open",),
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **_: ran.append(True),
            )
        )
        result = registry.invoke("web.open", {"url": "https://example.com"})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(ran, [True])

    def test_a_subagent_gets_no_approval_channel(self) -> None:
        """No plain session inherits an approval channel.

        The runtime wires one routed confirmer. Its only job is to separate
        scheduled sessions (``sched-*``, which file an Approval for later) from
        everything else. A subagent or plain chat session gets the fail-closed
        branch: it answers no, and -- because an approval is only ever filed for
        a ``sched-`` session -- a chat session cannot silently become one later.
        The shared gate itself never holds a confirmer, so nothing that invokes
        through the gate can prompt on the operator's behalf.
        """
        from alan.runtime import build_runtime

        runtime = build_runtime()
        self.addCleanup(runtime.close)
        confirmer = runtime.orchestrator.config.confirmer
        request = PermissionRequest(
            "web.open", RiskLevel.HIGH_RISK, session_id="session-test"
        )
        if confirmer is not None:
            self.assertFalse(confirmer(request))
            self.assertEqual(
                runtime.approvals.pending(), [],
                "a chat session must not file an approval",
            )
        self.assertIsNone(runtime.gate.confirmer,
                          "the shared gate must never hold an approval channel")

    def test_operator_denials_are_honoured(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED)
        gate.set_policy(Policy(capability="shell.execute", allow=False))
        self.assertEqual(
            gate.check(PermissionRequest("shell.execute", RiskLevel.SAFE)),
            PermissionDecision.DENIED,
        )

    def test_path_constraints_apply(self) -> None:
        gate = default_gate(RiskLevel.PRIVILEGED)
        gate.set_policy(
            Policy(capability="filesystem.write", allowed_prefixes=("d:/alan",))
        )
        self.assertEqual(
            gate.check(
                PermissionRequest("filesystem.write", RiskLevel.LOW_RISK,
                                  arguments={"path": "d:/alan/notes.md"})
            ),
            PermissionDecision.ALLOWED,
        )
        self.assertEqual(
            gate.check(
                PermissionRequest("filesystem.write", RiskLevel.LOW_RISK,
                                  arguments={"path": "c:/windows/system32/x"})
            ),
            PermissionDecision.DENIED,
        )


# --------------------------------------------------------------------------
# 4. Tool registry
# --------------------------------------------------------------------------
class TestToolRegistry(TempStoreMixin):
    def setUp(self) -> None:
        super().setUp()
        self.bus = EventBus()
        self.gate = default_gate(RiskLevel.MEDIUM_RISK)
        self.registry = ToolRegistry(self.gate, bus=self.bus)
        self.registry.register(
            ToolSpec(
                name="filesystem.read",
                description="read",
                risk=RiskLevel.SAFE,
                executor=lambda path="": (self.root / "a.txt").read_text(),
            )
        )
        (self.root / "a.txt").write_text("hello alan", encoding="utf-8")

    def tearDown(self) -> None:
        self.registry.close()
        super().tearDown()

    def test_spec_declares_the_required_fields(self) -> None:
        spec = self.registry.spec("filesystem.read")
        self.assertIsNotNone(spec)
        for field in ("name", "description", "input_schema", "output_schema",
                      "permissions", "risk", "executor", "timeout", "logging_policy"):
            self.assertTrue(hasattr(spec, field), f"spec missing {field}")
        self.assertIsInstance(spec.risk, RiskLevel)

    def test_tool_runs_only_through_the_gate(self) -> None:
        result = self.registry.invoke("filesystem.read", {})
        self.assertTrue(result.ok)
        names = [event.name for event in self.bus.history()]
        self.assertIn(Events.PERMISSION_REQUESTED, names)
        self.assertIn(Events.PERMISSION_GRANTED, names)
        self.assertIn(Events.TOOL_FINISHED, names)

    def test_denial_is_a_value_not_an_exception(self) -> None:
        self.registry.register(
            ToolSpec(name="filesystem.delete", description="delete",
                     risk=RiskLevel.HIGH_RISK, executor=lambda **kw: True)
        )
        result = self.registry.invoke("filesystem.delete", {})
        self.assertFalse(result.ok)
        self.assertIn("denied", result.error)

    def test_unknown_tool_fails_cleanly(self) -> None:
        result = self.registry.invoke("does.not.exist", {})
        self.assertFalse(result.ok)
        self.assertIn("unknown tool", result.error)

    def test_timeout_is_enforced(self) -> None:
        import time

        self.registry.register(
            ToolSpec(name="slow.tool", description="sleeps", risk=RiskLevel.SAFE,
                     executor=lambda: time.sleep(5), timeout=0.25)
        )
        result = self.registry.invoke("slow.tool", {})
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error)

    def test_executor_exception_becomes_a_failed_result(self) -> None:
        def explode(**_kwargs):
            raise ValueError("kaboom")

        self.registry.register(
            ToolSpec(name="bad.tool", description="raises", risk=RiskLevel.SAFE,
                     executor=explode)
        )
        result = self.registry.invoke("bad.tool", {})
        self.assertFalse(result.ok)
        self.assertIn("kaboom", result.error)
        self.assertIsInstance(result, ToolResult)

    def test_catalog_only_lists_allowed_tools(self) -> None:
        self.registry.register(
            ToolSpec(name="secret.tool", description="hidden", risk=RiskLevel.SAFE,
                     executor=lambda: "x")
        )
        self.assertIn("filesystem.read", self.registry.catalog(["filesystem.read"]))
        self.assertNotIn("secret.tool", self.registry.catalog(["filesystem.read"]))
        self.assertEqual(self.registry.catalog([]), "")


# --------------------------------------------------------------------------
# 5. Events and tracing
# --------------------------------------------------------------------------
class TestApprovalPrompt(unittest.TestCase):
    """The console prompt is the only place a person can approve an action."""

    def test_yes_approves(self) -> None:
        with mock.patch("alan.cli.typer.prompt", return_value="y"):
            self.assertTrue(cli._confirm_on_console(
                PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))

    def test_no_is_the_default_so_a_bare_enter_declines(self) -> None:
        captured: dict[str, object] = {}

        def fake_prompt(*args, **kwargs):
            captured.update(kwargs)
            return ""

        with mock.patch("alan.cli.typer.prompt", side_effect=fake_prompt):
            self.assertFalse(cli._confirm_on_console(
                PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))
        self.assertEqual(captured.get("default"), "n",
                         "an unattended Enter must decline, not approve")

    def test_anything_not_an_explicit_yes_declines(self) -> None:
        for answer in ("n", "no", "", "maybe", "yep", "true", "1", "YES please"):
            with self.subTest(answer=answer):
                with mock.patch("alan.cli.typer.prompt", return_value=answer):
                    self.assertFalse(cli._confirm_on_console(
                        PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))

    def test_yes_spelled_out_is_accepted(self) -> None:
        for answer in ("y", "Y", "yes", "YES", " yes "):
            with self.subTest(answer=answer):
                with mock.patch("alan.cli.typer.prompt", return_value=answer):
                    self.assertTrue(cli._confirm_on_console(
                        PermissionRequest("app.launch", RiskLevel.MEDIUM_RISK)))

    def test_ctrl_c_declines_rather_than_raising(self) -> None:
        # Aborting the tool call without letting it run is the whole point of
        # asking, so an interrupted prompt is a refusal and nothing else.
        with mock.patch("alan.cli.typer.prompt", side_effect=KeyboardInterrupt):
            self.assertFalse(cli._confirm_on_console(
                PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))

    def test_closed_stdin_declines(self) -> None:
        with mock.patch("alan.cli.typer.prompt", side_effect=EOFError):
            self.assertFalse(cli._confirm_on_console(
                PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))

    def test_a_prompt_that_cannot_be_shown_cannot_approve(self) -> None:
        with mock.patch("alan.cli.console.print", side_effect=RuntimeError("no tty")):
            self.assertFalse(cli._confirm_on_console(
                PermissionRequest("browser.open", RiskLevel.HIGH_RISK)))


class TestHungTools(unittest.TestCase):
    """A tool that never returns must not take the others down with it.

    The registry used to submit every call to one shared worker pool. Python
    cannot interrupt a running thread, so a tool that ignored its timeout held
    one of four workers forever: four of those and *every* later tool call timed
    out waiting for a thread that was never going to free up, and because those
    workers are non-daemon and joined at interpreter exit, the process itself
    could no longer exit either.
    """

    def _registry(self, max_workers: int = 4) -> "ToolRegistry":
        registry = ToolRegistry(default_gate(RiskLevel.PRIVILEGED),
                                bus=EventBus(), max_workers=max_workers)
        self.addCleanup(registry.close)
        return registry

    def test_a_hung_tool_does_not_stop_a_healthy_one(self) -> None:
        release = threading.Event()
        registry = self._registry()
        registry.register_tool("t.hang", "never returns",
                               lambda **_: release.wait(30),
                               risk=RiskLevel.SAFE, timeout=0.3)
        registry.register_tool("t.ok", "instant", lambda **_: "ok",
                               risk=RiskLevel.SAFE, timeout=5.0)
        self.addCleanup(release.set)

        for _ in range(12):  # more than the old pool had workers
            registry.invoke("t.hang")
            result = registry.invoke("t.ok")
            self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output, "ok")

    def test_only_the_offending_tool_is_quarantined(self) -> None:
        """A global ceiling would let one broken tool disable all of them.

        The fault is attributable, so the refusal is too -- and that is the only
        version of this that still leaves Alan usable.
        """
        release = threading.Event()
        registry = self._registry()
        registry.register_tool("t.hang", "never returns",
                               lambda **_: release.wait(30),
                               risk=RiskLevel.SAFE, timeout=0.3)
        registry.register_tool("t.ok", "instant", lambda **_: "ok",
                               risk=RiskLevel.SAFE, timeout=5.0)
        self.addCleanup(release.set)

        for _ in range(10):
            registry.invoke("t.hang")
        self.assertFalse(registry.invoke("t.hang").ok, "should be quarantined")
        self.assertTrue(registry.invoke("t.ok").ok, "a healthy tool must survive")

    def test_quarantine_says_restart_rather_than_pretending_to_stop_it(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        registry = self._registry()
        registry.register_tool("t.hang", "never returns",
                               lambda **_: release.wait(30),
                               risk=RiskLevel.SAFE, timeout=0.2)
        for _ in range(6):
            registry.invoke("t.hang")
        error = str(registry.invoke("t.hang").error)
        self.assertIn("Restart Alan", error)
        self.assertIn("not a permission problem", error)

    def test_a_quarantined_tool_recovers_once_its_threads_drain(self) -> None:
        """Threads that finish release their own quarantine entry."""
        release = threading.Event()
        registry = self._registry()
        registry.register_tool("t.gated", "blocks until released",
                               lambda **_: release.wait(30),
                               risk=RiskLevel.SAFE, timeout=0.2)
        for _ in range(6):
            registry.invoke("t.gated")
        self.assertFalse(registry.invoke("t.gated").ok)
        release.set()
        for _ in range(50):
            if registry.invoke("t.gated").ok:
                break
            time.sleep(0.05)
        self.assertTrue(registry.invoke("t.gated").ok,
                        "a drained tool must recover without a restart")

    def test_a_timeout_is_reported_as_a_timeout(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        registry = self._registry()
        registry.register_tool("t.hang", "never returns",
                               lambda **_: release.wait(30),
                               risk=RiskLevel.SAFE, timeout=0.2)
        result = registry.invoke("t.hang")
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error)
        self.assertIsNotNone(result.duration_ms)

    def test_a_tool_that_raises_is_a_value_not_an_exception(self) -> None:
        registry = self._registry()

        def boom(**_):
            raise RuntimeError("backend went away")

        registry.register_tool("t.boom", "raises", boom, risk=RiskLevel.SAFE)
        result = registry.invoke("t.boom")
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.error)

    def test_concurrency_stays_bounded(self) -> None:
        """Per-call threads must not become unbounded fan-out."""
        registry = self._registry(max_workers=4)
        lock = threading.Lock()
        live = {"now": 0, "peak": 0}

        def counted(**_):
            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            time.sleep(0.08)
            with lock:
                live["now"] -= 1
            return "ok"

        registry.register_tool("t.counted", "counts", counted,
                               risk=RiskLevel.SAFE, timeout=10.0)
        threads = [threading.Thread(target=registry.invoke, args=("t.counted",))
                   for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertGreater(live["peak"], 1, "the test must actually run in parallel")
        self.assertLessEqual(live["peak"], 4,
                             "fan-out must stay within max_workers")


class TestToolIsolation(unittest.TestCase):
    """An agent's allow-list and ceiling are enforced, not merely declared."""

    def _scoped(self, allowed, ceiling: RiskLevel, risk: RiskLevel = RiskLevel.SAFE):
        ran: list[bool] = []
        gate = default_gate(RiskLevel.PRIVILEGED)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        registry.register(
            ToolSpec(
                name="x.do", description="does a thing",
                permissions=("app.launch",), risk=risk,
                executor=lambda **_: ran.append(True) or "done",
            )
        )
        return ScopedTools(registry, allowed, ceiling), ran

    def test_a_tool_outside_the_allow_list_is_refused(self) -> None:
        scoped, ran = self._scoped({"other.tool"}, RiskLevel.PRIVILEGED)
        result = scoped.invoke("x.do", {})
        self.assertFalse(result.ok)
        self.assertIn("allow-list", result.error)
        self.assertEqual(ran, [])

    def test_an_empty_allow_list_means_no_tools_never_all_of_them(self) -> None:
        """The one reading of an empty allow-list that must never happen."""
        for allowed in (None, frozenset(), set()):
            with self.subTest(allowed=allowed):
                scoped, ran = self._scoped(allowed, RiskLevel.PRIVILEGED)
                result = scoped.invoke("x.do", {})
                self.assertFalse(result.ok)
                self.assertEqual(ran, [], "empty must not mean unrestricted")

    def test_a_tool_above_the_agents_ceiling_is_refused(self) -> None:
        scoped, ran = self._scoped({"x.do"}, RiskLevel.SAFE,
                                   risk=RiskLevel.HIGH_RISK)
        result = scoped.invoke("x.do", {})
        self.assertFalse(result.ok)
        self.assertIn("ceiling", result.error)
        self.assertEqual(ran, [])

    def test_a_tool_at_the_ceiling_is_allowed(self) -> None:
        scoped, ran = self._scoped({"x.do"}, RiskLevel.MEDIUM_RISK,
                                   risk=RiskLevel.MEDIUM_RISK)
        result = scoped.invoke("x.do", {})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(ran, [True])

    def test_approval_flags_are_not_forwarded_through_a_scope(self) -> None:
        """An agent must not be able to approve itself.

        ``confirm`` and ``confirmer`` are the operator's to give. Forwarding them
        would let any scoped caller wave through a capability that needs a human
        simply by passing ``confirm=True``.
        """
        gate = default_gate(RiskLevel.PRIVILEGED)
        registry = ToolRegistry(gate, bus=EventBus())
        self.addCleanup(registry.close)
        ran: list[bool] = []
        registry.register(
            ToolSpec(name="x.delete", description="destroys",
                     permissions=("filesystem.delete",),
                     risk=RiskLevel.HIGH_RISK,
                     executor=lambda **_: ran.append(True))
        )
        scoped = ScopedTools(registry, {"x.delete"}, RiskLevel.HIGH_RISK)
        result = scoped.invoke("x.delete", {}, confirm=True, confirmer=lambda _r: True)
        self.assertFalse(result.ok, "an agent approving itself must still be refused")
        self.assertEqual(ran, [])

    def test_registration_is_refused_because_it_would_widen(self) -> None:
        scoped, _ = self._scoped({"x.do"}, RiskLevel.PRIVILEGED)
        with self.assertRaises(PermissionError):
            scoped.register_tool("x.sneaky", "adds itself", lambda **_: 1)
        with self.assertRaises(PermissionError):
            scoped.unregister("x.do")
        with self.assertRaises(PermissionError):
            scoped.register(
                ToolSpec(name="x.sneaky", description="", executor=lambda **_: 1)
            )

    def test_a_scope_only_advertises_the_tools_it_allows(self) -> None:
        scoped, _ = self._scoped({"x.do"}, RiskLevel.PRIVILEGED)
        self.assertEqual(scoped.names(), ["x.do"])
        self.assertIn("x.do", scoped.catalog())
        self.assertIsNone(scoped.spec("something.else"))

    def test_scopes_narrow_rather_than_replace(self) -> None:
        """Delegation composes: a child cannot be wider than its parent."""
        parent, _ = self._scoped({"x.do"}, RiskLevel.LOW_RISK,
                                 risk=RiskLevel.MEDIUM_RISK)
        child = ScopedTools(parent, {"x.do"}, RiskLevel.LOW_RISK)
        result = child.invoke("x.do", {})
        self.assertFalse(result.ok)
        self.assertIn("ceiling", result.error)

    def test_the_orchestrator_hands_subagents_a_scoped_view(self) -> None:
        """The guarantee is structural only if the real wiring uses it."""
        from alan.runtime import build_runtime

        runtime = build_runtime()
        self.addCleanup(runtime.close)
        captured: dict[str, object] = {}
        agent = runtime.orchestrator.agents["system"]
        original = agent.run
        agent.run = lambda ctx: (captured.update(ctx=ctx), original(ctx))[1]
        runtime.orchestrator.run("delete the file notes.txt", agent="system")
        self.assertIsInstance(captured["ctx"].tools, ScopedTools)
        result = captured["ctx"].tools.invoke("filesystem.write",
                                              {"path": "a", "content": "b"})
        self.assertFalse(result.ok)


class TestEvents(unittest.TestCase):
    def test_trace_id_correlates_a_request(self) -> None:
        bus = EventBus()
        trace = new_trace()
        bus.emit(Events.REQUEST_STARTED, trace_id=trace, request="hi")
        bus.emit(Events.PLAN_CREATED, trace_id=trace, intent="chat")
        bus.emit(Events.REQUEST_FINISHED, trace_id=trace, success=True)

        events = bus.trace(trace)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event.trace_id == trace for event in events))

    def test_wildcard_subscription(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("tool.*", lambda event: seen.append(event.name))
        bus.subscribe("memory.*", lambda event: seen.append(event.name))
        bus.emit(Events.TOOL_STARTED)
        bus.emit(Events.MEMORY_CREATED)
        bus.emit(Events.AGENT_STARTED)
        self.assertEqual(sorted(seen),
                         sorted([Events.TOOL_STARTED, Events.MEMORY_CREATED]))

    def test_failing_subscriber_does_not_break_the_caller(self) -> None:
        bus = EventBus()
        bus.subscribe("*", lambda event: 1 / 0)
        bus.emit(Events.AGENT_STARTED)
        self.assertEqual(len(bus.history()), 1)

    def test_secrets_are_redacted_from_events(self) -> None:
        bus = EventBus()
        event = bus.emit(
            Events.MODEL_FAILED, error="auth failed OPENAI_API_KEY=sk-abcdefghijklmnop"
        )
        self.assertNotIn("sk-abcdefghijklmnop", str(event.fields))
        self.assertIn("***", str(event.fields))

    def test_every_canonical_event_name_is_namespaced(self) -> None:
        prefixes = {"agent", "tool", "memory", "permission", "model", "subagent"}
        names = [
            value for key, value in vars(Events).items()
            if not key.startswith("_") and isinstance(value, str)
        ]
        self.assertTrue(names)
        # `lesson` is the failure-memory namespace: lesson.recalled is shown to
        # the next attempt, lesson.recorded is written to the store. Scheduler,
        # approval and activity are the always-on (Phase 1) namespaces.
        for name in names:
            self.assertIn(
                name.split(".")[0],
                prefixes
                | {"request", "plan", "recovery", "lesson",
                   "scheduler", "approval", "activity", "task"},
                f"{name} is not in a documented namespace",
            )


# --------------------------------------------------------------------------
# 6. Model routing
# --------------------------------------------------------------------------
class TestModelRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.local = StubModel("ollama", local=True)
        self.local.categories = frozenset(
            {ModelCategory.GENERAL, ModelCategory.FAST, ModelCategory.CODING}
        )
        self.cloud = StubModel("openai", local=False)
        self.cloud.categories = frozenset({ModelCategory.GENERAL, ModelCategory.FAST})
        self.router = ModelRouter()
        self.router.add(self.cloud)
        self.router.add(self.local)

    def test_local_is_preferred_over_cloud(self) -> None:
        chosen = self.router.route(RoutingRequest())
        self.assertEqual(chosen.name, "ollama")

    def test_local_only_never_reaches_a_cloud_model(self) -> None:
        request = RoutingRequest(category=ModelCategory.CODING, local_only=True)
        self.assertTrue(self.router.route(request).local)
        self.assertEqual(
            [p.name for p in self.router.eligible(request)], ["ollama"]
        )

    def test_local_only_with_no_local_model_fails_loudly(self) -> None:
        cloud_only = ModelRouter()
        cloud_only.add(self.cloud)
        with self.assertRaises(NoModelAvailable):
            cloud_only.route(RoutingRequest(local_only=True))

    def test_operator_can_allow_cloud_for_private_requests(self) -> None:
        """The safe default is strict, but the operator can widen it."""
        bus = EventBus()
        registry = ToolRegistry(default_gate(RiskLevel.MEDIUM_RISK), bus=bus)
        router = ModelRouter()
        router.add(StubModel("openai", reply="cloud answer", local=False))

        strict = Orchestrator(
            router, registry, bus=bus,
            config=OrchestratorConfig(local_only=True),
        )
        blocked = strict.run("remember that I prefer dark mode")
        self.assertFalse(blocked.success)
        self.assertIn("will not send it to a cloud model", blocked.error)

        relaxed = Orchestrator(
            router, registry, bus=bus,
            config=OrchestratorConfig(local_only=False),
        )
        allowed = relaxed.run("remember that I prefer dark mode")
        self.assertTrue(allowed.success)
        # And the operator's choice is visible in the trace, not silent.
        notes = [str(event.fields.get("note", "")) for event in bus.history()]
        self.assertTrue(any("remote model" in note for note in notes))

    def test_remote_used_when_local_is_missing_the_capability(self) -> None:
        local_only_router = ModelRouter()
        local_only_router.add(StubModel("ollama", local=True))
        self.assertEqual(
            local_only_router.route(RoutingRequest(local_only=True)).name, "ollama"
        )
        cloud_choice = ModelRouter()
        cloud_choice.add(StubModel("openai", local=False))
        self.assertEqual(
            cloud_choice.route(RoutingRequest(category=ModelCategory.VISION)).name,
            "openai",
        )

    def test_category_routing_selects_a_specialist(self) -> None:
        reasoning = StubModel("deepseek", local=False)
        reasoning.categories = frozenset({ModelCategory.REASONING})
        router = ModelRouter()
        router.add(self.local)
        router.add(reasoning)
        self.assertEqual(
            router.route(RoutingRequest(category=ModelCategory.REASONING)).name,
            "deepseek",
        )

    def test_fallback_is_bounded(self) -> None:
        router = ModelRouter(max_attempts=3)
        router.add(StubModel("broken", fail=True))
        router.add(StubModel("second", fail=True))
        router.add(StubModel("third", local=True))
        router.add(StubModel("fourth", local=True))

        reply, used = router.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "stub reply")
        self.assertEqual(used.name, "third")
        self.assertEqual(len(router.attempts), 3, "retry count must be bounded")
        self.assertFalse(router.attempts[0].ok)

    def test_all_failing_gives_a_clear_error(self) -> None:
        router = ModelRouter()
        router.add(StubModel("broken", fail=True))
        with self.assertRaises(NoModelAvailable) as caught:
            router.complete([{"role": "user", "content": "hi"}])
        self.assertIn("broken", str(caught.exception))

    def test_empty_router_explains_itself(self) -> None:
        with self.assertRaises(NoModelAvailable) as caught:
            ModelRouter().route(RoutingRequest())
        message = str(caught.exception)
        self.assertIn("Ollama", message)
        self.assertIn("API key", message)


# --------------------------------------------------------------------------
# 7. Planning
# --------------------------------------------------------------------------
class TestPlanner(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = HeuristicPlanner()

    def test_intent_classification(self) -> None:
        cases = {
            "hello there": "chat",
            "remember that I prefer tabs": "memory",
            "search the web for the latest release notes": "research",
            # Alan does not write code. A change is delegated to Vibecoder.
            "fix the failing test in the parser": "vibecoder_delegate",
            # Not research: ordinary reasoning must not be sent to a web search.
            "what is 2 plus 2?": "chat",
            "what port is my model on?": "chat",
        }
        for request, expected in cases.items():
            plan = self.planner.plan(request)
            self.assertEqual(plan.intent, expected, f"misread {request!r}")

    def test_alan_never_plans_to_write_code_itself(self) -> None:
        """The `code` step must never come back.

        Retiring Alan's own write path is the point of the delegation design, so
        this is asserted rather than assumed: a request to change code must
        produce a Vibecoder step, and no step anywhere may use the `code`
        action that invokes Alan's model to edit a file.
        """
        for request in ("fix the bug in math.js", "refactor the parser",
                        "implement a retry in utils.py", "rename the helper",
                        "add a test for the cache", "ask vibecoder to patch it"):
            with self.subTest(request=request):
                plan = self.planner.plan(request)
                self.assertNotIn("code", plan.actions())
                self.assertIn(
                    "vibecoder_delegate", plan.actions(),
                    f"{request!r} must be delegated to Vibecoder",
                )

    def test_a_change_plan_always_ends_with_a_report(self) -> None:
        """The honesty contract is structural, not optional.

        A plan that delegates has no way to skip saying what was and was not
        done, because the report is the last step rather than something the model
        chooses to add.
        """
        for request in ("fix the bug in math.js", "read math.js and explain it",
                        "open src/core/planner.py"):
            with self.subTest(request=request):
                actions = self.planner.plan(request).actions()
                self.assertTrue(
                    any(a.startswith("vibecoder_") for a in actions),
                    f"{request!r} produced no delegation step",
                )
                self.assertEqual(
                    actions[-1], "vibecoder_report",
                    f"{request!r} must end by reporting, got {actions}",
                )

    def test_a_read_only_ask_delegates_read_only(self) -> None:
        """Asking to be shown a file must not imply a write.

        The instruction to Vibecoder is appended text, not a sandbox, so the
        guarantee has to be made where the decision is made -- here, in the plan.
        """
        for request in ("read math.js and tell me what it does",
                        "explain what config.yaml contains",
                        "look at index.html and describe it"):
            with self.subTest(request=request):
                plan = self.planner.plan(request)
                self.assertEqual(plan.intent, "vibecoder_inspect")
                inspect = next(s for s in plan.steps
                               if s.action == "vibecoder_inspect")
                self.assertTrue(inspect.arguments["read_only"])

    def test_a_compound_request_opens_every_thing_it_names(self) -> None:
        """"open cmd and then start vibecoder" is two launches.

        The remainder used to be planned as an `answer` step, which turned
        "start vibecoder" into a paragraph about Vibecoder -- the capability was
        detected and then discarded.
        """
        plan = self.planner.plan("open cmd and then start vibecoder")
        self.assertEqual(plan.actions()[:2], ["open_target", "open_target"])
        self.assertEqual(
            [s.arguments["target"] for s in plan.steps], ["cmd", "vibecoder"],
        )

    def test_open_a_source_file_reads_it_instead_of_launching_it(self) -> None:
        """"open planner.py" wants the file read, not executed."""
        plan = self.planner.plan("open src\\alan\\core\\planner.py")
        self.assertEqual(plan.intent, "vibecoder_inspect")
        self.assertIn("planner.py", plan.steps[0].arguments["task"])

    def test_launchable_targets_are_not_mistaken_for_files(self) -> None:
        """The source-file check must not eat the ordinary open path.

        Regression guard on the original YouTube bug: an app with a dot in its
        name, and a multi-word target, must still open.
        """
        for request in ("open notepad", "open youtube", "open visual studio code"):
            with self.subTest(request=request):
                self.assertEqual(self.planner.plan(request).intent, "open")

    def test_polite_request_wrappers_are_still_open_intents(self) -> None:
        """"Can you open calc" is how people actually ask.

        Only "please" was accepted before the verb, so the most ordinary polite
        phrasings were answered conversationally instead of doing anything.
        """
        for request in ("can you open calc", "could you open notepad",
                        "would you open youtube", "i need you to open spotify",
                        "let's open notepad", "will you open calc"):
            with self.subTest(request=request):
                self.assertEqual(self.planner.plan(request).intent, "open")

    def test_asking_about_opening_is_not_opening(self) -> None:
        """The other reading of the same words must still fall through.

        Every hazardous phrasing leads with an article -- "a socket", "a file
        descriptor", "a connection" -- and a determiner-led target is only
        honoured when the OS confirms a program by that name.
        """
        for request in ("can you open a socket in python",
                        "can you open a file descriptor",
                        "could you open a connection to postgres",
                        "can you open the door",
                        "would you open a floodgate",
                        "could you open a can of beans",
                        "can you explain how pipes work"):
            with self.subTest(request=request):
                self.assertNotEqual(self.planner.plan(request).intent, "open",
                                    f"hijacked {request!r}")

    def test_every_step_declares_a_risk(self) -> None:
        plan = self.planner.plan("delete the old database and refactor the api")
        self.assertTrue(plan.steps)
        for step in plan.steps:
            self.assertIsInstance(step.risk, RiskLevel)
            self.assertTrue(step.description)

    def test_plan_is_serialisable(self) -> None:
        import json

        payload = self.planner.plan("fix a bug").to_dict()
        json.dumps(payload)
        self.assertIn("steps", payload)
        self.assertIn("intent", payload)

    def test_planner_is_deterministic(self) -> None:
        request = "compare the cost of two local models"
        self.assertEqual(
            self.planner.plan(request).to_dict(),
            self.planner.plan(request).to_dict(),
        )

    # -- opening things --------------------------------------------------
    def test_open_intent_extracts_the_target(self) -> None:
        cases = {
            "open youtube": "youtube",
            "open youtube.com": "youtube.com",
            "please open notepad": "notepad",
            "launch chrome": "chrome",
            "go to wikipedia.org": "wikipedia.org",
            "visit news.ycombinator.com": "news.ycombinator.com",
            "navigate to github.com": "github.com",
            "pull up spotify": "spotify",
            "start notepad": "notepad",
            "open the file explorer": "file explorer",
        }
        for request, expected in cases.items():
            plan = self.planner.plan(request)
            targets = [s.arguments.get("target") for s in plan.steps
                       if s.action == "open_target"]
            self.assertEqual(plan.intent, "open", f"misread {request!r}")
            self.assertEqual(targets, [expected], f"wrong target for {request!r}")

    def test_open_does_not_hijack_engineering_commands(self) -> None:
        """"run the tests" and "start the server" are work, not launches.

        The verb alone cannot decide this -- both begin with a word that also
        means "open" -- so a determiner marks the request as a clause and the
        planner asks the operating system whether a program by that name exists.
        """
        for request in ("run the tests", "start the server", "open the door",
                        "run the test suite"):
            plan = self.planner.plan(request)
            self.assertNotEqual(plan.intent, "open",
                                f"{request!r} was mistaken for a launch")
            self.assertNotIn("open_target", plan.actions())

    def test_open_keeps_a_second_request_in_the_same_sentence(self) -> None:
        """Recognising the first clause must not silently drop the second."""
        plan = self.planner.plan("open youtube and tell me about it")
        self.assertEqual(plan.intent, "open")
        self.assertEqual(plan.actions(), ["open_target", "answer"])

    def test_open_is_only_anchored_at_the_start(self) -> None:
        """An unanchored match would fire on ordinary sentences."""
        for request in ("the fridge is open", "I left the window open",
                        "is the repository open to contributions"):
            self.assertNotEqual(self.planner.plan(request).intent, "open",
                                f"{request!r} was mistaken for a launch")


# --------------------------------------------------------------------------
# 7b. Whole-device filesystem planning
# --------------------------------------------------------------------------
class TestFilesystemPlanner(unittest.TestCase):
    """A request with a real path is a filesystem request, and nothing else is.

    The planner is the only thing standing between "read D:/work/x.txt" and a
    tool call that actually touches the disk, so both directions matter: a path
    must reach the filesystem tools, and prose that merely mentions files must
    not.
    """

    def setUp(self) -> None:
        self.planner = HeuristicPlanner()

    def test_a_path_reads_a_file(self) -> None:
        plan = self.planner.plan("read D:/work/test_unlimited.txt")
        self.assertEqual(plan.intent, "filesystem")
        self.assertEqual(plan.actions(), ["fs_read"])
        self.assertEqual(plan.steps[0].arguments["path"],
                         "D:/work/test_unlimited.txt")
        self.assertEqual(plan.steps[0].risk, RiskLevel.SAFE)

    def test_a_backslash_path_is_still_a_path(self) -> None:
        plan = self.planner.plan(r"read D:\work\notes.txt")
        self.assertEqual(plan.actions(), ["fs_read"])
        self.assertEqual(plan.steps[0].arguments["path"], r"D:\work\notes.txt")

    def test_a_quoted_path_with_spaces_survives(self) -> None:
        plan = self.planner.plan('read "D:/my work/today.txt"')
        self.assertEqual(plan.actions(), ["fs_read"])
        self.assertEqual(plan.steps[0].arguments["path"], "D:/my work/today.txt")

    def test_a_trailing_period_is_not_part_of_the_path(self) -> None:
        plan = self.planner.plan("read D:/work/notes.txt.")
        self.assertEqual(plan.steps[0].arguments["path"], "D:/work/notes.txt")

    def test_a_listing_request_lists(self) -> None:
        cases = {
            "list files in D:/work": "D:/work",
            "list the directory D:/work": "D:/work",
            "show me the files in D:/work": "D:/work",
            "ls D:/work": "D:/work",
            "dir D:/work": "D:/work",
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                plan = self.planner.plan(request)
                self.assertEqual(plan.intent, "filesystem", request)
                self.assertEqual(plan.actions(), ["fs_list"], request)
                self.assertEqual(plan.steps[0].arguments["path"], expected, request)
                self.assertEqual(plan.steps[0].risk, RiskLevel.SAFE)

    def test_reading_a_directory_lists_it(self) -> None:
        """A path with no extension names a folder, not a file."""
        plan = self.planner.plan("read D:/work")
        self.assertEqual(plan.actions(), ["fs_list"])
        self.assertEqual(plan.steps[0].arguments["path"], "D:/work")

    def test_writing_with_the_content_after_the_path(self) -> None:
        plan = self.planner.plan("write D:/work/a.txt with content hello world")
        self.assertEqual(plan.intent, "filesystem")
        self.assertEqual(plan.actions(), ["fs_write"])
        self.assertEqual(plan.steps[0].arguments["path"], "D:/work/a.txt")
        self.assertEqual(plan.steps[0].arguments["content"], "hello world")
        self.assertEqual(plan.steps[0].risk, RiskLevel.LOW_RISK)

    def test_writing_with_the_path_in_the_tail(self) -> None:
        plan = self.planner.plan("write hello world to D:/work/a.txt")
        self.assertEqual(plan.actions(), ["fs_write"])
        self.assertEqual(plan.steps[0].arguments["path"], "D:/work/a.txt")
        self.assertEqual(plan.steps[0].arguments["content"], "hello world")

    def test_a_bare_filename_is_writable(self) -> None:
        plan = self.planner.plan("create notes.txt with content hello there")
        self.assertEqual(plan.actions(), ["fs_write"])
        self.assertEqual(plan.steps[0].arguments["path"], "notes.txt")
        self.assertEqual(plan.steps[0].arguments["content"], "hello there")

    def test_delete_requires_the_tool_to_be_named(self) -> None:
        plan = self.planner.plan("filesystem.delete D:/work/old.txt")
        self.assertEqual(plan.actions(), ["fs_delete"])
        self.assertEqual(plan.steps[0].arguments["path"], "D:/work/old.txt")
        self.assertEqual(plan.steps[0].risk, RiskLevel.HIGH_RISK)

    def test_an_absolute_source_file_opened_is_read(self) -> None:
        """"open D:/work/x.txt" reads the file where it lies."""
        plan = self.planner.plan("open D:/work/test_unlimited.txt")
        self.assertEqual(plan.intent, "filesystem")
        self.assertEqual(plan.actions(), ["fs_read"])

    # -- the negative direction: prose about files keeps its own intent ----
    def test_prose_about_files_does_not_become_a_write(self) -> None:
        for request in ("write a test for the cache",
                        "add a test for the cache",
                        "implement a retry in utils.py"):
            with self.subTest(request=request):
                self.assertNotEqual(
                    self.planner.plan(request).intent, "filesystem", request
                )

    def test_bare_filenames_without_verbs_stay_inspect(self) -> None:
        for request in ("read math.js and tell me what it does",
                        "explain what config.yaml contains",
                        "look at index.html and describe it"):
            with self.subTest(request=request):
                plan = self.planner.plan(request)
                self.assertEqual(plan.intent, "vibecoder_inspect", request)

    def test_a_relative_source_path_opened_stays_inspect(self) -> None:
        plan = self.planner.plan(r"open src\alan\core\planner.py")
        self.assertEqual(plan.intent, "vibecoder_inspect")
        self.assertIn("planner.py", plan.steps[0].arguments["task"])

    def test_deleting_prose_stays_out_of_the_filesystem(self) -> None:
        plan = self.planner.plan("delete the file notes.txt")
        self.assertNotEqual(plan.intent, "filesystem")
        self.assertEqual(plan.actions(), ["answer"])

    def test_a_coding_request_that_mentions_deleting_is_untouched(self) -> None:
        plan = self.planner.plan("delete the old database and refactor the api")
        self.assertNotEqual(plan.intent, "filesystem")
        self.assertIn("vibecoder_delegate", plan.actions())

    def test_a_schedule_listing_is_not_a_directory_listing(self) -> None:
        plan = self.planner.plan("list the schedule")
        self.assertEqual(plan.intent, "schedule_list")

    def test_filesystem_plans_are_serialisable(self) -> None:
        import json

        payload = self.planner.plan("read D:/work/a.txt").to_dict()
        json.dumps(payload)
        self.assertEqual(payload["intent"], "filesystem")


# --------------------------------------------------------------------------
# 8. Subagents and least privilege
# --------------------------------------------------------------------------
class TestSubagents(TempStoreMixin):
    def _orchestrator(self, agents, allowed_tools, ceiling=RiskLevel.MEDIUM_RISK):
        registry = ToolRegistry(default_gate(ceiling))
        registry.register(
            ToolSpec(name="filesystem.read", description="read",
                     risk=RiskLevel.SAFE, executor=lambda: "ok")
        )
        registry.register(
            ToolSpec(name="filesystem.delete", description="delete",
                     risk=RiskLevel.HIGH_RISK, executor=lambda: "gone")
        )
        orchestrator = Orchestrator(
            ModelRouter(), registry, memory=self.store,
            agents=agents,
        )
        orchestrator._test_allowed = allowed_tools  # type: ignore[attr-defined]
        return orchestrator

    def test_agent_gets_only_its_own_tools(self) -> None:
        agent = RecordingAgent()
        orchestrator = self._orchestrator([agent], {"filesystem.read"})
        result = orchestrator.run("anything", agent="recorder")
        self.assertTrue(result.success)
        self.assertEqual(agent.seen_allowlist, frozenset({"filesystem.read"}))
        self.assertNotIn("filesystem.delete", agent.seen_allowlist)

    def test_delegation_cannot_widen_permissions(self) -> None:
        agent = RecordingAgent()
        orchestrator = self._orchestrator([agent], {"filesystem.read"})
        ctx = AgentContext(
            request="x", agent="parent", model=StubModel(), tools=orchestrator.registry,
            allowed_tools=frozenset({"filesystem.read", "filesystem.delete"}),
            ceiling=RiskLevel.MEDIUM_RISK,
        )
        orchestrator.delegate(ctx, "recorder", "do something")
        # The parent had both; the child may only ever be given less.
        self.assertEqual(agent.seen_allowlist, frozenset({"filesystem.read"}))
        self.assertLessEqual(agent.seen_ceiling, ctx.ceiling)

    def test_empty_allowlist_means_no_tools(self) -> None:
        agent = RecordingAgent()
        orchestrator = self._orchestrator([agent], set())
        ctx = AgentContext(
            request="x", agent="parent", model=StubModel(), tools=orchestrator.registry,
            allowed_tools=frozenset(), ceiling=RiskLevel.SAFE,
        )
        orchestrator.delegate(ctx, "recorder", "do something")
        self.assertEqual(agent.seen_allowlist, frozenset())
        self.assertFalse(agent.seen_allowlist)

    def test_subagent_events_are_emitted(self) -> None:
        agent = RecordingAgent()
        orchestrator = self._orchestrator([agent], {"filesystem.read"})
        orchestrator.run("x", agent="recorder")
        names = [event.name for event in orchestrator.bus.history()]
        self.assertIn(Events.SUBAGENT_STARTED, names)
        self.assertIn(Events.SUBAGENT_FINISHED, names)


# --------------------------------------------------------------------------
# 9. The orchestrator and its recovery behaviour
# --------------------------------------------------------------------------
def _orchestrator(
    *,
    model=None,
    memory=None,
    registry=None,
    bus=None,
    agents=(),
):
    router = ModelRouter()
    router.add(model or StubModel())
    return Orchestrator(
        router,
        registry or ToolRegistry(default_gate(RiskLevel.MEDIUM_RISK), bus=bus),
        memory=memory,
        bus=bus,
        agents=agents,
    )


class TestOrchestrator(TempStoreMixin):
    def test_successful_request_emits_a_full_trace(self) -> None:
        bus = EventBus()
        result = _orchestrator(bus=bus).run("hello")
        self.assertTrue(result.success)
        names = [event.name for event in bus.history()]
        for required in (
            Events.REQUEST_STARTED,
            Events.PLAN_CREATED,
            Events.MODEL_FINISHED,
            Events.REQUEST_FINISHED,
        ):
            self.assertIn(required, names, f"missing {required} from the trace")

    def test_missing_model_degrades_to_an_explained_failure(self) -> None:
        router = ModelRouter()  # no backends at all
        orchestrator = Orchestrator(router, ToolRegistry(default_gate()))
        result = orchestrator.run("hello")
        self.assertFalse(result.success)
        self.assertIn("model", result.error.lower())

    def test_model_failure_is_recovered_by_fallback(self) -> None:
        router = ModelRouter(max_attempts=2)
        router.add(StubModel("broken", fail=True))
        router.add(StubModel("working", reply="recovered reply"))
        result = Orchestrator(router, ToolRegistry(default_gate())).run("hello")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "recovered reply")

    def test_unavailable_capability_degrades_the_plan(self) -> None:
        """A research request with no browser must still produce an answer."""
        result = _orchestrator(memory=self.store).run(
            "search the web for the latest news about local AI"
        )
        self.assertTrue(result.success)
        self.assertIn("search_web", result.steps)
        self.assertTrue(
            any("unavailable" in note for note in result.artifacts["observations"]),
            "the failed step should be reported, not silently dropped",
        )

    def test_memory_is_decided_not_dumped(self) -> None:
        orchestrator = _orchestrator(memory=self.store)

        # An explicit instruction is remembered.
        result = orchestrator.run("remember that I prefer dark mode")
        self.assertEqual(len(result.memory_writes), 1)

        # A question is not.
        result = orchestrator.run("what is my favourite colour?")
        self.assertEqual(result.memory_writes, [])

        # A durable statement about the user is.
        result = orchestrator.run("I work in the Pacific timezone")
        self.assertEqual(len(result.memory_writes), 1)

        # Transient chatter is not.
        result = orchestrator.run("thanks, that was quick")
        self.assertEqual(result.memory_writes, [])

    def test_recalled_memory_reaches_the_prompt(self) -> None:
        model = StubModel()
        self.store.remember(MemoryItem(content="Alan's editor is Neovim"))
        result = _orchestrator(model=model, memory=self.store).run(
            "which editor does Alan use?"
        )
        self.assertTrue(result.success)
        self.assertIn("Neovim", model.prompts[-1][0])
        self.assertEqual(result.artifacts["recalled"], 1)

    def test_remember_can_be_switched_off(self) -> None:
        orchestrator = _orchestrator(memory=self.store)
        result = orchestrator.run("remember that I like tea", remember=False)
        self.assertTrue(result.success)
        self.assertEqual(result.memory_writes, [])

    def test_orchestrator_reports_its_plan(self) -> None:
        orchestrator = _orchestrator(memory=self.store)
        result = orchestrator.run("fix the bug in the parser")
        plan = result.artifacts["plan"]
        self.assertEqual(plan["intent"], "vibecoder_delegate")
        self.assertIn(
            "vibecoder_delegate", [step["action"] for step in plan["steps"]],
        )

    def test_read_only_coding_request_never_plans_an_edit(self) -> None:
        """"Find the test command" must not propose modifying files."""
        plan = HeuristicPlanner().plan(
            "find the test command for this repository and run the tests"
        )
        self.assertEqual(plan.intent, "vibecoder_inspect")
        actions = [step.action for step in plan.steps]
        self.assertNotIn("code", actions)
        self.assertNotIn("vibecoder_delegate", actions)
        inspect = next(s for s in plan.steps if s.action == "vibecoder_inspect")
        self.assertTrue(
            inspect.arguments["read_only"],
            "a read-only request must delegate read-only",
        )

    def test_change_request_does_plan_an_edit(self) -> None:
        """A change is delegated, and delegation is an edit."""
        plan = HeuristicPlanner().plan("fix the crash in the parser")
        actions = [step.action for step in plan.steps]
        self.assertNotIn("code", actions)
        self.assertIn("vibecoder_delegate", actions)
        delegate = next(s for s in plan.steps
                        if s.action == "vibecoder_delegate")
        self.assertFalse(delegate.arguments["read_only"])

    def test_a_tool_report_reaches_the_model_as_text_not_a_dict(self) -> None:
        """The observation must lead with a plain-English verdict.

        A live run handed the model a raw Python dict for an edit that had
        demonstrably landed, and the reply opened "Vibecoder claimed:" -- the
        model could not read the repr, so it described what it wished had
        happened. A verdict in the same words the report itself uses is much
        harder to get backwards than a structured field it has to infer.
        """
        from alan.core.orchestrator import Orchestrator

        rendered = Orchestrator._render({
            "acted": True,
            "verified": [{"path": "math.js", "detail": "mtime changed"}],
            "not_done": [],
            "report": "DONE (verified on disk):",
        })
        self.assertTrue(
            rendered.startswith("FILE CHANGES CONFIRMED ON DISK: math.js"),
            f"the verdict must lead, got: {rendered[:70]!r}",
        )
        self.assertIn("DONE (verified on disk)", rendered)
        self.assertIn("verified", rendered.lower())

    def test_an_unconfirmed_change_is_told_as_much(self) -> None:
        """The other half of the contract: no evidence, no claim."""
        from alan.core.orchestrator import Orchestrator

        rendered = Orchestrator._render({
            "acted": True,
            "verified": [],
            "not_done": ["hit an error"],
            "report": "DONE (claimed by Vibecoder, NOT verified):",
        })
        self.assertTrue(
            rendered.startswith("NO FILE CHANGE WAS CONFIRMED"),
            f"got: {rendered[:70]!r}",
        )

    def test_a_dict_with_no_report_becomes_json_not_a_repr(self) -> None:
        from alan.core.orchestrator import Orchestrator

        out = Orchestrator._render({"acted": False, "exit_code": 1})
        self.assertNotIn("'", out, "a Python repr is unreadable as a prompt")
        self.assertIn('"acted"', out)

    def test_a_non_dict_tool_result_is_passed_through(self) -> None:
        from alan.core.orchestrator import Orchestrator

        self.assertEqual(Orchestrator._render("plain text"), "plain text")

    def test_the_delegation_record_is_attached_not_left_to_the_model(self) -> None:
        """The record must survive even when the model omits half of it.

        The model was shown a report with a NOT DONE section listing three failed
        reads and answered with a DONE section alone. A contract the reader can
        edit out is not a contract, so the report is attached rather than
        summarised.
        """
        from alan.core.orchestrator import Orchestrator

        obs = [
            "[vibecoder_delegate] FILE CHANGES CONFIRMED ON DISK: math.js. "
            "State these as done, and say they are verified.\n\n"
            "DONE (verified on disk):\n"
            "  - math.js - verified on disk (mtime changed)\n\n"
            "NOT DONE:\n"
            "  - error reported: file not found",
        ]
        plan = HeuristicPlanner().plan("fix the bug in math.js")
        out = Orchestrator._attach_delegation_record.__get__(
            object.__new__(Orchestrator)
        )("Fixed the add function.", plan, obs)

        self.assertIn("DONE (verified on disk)", out)
        self.assertIn("NOT DONE", out, "the outstanding work must reach the user")
        self.assertIn("error reported", out)
        self.assertIn("math.js", out)

    def test_the_attach_step_does_not_leak_instructions_to_the_user(self) -> None:
        """The verdict line is written for the model, not for the reader."""
        from alan.core.orchestrator import Orchestrator

        obs = [
            "[vibecoder_delegate] FILE CHANGES CONFIRMED ON DISK: math.js. "
            "State these as done, and say they are verified.\n\n"
            "DONE (verified on disk):\n  - math.js",
        ]
        plan = HeuristicPlanner().plan("fix the bug in math.js")
        out = Orchestrator._attach_delegation_record.__get__(
            object.__new__(Orchestrator)
        )("ok.", plan, obs)
        self.assertNotIn("State these as done", out)
        self.assertNotIn("say they are verified", out)

    def test_no_record_is_attached_to_a_conversation(self) -> None:
        from alan.core.orchestrator import Orchestrator

        plan = HeuristicPlanner().plan("what is the capital of France")
        out = Orchestrator._attach_delegation_record.__get__(
            object.__new__(Orchestrator)
        )("Paris.", plan, ["[vibecoder_delegate] nonsense"])
        self.assertEqual(out, "Paris.")

    def test_an_inspection_record_is_attached_too(self) -> None:
        """A read-only plan writes `[vibecoder_inspect]`, not `[vibecoder_delegate]`.

        Matching only the delegate marker meant every inspection silently missed
        the attachment, so a hallucinated description of a file would reach the
        user unchecked -- precisely the failure the read-only path exists to
        prevent.
        """
        from alan.core.orchestrator import Orchestrator

        obs = [
            "[vibecoder_inspect] NO FILE CHANGE WAS CONFIRMED. Do not report any "
            "change as made.\n\nDONE (claimed by Vibecoder, NOT verified):\n"
            "  - math.js is a comprehensive math library\n\nNOT DONE:\n"
            "  - Vibecoder made no tool calls - it answered without inspecting",
        ]
        plan = HeuristicPlanner().plan("read math.js and tell me what it does")
        out = Orchestrator._attach_delegation_record.__get__(
            object.__new__(Orchestrator)
        )("It is a math library.", plan, obs)
        self.assertIn("made no tool calls", out)

    def test_a_reply_that_already_restates_the_record_is_not_doubled(self) -> None:
        from alan.core.orchestrator import Orchestrator

        obs = ["[vibecoder_delegate] verdict\n\nDONE (verified on disk):"]
        plan = HeuristicPlanner().plan("fix the bug in math.js")
        reply = "Here you go.\n\nDONE (verified on disk):"
        out = Orchestrator._attach_delegation_record.__get__(
            object.__new__(Orchestrator)
        )(reply, plan, obs)
        self.assertEqual(out, reply)


# --------------------------------------------------------------------------
# 10. End-to-end: USER -> ALAN -> PLANNER -> MEMORY -> TOOL -> SUBAGENT -> RESULT
# --------------------------------------------------------------------------
class TestEndToEnd(TempStoreMixin):
    def test_full_request_chain(self) -> None:
        bus = EventBus()
        gate = default_gate(RiskLevel.MEDIUM_RISK)
        registry = ToolRegistry(gate, bus=bus)

        # A tool the research subagent is allowed to use.
        registry.register(
            ToolSpec(
                name="web.search",
                description="search the web",
                risk=RiskLevel.LOW_RISK,
                executor=lambda query="": f"results for {query}",
            )
        )
        # A tool it must never reach.
        registry.register(
            ToolSpec(
                name="filesystem.delete",
                description="delete a file",
                risk=RiskLevel.HIGH_RISK,
                executor=lambda path="": "deleted",
            )
        )

        self.store.remember(
            MemoryItem(
                content="Alan is building a portable AI assistant on a USB stick",
                level=MemoryLevel.L4_USER,
                importance=0.9,
            )
        )

        agent = RecordingAgent()
        agent.tools = frozenset({"web.search"})
        model = StubModel(reply="Portable AI assistants should stay fully offline.")

        orchestrator = Orchestrator(
            ModelRouter(), registry, memory=self.store, bus=bus, agents=[agent]
        )
        orchestrator.router.add(model)

        # USER -> ALAN
        result = orchestrator.run(
            "search the web for portable offline AI assistants", session_id="s1"
        )

        # ... the whole chain must have happened.
        self.assertTrue(result.success)
        self.assertIsNotNone(result.artifacts["trace_id"])
        trace_id = result.artifacts["trace_id"]

        names = [event.name for event in bus.trace(trace_id)]

        # ALAN -> PLANNER
        self.assertIn(Events.REQUEST_STARTED, names)
        self.assertIn(Events.PLAN_CREATED, names)
        self.assertEqual(result.artifacts["plan"]["intent"], "research")

        # PLANNER -> MEMORY
        self.assertIn(Events.MEMORY_RETRIEVED, names)

        # MEMORY -> MODEL (the recalled fact reached the prompt)
        self.assertIn("USB stick", model.prompts[-1][0])

        # ALAN -> TOOL (gated, and it ran)
        self.assertIn(Events.TOOL_STARTED, names)
        self.assertIn(Events.TOOL_FINISHED, names)
        self.assertIn(Events.PERMISSION_GRANTED, names)
        self.assertTrue(
            any("results for" in note for note in result.artifacts["observations"]),
            "the tool's output should be visible to the final answer",
        )

        # TOOL -> SUBAGENT (delegation is narrowed, not widened)
        ctx = AgentContext(
            request="x", agent="alan", model=model, tools=registry, memory=self.store,
            allowed_tools=frozenset({"web.search", "filesystem.delete"}),
            ceiling=RiskLevel.MEDIUM_RISK, trace_id=trace_id,
        )
        sub = orchestrator.delegate(ctx, "recorder", "check the sources")
        self.assertTrue(sub.success)
        self.assertEqual(agent.seen_allowlist, frozenset({"web.search"}))

        # Recompute: delegation happened after the first snapshot.
        names = [event.name for event in bus.trace(trace_id)]
        self.assertIn(Events.SUBAGENT_STARTED, names)

        # SUBAGENT -> RESULT
        self.assertIn(Events.REQUEST_FINISHED, names)
        self.assertIn("offline", result.output)
        self.assertIsInstance(result.memory_writes, list)

        # Every event in the trace carries the same trace id.
        self.assertTrue(all(e.trace_id == trace_id for e in bus.trace(trace_id)))

    def test_denied_tool_never_reaches_its_executor(self) -> None:
        ran: list[str] = []
        registry = ToolRegistry(default_gate(RiskLevel.MEDIUM_RISK))
        registry.register(
            ToolSpec(
                name="filesystem.delete",
                description="delete",
                risk=RiskLevel.HIGH_RISK,
                executor=lambda **kw: ran.append("executed"),
            )
        )
        result = registry.invoke("filesystem.delete", {"path": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(ran, [], "a denied tool must not run at all")


# --------------------------------------------------------------------------
# 12. The coding provider
# --------------------------------------------------------------------------
class TestCodingProvider(TempStoreMixin):
    def _repo(self) -> Path:
        repo = self.root / "project"
        (repo / "tests").mkdir(parents=True)
        (repo / "main.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        (repo / "tests" / "test_main.py").write_text(
            "def test_add():\n    assert True\n", encoding="utf-8"
        )
        return repo

    def test_analyze_finds_language_entry_points_and_tests(self) -> None:
        analysis = LocalCoding().analyze(str(self._repo()))
        self.assertEqual(analysis.language, "python")
        self.assertEqual(analysis.entry_points, ["main.py"])
        self.assertEqual(analysis.test_command, "python -m pytest -q")
        # The executable argv is kept separately from the display string, so a
        # home directory containing a space cannot corrupt it.
        self.assertEqual(analysis.test_argv[0], sys.executable)
        self.assertEqual(analysis.test_argv[1:], ["-m", "pytest", "-q"])
        self.assertIn("has tests", analysis.conventions)

    def test_analyze_skips_dependency_directories(self) -> None:
        """A large skipped tree must not be walked, only not counted."""
        repo = self._repo()
        (repo / "env" / "lib" / "deep").mkdir(parents=True)
        for index in range(200):
            (repo / "env" / "lib" / "deep" / f"mod{index}.py").write_text("x=1\n")
        analysis = LocalCoding().analyze(str(repo))
        self.assertEqual(analysis.files, 2, "vendored files must not be counted")

    def test_analyze_reports_an_empty_tree_honestly(self) -> None:
        analysis = LocalCoding().analyze(str(self.root))
        self.assertEqual(analysis.language, "")
        self.assertEqual(analysis.files, 0)
        self.assertIn("no recognised", analysis.notes)

    def test_edit_produces_a_unified_diff(self) -> None:
        repo = self._repo()
        provider = LocalCoding()
        diff = provider.edit(str(repo), {"main.py": "def add(a, b):\n    return b + a\n"})
        self.assertEqual(diff.files_changed, ["main.py"])
        self.assertGreater(diff.additions, 0)
        self.assertIn("-    return a + b", diff.patch)
        self.assertIn("+    return b + a", diff.patch)
        self.assertIn("b + a", (repo / "main.py").read_text(encoding="utf-8"))

    def test_unchanged_file_is_not_reported_as_a_change(self) -> None:
        repo = self._repo()
        same = (repo / "main.py").read_text(encoding="utf-8")
        diff = LocalCoding().edit(str(repo), {"main.py": same})
        self.assertEqual(diff.files_changed, [])

    def test_review_flags_dangerous_code(self) -> None:
        provider = LocalCoding()
        diff = provider.edit(
            str(self.root), {"bad.py": "eval(payload)\nsubprocess.run(c, shell=True)\n"}
        )
        review = provider.review(diff)
        self.assertIn("dynamic execution", review)
        self.assertIn("spawns a process", review)
        self.assertIn("human review required", review)

    def test_review_of_no_change_is_not_a_concern(self) -> None:
        review = LocalCoding().review(DiffSummary())
        self.assertEqual(review, "No files changed.")

    def test_tests_cannot_run_without_execution(self) -> None:
        result = LocalCoding(execution=None).run_tests(str(self._repo()))
        self.assertFalse(result.ok)
        self.assertIn("execution is disabled", result.stderr)
        self.assertFalse(LocalCoding(execution=None).available()[0])

    def test_tests_actually_run_when_execution_is_enabled(self) -> None:
        """The suite must genuinely execute, and never be a silent no-op.

        Two acceptable outcomes: the suite runs (pass or fail), or the runner is
        missing and says so. What is not acceptable is a pass that never invoked
        anything, which is what a bare `pytest` on a non-PATH portable install
        used to produce.
        """
        repo = self._repo()
        provider = LocalCoding(LocalExecution(root=self.root))
        result = provider.run_tests(str(repo))
        if "test runner was not found" in result.stderr:
            return  # honest absence: the suite could not start, and said so
        self.assertNotEqual(
            result.exit_code, 127, "the runner could not be started at all"
        )
        self.assertIn("1 passed", result.stdout)
        self.assertTrue(result.ok)


# --------------------------------------------------------------------------
# 13. Privacy and portability
# --------------------------------------------------------------------------
class TestSafetyBoundaries(unittest.TestCase):
    def test_secrets_are_redacted(self) -> None:
        for secret in (
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrst",
            "ANTHROPIC_API_KEY: hunter2hunter2",
            "token=ghp_abcdefghijklmnopqrstuvwxyz0123",
        ):
            self.assertNotIn(secret.split("=")[-1].split(":")[-1].strip(),
                             redact(secret), f"leaked: {secret}")

    def test_permission_arguments_are_redacted(self) -> None:
        request = PermissionRequest(
            "browser.open", RiskLevel.HIGH_RISK,
            arguments={"url": "https://example.com", "api_key": "supersecret"},
        )
        redacted = request.redacted()
        self.assertEqual(redacted["arguments"]["api_key"], "***")
        self.assertEqual(redacted["arguments"]["url"], "https://example.com")

    def test_memory_item_metadata_is_bounded(self) -> None:
        item = MemoryItem(content="x", confidence=5.0, importance=-3.0)
        self.assertEqual(item.confidence, 1.0)
        self.assertEqual(item.importance, 0.0)

    def test_no_host_absolute_paths_in_source(self) -> None:
        """The product must resolve everything from ALAN_HOME (portability)."""
        package = Path(__file__).resolve().parents[1] / "src" / "alan"
        offenders: list[str] = []
        for path in package.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in ('"C:\\\\', '"D:\\\\', '"F:\\\\'):
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [], f"hard-coded drive paths: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)