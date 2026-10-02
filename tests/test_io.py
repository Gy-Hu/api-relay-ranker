import tempfile
import unittest
from datetime import date
from pathlib import Path

from relayrank.io import load_config, load_exclusions
from relayrank.live import SOURCES

DAY = date(2026, 10, 1)


class ConfigTests(unittest.TestCase):
    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_shipped_config_covers_every_live_source(self):
        _, sources, _, _ = load_config(Path(__file__).parents[1] / "examples" / "config.toml", DAY)
        self.assertEqual(set(sources), {info.name for info in SOURCES})

    def test_retired_or_misspelled_aggregation_keys_fail_loudly(self):
        for key in ("variance_penalty", "four_source_bonus", "half_life"):
            with self.assertRaisesRegex(ValueError, "unknown \\[aggregation\\] keys"):
                load_config(self.write(f"[aggregation]\n{key} = 1\n"), DAY)

    def test_duplicate_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate source"):
            load_config(self.write('[[sources]]\nname = "a"\n[[sources]]\nname = "a"\n'), DAY)

    def test_owner_exclusion_matches_alias_domains_and_keeps_unrelated_vendors(self):
        path = self.write('[[exclusions]]\ndomain = "https://www.blocked.com"\nreason = "registration closed"\n')
        self.assertEqual(load_exclusions(path, {"Renamed": ("other.ai", "blocked.com"),
                                               "Available": ("available.com",)}),
                         {"Renamed": "registration closed"})

    def test_owner_exclusion_requires_reason(self):
        with self.assertRaisesRegex(ValueError, "domain and reason"):
            load_exclusions(self.write('[[exclusions]]\ndomain = "blocked.com"\n'), {})


if __name__ == "__main__":
    unittest.main()
