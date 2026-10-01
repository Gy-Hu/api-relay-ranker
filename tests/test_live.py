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
DAY = date(2026, 10, 1)
SOURCES = {name: Source(name, window_days=60 if name == "veridrop" else None)
           for name in ("helpaio", "relaypick", "okkmax", "veridrop")}


def fixture(name):
    return gzip.decompress((FIXTURES / (name + ".gz")).read_bytes())


def fake_fetcher(failing=()):
    pages = {
        "https://www.helpaio.com/transit": fixture("helpaio.html"),
        "https://relaypick.com/ranking": fixture("relaypick.html"),
        "https://www.okkmax.com/list": fixture("okkmax-list.html"),
        "https://www.okkmax.com/availability": fixture("okkmax-availability.html"),
        "https://veridrop.org/search?q=micuapi.ai": fixture("veridrop-search-right.codes.html").replace(
            b"right.codes", b"micuapi.ai"),
        "https://veridrop.org/leaderboard/micuapi.ai": fixture("veridrop-detail-micuapi.html"),
        "https://veridrop.org/leaderboard/www.micuapi.ai": fixture("veridrop-detail-micuapi.html"),
    }
    empty_search = b'<section class="domain-search-panel"></section>'

    def fetcher(label, url, timeout):
        if any(f in url for f in failing):
            raise RuntimeError(f"offline: {url}")
        if url.startswith("https://veridrop.org/search?q=") and url not in pages:
            body = empty_search
        elif url.startswith("https://veridrop.org/leaderboard/") and "?page=" in url:
            body = b'<section id="history"><p>\xe7\xac\xac 4 / 4 \xe9\xa1\xb5</p></section>'
        else:
            body = pages[url]
        return FetchResult(label, url, datetime.now(timezone.utc), body, "text/html", hashlib.sha256(body).hexdigest())
    return fetcher


class LiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.directory = Directory((VendorSpec("Micu", ("micuapi.ai",)),))

    def test_full_collection_joins_sources_by_domain_and_archives_raw_pages(self):
        raw, reports = collect_live(SOURCES, self.directory, DAY, self.root / "snap", fetcher=fake_fetcher())
        self.assertTrue(all(r.ok for r in reports))
        observations, _ = self.directory.resolve(raw)
        micu = {o.source for o in observations if o.vendor == "Micu"}
        self.assertEqual(micu, {"helpaio", "okkmax", "veridrop"})
        veridrop = next(o for o in observations if o.vendor == "Micu" and o.source == "veridrop")
        self.assertEqual(sorted(veridrop.raw_evidence["hosts"]), ["micuapi.ai", "www.micuapi.ai"])
        self.assertTrue(any((self.root / "snap").glob("*/helpaio.html")))
        ranking = aggregate(observations, SOURCES, Config(DAY))
        self.assertIn("Micu", [r.vendor for r in ranking.results])

    def test_required_source_failure_aborts_with_reports(self):
        with self.assertRaises(LiveCollectionError) as caught:
            collect_live(SOURCES, self.directory, DAY, self.root / "snap", fetcher=fake_fetcher(("relaypick.com",)))
        failed = [r.name for r in caught.exception.reports if not r.ok]
        self.assertEqual(failed, ["relaypick"])

    def test_partial_run_publishes_without_the_failed_source(self):
        raw, reports = collect_live(SOURCES, self.directory, DAY, self.root / "snap", allow_partial=True,
                                    fetcher=fake_fetcher(("okkmax.com",)))
        self.assertFalse(next(r for r in reports if r.name == "okkmax").ok)
        self.assertNotIn("okkmax", {o.source for o in raw})

    def test_empty_ranking_publishes_explicit_abstention(self):
        ranking = aggregate([], SOURCES, Config(DAY))
        target = write_site(self.root / "site", ranking, [], SOURCES, Config(DAY), generated_at="2026-10-01T00:00:00Z")
        self.assertIn("没有商家满足入榜所需的独立来源数量", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
