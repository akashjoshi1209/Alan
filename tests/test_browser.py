"""Alan's browser capability tests.

Offline by construction: no sidecar, no browser, no network. Two things are
tested and they are deliberately different in kind.

**The client** (:mod:`alan.tools.browser.sidecar`) is exercised through a stub
transport, so the contract Alan's tools depend on is pinned: how a sidecar reply
becomes a :class:`BrowserResult`, how a screenshot crosses the wire, and what a
caller is told when the sidecar is not there. A missing sidecar must produce an
actionable message, never a traceback.

**The sidecar's pure helpers** are imported and tested directly. That is possible
only because ``browser_sidecar.py`` imports nothing outside the standard library
at module level -- ``browser_use`` is imported lazily, inside the function that
needs it. If someone ever hoists that import to the top of the file, these tests
stop being runnable from Alan's own environment and that is worth discovering
here rather than in production.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import browser_sidecar as sidecar  # noqa: E402
from alan.interfaces.capabilities import BrowserResult  # noqa: E402
from alan.tools.browser import (  # noqa: E402
    DEFAULT_BASE_URL,
    BrowserUnavailable,
    SidecarBrowser,
)
from alan.tools.browser.sidecar import _to_result  # noqa: E402


class _Response:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, payload=None, status_code=200, *, raises=None):
        self._payload = payload
        self.status_code = status_code
        self._raises = raises

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


class _Transport:
    """Records calls and replays canned replies.

    ``RequestException`` is carried through from the real library because the
    client catches exactly that. A stub that raised ``OSError`` instead would be
    testing a branch that cannot execute in production, and a stub with no such
    attribute would fail on the ``except`` clause itself -- which is how the
    mismatch was found.
    """

    RequestException = requests.RequestException

    def __init__(self, get=None, post=None):
        self.get_result = get
        self.post_result = post
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, timeout))
        result = self.get_result
        return result(url, timeout) if callable(result) else result

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        result = self.post_result
        return result(url, json) if callable(result) else result


class _StubbedTransportCase(unittest.TestCase):
    """Base for tests that need the client's HTTP layer replaced.

    The swap has to last for the whole test, not just the construction of the
    provider. Restoring it eagerly means the browser object under test quietly
    reaches the real network, and the test either hangs or -- worse -- passes
    because the machine happened to be online.
    """

    def use_transport(self, get=None, post=None) -> SidecarBrowser:
        import alan.tools.browser.sidecar as module

        self.transport = _Transport(get=get, post=post)
        original = module.requests
        module.requests = self.transport
        self.addCleanup(setattr, module, "requests", original)
        browser = SidecarBrowser(DEFAULT_BASE_URL)
        # Liveness probes are cached between processes, so a probe recorded
        # against the real sidecar would answer for a stubbed transport and the
        # test would pass or fail depending on what ran before it. A stubbed
        # transport has to be probed for real.
        browser.fresh_probe = True
        return browser


# ---------------------------------------------------------------------------
# Client: result mapping
# ---------------------------------------------------------------------------
class ResultMappingTest(unittest.TestCase):
    def test_maps_every_field(self):
        result = _to_result({
            "ok": True,
            "content": "hello",
            "url": "https://example.com",
            "title": "Example",
            "data": {"engine": "browser-use"},
        })
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.url, "https://example.com")
        self.assertEqual(result.title, "Example")
        self.assertEqual(result.data["engine"], "browser-use")
        self.assertIsNone(result.error)

    def test_failure_keeps_its_reason(self):
        result = _to_result({"ok": False, "error": "no element matched 'h1'"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "no element matched 'h1'")
        self.assertEqual(result.content, "")

    def test_absent_fields_default_rather_than_raise(self):
        result = _to_result({})
        self.assertFalse(result.ok)
        self.assertEqual(result.data, {})
        self.assertEqual(result.url, "")


# ---------------------------------------------------------------------------
# Client: transport failures must be actionable
# ---------------------------------------------------------------------------
class TransportFailureTest(_StubbedTransportCase):
    def test_missing_sidecar_says_how_to_start_it(self):
        def explode(url, timeout=None):
            raise requests.ConnectionError("connection refused")

        browser = self.use_transport(get=explode, post=explode)
        ok, reason = browser.available()
        self.assertFalse(ok)
        self.assertIn("not reachable", reason)

        with self.assertRaises(BrowserUnavailable) as caught:
            browser.open("https://example.com")
        message = str(caught.exception)
        # A caller who hits this must be able to act on it without reading docs.
        self.assertIn("browser-serve.ps1", message)
        self.assertIn("8022", message)

    def test_http_error_is_not_confused_with_an_action_error(self):
        browser = self.use_transport(post=_Response({"ok": True}, status_code=503))
        with self.assertRaises(BrowserUnavailable) as caught:
            browser.navigate("https://example.com")
        self.assertIn("503", str(caught.exception))

    def test_non_json_body_is_reported_as_such(self):
        browser = self.use_transport(post=_Response(raises=ValueError("not json")))
        with self.assertRaises(BrowserUnavailable) as caught:
            browser.open("https://example.com")
        self.assertIn("non-JSON", str(caught.exception))

    def test_available_reports_engine_and_version(self):
        browser = self.use_transport(get=_Response({
            "ok": True, "engine": "browser-use", "browser_use": "0.13.10",
        }))
        ok, reason = browser.available()
        self.assertTrue(ok)
        self.assertIn("browser-use 0.13.10", reason)


# ---------------------------------------------------------------------------
# Client: verbs map to the right endpoints and payloads
# ---------------------------------------------------------------------------
class VerbRoutingTest(_StubbedTransportCase):
    def setUp(self):
        self.browser = self.use_transport(
            post=_Response({"ok": True, "content": "x"})
        )

    def _last(self):
        return self.transport.calls[-1]

    def test_search_leaves_the_engine_choice_to_the_sidecar(self):
        """The default must be empty, not a named engine.

        Naming one engine at the client opts out of every other one, and that
        silently defeated the sidecar's fallback chain: with ``engine="bing"``
        hard-coded here, Marginalia -- the one engine measured as returning
        relevant results from this network -- was never tried at all. Which
        engine works is a property of the network, which the sidecar can observe
        and a client-side default cannot.
        """
        self.browser.search("python list comprehension")
        method, url, payload = self._last()
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/search"))
        self.assertEqual(payload["engine"], "")

    def test_an_explicit_engine_is_still_passed_through(self):
        """Empty means "choose for me"; a named engine is an override, and the
        caller may have a reason (a query one engine handles better)."""
        self.browser.search("python list comprehension", engine="marginalia")
        self.assertEqual(self._last()[2]["engine"], "marginalia")

    def test_each_verb_targets_its_own_endpoint(self):
        cases = [
            (lambda: self.browser.open("https://example.com"), "/open"),
            (lambda: self.browser.navigate("https://example.com"), "/navigate"),
            (lambda: self.browser.click("#go"), "/click"),
            (lambda: self.browser.type_text("#q", "text"), "/type_text"),
            (lambda: self.browser.extract("h1"), "/extract"),
            (lambda: self.browser.close(), "/close"),
        ]
        for call, suffix in cases:
            with self.subTest(suffix=suffix):
                call()
                self.assertTrue(self._last()[1].endswith(suffix), suffix)

    def test_screenshot_decodes_base64_into_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        encoded = base64.b64encode(png).decode("ascii")
        browser = self.use_transport(post=_Response({
            "ok": True, "data": {"png_base64": encoded},
        }))
        result = browser.screenshot()
        self.assertEqual(result.screenshot, png)
        # The encoded copy is not left behind to bloat the trace.
        self.assertNotIn("png_base64", result.data)

    def test_screenshot_that_is_not_base64_is_rejected(self):
        browser = self.use_transport(post=_Response({
            "ok": True, "data": {"png_base64": "!!! not base64 !!!"},
        }))
        with self.assertRaises(BrowserUnavailable):
            browser.screenshot()

    def test_open_passes_its_timeout_through(self):
        self.browser.open("https://example.com", timeout=12.5)
        self.assertEqual(self._last()[2]["timeout"], 12.5)


# ---------------------------------------------------------------------------
# Sidecar: search correctness, tested offline
# ---------------------------------------------------------------------------
class SearchRelevanceTest(unittest.TestCase):
    """The relevance rule exists because of a real failure, so it is pinned.

    On the network this was built on, search pages arrive rewritten: the page
    reports the right URL and title while its result rows belong to an entirely
    different query. Returning those to a user is worse than admitting the
    engine is unusable.
    """

    def test_terms_drop_stopwords_and_short_tokens(self):
        terms = sidecar._query_terms("How to bake the sourdough bread?")
        self.assertIn("sourdough", terms)
        self.assertIn("bake", terms)
        self.assertNotIn("the", terms)
        self.assertNotIn("how", terms)

    def test_matching_result_is_kept(self):
        terms = sidecar._query_terms("how to bake sourdough bread")
        self.assertTrue(sidecar._is_relevant(
            "How to make sourdough bread", "https://example.com/sourdough", "", terms
        ))

    def test_result_for_a_different_query_is_dropped(self):
        terms = sidecar._query_terms("how to bake sourdough bread")
        self.assertFalse(sidecar._is_relevant(
            "Separate playlists between YouTube and YouTube Music",
            "https://support.google.com/youtubetv/", "", terms,
        ))

    def test_a_query_of_only_stopwords_accepts_everything(self):
        """Otherwise a query like "what is it" would filter out all results."""
        terms = sidecar._query_terms("what is it")
        self.assertEqual(terms, set())
        self.assertTrue(sidecar._is_relevant("anything", "https://x.test", "", terms))


class SearchUrlTest(unittest.TestCase):
    def test_bing_click_tracker_is_unwrapped(self):
        """Without this the caller gets bing.com/ck/a links that are useless to
        fetch and misleading to quote back."""
        # base64url of "https://www.python.org/" with Bing's leading "a1" marker.
        wrapped = (
            "https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8"
        )
        self.assertEqual(sidecar._unwrap_bing(wrapped), "https://www.python.org/")

    def test_duckduckgo_redirect_is_unwrapped(self):
        wrapped = (
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=x"
        )
        self.assertEqual(
            sidecar._unwrap_redirect(wrapped), "https://example.com/page"
        )

    def test_ordinary_urls_pass_through_untouched(self):
        for url in ("https://example.com/", "https://a.test/b?c=d"):
            self.assertEqual(sidecar._unwrap_redirect(url), url)

    def test_undecodable_bing_wrapper_is_left_alone(self):
        """Falling back to the wrapper beats returning an empty string."""
        wrapped = "https://www.bing.com/ck/a?u=a1!!!not-base64!!!"
        self.assertEqual(sidecar._unwrap_bing(wrapped), wrapped)

    def test_engines_filter_out_their_own_navigation(self):
        for url in (
            "https://www.bing.com/search?q=x",
            "https://marginalia-search.com/search?query=x",
            "https://duckduckgo.com/",
        ):
            self.assertTrue(sidecar._is_self_link(url), url)
        self.assertFalse(sidecar._is_self_link("https://www.python.org/"))


class SearchEngineRegistryTest(unittest.TestCase):
    def test_the_engines_that_work_are_registered(self):
        for name in ("marginalia", "bing"):
            self.assertIn(name, sidecar.SEARCH_ENGINES)

    def test_default_engine_is_the_one_measured_to_work(self):
        self.assertEqual(sidecar.DEFAULT_ENGINE, "marginalia")
        self.assertIn(sidecar.DEFAULT_ENGINE, sidecar._FALLBACK_ORDER)

    def test_a_first_fetch_often_returns_nothing_so_retry(self):
        """Marginalia returned 2 rows on the first fetch and 40 on the second.
        One attempt would have called a working engine dead."""
        self.assertGreaterEqual(sidecar._ATTEMPTS_PER_ENGINE, 2)

    def test_fallback_order_has_no_duplicates(self):
        self.assertEqual(
            len(sidecar._FALLBACK_ORDER), len(set(sidecar._FALLBACK_ORDER))
        )


class TextCleaningTest(unittest.TestCase):
    def test_collapses_whitespace_and_blank_runs(self):
        self.assertEqual(
            sidecar._clean("  a  b  \n\n\n\n c  "), "a b\n\nc"
        )

    def test_readable_text_is_bounded(self):
        """An unbounded page body would swamp a 1.5B model's context."""
        self.assertLessEqual(sidecar._MAX_TEXT, 50_000)
        self.assertGreater(sidecar._MAX_TEXT, 0)

    def test_action_timeout_is_bounded(self):
        self.assertLessEqual(sidecar._ACTION_TIMEOUT, 300.0)

    def test_browser_candidates_are_absolute_installed_paths(self):
        """The sidecar drives an installed browser; it never downloads one."""
        for candidate in sidecar.BROWSER_CANDIDATES:
            self.assertTrue(candidate.endswith(".exe"), candidate)
            self.assertIn("\\", candidate)

    def test_readable_js_prefers_main_and_drops_furniture(self):
        self.assertIn("main", sidecar._READABLE_JS)
        self.assertIn("innerText", sidecar._READABLE_JS)
        for noise in ("script", "style", "nav", "footer"):
            self.assertIn(noise, sidecar._READABLE_JS)


