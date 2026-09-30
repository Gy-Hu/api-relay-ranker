import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from relayrank.cli import main
from relayrank.fetch import FetchResult
from relayrank.live import LIVE_SOURCES, LiveCollectionError, LiveSource, collect_live
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


if __name__ == "__main__":
    unittest.main()
