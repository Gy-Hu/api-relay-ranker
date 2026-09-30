import gzip
import json
import unittest
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch

from relayrank.details import parse_benchmarks, parse_channel_detail
from relayrank.live import LIVE_SOURCES, _fetch_source, collect_live
from relayrank.fetch import FetchResult

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return gzip.decompress((FIXTURES/(name+'.gz')).read_bytes())


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = fixture('apiranking-benchmark.html')
        cls.data = parse_benchmarks(cls.body, {'sssaicode': 'SSSAiCode'})

    def test_both_tabs_and_anonymous_negative_batches_are_preserved(self):
        self.assertEqual(self.data['batch_count'], 57)
        self.assertEqual(self.data['anonymous_count'], 30)
        self.assertEqual({b['model_family'] for b in self.data['batches']}, {'openai', 'claude'})
        self.assertTrue(all(b['vendor'] is None and b['channel'] is None and b['listing_slug'] is None
                            for b in self.data['batches'] if b['anonymous']))
        self.assertTrue(any(b['vendor']=='SSSAiCode' for b in self.data['batches']))

    def test_detail_counts_money_precision_and_history(self):
        b = self.data['batches'][0]
        self.assertEqual((b['vendor'], b['round_count'], b['successful_round_count']), ('UU API', 11, 11))
        self.assertEqual((b['cache_hits'], b['cache_trials']), (10, 10))
        self.assertEqual(b['expected_charge_exact'], .0743)
        self.assertEqual(b['actual_charge_total'], .072)
        self.assertEqual(len(b['billing_records']), 11)
        self.assertEqual(len(b['history']), 5)
        self.assertIsNone(b['test_time']['timestamp'])
        self.assertEqual(b['test_time']['display'], '09-12 11时')

    def test_zero_missing_and_failed_rounds_are_distinct(self):
        self.assertTrue(any(b['cache_hits']==0 for b in self.data['batches']))
        self.assertTrue(any(b['cache_hits'] is None for b in self.data['batches']))
        failed = [b for b in self.data['batches'] if b['successful_round_count'] < b['round_count']]
        self.assertTrue(failed)
        self.assertTrue(any(b['expected_charge_display'] is None for b in failed))
        self.assertIsNone(self.data['batches'][0]['rounds'][0]['cache_write_tokens'])

    def test_missing_panel_and_changed_header_fail_loudly(self):
        for body in [self.body.replace(b'id="bxd-162"', b'id="changed"'),
                     self.body.replace('首字'.encode(), '首字变更'.encode())]:
            with self.assertRaises(ValueError):parse_benchmarks(body, {})

    def test_changed_cache_denominator_is_rejected(self):
        body=self.body.replace(b'data-cache="10"', b'data-cache="1000"', 1)
        with self.assertRaisesRegex(ValueError, 'numerator/denominator'):
            parse_benchmarks(body, {})

    def test_live_enrichment_links_by_listing_slug_without_changing_scoring_role(self):
        apirank = next(s for s in LIVE_SOURCES if s.name == "apiranking")
        helpaio = next(s for s in LIVE_SOURCES if s.name == "helpaio")
        def fetch(source, url, timeout, **kwargs):
            bodies = {"apiranking": fixture("apiranking.html"), "apiranking-benchmark": self.body,
                      "helpaio": fixture("helpaio.html")}
            return FetchResult(source, url, datetime.now(timezone.utc), bodies[source], "text/html", "hash")
        with TemporaryDirectory() as d, patch("relayrank.live.LIVE_SOURCES", (apirank, helpaio)), patch("relayrank.live.fetch", side_effect=fetch):
            observations, sources, reports = collect_live({}, d)
        uu = next(o for o in observations if o.source == "apiranking" and o.vendor == "UU API")
        self.assertEqual(len(uu.raw_evidence["benchmark_batches"]), 2)
        self.assertEqual(uu.state, "reference")
        self.assertIsNone(uu.score)
        report = next(r for r in reports if r.name == "apiranking")
        self.assertEqual(report.detail_count, 57)
        self.assertEqual(report.details["anonymous_count"], 30)


class ProbeDetailTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(fixture('tokhub-runapi-detail.json'))
        self.channel = self.data['channel']

    def test_l1_only_sample_does_not_become_generation_failure_rate(self):
        parsed = parse_channel_detail(self.data, self.channel)
        self.assertEqual(parsed['record_count'], 20)
        self.assertEqual(parsed['l3_record_count'], 0)
        self.assertIsNone(parsed['measured_uptime'])
        self.assertIn('l3_not_run', parsed['issues'])
        self.assertIn('no_l3_records_in_returned_sample', parsed['issues'])

    def test_actual_l3_record_is_preserved_without_claiming_window_coverage(self):
        self.data['recentRecords'].append({'time': '2026-09-30T01:00:00Z','layer':'L3','type':'generation','result':'down','latencyMs':20000})
        parsed=parse_channel_detail(self.data,self.channel)
        self.assertEqual(parsed['l3_record_count'],1)
        self.assertIn('recent_records_are_bounded_sample', parsed['issues'])
        self.assertIsNone(parsed['measured_uptime'])

    def test_wrong_channel_duplicate_record_and_naive_date_rejected(self):
        with self.assertRaisesRegex(ValueError,'identity'):
            parse_channel_detail(self.data,{**self.channel,'id':'wrong'})
        duplicate=deepcopy(self.data);duplicate['recentRecords'].append(duplicate['recentRecords'][0])
        with self.assertRaisesRegex(ValueError,'duplicate'):
            parse_channel_detail(duplicate,self.channel)
        invalid=deepcopy(self.data);invalid['recentRecords'][0]['time']='2026-09-30T01:00:00'
        with self.assertRaisesRegex(ValueError,'timezone'):
            parse_channel_detail(invalid,self.channel)

    def test_detail_fetch_failure_is_explicit_and_overview_survives(self):
        spec=next(s for s in LIVE_SOURCES if s.name=='tokhub')
        overview=json.dumps({'items':[self.channel],'total':1,'page':1,'pageSize':20}).encode()
        def fetch(source,url,timeout,**kwargs):
            if source=='tokhub':return FetchResult(source,url,datetime.now(timezone.utc),overview,'application/json','hash')
            raise RuntimeError('detail endpoint unavailable')
        with TemporaryDirectory() as d, patch('relayrank.live.fetch',side_effect=fetch):
            result,pages=_fetch_source(spec,10,d,'run')
        item=json.loads(result.body)['items'][0]
        self.assertIn('detail endpoint unavailable',item['detail_error'])
        self.assertNotIn('detail',item)

    def test_detail_fetch_archives_and_validates_identity(self):
        spec=next(s for s in LIVE_SOURCES if s.name=='tokhub')
        overview=json.dumps({'items':[self.channel],'total':1,'page':1,'pageSize':20}).encode()
        def fetch(source,url,timeout,**kwargs):
            body=overview if source=='tokhub' else json.dumps(self.data).encode()
            return FetchResult(source,url,datetime.now(timezone.utc),body,'application/json','hash')
        with TemporaryDirectory() as d, patch('relayrank.live.fetch',side_effect=fetch):
            result,pages=_fetch_source(spec,10,d,'run')
            self.assertEqual(len(list(Path(d).glob('run/*.meta.json'))),3)
        self.assertEqual(len(pages),2)
        self.assertEqual(json.loads(result.body)['items'][0]['detail']['l3_record_count'],0)


if __name__=='__main__':unittest.main()
