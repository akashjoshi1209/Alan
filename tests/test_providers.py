"""Provider configuration, routing policy and key redaction.

These tests exist because of a specific failure: an operator added an API key,
nothing changed, and the symptom was indistinguishable from an invalid key.
Four separate defects produced it -- the local tie-break outranked priority,
priority was only consulted at ``complexity >= 4``, every provider reported the
same priority, and capability inference guessed identically from unrelated
model names. Each has a test here.

Nothing in this file makes a network call: providers are either stubbed or
constructed and asked only whether a key is present.
"""

from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from unittest import mock

from alan.config import (
    API_KEY_ENV,
    PROVIDER_NAMES,
    Settings,
    _provider_from_table,
    load_settings,
)
from alan.interfaces.model import (
    ModelCategory,
    ModelProvider,
    as_model_provider,
)
from alan.interfaces.types import _SECRET_ENV_NAMES, redact
from alan.llm import _NAMES, _construct
from alan.llm.base import Provider, ProviderError, _is_connect_timeout, connect_hint
from alan.llm.cloud import OpenAIProvider
from alan.llm.local import OllamaProvider
from alan.models.router import ModelRouter, RoutingRequest


class Stub(ModelProvider):
    """A provider whose routing signals are set by the test."""

    def __init__(self, name, *, local=False, priority=100, categories=(),
                 available=True, model="stub-model"):
        self.name = name
        self.model = model
        self._local = local
        self._priority = priority
        self._available = available
        if categories:
            self.categories = frozenset(categories)

    @property
    def local(self):
        return self._local

    def available(self):
        return (True, "ready") if self._available else (False, "no key")

    def priority(self):
        return self._priority

    def chat(self, messages, system=None):
        return f"reply from {self.name}"


@contextmanager
def key_in_env(name: str, value: str | None):
    """Put a key in the environment for the duration of a test, then restore it.

    Restoring matters: ``config/.env`` is loaded into ``os.environ`` at import,
    so a test that merely deletes a key would change what every later test
    sees -- including whether a real cloud backend is available.
    """
    saved = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved


def rank(*providers, request=None):
    router = ModelRouter()
    for provider in providers:
        router.add(provider)
    return [p.name for p in router.eligible(request or RoutingRequest())]


class SingleSourceOfTruthTest(unittest.TestCase):
    """Two independent provider lists is how a backend reaches one code path
    and not the other -- which presents as 'my key does nothing'."""

    def test_llm_reuses_the_config_provider_list(self) -> None:
        self.assertIs(_NAMES, PROVIDER_NAMES)

    def test_every_known_name_is_constructible(self) -> None:
        settings = load_settings()
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                provider = _construct(name, settings)
                self.assertEqual(provider.name, name)

    def test_unknown_provider_fails_loudly(self) -> None:
        with self.assertRaises(ProviderError):
            _construct("not-a-provider", load_settings())

    def test_router_builder_refuses_an_unknown_name_with_a_reason(self) -> None:
        from alan.runtime import build_router

        settings = Settings()
        settings.provider = "nonsense"
        router, note = build_router(settings)
        self.assertEqual(router.providers, [])
        self.assertIn("unknown provider", note)


class PriorityPlumbingTest(unittest.TestCase):
    def test_unconfigured_priority_is_neutral(self) -> None:
        """100 for everyone, so 'no opinion' can never be read as 'prefer this'."""
        provider = OpenAIProvider(provider="openai")
        self.assertEqual(provider.priority(), 100)

    def test_adapter_forwards_the_priority_it_was_given(self) -> None:
        """Regression: the adapter used to fall through to the base class and
        report 100 for every provider, which made the tie-break inert."""
        adapted = as_model_provider(
            OpenAIProvider(model="some-model", provider="groq", priority=7)
        )
        self.assertEqual(adapted.priority(), 7)

    def test_adapter_defaults_to_neutral_when_the_backend_has_no_opinion(self) -> None:
        class NoOpinion(Provider):
            def chat(self, messages, system=None):
                return ""

        self.assertEqual(as_model_provider(NoOpinion()).priority(), 100)

    def test_explicit_categories_beat_name_guessing(self) -> None:
        """'gpt-4o-mini' and 'qwen2.5:1.5b' infer identically, so only a
        stated category can separate a coding specialist from a chat model."""
        stated = as_model_provider(
            OpenAIProvider(model="totally-generic-name", provider="openai",
                           categories=frozenset({ModelCategory.CODING}))
        )
        guessed = as_model_provider(
            OpenAIProvider(model="totally-generic-name", provider="openai")
        )
        self.assertTrue(stated.supports(ModelCategory.CODING))
        self.assertFalse(guessed.supports(ModelCategory.CODING))
        # The guess is still the fallback, not an error.
        self.assertTrue(guessed.supports(ModelCategory.GENERAL))

    def test_unknown_category_in_config_is_dropped_not_fatal(self) -> None:
        from alan.llm import _to_categories

        out = _to_categories(["coding", "not-a-real-category"])
        self.assertEqual(out, frozenset({ModelCategory.CODING}))


