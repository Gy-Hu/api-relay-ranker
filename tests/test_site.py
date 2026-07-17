import tempfile
import unittest
from pathlib import Path

from relayrank.models import RankedVendor
from relayrank.site import write_site


class SiteTests(unittest.TestCase):
    def test_site_escapes_vendor_names_and_shows_source_evidence(self):
        result = RankedVendor(
            rank=1,
            vendor="Example <Relay>",
            score=88.2,
            confidence=0.91,
            source_count=3,
            effective_weight=2.45,
            rank_best=1,
            rank_worst=4,
            contributions=(
                {
                    "source": "helpaio",
                    "rank": 2,
                    "total_vendors": 19,
                    "raw_score": 90.0,
                    "weight": 0.9,
                    "metrics": {"score": 89.5},
                },
            ),
            website_url="https://relay.example/",
        )
        reports = [
            {"name": name, "ok": True, "vendor_count": 10, "fetched_at": "2026-07-17T06:42:23+00:00"}
            for name in ("helpaio", "zhaotutu", "apiranking", "tokhub")
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = write_site(directory, [result], reports, "2026-07-17T06:42:23+00:00")
            rendered = Path(target).read_text(encoding="utf-8")

        self.assertIn("Example &lt;Relay&gt;", rendered)
        self.assertNotIn("Example <Relay>", rendered)
        self.assertIn('href="https://relay.example/"', rendered)
        self.assertIn('rel="noopener noreferrer external"', rendered)
        self.assertIn("#2 / 19", rendered)
        self.assertIn("4/4 正常", rendered)
        self.assertIn("高置信", rendered)


if __name__ == "__main__":
    unittest.main()
