import unittest
from pathlib import Path

from relayrank.io import load_config


class ConfigTests(unittest.TestCase):
    def test_neko_code_and_neko_api_are_distinct_vendors(self):
        config_path = Path(__file__).parents[1] / "examples" / "config.toml"
        _, _, aliases = load_config(config_path)

        self.assertEqual(aliases["nekocode"], "Neko Code")
        self.assertEqual(aliases["nekoapi"], "Neko API")
        self.assertNotEqual(aliases["nekocode"], aliases["nekoapi"])


if __name__ == "__main__":
    unittest.main()
