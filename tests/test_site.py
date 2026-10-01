import tempfile
import unittest
from datetime import date
from pathlib import Path

from relayrank.engine import aggregate
from relayrank.live import SOURCES as SOURCE_INFO
from relayrank.models import Config, Observation, Source
from relayrank.site import write_site

DAY = date(2026, 10, 1)


class SiteTests(unittest.TestCase):
    def test_site_escapes_untrusted_names_and_links_every_source(self):
        sources = {info.name: Source(info.name) for info in SOURCE_INFO}
        rows = [Observation("helpaio", "Evil <Relay>", domain="evil.example", score=90, observed_at=DAY,
                            website_url='https://evil.example/"><script>'),
                Observation("okkmax", "Evil <Relay>", domain="evil.example", score=70, observed_at=DAY),
                Observation("helpaio", "Other", domain="other.example", score=10, observed_at=DAY),
                Observation("okkmax", "Other", domain="other.example", score=20, observed_at=DAY)]
        config = Config(DAY, minimum_peers=1)
        ranking = aggregate(rows, sources, config)
        with tempfile.TemporaryDirectory() as directory:
            rendered = write_site(directory, ranking, [], sources, config, "2026-10-01T00:00:00Z").read_text(encoding="utf-8")
        self.assertIn("Evil &lt;Relay&gt;", rendered)
        self.assertNotIn("<Relay>", rendered)
        self.assertNotIn('"><script>', rendered)
        for info in SOURCE_INFO:
            self.assertIn(f'href="{info.homepage}"', rendered)


if __name__ == "__main__":
    unittest.main()
