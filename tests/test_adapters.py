import gzip
import unittest
from datetime import date
from pathlib import Path

from relayrank.adapters import parse_helpaio, parse_okkmax
from relayrank.adapters.common import safe_http_url

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return gzip.decompress((FIXTURES / (name + ".gz")).read_bytes())


class AdapterTests(unittest.TestCase):
    def test_vendor_links_only_allow_http_urls(self):
        for url in ["javascript:alert(1)", "//example.com", "https://user:secret@example.com"]:
            self.assertIsNone(safe_http_url(url))

    def test_helpaio_keeps_composite_domain_and_missing_scores(self):
        rows = {o.vendor: o for o in parse_helpaio(fixture("helpaio.html"))}
        micu = rows["Micu"]
        self.assertEqual((micu.score, micu.domain, micu.observed_at), (79.49, "www.micuapi.ai", date(2026, 10, 1)))
        self.assertEqual(micu.raw_evidence["uptime3d"], 93.59)
        self.assertEqual(micu.raw_evidence["listed_days"], 257)
        self.assertEqual(rows["Duck Code"].state, "missing")
        self.assertIsNone(rows["Duck Code"].score)
        self.assertEqual(rows["Yunwu"].score, 0)
        self.assertIn("source_zero_availability_requires_verification", rows["Yunwu"].issues)

    def test_helpaio_formula_drift_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "formula mismatch"):
            parse_helpaio(fixture("helpaio.html").replace(b">79.49<", b">99.49<", 1))

    def test_okkmax_uses_composite_and_probe_bucket_date(self):
        rows = {o.domain: o for o in parse_okkmax(fixture("okkmax-list.html"), fixture("okkmax-availability.html"))}
        timi = rows["timicc.com"]
        self.assertAlmostEqual(timi.score, 79.37145396825399)
        self.assertEqual(timi.observed_at, date(2026, 10, 1))
        unscored = [o for o in rows.values() if o.score is None]
        self.assertTrue(unscored and all(o.state == "missing" and o.observed_at is None for o in unscored))


if __name__ == "__main__":
    unittest.main()
