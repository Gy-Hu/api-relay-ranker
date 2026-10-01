import unittest
from datetime import date, timedelta

from relayrank.engine import aggregate, percentiles
from relayrank.models import Config, Observation, Source

DAY = date(2026, 10, 1)


def config(**kwargs):
    kwargs.setdefault("prior_strength", 0)
    kwargs.setdefault("minimum_peers", 1)
    return Config(as_of=DAY, **kwargs)


def obs(source, vendor, score, **kwargs):
    kwargs.setdefault("observed_at", DAY)
    return Observation(source, vendor, score=score, **kwargs)


def ranks(ranking):
    return [r.vendor for r in ranking.results]


class PercentileTests(unittest.TestCase):
    def test_ties_share_midpoint_and_extremes_span_0_to_100(self):
        self.assertEqual(percentiles({"a": 10, "b": 20, "c": 20, "d": 30}), {"a": 0, "b": 50, "c": 50, "d": 100})

    def test_single_vendor_is_neutral(self):
        self.assertEqual(percentiles({"a": 99}), {"a": 50})


class AggregationTests(unittest.TestCase):
    def test_source_scale_does_not_change_ranking(self):
        # Source b scores everyone around 95 with tiny spread; a raw-score mean would let
        # source a decide alone. Percentiles give both sources the same say.
        sources = {s: Source(s) for s in "ab"}
        rows = [obs("a", "x", 80), obs("a", "y", 60), obs("a", "z", 40),
                obs("b", "x", 94.0), obs("b", "y", 96.0), obs("b", "z", 95.0)]
        compressed = aggregate(rows, sources, config())
        stretched = aggregate([Observation(o.source, o.vendor, score=(o.score - 90) * 10 if o.source == "b" else o.score,
                                           observed_at=DAY) for o in rows], sources, config())
        self.assertEqual(ranks(compressed), ranks(stretched))
        self.assertEqual([r.score for r in compressed.results], [r.score for r in stretched.results])
        self.assertEqual(ranks(compressed), ["y", "x", "z"])

    def test_raising_one_source_score_never_lowers_that_vendor(self):
        sources = {s: Source(s, reliability=w) for s, w in zip("ab", [0.9, 0.4])}
        base = [obs("a", v, s) for v, s in zip("vwxyz", [50, 60, 70, 80, 90])]
        base += [obs("b", v, s) for v, s in zip("vwxyz", [90, 10, 50, 30, 70])]
        previous = None
        for raised in range(0, 101, 5):
            rows = [o if (o.source, o.vendor) != ("b", "v") else obs("b", "v", raised) for o in base]
            score = next(r.score for r in aggregate(rows, sources, config()).results if r.vendor == "v")
            if previous is not None:
                self.assertGreaterEqual(score, previous)
            previous = score

    def test_prior_shrinks_thin_evidence_toward_median(self):
        sources = {s: Source(s) for s in "ab"}
        rows = [obs("a", "top", 100), obs("a", "low", 0), obs("b", "top", 100), obs("b", "low", 0)]
        result = aggregate(rows, sources, config(prior_strength=2)).results[0]
        self.assertEqual(result.vendor, "top")
        self.assertEqual(result.score, 75)

    def test_same_lineage_counts_as_one_vote_and_one_group(self):
        sources = {"a": Source("a", group="okkmax"), "mirror": Source("mirror", group="okkmax"), "b": Source("b")}
        rows = [obs("a", "v", 100), obs("mirror", "v", 100), obs("a", "w", 0), obs("mirror", "w", 0)]
        # Two sources of one lineage are not two independent groups.
        self.assertEqual(aggregate(rows, sources, config(), min_sources=2).results, [])
        rows += [obs("b", "v", 0), obs("b", "w", 100)]
        ranking = aggregate(rows, sources, config(), min_sources=2)
        # Lineage weight 1 vs b weight 1: the clone does not double okkmax's say.
        self.assertEqual([r.score for r in ranking.results], [50, 50])

    def test_stale_future_and_missing_do_not_score_or_count_as_coverage(self):
        sources = {s: Source(s) for s in "ab"}
        for other in (obs("b", "v", 90, observed_at=DAY - timedelta(days=91)),
                      obs("b", "v", 90, observed_at=DAY + timedelta(days=1)),
                      Observation("b", "v", state="missing")):
            rows = [obs("a", "v", 90), obs("a", "w", 10), other]
            self.assertEqual(aggregate(rows, sources, config(), min_sources=2).results, [])

    def test_missing_composite_is_not_a_zero(self):
        sources = {s: Source(s) for s in "abc"}
        rows = [obs("a", "v", 90), obs("a", "w", 10), obs("b", "v", 90), obs("b", "w", 10),
                Observation("c", "v", state="missing"), obs("c", "w", 50)]
        ranking = aggregate(rows, sources, config(), min_sources=2)
        self.assertEqual(ranks(ranking), ["v", "w"])
        self.assertEqual(ranking.results[0].score, 100)

    def test_fresher_and_better_sampled_evidence_weighs_more(self):
        sources = {s: Source(s) for s in "ab"}
        rows = [obs("a", "v", 100), obs("a", "w", 0),
                obs("b", "v", 0, observed_at=DAY - timedelta(days=30)), obs("b", "w", 100, observed_at=DAY - timedelta(days=30))]
        self.assertEqual(ranks(aggregate(rows, sources, config(half_life_days=30))), ["v", "w"])
        sampled = [obs("a", "v", 100, sample_count=1), obs("a", "w", 0, sample_count=1),
                   obs("b", "v", 0, sample_count=50), obs("b", "w", 100, sample_count=50)]
        self.assertEqual(ranks(aggregate(sampled, sources, config())), ["w", "v"])

    def test_thin_source_is_excluded_instead_of_handing_out_0_and_100(self):
        sources = {s: Source(s) for s in "abt"}
        rows = [obs(s, v, sc) for s in "ab" for v, sc in zip("vwxyz", [90, 80, 70, 60, 50])]
        rows += [obs("t", "v", 1), obs("t", "w", 99)]
        ranking = aggregate(rows, sources, config(minimum_peers=5))
        self.assertEqual(ranking.thin_sources, ("t",))
        self.assertEqual(ranks(ranking)[:2], ["v", "w"])
        self.assertEqual(ranking.evaluations[("t", "v")]["quality"], "insufficient_peers")

    def test_weak_second_source_scores_but_cannot_qualify_a_vendor(self):
        sources = {s: Source(s) for s in "ab"}
        weak = dict(observed_at=DAY - timedelta(days=55), sample_count=2)  # weight ~0.11
        rows = [obs("a", "v", 90), obs("a", "w", 10), obs("b", "v", 99, **weak), obs("b", "w", 98)]
        ranking = aggregate(rows, sources, config(half_life_days=30), min_sources=2)
        self.assertEqual(ranks(ranking), ["w"])
        self.assertEqual(ranking.evaluations[("a", "v")]["vendor_status"], "insufficient_sources")

    def test_leave_one_group_out_range_includes_baseline(self):
        sources = {s: Source(s) for s in "abc"}
        # Removing group a or b leaves a tie, which breaks by name in favour of "u".
        rows = [obs("a", "v", 100), obs("a", "u", 0), obs("b", "v", 100), obs("b", "u", 0),
                obs("c", "v", 0), obs("c", "u", 100)]
        ranking = aggregate(rows, sources, config(), min_sources=2)
        v = ranking.results[0]
        self.assertEqual((v.vendor, v.rank_best, v.rank_worst), ("v", 1, 2))

    def test_duplicate_and_unknown_sources_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            aggregate([obs("a", "v", 1), obs("a", "v", 2)], {"a": Source("a")}, config())
        with self.assertRaisesRegex(ValueError, "unknown source"):
            aggregate([obs("zz", "v", 1)], {"a": Source("a")}, config())

    def test_invalid_values_are_rejected(self):
        for kwargs in ({"score": 101}, {"score": float("nan")}, {"sample_count": -1}, {"state": "valid"}):
            with self.assertRaises(ValueError):
                Observation("a", "v", **kwargs)
        with self.assertRaises(ValueError):
            Observation("a", "v", score=1, raw_evidence={"x": float("inf")})


if __name__ == "__main__":
    unittest.main()
