import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

from relayrank.adapters import ztest

FIXTURES = Path(__file__).parent / "fixtures"
DIRECTORY = (FIXTURES / "ztest-sites.html").read_bytes()
DETAIL = (FIXTURES / "ztest-nekocode.html").read_text(encoding="utf-8")
URL = "https://ztest.ai/site/nekocode"


def collect(detail=DETAIL, directory=DIRECTORY):
    pages = {("ztest-sites", ztest.URL): directory, ("ztest-site-nekocode", URL): detail.encode()}
    return ztest.collect(lambda label, url: pages[(label, url)])


class ZtestTests(unittest.TestCase):
    def test_published_mean_not_directory_rank_uptime_or_single_model_score(self):
        row, = collect()
        self.assertEqual((row.source, row.vendor, row.domain), ("ztest", "nekocode", "nekocode.ai"))
        self.assertEqual((row.source_url, row.website_url), (URL, "https://nekocode.ai/"))
        self.assertEqual((row.score, row.state, row.sample_count), (83.8, "valid", None))
        self.assertEqual(row.raw_evidence, {"ranked_combinations": 5, "latest_check": "2026-11-21 11:58",
                                            "availability_checks_24h": 714, "availability_checks_7d": 4441})

    def test_check_date_not_snapshot_or_listing_date(self):
        row, = collect(DETAIL.replace("最近检查 2026-11-21 11:58", "最近检查 2026-11-20 23:58"))
        self.assertEqual(row.observed_at, date(2026, 11, 20))

    def test_unranked_is_missing_even_with_successful_or_failed_availability_checks(self):
        unranked = (DETAIL.replace("83.8", "—").replace("平均综合评分", "暂无评分")
                    .replace("5 个组合进入排名", "暂无进入排名的组合"))
        for rate in ("0.0%", "99.9%"):
            with self.subTest(rate=rate):
                row, = collect(unranked.replace("92.8%", rate))
                self.assertEqual((row.score, row.state, row.sample_count), (None, "missing", None))
                self.assertEqual(row.observed_at, date(2026, 11, 21))
                self.assertEqual(row.raw_evidence["ranked_combinations"], 0)
                self.assertEqual(row.raw_evidence["availability_checks_24h"], 714)
        # Observed monitor states: no monitoring section data yet vs a window with no valid checks.
        unchecked = unranked.replace("最近检查 2026-11-21 11:58", "最近检查 —")
        start, end = unchecked.index('<div class="nums">'), unchecked.index("</section>")
        empty, = collect(unchecked[:start] + '<div class="empty"><strong>还没有监测数据</strong></div>' + unchecked[end:])
        self.assertIsNone(empty.observed_at)
        self.assertIsNone(empty.raw_evidence["availability_checks_24h"])
        window, = collect(unchecked.replace('92.8%', '—').replace('有效样本 714 · 含 5 个异常分组', '暂无有效样本'))
        self.assertIsNone(window.observed_at)
        self.assertEqual(window.raw_evidence["availability_checks_24h"], 0)

    def test_ambiguous_or_unattributed_scores_fail_closed(self):
        for detail in (DETAIL.replace("平均综合评分", "最佳评分"), DETAIL.replace("83.8", "101"),
                       DETAIL.replace("最近检查 2026-11-21 11:58", "最近检查 —"),
                       DETAIL.replace("5 个组合进入排名", "0 个组合进入排名"), DETAIL.replace(URL, URL + "-other")):
            with self.subTest(detail=detail[:50]), self.assertRaises(ValueError):
                collect(detail)

    def test_incomplete_directory_or_unsafe_detail_url_never_fetched(self):
        for body in (DIRECTORY.replace(b" 1 ", b" 2 "), DIRECTORY.replace(b'/site/nekocode', b'//bad.example/x'),
                     DIRECTORY.replace(b'/site/nekocode', b'/site/../config')):
            with self.subTest(body=body[:50]), self.assertRaises(ValueError):
                collect(directory=body)

    def test_removed_detail_is_missing_but_access_errors_fail_collection(self):
        def fetch_error(code):
            def get(label, url):
                if url == ztest.URL:
                    return DIRECTORY
                raise HTTPError(url, code, "error", {}, None)
            return get

        for status in (404, 410):
            row, = ztest.collect(fetch_error(status))
            self.assertEqual((row.domain, row.score, row.observed_at, row.state), ("nekocode.ai", None, None, "missing"))
            self.assertEqual(row.source_url, ztest.URL)
            self.assertEqual(row.raw_evidence, {"detail_url": URL, "http_status": status})
        with self.assertRaises(HTTPError):
            ztest.collect(fetch_error(403))
