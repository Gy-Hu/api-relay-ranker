import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from relayrank.cli import main
from relayrank.fetch import FetchResult
from relayrank.live import LIVE_SOURCES, LiveCollectionError, LiveSource, collect_live, _fetch_source
from relayrank.models import Observation
from relayrank.site import SOURCE_URLS


class LiveTests(unittest.TestCase):
    def test_zhaotutu_fetch_and_site_link_use_migrated_ranking(self):
        source = next(source for source in LIVE_SOURCES if source.name == "zhaotutu")
        self.assertEqual(source.url, "https://api.zhaotutu.ai/")
        self.assertEqual(SOURCE_URLS[source.name], source.url)

    def test_failed_collection_preserves_report_without_publishing(self):
        def parse_ok(body, aliases):
            return [Observation(source="good", vendor="Vendor", score=90)]

        def parse_bad(body, aliases):
            raise ValueError("no ranking on product homepage")

        specs = (
            LiveSource("good", "https://good.example/", 0.9, parse_ok),
            LiveSource("bad", "https://bad.example/", 0.9, parse_bad),
        )

        def fake_fetch(source, url, timeout):
            return FetchResult(source, url, datetime.now(timezone.utc), b"<html></html>", "text/html", "hash")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "ranking.csv"
            output.write_text("previous valid ranking")
            report = root / "data" / "sources.json"
            config = Path(__file__).resolve().parents[1] / "examples" / "config.toml"
            with patch("relayrank.live.LIVE_SOURCES", specs), patch("relayrank.live.fetch", side_effect=fake_fetch):
                with self.assertRaisesRegex(LiveCollectionError, "bad: parse error"):
                    main([
                        "live", "--config", str(config),
                        "--snapshot-dir", str(root / "snapshots"),
                        "--source-report", str(report),
                        "--output", str(output), "--json", str(root / "audit.json"),
                        "--site-dir", str(root / "site"),
                    ])
            reports = json.loads(report.read_text())
            self.assertEqual([item["ok"] for item in reports], [True, False])
            self.assertIn("no ranking", reports[1]["error"])
            self.assertEqual(output.read_text(), "previous valid ranking")
            self.assertFalse((root / "site").exists())
            self.assertFalse((root / "audit.json").exists())
            self.assertEqual(len(list((root / "snapshots").glob("*/*.html"))), 2)

    def test_partial_collection_still_requires_two_sources_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("relayrank.live.fetch", side_effect=RuntimeError("offline")):
                with self.assertRaises(LiveCollectionError) as caught:
                    collect_live({}, directory, allow_partial=True)
        self.assertEqual(len(caught.exception.reports), 4)
        self.assertTrue(all(not report.ok for report in caught.exception.reports))

    def test_tokhub_pagination_fetches_and_archives_every_page(self):
        spec = next(s for s in LIVE_SOURCES if s.name == "tokhub")
        def page(number, ids):
            body = json.dumps({"items": [{"id": i} for i in ids], "total": 3, "page": number, "pageSize": 2}).encode()
            return FetchResult("tokhub" if number == 1 else "tokhub-page-2", spec.url, datetime.now(timezone.utc), body, "application/json", hashlib.sha256(body).hexdigest())
        with tempfile.TemporaryDirectory() as directory, patch("relayrank.live.fetch", side_effect=[page(1, ["a", "b"]), page(2, ["c"])]) as fetch_mock:
            merged, pages = _fetch_source(spec, 10, directory, "run")
            self.assertEqual(len(json.loads(merged.body)["items"]), 3)
            self.assertEqual(len(pages), 2)
            self.assertEqual(len(list(Path(directory).glob("run/*.meta.json"))), 3)
            self.assertIn("page=2", fetch_mock.call_args.args[1])

    def test_tokhub_repeated_page_or_total_drift_fails(self):
        spec = next(s for s in LIVE_SOURCES if s.name == "tokhub")
        first = {"items": [{"id": "a"}], "total": 2, "page": 1, "pageSize": 1}
        for second in [{**first, "page": 2}, {**first, "page": 2, "total": 3, "items": [{"id": "b"}]}]:
            replies = [FetchResult("tokhub", spec.url, datetime.now(timezone.utc), json.dumps(p).encode(), "application/json", "hash") for p in [first, second]]
            with tempfile.TemporaryDirectory() as directory, patch("relayrank.live.fetch", side_effect=replies):
                with self.assertRaisesRegex(ValueError, "pagination"):
                    _fetch_source(spec, 10, directory, "run")

    def test_fetch_timestamp_does_not_become_source_timestamp(self):
        specs = tuple(LiveSource(n, f"https://{n}.example", .9,
                      lambda body, aliases, name=n: [Observation(name, "v", score=80)]) for n in ["a", "b"])
        def fetch(source, url, timeout):
            return FetchResult(source, url, datetime.now(timezone.utc), b"test", "text/html", "hash")
        with tempfile.TemporaryDirectory() as directory, patch("relayrank.live.LIVE_SOURCES", specs), patch("relayrank.live.fetch", side_effect=fetch):
            observations, sources, reports = collect_live({}, directory)
        self.assertTrue(all(s.published_at is None for s in sources.values()))
        self.assertTrue(all(r.quality == "limited" and r.unknown_date_count == 1 for r in reports))

    def test_empty_result_publishes_explicit_abstention_and_audit(self):
        from relayrank.models import Source
        from relayrank.live import SourceReport
        observations = [Observation("a", "v", state="reference", evidence_kind="ordering")]
        sources = {"a": Source("a", None)}
        reports = [SourceReport("a", True, 1)]
        with tempfile.TemporaryDirectory() as directory, patch("relayrank.cli.collect_live", return_value=(observations, sources, reports)):
            root = Path(directory)
            main(["live", "--config", str(Path(__file__).parents[1] / "examples/config.toml"),
                  "--output", str(root/"ranking.csv"), "--json", str(root/"audit.json"),
                  "--source-report", str(root/"sources.json"), "--site-dir", str(root/"site")])
            self.assertEqual(json.loads((root/"site/data/audit.json").read_text()), [])
            self.assertEqual(len(json.loads((root/"site/data/observations.json").read_text())), 1)
            self.assertIn("没有足够独立评分证据", (root/"site/index.html").read_text())


if __name__ == "__main__":
    unittest.main()