class ConfigParsingTest(unittest.TestCase):
    def test_shipped_config_ranks_cloud_above_local(self) -> None:
        settings = load_settings()
        self.assertEqual(settings.ollama.priority, 10)
        for name in ("openai", "anthropic", "groq", "openrouter"):
            with self.subTest(provider=name):
                self.assertEqual(getattr(settings, name).priority, 0)

    def test_an_explicit_zero_survives_parsing(self) -> None:
        """`priority = 0` means 'prefer this'. A .get(..., default) or an
        `or 0` fallback would mistake it for a missing key and silently
        reinstate the shipped default."""
        parsed = _provider_from_table("ollama", {"priority": 0})
        self.assertEqual(parsed.priority, 0)

    def test_absent_keys_keep_the_shipped_policy(self) -> None:
        """A settings.toml written before providers had priorities must not
        lose them -- otherwise an upgrade silently reverts to local-only."""
        parsed = _provider_from_table("ollama", {"model": "llama3.2:1b"})
        self.assertEqual(parsed.priority, 10)
        self.assertEqual(parsed.categories, ("general", "fast"))

    def test_a_table_overrides_only_what_it_names(self) -> None:
        parsed = _provider_from_table("groq", {"model": "llama-3.3-70b-versatile"})
        self.assertEqual(parsed.model, "llama-3.3-70b-versatile")
        self.assertEqual(parsed.base_url, "https://api.groq.com/openai/v1")
        self.assertEqual(parsed.priority, 0)

    def test_every_provider_defaults_are_complete(self) -> None:
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                default = _provider_from_table(name, {})
                self.assertTrue(default.model, "a provider with no model is useless")
                self.assertGreaterEqual(default.priority, 0)


class KeyPresenceTest(unittest.TestCase):
    def test_missing_key_names_the_env_var_to_set(self) -> None:
        with key_in_env("GROQ_API_KEY", None):
            provider = OpenAIProvider(provider="groq")
            ok, reason = provider.available()
            self.assertFalse(ok)
            self.assertEqual(reason, "GROQ_API_KEY is not set (see config/.env)")

    def test_present_key_is_ready_without_a_network_call(self) -> None:
        with key_in_env("GROQ_API_KEY", "gsk_" + "x" * 40):
            provider = OpenAIProvider(provider="groq")
            self.assertEqual(provider.available(), (True, "ready"))

    def test_error_text_names_the_backend_that_failed(self) -> None:
        """A Groq failure reported as "OpenAI request failed" sends the
        operator to the wrong dashboard."""
        from alan.llm.base import ProviderError

        provider = OpenAIProvider(provider="groq")
        with self.assertRaises(ProviderError) as caught:
            provider.chat([{"role": "user", "content": "hi"}])
        self.assertIn("groq", str(caught.exception))
        self.assertNotIn("OpenAI request", str(caught.exception))

    def test_every_key_has_an_env_var_and_a_redaction_rule(self) -> None:
        """Adding a provider must not add an unmasked key.

        The two lists live in different modules (config and interfaces.types)
        because interfaces sits below config in the layering; this test is what
        stops them drifting apart.
        """
        for env_name in API_KEY_ENV.values():
            with self.subTest(env=env_name):
                self.assertIn(env_name, _SECRET_ENV_NAMES)


