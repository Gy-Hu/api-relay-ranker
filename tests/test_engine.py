import unittest
from datetime import date

from relayrank.engine import aggregate
from relayrank.models import Config, Observation, Source


def make_config(**changes):
    values = {"as_of": date(2026, 7, 12), "prior_strength": 0.0, "minimum_sources": 1}
    values.update(changes)
    return Config(**values)


class AggregationTests(unittest.TestCase):
    def test_canonical_vendor_is_preserved(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        observations = [Observation("a", "Packy Code", rank=1, total_vendors=2, score=90)]
        self.assertEqual(aggregate(observations, sources, make_config())[0].vendor, "Packy Code")

    def test_missing_metrics_are_renormalized_not_zeroed(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        full = Observation("a", "full", score=80, uptime=80)
        sparse = Observation("a", "sparse", score=80)
        results = {item.vendor: item.score for item in aggregate([full, sparse], sources, make_config())}
        self.assertAlmostEqual(results["full"], results["sparse"])

    def test_correlated_sources_share_one_vote(self):
        sources = {
            "clone-a": Source("clone-a", date(2026, 7, 12), independence_group="same"),
            "clone-b": Source("clone-b", date(2026, 7, 12), independence_group="same"),
            "independent": Source("independent", date(2026, 7, 12), independence_group="other"),
        }
        observations = [
            Observation("clone-a", "vendor", score=100),
            Observation("clone-b", "vendor", score=100),
            Observation("independent", "vendor", score=0),
        ]
        self.assertAlmostEqual(aggregate(observations, sources, make_config())[0].score, 50)

    def test_stale_source_decays(self):
        sources = {
            "new": Source("new", date(2026, 7, 12)),
            "old": Source("old", date(2026, 6, 12)),
        }
        observations = [Observation("new", "vendor", score=100), Observation("old", "vendor", score=0)]
        result = aggregate(observations, sources, make_config(half_life_days=30))[0]
        self.assertAlmostEqual(result.score, 66.6667, places=4)

    def test_prior_shrinks_thin_evidence(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        result = aggregate([Observation("a", "vendor", score=100)], sources, make_config(prior_strength=1))[0]
        self.assertAlmostEqual(result.score, 75)

    def test_duplicate_source_vendor_is_rejected(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        observations = [Observation("a", "vendor", score=80), Observation("a", "vendor", score=90)]
        with self.assertRaisesRegex(ValueError, "duplicate observation"):
            aggregate(observations, sources, make_config())


if __name__ == "__main__":
    unittest.main()
