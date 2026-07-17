import json
import unittest

from relayrank.adapters import parse_apiranking, parse_helpaio, parse_tokhub, parse_zhaotutu
from relayrank.adapters.common import safe_http_url


def item_list(names):
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": "AI 中转站排行榜",
            "itemListElement": [
                {"@type": "ListItem", "position": index, "item": {"name": name}}
                for index, name in enumerate(names, 1)
            ],
        },
        ensure_ascii=False,
    )


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.names = [f"Vendor {index}" for index in range(1, 11)]

    def test_vendor_links_only_allow_http_urls(self):
        self.assertIsNone(safe_http_url("javascript:alert(1)"))
        self.assertIsNone(safe_http_url("//example.com"))

    def test_helpaio_uses_json_ld_rank_and_embedded_score(self):
        details = "".join(
            f'\\"siteName\\":\\"{name}\\",\\"siteScore\\":{101-index}'
            for index, name in enumerate(self.names, 1)
        )
        html = f'<script type="application/ld+json">{item_list(self.names)}</script>{details}'
        observations = parse_helpaio(html.encode(), {})
        self.assertEqual((observations[0].vendor, observations[0].rank, observations[0].score), ("Vendor 1", 1, 100))

    def test_zhaotutu_parses_embedded_provider_objects(self):
        providers = "".join(
            f'\\"id\\":\\"v{i}\\",\\"name\\":\\"Vendor {i}\\",\\"nameCn\\":\\"Vendor {i}\\",'
            f'\\"url\\":\\"https://vendor{i}.example/register?ref=test\\",'
            f'\\"status\\":\\"active\\",\\"overallScore\\":{101-i},'
            f'\\"uptimeSummary\\":{{\\"uptime24h\\":99,\\"uptime3d\\":98,\\"uptime30d\\":97}},'
            f'\\"models\\":[{{\\"cacheHitRate\\":90}}]'
            for i in range(1, 11)
        )
        observations = parse_zhaotutu(providers.encode(), {})
        self.assertEqual(len(observations), 10)
        self.assertEqual((observations[0].rank, observations[0].cache_rate), (1, 90))
        self.assertEqual(observations[0].website_url, "https://vendor1.example/register")

    def test_apiranking_reads_ordered_json_ld(self):
        html = f'<script type="application/ld+json">{item_list(self.names)}</script>'
        observations = parse_apiranking(html.encode(), {})
        self.assertEqual((observations[-1].rank, observations[-1].total_vendors), (10, 10))

    def test_apiranking_prefers_provider_website(self):
        document = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": index,
                    "item": {
                        "name": name,
                        "url": f"https://ranking.example/go/{index}",
                        "provider": {"url": f"https://vendor{index}.example/?ref=list"},
                    },
                }
                for index, name in enumerate(self.names, 1)
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(document)}</script>'
        observations = parse_apiranking(html.encode(), {})
        self.assertEqual(observations[0].website_url, "https://vendor1.example/")

    def test_tokhub_groups_channels_by_provider_and_uses_tied_rank(self):
        items = []
        for index in range(1, 10):
            items.append({"provider": f"Vendor {index}", "score": 90 - index, "uptime24h": 98, "endpoint": f"https://vendor{index}.example/v1"})
        items.extend(
            [
                {"provider": "PackyCode", "score": 99, "uptime24h": 100, "officialSiteUrl": "https://packy.example/register?aff=test"},
                {"provider": "PackyCode", "score": 99, "uptime24h": 98},
            ]
        )
        observations = parse_tokhub(json.dumps({"items": items}).encode(), {"packycode": "Packy Code"})
        packy = observations[0]
        self.assertEqual((packy.vendor, packy.rank, packy.score, packy.uptime), ("Packy Code", 1, 99, 99))
        self.assertEqual(packy.website_url, "https://packy.example/")
        self.assertEqual(len(observations), 10)


if __name__ == "__main__":
    unittest.main()
