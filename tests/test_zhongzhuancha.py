import unittest
from datetime import date
from pathlib import Path

from relayrank.adapters import zhongzhuancha as source


FIXTURES = Path(__file__).parent / "fixtures"


def collect(index=None, details=None):
    calls = []

    def get(label, url):
        calls.append(url)
        if url == source.URL:
            assert label == "zhongzhuancha"
            if index is not None:
                return index
        else:
            assert url == f"{source.URL}/info/{label.removeprefix('zhongzhuancha-')}"
            if details and url in details:
                return details[url]
        return (FIXTURES / (label + ".html")).read_bytes()

    return source.collect(get), calls


class ZhongZhuanChaTests(unittest.TestCase):
    def setUp(self):
        self.index = (FIXTURES / "zhongzhuancha.html").read_bytes()

    def test_real_probe_date_card_score_and_attribution(self):
        rows, calls = collect(self.index)
        self.assertEqual(calls, [source.URL, source.URL + "/info/packy-code", source.URL + "/info/miaoniuai"])
        packy, zero = rows
        self.assertEqual((packy.vendor, packy.domain, packy.score, packy.sample_count, packy.observed_at),
                         ("Packy Code", "packyapi.ai", 56.27, 284, date(2026, 11, 21)))
        self.assertEqual(packy.source_url, source.URL + "#site-packy-code")
        raw = packy.raw_evidence
        self.assertEqual((raw["passed"], raw["total"], raw["window_hours"], raw["directory_sites"]), (188, 284, 72, 422))
        self.assertEqual(raw["last_probe_at"], "2026-11-21T04:19:41.489Z")
        self.assertEqual(raw["detail_url"], source.URL + "/info/packy-code")
        self.assertIn("GPT 0.5 倍率", raw["monitor_channels"][0])
        self.assertEqual(raw["detail_model_samples"][0], {
            "model": "gpt-6-astra", "protocol": "openai-compatible", "passed": 47, "total": 71,
            "first_token_latency": "2595 ms", "last_probe_local": "2026/11/21 12:19",
        })
        self.assertEqual((zero.score, zero.sample_count, zero.state, zero.raw_evidence["passed"]), (0, 284, "valid", 0))
        self.assertIsNone(zero.raw_evidence["detail_model_samples"][0]["first_token_latency"])

    def test_no_samples_is_not_a_zero_score(self):
        for no_samples in (b"0", b"null"):
            with self.subTest(no_samples=no_samples):
                index = self.index.replace(br'\"completionSamples\":284', br'\"completionSamples\":' + no_samples, 1)
                rows, calls = collect(index)
                self.assertEqual([r.vendor for r in rows], ["秒牛中转站"])
                self.assertNotIn(source.URL + "/info/packy-code", calls)

    def test_cannot_replace_missing_score_or_window_with_profile_data(self):
        with self.assertRaisesRegex(ValueError, "card score"):
            collect(self.index.replace(b"56.27<small>/100", b"85<small>/90"))
        with self.assertRaisesRegex(ValueError, "model sample columns"):
            detail = (FIXTURES / "zhongzhuancha-packy-code.html").read_bytes()
            collect(details={source.URL + "/info/packy-code": detail.replace("通过 / 总数".encode(), b"price")})

    def test_unsafe_slug_is_never_used_in_a_fetch(self):
        delivered = []

        def get(label, url):
            delivered.append((label, url))
            return self.index.replace(b"packy-code", b"../other")

        with self.assertRaisesRegex(ValueError, "unsafe"):
            source.collect(get)
        self.assertEqual(delivered, [("zhongzhuancha", source.URL)])
