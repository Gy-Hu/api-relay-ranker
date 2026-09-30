import gzip
import json
import unittest
from pathlib import Path
from dataclasses import replace

from relayrank.adapters import parse_apiranking, parse_helpaio, parse_tokhub, parse_zhaotutu
from relayrank.adapters.common import safe_http_url
from relayrank.models import Config, Source
from relayrank.engine import aggregate
from datetime import date

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return gzip.decompress((FIXTURES / (name + ".gz")).read_bytes())


def synthetic_zhaotutu(providers):
    data = '0:' + json.dumps({"providers": providers}, ensure_ascii=False, separators=(",", ":"))
    # Split inside a JSON object to reproduce streamed Next.js chunks.
    chunks = [data[:len(data)//2], data[len(data)//2:]]
    return ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk]) + ')</script>' for chunk in chunks).encode()


class AdapterTests(unittest.TestCase):
    def test_vendor_links_only_allow_http_urls(self):
        for url in ["javascript:alert(1)", "//example.com", "https://user:secret@example.com"]:
            self.assertIsNone(safe_http_url(url))

    def test_helpaio_real_cards_preserve_composite_and_missing(self):
        rows = {o.vendor: o for o in parse_helpaio(fixture("helpaio.html"), {})}
        self.assertEqual(len(rows), 21)
        self.assertEqual(sum(o.state == "valid" for o in rows.values()), 18)
        self.assertEqual(rows["SSSAiCode"].score, 79.25)
        self.assertEqual(rows["SSSAiCode"].uptime, 95.42)
        self.assertEqual(rows["SSSAiCode"].observed_at, date(2026, 9, 30))
        self.assertEqual(rows["Yunwu"].score, 0)
        for name in ["Duck Code", "88 Code", "Privnode"]:
            self.assertIsNone(rows[name].score)
            self.assertEqual(rows[name].state, "missing")

    def test_helpaio_no_silent_fallback_when_markup_changes(self):
        with self.assertRaises(ValueError):
            parse_helpaio(fixture("helpaio.html").replace(b'data-station-index=', b'data-new-index='), {})

    def test_helpaio_formula_drift_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "formula mismatch"):
            parse_helpaio(fixture("helpaio.html").replace(b'>79.25<', b'>99.25<'), {})

    def test_apiranking_full_list_and_status_are_reference_only(self):
        rows = parse_apiranking(fixture("apiranking.html"), {})
        self.assertEqual(len(rows), 296)
        self.assertTrue(all(o.total_vendors == 296 for o in rows))
        self.assertEqual(rows[25].rank, 26)
        self.assertEqual(rows[49].vendor, "Micu")
        self.assertTrue(all(o.score is None for o in rows))
        self.assertTrue(any(o.state == "inactive" for o in rows))
        self.assertEqual(rows[0].website_url, "https://uuapi.io")

    def test_apiranking_truncation_is_rejected(self):
        body = fixture("apiranking.html").replace(b'rank=296" class="provider-link"', b'position=296" class="provider-link"')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_apiranking(body, {})

    def test_apiranking_does_not_turn_listing_position_into_score(self):
        rows = parse_apiranking(fixture("apiranking.html"), {})
        self.assertEqual(aggregate(rows, {"apiranking": Source("apiranking", None)}, Config(date(2026, 9, 30))), [])

    def test_tokhub_preserves_all_channel_states_without_fake_rates(self):
        rows = {o.vendor: o for o in parse_tokhub(fixture("tokhub.json"), {"packycode": "Packy Code"})}
        self.assertEqual(len(rows), 9)
        self.assertIn("Packy Code", rows)
        self.assertEqual(len(rows["CrazyRouter"].raw_evidence["channels"]), 4)
        run = rows["RunAPI"]
        self.assertIsNone(run.score)
        self.assertIsNone(run.uptime)
        self.assertIsNone(run.rank)
        self.assertEqual(run.raw_evidence["channels"][0]["uptime24h"], 88)
        self.assertEqual(run.raw_evidence["channels"][0]["status"], "functional_down")
        self.assertEqual(run.state, "reference")

    def test_tokhub_null_score_does_not_drop_provider_or_failure(self):
        data = json.loads(fixture("tokhub.json"))
        for c in data["items"]:
            c["score"] = None
        self.assertEqual(len(parse_tokhub(json.dumps(data).encode(), {})), 9)

    def test_tokhub_rejects_incomplete_page_and_duplicate_channels(self):
        data = json.loads(fixture("tokhub.json"))
        data["total"] += 1
        with self.assertRaisesRegex(ValueError, "pagination"):
            parse_tokhub(json.dumps(data).encode(), {})
        data["items"].append(data["items"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_tokhub(json.dumps(data).encode(), {})

    def test_zhaotutu_real_data_preserves_all_states_and_raw_zeroes(self):
        rows = {o.vendor: o for o in parse_zhaotutu(fixture("zhaotutu.html"), {})}
        self.assertEqual(len(rows), 64)
        self.assertEqual(rows["Gotoken"].state, "valid")
        self.assertIn("source_reports_degraded", rows["Gotoken"].issues)
        self.assertEqual(rows["灵芽API"].state, "inactive")
        micu = rows["Micu"]
        self.assertEqual(micu.score, 92.33)
        self.assertIsNone(micu.uptime)
        self.assertIsNone(micu.cache_rate)
        self.assertIsNone(micu.observed_at)
        self.assertIn(0, [m["cacheHitRate"] for m in micu.raw_evidence["models"]])
        self.assertEqual(micu.raw_evidence["lastUpdated"], "2026-04-30")

    def test_zhaotutu_null_zero_and_field_order_survive_streaming(self):
        rows = [{"nameCn": "Synthetic", "overallScore": 0 if i == 0 else (None if i == 1 else 80),
                 "status": "active", "id": str(i), "name": f'Synthetic "Vendor" {i}',
                 "models": [{"cacheHitRate": 0, "cacheSamples": 10}],
                 "modelMonitorByVendor": [{"cacheHitRate": 90}]} for i in range(10)]
        parsed = parse_zhaotutu(synthetic_zhaotutu(rows), {})
        self.assertEqual(parsed[0].score, 0)
        self.assertEqual(parsed[1].state, "missing")
        self.assertEqual(parsed[0].raw_evidence["models"][0]["cacheHitRate"], 0)
        self.assertIsNone(parsed[0].cache_rate)

    def test_zhaotutu_malformed_score_fails_instead_of_clamping(self):
        rows = [{"id": str(i), "name": f"Synthetic {i}", "status": "active", "overallScore": 101} for i in range(10)]
        with self.assertRaisesRegex(ValueError, "must be in"):
            parse_zhaotutu(synthetic_zhaotutu(rows), {})


if __name__ == "__main__":
    unittest.main()
