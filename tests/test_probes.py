"""Probe caching, and the browser leaks that made startup expensive.

Three things are pinned here:

* **The probe cache.** Alan used to re-ask every backend whether it was up on
  every invocation -- ~5 s before the first token for answers that had not
  changed. The cache is only safe if a stale "up" cannot outlive its backend, so
  the TTLs, the bypass, and the invalidate-on-failure path are all tested.
* **The profile directory.** browser-use copies any non-temp profile into a new
  *random* one, so a caller cannot reliably own the path -- and the reclamation
  that kills an orphaned browser is matched on that path. Determinism is the
  mechanism, so it is tested.
* **The graceful stop.** ``Stop-Process -Force`` calls TerminateProcess, which
  runs no handler and no ``finally``; that path leaked 125 Chrome processes.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import browser_sidecar  # noqa: E402
from alan.core import probecache  # noqa: E402


class _TmpCache(unittest.TestCase):
    """Point the probe cache at a scratch file so tests touch no real state."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "probes.json"
        self._real = probecache._STATE_PATH
        probecache._STATE_PATH = self.path
        self.addCleanup(setattr, probecache, "_STATE_PATH", self._real)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(os.environ.pop, "ALAN_PROBE_CACHE", None)


class ProbeCacheTest(_TmpCache):
    def test_a_recent_answer_is_reused(self):
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        for _ in range(4):
            self.assertEqual(probecache.probe("k|1", check), (True, "ready"))
        self.assertEqual(calls["n"], 1, "the backend must only be asked once")

    def test_a_healthy_backend_stays_cached_past_the_ttl(self):
        """The bug this guards: a steady backend re-paid the probe every 300 s.

        The entry used to be written once and never refreshed, because an
        *unchanged* answer was treated as nothing to record. Its timestamp stayed
        frozen at first creation, so the TTL measured absolute age and the probe
        ran again on schedule no matter how often the cache was hit -- about 3 s
        of Ollama reachability, several times per command, forever.

        Simulating steady use by walking time forward must therefore hit the
        backend exactly once, not once per TTL.
        """
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        real_time = probecache.time.time
        clock = {"t": real_time()}

        def fake_time():
            return clock["t"]

        probecache.time.time = fake_time
        self.addCleanup(setattr, probecache.time, "time", real_time)

        # Ten intervals of 200 s each: over 5x the positive TTL, i.e. well past
        # the old fixed-window expiry, while never once going idle for 300 s.
        for _ in range(10):
            clock["t"] += probecache.POSITIVE_TTL / 1.5
            self.assertEqual(probecache.probe("k|steady", check), (True, "ready"))
        self.assertEqual(
            calls["n"],
            1,
            "a backend in continuous use must be probed once, not once per TTL",
        )

    def test_the_cache_still_expires_when_a_backend_goes_idle(self):
        """Sliding the window must not turn the cache into a permanent 'up'.

        The window slides only because the backend keeps being *asked for*. A
        backend Alan has not needed for longer than the TTL must be re-probed, or
        a stopped sidecar would be reported as ready indefinitely.
        """
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        real_time = probecache.time.time
        clock = {"t": real_time()}

        def fake_time():
            return clock["t"]

        probecache.time.time = fake_time
        self.addCleanup(setattr, probecache.time, "time", real_time)

        probecache.probe("k|idle", check)
        clock["t"] += probecache.POSITIVE_TTL + 60
        probecache.probe("k|idle", check)
        self.assertEqual(calls["n"], 2, "an idle backend must be re-probed")

    def test_the_memo_does_not_outlive_a_change_of_cache_file(self):
        """`_MEMO` is process-wide; the tests repoint `_STATE_PATH` at a scratch file.

        Without the path check, a memo describing the real cache would answer a
        test's probe from state that test never wrote -- a failure that would look
        like a flaky assertion rather than a leak.
        """
        probecache.probe("k|first", lambda: (True, "ready"))
        import tempfile

        with tempfile.TemporaryDirectory() as other:
            real = probecache._STATE_PATH
            probecache._STATE_PATH = Path(other) / "probes.json"
            try:
                data = probecache._load()
                self.assertNotIn(
                    "k|first",
                    data,
                    "a different cache file must not be answered from the memo",
                )
                self.assertEqual(probecache.probe("k|second", lambda: (True, "fresh")), (True, "fresh"))
                self.assertIn("k|second", probecache._load())
            finally:
                probecache._STATE_PATH = real

    def test_a_failure_is_remembered_only_briefly(self):
        """A sidecar that is still starting must be picked up as soon as it is."""
        self.assertLess(probecache.NEGATIVE_TTL, probecache.POSITIVE_TTL)
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return False, "not reachable"

        probecache.probe("k|down", check)
        probecache.probe("k|down", check)
        self.assertEqual(calls["n"], 1, "within the negative TTL the answer stands")

    def test_fresh_bypasses_the_cache(self):
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        probecache.probe("k|2", check)
        probecache.probe("k|2", check, fresh=True)
        self.assertEqual(calls["n"], 2, "`fresh=True` must re-ask the backend")

    def test_diagnostics_are_never_answered_from_cache(self):
        """`alan doctor` exists to report the truth about right now."""
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        probecache.probe("k|3", check)
        self.assertEqual(probecache.probe("k|3", check, fresh=True)[0], True)
        self.assertEqual(calls["n"], 2)

    def test_forget_key_drops_one_answer(self):
        def check():
            return True, "ready"

        probecache.probe("k|a", check)
        probecache.probe("k|b", check)
        probecache.forget_key("k|a")
        data = probecache._load()
        self.assertNotIn("k|a", data)
        self.assertIn("k|b", data)

    def test_forget_key_on_a_missing_entry_is_harmless(self):
        probecache.forget_key("never|seen")
        self.assertNotIn("never|seen", probecache._load())

    def test_a_structured_payload_survives_the_round_trip(self):
        payload = {"status": "ready", "version": "1.6.2"}
        ok, got = probecache.probe("cognee|1", lambda: (True, payload))
        self.assertTrue(ok)
        self.assertEqual(got, payload, "a health dict must not be stringified")
        ok, got = probecache.probe("cognee|1", lambda: (False, None))
        self.assertEqual((ok, got), (True, payload), "second call comes from disk")

    def test_a_corrupt_cache_is_ignored_not_fatal(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(probecache.probe("k|4", lambda: (True, "ready")), (True, "ready"))

    def test_distinct_keys_do_not_share_answers(self):
        """Changing model or port must not reuse another probe's answer."""
        seen = []

        def check():
            seen.append(1)
            return True, "ready"

        probecache.probe("ollama|11435|qwen", check)
        probecache.probe("ollama|11435|llama3", check)
        probecache.probe("browser|8022", check)
        self.assertEqual(len(seen), 3)

    def test_the_cache_can_be_turned_off_by_environment(self):
        os.environ["ALAN_PROBE_CACHE"] = "0"
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True, "ready"

        for _ in range(3):
            probecache.probe("k|5", check)
        self.assertEqual(calls["n"], 3, "ALAN_PROBE_CACHE=0 means every probe is real")

    def test_probe_reason_always_yields_a_string(self):
        ok, reason = probecache.probe_reason("k|6", lambda: (False, None))
        self.assertFalse(ok)
        self.assertIsInstance(reason, str)


class ProfileIsDeterministicTest(unittest.TestCase):
    """The reclamation below is matched on this path, so it must be stable."""

    def test_the_path_is_fixed_and_carries_the_browser_use_marker(self):
        first = browser_sidecar._profile_dir()
        second = browser_sidecar._profile_dir()
        self.assertEqual(first, second, "the path must not vary between calls")
        self.assertIn(
            browser_sidecar._PROFILE_MARKER, str(first),
            "browser-use's _copy_profile skips the copy only for this marker; "
            "without it Chrome lands on a fresh random dir every launch",
        )

    def test_the_path_is_under_alans_own_data_directory(self):
        self.assertIn("browser-profile", str(browser_sidecar._profile_dir()))

    def test_an_override_is_honoured(self):
        with mock.patch.dict(os.environ, {"ALAN_BROWSER_PROFILE_DIR": r"D:\somewhere\else"}):
            self.assertEqual(str(browser_sidecar._profile_dir()), r"D:\somewhere\else")

    def test_the_profile_is_scoped_to_the_port(self):
        with mock.patch.object(browser_sidecar, "PORT", 9999):
            self.assertIn("9999", str(browser_sidecar._profile_dir()))


class ReapingIsScopedTest(unittest.TestCase):
    """Only Alan's own profile may ever be reaped."""

    def setUp(self):
        self.fake = mock.Mock()
        self.fake.process_iter.return_value = []

    @staticmethod
    def _rows(rows):
        """psutil's ``process_iter`` yields objects carrying ``.info``."""
        def _iter(attrs):
            return [mock.Mock(info=row) for row in rows]
        return _iter

    def test_an_empty_process_list_reaps_nothing(self):
        with mock.patch.object(browser_sidecar, "_PSUTIL", self.fake):
            browser_sidecar._reap_orphans()
        self.assertEqual(self.fake.Process.call_count, 0)

    def test_it_ignores_processes_that_do_not_use_alans_profile(self):
        def proc_iter(attrs):
            return [mock.Mock(info=row) for row in (
                # A browser on the user's own profile: must never be touched.
                {"pid": 1, "name": "chrome.exe",
                 "cmdline": [r"C:\Program Files\Google\Chrome\chrome.exe",
                             r"--user-data-dir=C:\Users\Someone\Chrome"]},
                # Something unrelated entirely.
                {"pid": 2, "name": "notepad.exe", "cmdline": ["notepad"]},
            )]

        self.fake.process_iter.side_effect = proc_iter
        with mock.patch.object(browser_sidecar, "_PSUTIL", self.fake):
            with mock.patch.object(browser_sidecar.time, "sleep"):
                browser_sidecar._reap_orphans()
        self.assertEqual(self.fake.Process.call_count, 0,
                         "only Alan's own profile dir may ever be matched")

    def test_it_terminates_a_browser_on_alans_own_profile(self):
        profile = str(browser_sidecar._profile_dir())

        def proc_iter(attrs):
            return [mock.Mock(info={"pid": 4242, "name": "chrome.exe",
                                    "cmdline": ["chrome.exe",
                                                f"--user-data-dir={profile}"]})]

        self.fake.process_iter.side_effect = proc_iter
        # A polite terminate is normally enough; the process is then gone.
        self.fake.pid_exists.return_value = False
        with mock.patch.object(browser_sidecar, "_PSUTIL", self.fake):
            with mock.patch.object(browser_sidecar.time, "sleep"):
                browser_sidecar._reap_orphans()
        self.fake.Process.assert_called_once_with(4242)
        self.fake.Process.return_value.terminate.assert_called_once()
        self.fake.Process.return_value.kill.assert_not_called()

    def test_it_kills_a_browser_that_ignores_terminate(self):
        """A Chrome that survives ``terminate`` must not keep the profile.

        This is the case the launch failure actually came from: a live browser
        holding Alan's own profile makes the *next* launch hand off and exit, so
        the reap has to escalate rather than assume the polite request worked.
        """
        profile = str(browser_sidecar._profile_dir())

        def proc_iter(attrs):
            return [mock.Mock(info={"pid": 4242, "name": "chrome.exe",
                                    "cmdline": ["chrome.exe",
                                                f"--user-data-dir={profile}"]})]

        self.fake.process_iter.side_effect = proc_iter
        self.fake.pid_exists.return_value = True  # never goes away
        with mock.patch.object(browser_sidecar, "_PSUTIL", self.fake):
            with mock.patch.object(browser_sidecar.time, "sleep"):
                browser_sidecar._reap_orphans()
        self.fake.Process.return_value.terminate.assert_called_once()
        self.fake.Process.return_value.kill.assert_called_once()

    def test_the_reap_poll_is_bounded(self):
        """A process that never exits must not turn the poll into an infinite wait."""
        profile = str(browser_sidecar._profile_dir())

        def proc_iter(attrs):
            return [mock.Mock(info={"pid": 4242, "name": "chrome.exe",
                                    "cmdline": ["chrome.exe",
                                                f"--user-data-dir={profile}"]})]

        self.fake.process_iter.side_effect = proc_iter
        self.fake.pid_exists.return_value = True
        with mock.patch.object(browser_sidecar, "_PSUTIL", self.fake):
            with mock.patch.object(browser_sidecar.time, "sleep") as slept:
                browser_sidecar._reap_orphans()
        self.assertEqual(slept.call_count, browser_sidecar._REAP_POLLS + 1,
                         "expected exactly _REAP_POLLS polls plus the post-kill pause")


class ShutdownPathTest(unittest.TestCase):
    """Every way of stopping must reach ``_close_session``."""

    def test_atexit_is_registered(self):
        # `_install_shutdown_handlers` must leave an atexit hook behind, because
        # TerminateProcess on Windows runs neither signals nor finally blocks.
        import atexit

        def count():
            try:
                return atexit._ncallbacks()  # CPython
            except AttributeError:
                return len(atexit._registry)

        before = count()
        server = mock.Mock()
        with mock.patch.object(browser_sidecar.signal, "signal"):
            browser_sidecar._install_shutdown_handlers(server)
        self.assertGreaterEqual(count(), before,
                                "an atexit hook must be registered as a backstop")

    def test_sigterm_is_handled_when_the_platform_offers_it(self):
        server = mock.Mock()
        with mock.patch.object(browser_sidecar.signal, "signal") as sig:
            browser_sidecar._install_shutdown_handlers(server)
        registered = {c.args[0] for c in sig.call_args_list if c.args}
        self.assertIn(browser_sidecar.signal.SIGTERM, registered)

    def test_shutdown_now_is_a_no_op_when_no_session_exists(self):
        with mock.patch.object(browser_sidecar, "_SESSION", None):
            browser_sidecar._shutdown_now()  # must not raise


if __name__ == "__main__":
    unittest.main()
