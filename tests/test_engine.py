import unittest
from datetime import date, timedelta
from dataclasses import replace
from relayrank.engine import aggregate, _observation_score
from relayrank.models import Config, Observation, Source

DAY = date(2026, 9, 30)


def config(**kwargs):
    return Config(as_of=DAY, prior_strength=kwargs.pop("prior_strength", 0), **kwargs)


class AggregationTests(unittest.TestCase):
    def test_composite_not_counted_again_as_rank_uptime_or_cache(self):
        cfg = config()
        a = Observation("a", "vendor", score=70, rank=1, total_vendors=10, uptime=99, cache_rate=100)
        self.assertEqual(_observation_score(a, cfg), (70, {"score": 70}))

    def test_atomic_missing_metric_not_zeroed_but_measured_zero_kept(self):
        cfg = config(metric_weights={"uptime": 1, "cache_rate": 1})
        self.assertEqual(_observation_score(Observation("a", "v", evidence_kind="metrics", uptime=80), cfg)[0], 80)
        self.assertEqual(_observation_score(Observation("a", "v", evidence_kind="metrics", uptime=80, cache_rate=0), cfg)[0], 40)

    def test_sparse_weight_config_no_keyerror(self):
        self.assertEqual(_observation_score(Observation("a", "v", evidence_kind="metrics", uptime=80, cache_rate=50), config(metric_weights={"uptime": 1}))[0], 80)

    def test_prior_shrinks_thin_evidence(self):
        result = aggregate([Observation("a", "v", score=100)], {"a": Source("a", DAY)}, config(prior_strength=1))[0]
        self.assertEqual(result.score, 75)

    def test_negative_evidence_never_winsorized(self):
        sources = {s: Source(s, DAY) for s in "abc"}
        rows = [Observation(s, "v", score=score) for s, score in zip("abc", [10, 80, 85])]
        r = aggregate(rows, sources, config())[0]
        self.assertAlmostEqual(r.score, 175/3)
        self.assertEqual(r.low_outlier_sources, ())
        self.assertEqual(r.disagreement_penalty, 0)
        self.assertGreater(r.score_stddev, 30)

    def test_monotonic_for_every_score_and_unequal_weights(self):
        sources = {s: Source(s, DAY, reliability=w) for s, w in zip("abc", [0.1, 0.9, 0.8])}
        for varied in "abc":
            previous = -1
            for value in range(101):
                rows = [Observation(s, "v", score=value if s == varied else 80) for s in sources]
                result = aggregate(rows, sources, config(prior_strength=0.8))[0]
                self.assertGreaterEqual(result.score, previous)
                previous = result.score

    def test_disabled_legacy_knobs_cannot_silently_reactivate(self):
        for knob in ["low_outlier_gap", "variance_penalty", "three_source_bonus", "four_source_bonus"]:
            with self.assertRaisesRegex(ValueError, "retired"):
                config(**{knob: 1})

    def test_correlated_sources_count_once_and_do_not_get_bonus(self):
        sources = {s: Source(s, DAY, independence_group="same") for s in "abc"}
        rows = [Observation(s, "v", score=80) for s in sources]
        r = aggregate(rows, sources, config())[0]
        self.assertEqual(r.source_count, 1)
        self.assertEqual(r.coverage_bonus, 0)
        self.assertEqual(r.effective_weight, 1)
        self.assertEqual(aggregate(rows, sources, config(), min_sources=2), [])

    def test_clone_does_not_change_independent_groups_vote(self):
        sources = {"a": Source("a", DAY, independence_group="same"), "b": Source("b", DAY), "clone": Source("clone", DAY, independence_group="same")}
        rows = [Observation("a", "v", score=100), Observation("b", "v", score=0)]
        self.assertEqual(aggregate(rows, sources, config())[0].score, 50)
        rows.append(Observation("clone", "v", score=100))
        self.assertEqual(aggregate(rows, sources, config())[0].score, 50)

    def test_unknown_time_downweighted_not_made_fresh(self):
        sources = {"new": Source("new", DAY), "unknown": Source("unknown", None)}
        rows = [Observation("new", "v", score=100), Observation("unknown", "v", score=0)]
        r = aggregate(rows, sources, config())[0]
        self.assertEqual(r.score, 80)
        self.assertIn("unknown_date", r.issues)

    def test_expired_future_and_zero_reliability_not_counted(self):
        for source in [Source("b", DAY-timedelta(days=91)), Source("b", DAY+timedelta(days=1)), Source("b", DAY, 0)]:
            rows = [Observation("a", "v", score=80), Observation("b", "v", score=100)]
            sources = {"a": Source("a", DAY), "b": source}
            self.assertEqual(aggregate(rows, sources, config(), min_sources=2), [])

    def test_observation_date_overrides_source_date_and_decays(self):
        sources = {s: Source(s, DAY) for s in "ab"}
        rows = [Observation("a", "v", score=100), Observation("b", "v", score=0, observed_at=DAY-timedelta(days=30))]
        self.assertAlmostEqual(aggregate(rows, sources, config(half_life_days=30))[0].score, 200/3)

    def test_reference_and_missing_do_not_inflate_coverage(self):
        sources = {s: Source(s, DAY) for s in "abcd"}
        rows = [Observation("a", "v", score=80), Observation("b", "v", rank=1, total_vendors=100, state="reference", evidence_kind="ordering"), Observation("c", "v", state="missing"), Observation("d", "v", state="reference", evidence_kind="status", issues=("channel_status: down",))]
        r = aggregate(rows, sources, config())[0]
        self.assertEqual(r.source_count, 1)
        self.assertEqual(len(r.contributions), 4)
        self.assertIn("channel_status: down", r.issues)
        self.assertEqual(aggregate(rows, sources, config(), min_sources=2), [])

    def test_inactive_vendor_not_recommended(self):
        sources = {s: Source(s, DAY) for s in "ab"}
        self.assertEqual(aggregate([Observation("a", "v", score=90), Observation("b", "v", state="inactive")], sources, config()), [])

    def test_stability_uses_same_eligible_cohort_and_includes_baseline(self):
        sources = {s: Source(s, DAY) for s in "abc"}
        rows = [Observation(s, v, score=value) for v, value in [("v1", 70), ("v2", 80)] for s in "ab"]
        rows += [Observation("c", "single", score=100)]
        results = aggregate(rows, sources, config(), min_sources=2)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertLessEqual(r.rank_best, r.rank)
            self.assertGreaterEqual(r.rank_worst, r.rank)
            self.assertLessEqual(r.rank_worst, 2)
            self.assertEqual(r.coverage_loss_groups, ("a", "b"))

    def test_duplicate_unknown_source_and_invalid_numbers_rejected(self):
        row = Observation("a", "v", score=80)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            aggregate([row, row], {"a": Source("a", DAY)}, config())
        with self.assertRaisesRegex(ValueError, "unknown source"):
            aggregate([row], {}, config())
        for value in [float("nan"), float("inf"), -1, 101, True]:
            with self.assertRaises(ValueError):
                Observation("a", "v", score=value)
        for kwargs in [{"rank": 1}, {"rank": 3, "total_vendors": 2}, {"rank": 1.5, "total_vendors": 2}]:
            with self.assertRaises(ValueError):
                Observation("a", "v", **kwargs)

    def test_no_fallback_when_composite_goes_missing(self):
        row = Observation("a", "v", uptime=100)
        self.assertEqual(aggregate([row], {"a": Source("a", DAY)}, config()), [])

    def test_nested_nonfinite_source_values_are_rejected(self):
        with self.assertRaises(ValueError):
            Observation("a", "v", score=80, raw_evidence={"cache": float("nan")})

    def test_one_group_removal_removes_all_clones_in_stability(self):
        sources = {"a": Source("a", DAY, independence_group="shared"), "clone": Source("clone", DAY, independence_group="shared"), "b": Source("b", DAY)}
        rows = [Observation(s, v, score=score) for v, av, bv in [("x", 100, 0), ("y", 0, 100)] for s, score in [("a", av), ("clone", av), ("b", bv)]]
        for r in aggregate(rows, sources, config(), min_sources=2):
            self.assertEqual((r.rank_best, r.rank_worst), (1, 2))
            self.assertEqual(r.coverage_loss_groups, ("b", "shared"))


if __name__ == "__main__":
    unittest.main()
