import gzip
import hashlib
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from relayrank.engine import aggregate
from relayrank.fetch import FetchResult
from relayrank.identity import Directory, VendorSpec
from relayrank.live import LiveCollectionError, collect_live
from relayrank.models import Config, Source
from relayrank.site import write_site

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 11, 21)
SOURCES = {name: Source(name) for name in ("helpaio", "okkmax", "ztest", "zhongzhuancha")}


def fixture(name):
    return gzip.decompress((FIXTURES / (name + ".gz")).read_bytes())


def fake_fetcher(failing=()):
    pages = {
        "https://www.helpaio.com/transit": fixture("helpaio.html"),
        "https://www.okkmax.com/list": fixture("okkmax-list.html"),
        "https://www.okkmax.com/availability": fixture("okkmax-availability.html"),
        "https://ztest.ai/sites": (FIXTURES / "ztest-sites.html").read_bytes(),
        "https://ztest.ai/site/nekocode": (FIXTURES / "ztest-nekocode.html").read_bytes(),
        "https://zhongzhuancha.com/transit": (FIXTURES / "zhongzhuancha.html").read_bytes(),
        **{f"https://zhongzhuancha.com/transit/info/{slug}": (FIXTURES / f"zhongzhuancha-{slug}.html").read_bytes()
           for slug in ("packy-code", "miaoniuai")},
    }

    def fetcher(label, url, timeout):
        if any(f in url for f in failing):
            raise RuntimeError(f"offline: {url}")
        body = pages[url]
        return FetchResult(label, url, datetime.now(timezone.utc), body, "text/html", hashlib.sha256(body).hexdigest())
    return fetcher


class LiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.directory = Directory((VendorSpec("Packy", ("packyapi.ai", "packyapi.com")),))

    def test_full_collection_joins_sources_by_domain_and_archives_raw_pages(self):
        raw, reports = collect_live(SOURCES, self.root / "snap", fetcher=fake_fetcher())
        self.assertTrue(all(r.ok for r in reports))
        observations, _ = self.directory.resolve(raw)
        packy = {o.source for o in observations if o.vendor == "Packy"}
        self.assertEqual(packy, {"helpaio", "okkmax", "zhongzhuancha"})
        self.assertEqual({r.name for r in reports if r.valid_count > 0}, set(SOURCES))
        self.assertTrue(any((self.root / "snap").glob("*/helpaio.html")))
        self.assertTrue(any((self.root / "snap").glob("*/ztest-site-nekocode.html")))
        ranking = aggregate(observations, SOURCES, Config(DAY))
        self.assertIn("Packy", [r.vendor for r in ranking.results])

    def test_required_source_failure_aborts_with_reports(self):
        with self.assertRaises(LiveCollectionError) as caught:
            collect_live(SOURCES, self.root / "snap", fetcher=fake_fetcher(("ztest.ai/site/nekocode",)))
        failed = [r.name for r in caught.exception.reports if not r.ok]
        self.assertEqual(failed, ["ztest"])

    def test_partial_run_publishes_without_the_failed_source(self):
        raw, reports = collect_live(SOURCES, self.root / "snap", allow_partial=True,
                                    fetcher=fake_fetcher(("okkmax.com",)))
        self.assertFalse(next(r for r in reports if r.name == "okkmax").ok)
        self.assertNotIn("okkmax", {o.source for o in raw})

    def test_partial_mode_cannot_publish_one_source(self):
        with self.assertRaisesRegex(LiveCollectionError, "at least two"):
            collect_live(SOURCES, self.root, allow_partial=True,
                         fetcher=fake_fetcher(("ztest.ai", "okkmax.com", "zhongzhuancha.com")))

    def test_empty_ranking_publishes_explicit_abstention(self):
        ranking = aggregate([], SOURCES, Config(DAY))
        target = write_site(self.root / "site", ranking, [], SOURCES, Config(DAY), generated_at="2026-10-01T00:00:00Z")
        self.assertIn("没有商家满足入榜所需的独立来源数量", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