class RoutingPolicyTest(unittest.TestCase):
    """The behaviour the operator sees, in the order the ranking applies."""

    def test_a_configured_cloud_key_is_actually_used(self) -> None:
        """The bug that started all of this: local outranked priority, so a key
        changed nothing at any complexity."""
        chosen = rank(
            Stub("cloud", priority=0, categories=(ModelCategory.CODING,)),
            Stub("local", local=True, priority=10,
                 categories=(ModelCategory.GENERAL,)),
            request=RoutingRequest(category=ModelCategory.CODING, complexity=1),
        )
        self.assertEqual(chosen[0], "cloud")

    def test_priority_is_consulted_at_any_complexity(self) -> None:
        """Priority used to apply only at complexity >= 4, and Alan's plans
        score 1-3 -- so it never applied at all."""
        for complexity in (1, 2, 3, 4, 5):
            with self.subTest(complexity=complexity):
                self.assertEqual(
                    rank(Stub("cloud", priority=0),
                         Stub("local", local=True, priority=10),
                         request=RoutingRequest(complexity=complexity))[0],
                    "cloud",
                )

    def test_equal_priority_falls_back_to_local(self) -> None:
        """Portable default: with nothing configured, the machine you are on
        answers."""
        self.assertEqual(
            rank(Stub("cloud", priority=100), Stub("local", local=True)),
            ["local", "cloud"],
        )

    def test_local_only_never_considers_a_remote_model(self) -> None:
        """Asserted on the whole ranking, not just the winner: a remote model
        that is merely ranked lower would still be a leak."""
        self.assertEqual(
            rank(Stub("cloud", priority=0),
                 Stub("local", local=True, priority=10),
                 request=RoutingRequest(local_only=True)),
            ["local"],
        )

    def test_allow_remote_false_also_excludes_remote_models(self) -> None:
        """This flag was read only for local providers, so a caller setting it
        to False still had cloud models considered -- it did nothing."""
        self.assertEqual(
            rank(Stub("cloud", priority=0), Stub("local", local=True),
                 request=RoutingRequest(allow_remote=False)),
            ["local"],
        )

    def test_a_specialist_beats_a_generalist_at_equal_priority(self) -> None:
        self.assertEqual(
            rank(Stub("chat", priority=5, categories=(ModelCategory.GENERAL,)),
                 Stub("coder", priority=5,
                      categories=(ModelCategory.GENERAL, ModelCategory.CODING)),
                 request=RoutingRequest(category=ModelCategory.CODING)),
            ["coder", "chat"],
        )

    def test_an_explicit_preference_beats_both(self) -> None:
        chosen = rank(
            Stub("wanted", priority=50, categories=(ModelCategory.GENERAL,)),
            Stub("specialist", priority=5,
                 categories=(ModelCategory.CODING,)),
            request=RoutingRequest(category=ModelCategory.CODING,
                                   prefer="wanted"),
        )
        self.assertEqual(chosen[0], "wanted")

    def test_unavailable_backends_are_not_ranked(self) -> None:
        self.assertEqual(
            rank(Stub("cloud", priority=0, available=False),
                 Stub("local", local=True, priority=10)),
            ["local"],
        )


class RedactionTest(unittest.TestCase):
    def test_provider_key_env_values_are_masked_in_text(self) -> None:
        for env_name in API_KEY_ENV.values():
            with self.subTest(env=env_name):
                masked = redact(f"{env_name}=" + "A" * 40)
                self.assertNotIn("A" * 40, masked)

    def test_a_bare_groq_key_shape_is_masked(self) -> None:
        """Provider error messages are the usual way a key gets out, and they
        echo the token, not the variable name."""
        key = "gsk_" + "abcdefghij1234567890"
        self.assertNotIn(key, redact(f"request failed: {key}"))

    def test_a_bare_openrouter_key_shape_is_masked(self) -> None:
        key = "sk-or-v1-" + "abcdefghij1234567890"
        self.assertNotIn(key, redact(f"401 from {key}"))


