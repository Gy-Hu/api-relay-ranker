import gzip
import unittest
from datetime import date
from pathlib import Path

from relayrank.adapters import parse_helpaio, parse_okkmax, parse_relaypick
from relayrank.adapters.common import safe_http_url
from relayrank.adapters.veridrop import Report, build_observation, parse_detail, parse_search

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

    def test_relaypick_reads_every_eligible_row_with_domain_and_date(self):
        rows = parse_relaypick(fixture("relaypick.html"))
        self.assertEqual(len(rows), 90)
        packy = next(o for o in rows if o.domain == "packyapi.com")
        self.assertEqual((packy.score, packy.observed_at), (63.1, date(2026, 10, 1)))
        self.assertIn("authenticity_unsampled", packy.issues)

    def test_relaypick_table_and_data_disagreement_is_rejected(self):
        body = fixture("relaypick.html").replace(b"<tr", b"<tr-x", 3)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            parse_relaypick(body)

    def test_okkmax_uses_composite_and_probe_bucket_date(self):
        rows = {o.domain: o for o in parse_okkmax(fixture("okkmax-list.html"), fixture("okkmax-availability.html"))}
        timi = rows["timicc.com"]
        self.assertAlmostEqual(timi.score, 79.37145396825399)
        self.assertEqual(timi.observed_at, date(2026, 10, 1))
        unscored = [o for o in rows.values() if o.score is None]
        self.assertTrue(unscored and all(o.state == "missing" and o.observed_at is None for o in unscored))

    def test_veridrop_search_lists_hosts_with_counts_and_skips_sponsors(self):
        hits = parse_search(fixture("veridrop-search-right.codes.html"))
        self.assertEqual(hits, [("right.codes", 54), ("www.right.codes", 62), ("api.right.codes", 0)])

    def test_veridrop_detail_parses_history_and_keeps_invalid_reports(self):
        page = parse_detail(fixture("veridrop-detail-micuapi.html"))
        self.assertEqual((page.page, page.pages, len(page.reports)), (1, 4, 50))
        first = page.reports[0]
        self.assertEqual((first.day, first.protocol, first.score, first.verdict), (date(2026, 10, 1), "Claude", 99, "通过"))
        self.assertTrue(any(r.score is None and r.verdict == "检测无效" for r in page.reports))

    def test_veridrop_cold_placeholder_is_missing_not_zero(self):
        self.assertIsNone(parse_detail("<html><body><p>数据正在整理</p></body></html>".encode()))
        with self.assertRaisesRegex(ValueError, "history"):
            parse_detail(b"<html><body>redesigned</body></html>")

    def test_veridrop_composite_is_not_driven_by_protocol_mix(self):
        day = date(2026, 10, 1)
        # Twenty failing OpenAI reports vs ten passing Claude reports: a pooled median would be 0.
        reports = [Report(day, "OpenAI", "gpt", 0, "未达标", None)] * 20
        reports += [Report(day, "Claude", "opus", 90, "通过", None)] * 10
        reports += [Report(day, "Claude", "opus", None, "检测无效", None)] * 5
        reports += [Report(date(2026, 6, 1), "Claude", "opus", 0, "未达标", None)] * 50
        observation = build_observation("example.com", {"api.example.com": reports}, [], day, 60)
        self.assertEqual(observation.sample_count, 30)
        self.assertAlmostEqual(observation.score, (0 * 20 / 23 + 90 * 10 / 13) / (20 / 23 + 10 / 13))
        self.assertIn("veridrop_protocols_disagree", observation.issues)


if __name__ == "__main__":
    unittest.main()
