import unittest
from datetime import date

from relayrank.engine import aggregate
from relayrank.models import Config, Observation, Source


def make_config(**changes):
    values = {
        "as_of": date(2026, 7, 12),
        "prior_strength": 0.0,
        "minimum_sources": 1,
        "variance_penalty": 0.0,
        "low_outlier_gap": 0.0,
        "three_source_bonus": 0.0,
        "four_source_bonus": 0.0,
    }
    values.update(changes)
    return Config(**values)


class AggregationTests(unittest.TestCase):
    def test_canonical_vendor_is_preserved(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        observations = [Observation("a", "Packy Code", rank=1, total_vendors=2, score=90)]
        self.assertEqual(aggregate(observations, sources, make_config())[0].vendor, "Packy Code")

    def test_vendor_website_is_preserved(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        observations = [Observation("a", "vendor", score=90, website_url="https://vendor.example/")]
        self.assertEqual(aggregate(observations, sources, make_config())[0].website_url, "https://vendor.example/")

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

    def test_source_disagreement_lowers_score_and_rank(self):
        sources = {
            "a": Source("a", date(2026, 7, 12)),
            "b": Source("b", date(2026, 7, 12)),
        }
        observations = [
            Observation("a", "stable", score=80),
            Observation("b", "stable", score=80),
            Observation("a", "disputed", score=100),
            Observation("b", "disputed", score=60),
        ]

        stable, disputed = aggregate(
            observations,
            sources,
            make_config(variance_penalty=0.25),
        )

        self.assertEqual((stable.vendor, stable.score), ("stable", 80))
        self.assertEqual(disputed.vendor, "disputed")
        self.assertAlmostEqual(disputed.score_stddev, 20)
        self.assertAlmostEqual(disputed.disagreement_penalty, 5)
        self.assertAlmostEqual(disputed.score, 75)

    def test_prior_shrinks_thin_evidence(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        result = aggregate([Observation("a", "vendor", score=100)], sources, make_config(prior_strength=1))[0]
        self.assertAlmostEqual(result.score, 75)

    def test_isolated_low_score_is_guarded_with_three_independent_sources(self):
        sources = {
            name: Source(name, date(2026, 7, 12))
            for name in ("a", "b", "buggy")
        }
        observations = [
            Observation("a", "vendor", score=90),
            Observation("b", "vendor", score=88),
            Observation("buggy", "vendor", score=10),
        ]

        result = aggregate(observations, sources, make_config(low_outlier_gap=25))[0]

        self.assertAlmostEqual(result.score, (90 + 88 + 88) / 3)
        self.assertGreater(result.raw_score_stddev, result.score_stddev)
        self.assertEqual(result.low_outlier_sources, ("buggy",))
        guarded = next(item for item in result.contributions if item["source"] == "buggy")
        self.assertEqual(guarded["adjusted_score"], 88)
        self.assertTrue(guarded["low_outlier_guarded"])

    def test_two_source_disagreement_is_not_guarded(self):
        sources = {
            name: Source(name, date(2026, 7, 12))
            for name in ("a", "b")
        }
        observations = [
            Observation("a", "vendor", score=90),
            Observation("b", "vendor", score=10),
        ]

        result = aggregate(observations, sources, make_config(low_outlier_gap=25))[0]

        self.assertEqual(result.score, 50)
        self.assertEqual(result.low_outlier_sources, ())

    def test_broad_disagreement_is_not_mistaken_for_one_low_outlier(self):
        sources = {
            name: Source(name, date(2026, 7, 12))
            for name in ("a", "b", "c")
        }
        observations = [
            Observation("a", "vendor", score=10),
            Observation("b", "vendor", score=40),
            Observation("c", "vendor", score=100),
        ]

        result = aggregate(observations, sources, make_config(low_outlier_gap=25))[0]

        self.assertEqual(result.score, 50)
        self.assertEqual(result.low_outlier_sources, ())

    def test_three_and_four_source_coverage_receive_bonuses(self):
        sources = {
            name: Source(name, date(2026, 7, 12))
            for name in ("a", "b", "c", "d")
        }
        observations = [
            Observation(source, vendor, score=60)
            for vendor, used_sources in (
                ("two", ("a", "b")),
                ("three", ("a", "b", "c")),
                ("four", ("a", "b", "c", "d")),
            )
            for source in used_sources
        ]

        results = {
            item.vendor: item
            for item in aggregate(
                observations,
                sources,
                make_config(three_source_bonus=2, four_source_bonus=4),
            )
        }

        self.assertEqual((results["two"].score, results["two"].coverage_bonus), (60, 0))
        self.assertEqual((results["three"].score, results["three"].coverage_bonus), (62, 2))
        self.assertEqual((results["four"].score, results["four"].coverage_bonus), (64, 4))

    def test_duplicate_source_vendor_is_rejected(self):
        sources = {"a": Source("a", date(2026, 7, 12))}
        observations = [Observation("a", "vendor", score=80), Observation("a", "vendor", score=90)]
        with self.assertRaisesRegex(ValueError, "duplicate observation"):
            aggregate(observations, sources, make_config())


if __name__ == "__main__":
    unittest.main()