class ConnectTimeoutTest(unittest.TestCase):
    """A handshake that never finished must not be reported as a slow API.

    Reproduced live on this machine: Quick Heal's web-security driver
    (``webssx8.sys``) added ~10.5s to every TLS handshake while SMTP, NNTP and
    plain TCP all stayed under 0.9s. The SDKs' connect default is 5.0s, so
    every cloud request failed before the API was reached and surfaced as
    "groq stream failed: Request timed out." -- naming a provider that had
    never been contacted, and which answered in 10.6s once only the connect
    phase was widened.
    """

    def test_a_table_can_widen_the_connect_timeout(self) -> None:
        self.assertEqual(
            _provider_from_table("groq", {"connect_timeout": 45}).connect_timeout,
            45.0,
        )

    def test_the_connect_timeout_defaults_to_thirty_for_every_provider(self) -> None:
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                self.assertEqual(
                    _provider_from_table(name, {}).connect_timeout, 30.0
                )

    def test_an_older_settings_table_still_gets_the_default(self) -> None:
        """settings.toml files written before this key existed must keep
        working, and keep a sane handshake ceiling rather than inheriting 0."""
        parsed = _provider_from_table("groq", {"model": "llama-3.3-70b-versatile"})
        self.assertEqual(parsed.connect_timeout, 30.0)

    def test_the_provider_passes_it_to_the_sdk_client(self) -> None:
        with key_in_env("GROQ_API_KEY", "gsk_" + "x" * 40):
            provider = OpenAIProvider(provider="groq", connect_timeout=45.0)
            timeout = provider._get_client().timeout
        self.assertEqual(timeout.connect, 45.0)
        # Only the connect phase moves. Read stays at the SDK default, so a
        # generation that genuinely hangs is still bounded as it was before.
        self.assertEqual(timeout.read, 600.0)

    def test_a_nested_connect_timeout_is_recognised(self) -> None:
        """The signal is one level down: the SDK's own error says only
        "Request timed out.", while the HTTP layer's names the phase."""

        class ConnectTimeout(Exception):
            pass

        inner = ConnectTimeout("_ssl.c:993: The handshake operation timed out")
        outer = Exception("Request timed out.")
        outer.__cause__ = inner

        self.assertTrue(_is_connect_timeout(outer))

    def test_a_connect_timeout_several_layers_down_is_still_found(self) -> None:
        class ConnectTimeout(Exception):
            pass

        inner = ConnectTimeout("all connection attempts failed")
        middle = Exception("wrapper")
        middle.__cause__ = inner
        outer = Exception("Request timed out.")
        outer.__cause__ = middle

        self.assertTrue(_is_connect_timeout(outer))

    def test_a_slow_read_is_not_mistaken_for_a_connect_timeout(self) -> None:
        """The two want opposite advice: widening connect fixes one and cannot
        touch the other, so claiming the hint for a read stall would send the
        operator to change a setting that does nothing."""
        self.assertFalse(_is_connect_timeout(Exception("Request timed out.")))
        self.assertFalse(_is_connect_timeout(Exception("stream closed early")))

    def test_a_cyclic_exception_chain_terminates(self) -> None:
        first = Exception("a")
        second = Exception("b")
        first.__cause__ = second
        second.__context__ = first
        self.assertFalse(_is_connect_timeout(first))

    def test_the_hint_is_only_emitted_when_it_is_earned(self) -> None:
        """Every other failure keeps its message byte for byte: the hint is
        additive, so it can never mask the SDK's own text."""

        class ConnectTimeout(Exception):
            pass

        inner = ConnectTimeout("The handshake operation timed out")
        outer = Exception("Request timed out.")
        outer.__cause__ = inner

        self.assertEqual(connect_hint(Exception("401 unauthorized"), 30.0), "")
        hint = connect_hint(outer, 30.0)
        self.assertTrue(hint.startswith(" (the TLS handshake"))
        self.assertIn("30s", hint)
        self.assertIn("antivirus", hint)

    def test_the_failed_request_message_still_carries_the_sdk_text(self) -> None:
        """A failing request must name the backend, keep the SDK's message and
        add the phase, in that order -- the operator reads left to right.

        Aimed at the discard port so it fails locally and instantly rather than
        spending a real request on a real provider.
        """
        with key_in_env("GROQ_API_KEY", "gsk_" + "x" * 40):
            provider = OpenAIProvider(
                provider="groq",
                base_url="http://127.0.0.1:9/v1",
                connect_timeout=5.0,
            )
            with self.assertRaises(ProviderError) as caught:
                provider.chat([{"role": "user", "content": "hi"}])
        message = str(caught.exception)
        self.assertTrue(message.startswith("groq request failed: "), message)


class OllamaProbeTest(unittest.TestCase):
    """The local liveness probe reads ``/api/tags`` directly.

    Pinned because the obvious implementation -- ``ollama.Client().list()`` --
    drags the whole SDK in, and measured here that is ~2.4 s to import plus
    ~0.7 s to construct, on a path that runs on every uncached command.
    """

    def _payload(self, names: list[str]):
        body = json.dumps({"models": [{"model": n} for n in names]}).encode("utf-8")
        response = mock.MagicMock()
        response.read.return_value = body
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: False
        return response

    def test_list_models_reads_the_rest_endpoint(self) -> None:
        provider = OllamaProvider()
        with mock.patch("alan.sidecars.listening_on", return_value=True), \
                mock.patch(
                    "urllib.request.urlopen", return_value=self._payload(["a:1", "b:2"])
                ) as urlopen:
            names = provider._list_models()

        self.assertEqual(names, ["a:1", "b:2"])
        self.assertIn("/api/tags", urlopen.call_args.args[0].full_url)

    def test_a_listed_model_is_ready(self) -> None:
        provider = OllamaProvider(model="qwen2.5:1.5b")
        with mock.patch.object(provider, "_list_models", lambda: ["qwen2.5:1.5b"]):
            ok, reason = provider._probe()
        self.assertTrue(ok, reason)

    def test_a_model_that_is_not_pulled_names_the_pull_script(self) -> None:
        provider = OllamaProvider(model="missing:1b")
        with mock.patch.object(provider, "_list_models", lambda: ["qwen2.5:1.5b"]):
            ok, reason = provider._probe()
        self.assertFalse(ok)
        self.assertIn("pull-model.ps1", reason)

    def test_a_closed_port_is_rejected_before_any_request_is_made(self) -> None:
        """Port 1 is reserved and nothing listens on it: the probe must fail on
        the liveness check, not on a request that waits out a SYN retransmit."""
        provider = OllamaProvider(base_url="http://127.0.0.1:1")
        with mock.patch("urllib.request.urlopen") as urlopen:
            ok, reason = provider._probe()
        self.assertFalse(ok)
        self.assertIn("not reachable", reason)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