class SidecarIsolationTest(unittest.TestCase):
    def test_the_sidecar_cannot_import_alan(self):
        """The sidecar environment has Alan's src hook stripped from its ._pth.
        If the sidecar ever grew an `import alan`, that isolation would be
        silently gone and the two environments would couple."""
        source = Path(sidecar.__file__).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import alan", "from alan")):
                self.fail(f"the sidecar imports Alan: {stripped}")

    def test_sidecar_imports_browser_use_lazily(self):
        """Module-level, this file must import with nothing but the standard
        library, so Alan's own test suite can exercise the helpers above.

        This is a real constraint, not a style preference: browser-use is
        installed only in the browser env, so a module-level import would make
        the sidecar unimportable from Alan's own env and take these tests with
        it.
        """
        source = Path(sidecar.__file__).read_text(encoding="utf-8")
        at_module_level = True
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("def ", "async def ", "class ")):
                at_module_level = False
            if "import browser_use" in stripped and at_module_level:
                self.fail(
                    "browser_use is imported at module level, which breaks both "
                    f"the lazy-import rule and offline testing: {stripped}"
                )


class DoctorSurfaceTest(unittest.TestCase):
    """`alan doctor` must actually show the browser's state.

    The runtime already spends a network round-trip probing the sidecar and
    builds an explanatory note for it -- and the CLI originally dropped that note
    on the floor, leaving the highest-risk capability invisible in the one command
    a user runs when something is not working. This pins the reporting.
    """

    def test_status_exposes_a_browser_key_even_when_disabled(self) -> None:
        from alan.config import load_settings
        from alan.runtime import build_runtime

        settings = load_settings()
        settings.tools.browser = False
        runtime = build_runtime(settings, register_tools=False)
        try:
            browser = runtime.status()["browser"]
        finally:
            runtime.close()
        self.assertIsNone(browser["provider"])
        self.assertTrue(browser["note"], "a disabled browser still needs a reason")

    def test_doctor_renders_the_browser_note(self) -> None:
        """A source-level check, because rendering the real panel would build a
        live runtime. The regression it guards -- computing the note and not
        printing it -- is invisible to any behavioural test short of one that
        starts a sidecar. Both web paths (browser sidecar and the zero-config
        internet backends) must be visible."""
        cli_source = (
            Path(__file__).resolve().parents[1] / "src" / "alan" / "cli.py"
        ).read_text(encoding="utf-8")
        # The status-to-row mapping lives in _doctor_rows and doctor() renders
        # those rows; the guard follows the field to where it moved. The
        # mapping binds status['internet'] to a local, so assert the field
        # (internet['note']) actually reaches a rendered row.
        doctor_body = cli_source.split("def _doctor_rows(", 1)[1].split("@app.command()", 1)[0]
        self.assertIn("browser", doctor_body)
        self.assertIn("internet['note']", doctor_body)


if __name__ == "__main__":
    unittest.main()
