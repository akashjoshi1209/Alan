"""Phase 2: the agent-reach internet backends and how web.* prefers them.

What is pinned here, in behaviour terms:

* Exa search is one specific argv list (no shell string is ever built) and its
  transcript parses into structured results with clipped snippets;
* reading a page goes through ``r.jina.ai`` and will *refuse* a URL that is
  not http(s) -- no ``javascript:``, no missing host;
* ``web.search`` takes the internet path when available and quietly uses the
  browser path otherwise; naming an engine is an explicit opt-out of Exa;
* when there is no path at all the error names the fix, never a traceback.

Tests never touch the network: the subprocess runner is injected, and the
availabilities are fakes.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from alan.config import Settings
from alan.tools.internet import (
    MAX_READ_CHARS,
    ExaSearch,
    Internet,
    InternetUnavailable,
    JinaReader,
    _run,
    _validate_url,
    format_results,
)
from alan.tools.standard import StandardTools

TRANSCRIPT = """\
Title: 5. Data Structures — Python 3.14.8 documentation
URL: https://docs.python.org/3/tutorial/datastructures.html
Published: N/A
Author: N/A
Highlights:
Real snippet line one.
A longer line that is clipped later if it ever gets that long.

---

Title: List comprehension
URL: https://en.wikipedia.org/wiki/List_comprehension
Published: 1995-05-01
Author: Somebody
Highlights:
Another snippet for the second result.
"""

JSON_REPLY = json.dumps({"content": [{"type": "text", "text": TRANSCRIPT}]})


def _recorder(captured: list, payload: str = JSON_REPLY):
    """A ``runner`` that records the argv it was given and returns payload."""

    def run(argv, timeout):
        captured.append((list(argv), timeout))
        return payload

    return run


class TestExaSearch(unittest.TestCase):
    def test_search_builds_the_exact_argv_and_parses(self) -> None:
        captured: list = []
        exa = ExaSearch(runner=_recorder(captured))
        results = exa.search("python list comprehension", count=3)
        argv, timeout = captured[0]
        self.assertEqual(
            argv,
            [
                "mcporter", "call", "exa.web_search_exa",
                "--output", "json",
                "query=python list comprehension",
                "numResults=3",
            ],
        )
        self.assertGreater(timeout, 0)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://docs.python.org/3/tutorial/datastructures.html")
        self.assertIn("Real snippet line one.", results[0]["snippet"])
        self.assertEqual(results[1]["author"], "Somebody")

    def test_empty_query_is_refused(self) -> None:
        exa = ExaSearch(runner=_recorder([]))
        with self.assertRaises(ValueError):
            exa.search("   ")

    def test_count_is_clamped(self) -> None:
        captured: list = []
        exa = ExaSearch(runner=_recorder(captured))
        exa.search("q", count=999)
        self.assertIn("numResults=8", captured[0][0])
        exa.search("q", count=0)  # 0 means "no opinion", so the default stands
        self.assertIn("numResults=5", captured[1][0])

    def test_non_json_reply_is_an_error_not_a_crash(self) -> None:
        exa = ExaSearch(runner=_recorder([], payload="<html>scraper wall</html>"))
        with self.assertRaises(InternetUnavailable):
            exa.search("q")

    def test_missing_text_is_an_error(self) -> None:
        exa = ExaSearch(runner=_recorder([], payload='{"content": []}'))
        with self.assertRaises(InternetUnavailable):
            exa.search("q")

    def test_snippet_is_clipped(self) -> None:
        exa = ExaSearch(runner=_recorder([], payload=json.dumps({
            "content": [{
                "type": "text",
                "text": "Title: Long\nURL: https://x.y\nHighlights:\n" + ("y" * 4000),
            }],
        })))
        results = exa.search("q")
        self.assertLess(len(results[0]["snippet"]), 500)


class TestJinaReader(unittest.TestCase):
    def test_read_builds_the_exact_argv(self) -> None:
        captured: list = []
        jina = JinaReader(runner=_recorder(captured, payload="markdown text"))
        text = jina.read("https://example.com/page")
        argv, timeout = captured[0]
        self.assertEqual(
            argv,
            ["curl", "-s", "-L", "--max-time", "45", "https://r.jina.ai/https://example.com/page"],
        )
        self.assertEqual(text, "markdown text")

    def test_bare_host_is_coerced_to_https(self) -> None:
        captured: list = []
        jina = JinaReader(runner=_recorder(captured, payload="ok"))
        jina.read("example.com")
        self.assertIn("https://r.jina.ai/https://example.com", captured[0][0])

    def test_non_http_scheme_is_refused(self) -> None:
        jina = JinaReader(runner=_recorder([]))
        with self.assertRaises(ValueError):
            jina.read("javascript:alert(1)")
        with self.assertRaises(ValueError):
            jina.read("file:///etc/passwd")

    def test_missing_host_is_refused(self) -> None:
        jina = JinaReader(runner=_recorder([]))
        with self.assertRaises(ValueError):
            jina.read("https://")

    def test_empty_page_is_an_error(self) -> None:
        jina = JinaReader(runner=_recorder([], payload="   "))
        with self.assertRaises(InternetUnavailable):
            jina.read("https://example.com")

    def test_pages_are_clipped_for_the_model(self) -> None:
        jina = JinaReader(runner=_recorder([], payload="x" * (MAX_READ_CHARS + 10_000)))
        text = jina.read("https://example.com")
        self.assertLessEqual(len(text), MAX_READ_CHARS)


class TestRunSubprocess(unittest.TestCase):
    def test_timeout_becomes_an_actionable_error(self) -> None:
        with mock.patch("alan.tools.internet.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("curl", 9)):
            with self.assertRaisesRegex(InternetUnavailable, "9s"):
                _run(["curl", "x"], timeout=9.0)

    def test_nonzero_exit_carries_the_stderr(self) -> None:
        with mock.patch("alan.tools.internet.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=7, stdout="", stderr="could not resolve")
            with self.assertRaisesRegex(InternetUnavailable, "could not resolve"):
                _run(["curl", "x"], timeout=9.0)

    def test_missing_binary_is_an_error(self) -> None:
        with mock.patch("alan.tools.internet.subprocess.run",
                        side_effect=FileNotFoundError("no such tool")):
            with self.assertRaisesRegex(InternetUnavailable, "no such tool"):
                _run(["nope"], timeout=9.0)

    def test_npm_cmd_shim_is_resolved_before_spawn(self) -> None:
        # npm's Windows shims only run when given their full path; the bare
        # name ``mcporter`` cannot be spawned by CreateProcess. Resolution
        # belongs in the default runner so the argv a test sees stays exact.
        shim = r"C:\Users\me\AppData\Roaming\npm\mcporter.CMD"
        with mock.patch("alan.tools.internet.shutil.which", return_value=shim), \
             mock.patch("alan.tools.internet.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            self.assertEqual(_run(["mcporter", "call", "exa.web_search_exa"], 9.0), "ok")
            spawned = run.call_args[0][0]
            self.assertEqual(spawned[0], shim)
            self.assertEqual(spawned[1:], ["call", "exa.web_search_exa"])


class TestValidateUrl(unittest.TestCase):
    def test_schemes_and_hosts(self) -> None:
        self.assertEqual(_validate_url("example.com/page"), "https://example.com/page")
        self.assertEqual(_validate_url("http://local.dev/x"), "http://local.dev/x")
        with self.assertRaises(ValueError):
            _validate_url("")
        with self.assertRaises(ValueError):
            _validate_url("https://")

    def test_local_addresses_are_still_readable_but_remote(self) -> None:
        # The whole point of the proxy: a local address is a *string* here, and
        # the fetch happens on Jina's machine, not Alan's.
        self.assertEqual(_validate_url("http://127.0.0.1:8080/admin"), "http://127.0.0.1:8080/admin")


class TestInternetFacade(unittest.TestCase):
    def test_available_combines_both_backends(self) -> None:
        internet = Internet(
            search_backend=SimpleNamespace(available=lambda: (True, "s")),
            read_backend=SimpleNamespace(available=lambda: (False, "no curl")),
        )
        ok, reason = internet.available()
        self.assertFalse(ok)
        self.assertIn("no curl", reason)

        internet.read_backend = SimpleNamespace(available=lambda: (True, "r"))
        self.assertTrue(internet.available()[0])

    def test_delegates_search_and_read(self) -> None:
        seen: list = []
        internet = Internet(
            search_backend=SimpleNamespace(
                search=lambda query, count=5: seen.append(("s", query, count)) or [],
            ),
            read_backend=SimpleNamespace(read=lambda url: seen.append(("r", url)) or "page"),
        )
        internet.search("q", count=3)
        internet.read("https://x.y")
        self.assertEqual(seen, [("s", "q", 3), ("r", "https://x.y")])


class TestWebToolsPreferInternet(unittest.TestCase):
    def _tools(
        self,
        *,
        internet=None,
        browser=None,
    ) -> StandardTools:
        return StandardTools(
            Settings(),
            execution=None,
            memory=None,
            browser=browser,
            internet=internet,
        )

    @staticmethod
    def _internet(results=None, read="page text"):
        calls = []

        class Fake:
            def available(self_inner):
                return True, "ready"

            def search(self_inner, query, count=5):
                calls.append(("search", query, count))
                return results or []

            def read(self_inner, url):
                calls.append(("read", url))
                return read

        return Fake(), calls

    @staticmethod
    def _browser():
        calls = []

        class Fake:
            def search(self_inner, query, engine=""):
                calls.append(("search", query, engine))
                return SimpleNamespace(ok=True, content="browser search", url="", data={})

            def open(self_inner, url):
                calls.append(("open", url))
                return SimpleNamespace(ok=True, content="browser page", url=url, title="t")

        return Fake(), calls

    def test_search_prefers_exa_when_configured(self) -> None:
        internet, i_calls = self._internet(results=[{"title": "t", "url": "https://u", "snippet": "s"}])
        tools = self._tools(internet=internet)
        out = tools._web_do_search("pizza recipes")
        self.assertEqual(i_calls, [("search", "pizza recipes", 5)])
        self.assertIn("https://u", out["content"])
        self.assertEqual(out["data"]["source"], "exa")

    def test_naming_an_engine_opts_out_of_exa(self) -> None:
        internet, i_calls = self._internet()
        browser, b_calls = self._browser()
        tools = self._tools(internet=internet, browser=browser)
        out = tools._web_do_search("pizza", engine="marginalia")
        self.assertEqual(i_calls, [], "explicit engine must not use Exa")
        self.assertEqual(b_calls, [("search", "pizza", "marginalia")])
        self.assertEqual(out["content"], "browser search")

    def test_search_falls_back_to_browser_when_internet_unavailable(self) -> None:
        browser, b_calls = self._browser()

        class Down:
            def available(self_inner):
                return False, "no mcporter"

        tools = self._tools(internet=Down(), browser=browser)
        tools._web_do_search("pizza")
        self.assertEqual(b_calls, [("search", "pizza", "")])

    def test_search_with_no_path_names_the_fix(self) -> None:
        tools = self._tools(internet=None, browser=None)
        with self.assertRaisesRegex(RuntimeError, "enable \\[tools\\] internet"):
            tools._web_do_search("pizza")

    def test_open_prefers_jina(self) -> None:
        internet, i_calls = self._internet(read="jina page")
        tools = self._tools(internet=internet)
        out = tools._web_do_open("example.com")
        self.assertEqual(i_calls, [("read", "https://example.com")])
        self.assertEqual(out["content"], "jina page")

    def test_open_uses_browser_when_internet_off(self) -> None:
        browser, b_calls = self._browser()
        tools = self._tools(browser=browser)
        out = tools._web_do_open("example.com")
        self.assertEqual(b_calls, [("open", "https://example.com")])
        self.assertEqual(out["content"], "browser page")

    def test_open_falls_back_to_browser_when_jina_fails(self) -> None:
        class Failing:
            def available(self_inner):
                return True, "ready"

            def read(self_inner, url):
                raise InternetUnavailable("jina down")

        browser, _ = self._browser()
        tools = self._tools(internet=Failing(), browser=browser)
        out = tools._web_do_open("example.com")
        self.assertEqual(out["content"], "browser page")

    def test_open_with_no_path_names_the_fix(self) -> None:
        tools = self._tools(internet=None, browser=None)
        with self.assertRaisesRegex(RuntimeError, "enable \\[tools\\] internet"):
            tools._web_do_open("example.com")


class TestFormatResults(unittest.TestCase):
    def test_numbers_and_urls(self) -> None:
        text = format_results(
            [{"title": "A", "url": "https://a.x", "snippet": "aa"},
             {"title": "", "url": "https://b.x", "snippet": ""}]
        )
        self.assertIn("1. A", text)
        self.assertIn("https://a.x", text)
        self.assertIn("(no title)", text)
        self.assertNotIn("None", text)


class RuntimeSurfaceTest(unittest.TestCase):
    """`alan doctor` and the runtime status must surface the internet backends.

    The probe only asks whether mcporter/curl exist -- no network -- so these
    run offline alongside the browser DoctorSurface tests.
    """

    def test_status_exposes_internet_even_when_disabled(self) -> None:
        from alan.config import load_settings
        from alan.runtime import build_runtime

        settings = load_settings()
        settings.tools.internet = False
        runtime = build_runtime(settings, register_tools=False)
        try:
            entry = runtime.status()["internet"]
        finally:
            runtime.close()
        self.assertIsNone(entry["provider"])
        self.assertEqual(entry["note"], "disabled")

    def test_status_reports_enabled_internet(self) -> None:
        from alan.config import load_settings
        from alan.runtime import build_runtime

        settings = load_settings()
        settings.tools.internet = True
        runtime = build_runtime(settings, register_tools=False)
        try:
            entry = runtime.status()["internet"]
        finally:
            runtime.close()
        self.assertEqual(entry["provider"], "internet")
        self.assertTrue(entry["note"], "an enabled internet must say something")


if __name__ == "__main__":
    unittest.main()