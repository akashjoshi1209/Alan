"""Alan smoke tests - stdlib only, no network required.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

import alan
from alan.assistant import Alan
from alan.config import CONFIG_DIR, HOME, PROVIDER_NAMES, SETTINGS_FILE, load_settings
from alan.llm import known_providers


class SmokeTest(unittest.TestCase):
    def test_version_present(self) -> None:
        self.assertTrue(alan.__version__)

    def test_home_is_dir(self) -> None:
        self.assertTrue(HOME.is_dir(), f"home not found: {HOME}")

    def test_settings_file_exists(self) -> None:
        self.assertTrue(SETTINGS_FILE.is_file())

    def test_default_settings(self) -> None:
        settings = load_settings()
        self.assertEqual(settings.name, "Alan")
        self.assertIn(settings.provider, ("auto", *PROVIDER_NAMES))

    def test_known_providers(self) -> None:
        """Every provider is constructible and reported by doctor.

        Compared against ``PROVIDER_NAMES`` rather than a literal set: the
        contract is that the list is *enumerated completely*, and a hardcoded
        set would fail for the right reason (a provider was added) at the wrong
        place, while a provider silently missing from the table would still
        pass. Adding a provider must never make this test pass by accident.
        """
        rows = known_providers(load_settings())
        names = {row[0] for row in rows}
        self.assertEqual(names, set(PROVIDER_NAMES))

    def test_assistant_history(self) -> None:
        # Instantiate without calling a provider (provider may be unavailable).
        from alan.llm import build_provider

        settings = load_settings()
        try:
            provider = build_provider(settings)
        except Exception:
            self.skipTest("no provider available in this environment")
        bot = Alan(settings=settings, provider=provider)
        try:
            self.assertEqual(bot.history, [])
            bot.reset()
            self.assertEqual(len(bot.history), 0)
        finally:
            provider.close()


if __name__ == "__main__":
    unittest.main()
