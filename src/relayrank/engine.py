"""Aggregate per-source composites into one reference ranking.

Each source scores vendors on its own scale (RelayPick's leader has 63, Veridrop medians cluster
at 90+), so raw scores are not comparable. Every source's scorable vendors are converted to
mid-rank percentiles over that source's whole list (0 = its worst, 100 = its best), then combined
as a weighted mean shrunk toward a neutral prior. Percentiles use the source's full list, not the
final cohort, so a vendor's per-source value does not depend on which other vendors qualify.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Any, Mapping

from .models import Config, Observation, RankedVendor, Ranking, Source


def observation_weight(observation: Observation, source: Source, config: Config) -> tuple[float, str]:
    if observation.state != "valid":
        return 0.0, observation.state
    if observation.observed_at is None:
        weight, quality = source.reliability * config.unknown_date_weight, "unknown_date"
    else:
        age = (config.as_of - observation.observed_at).days
        if age < 0:
            return 0.0, "future_date"
        if age > config.max_age_days:
            return 0.0, "stale"
        weight, quality = source.reliability * 0.5 ** (age / config.half_life_days), "dated"
    if observation.sample_count is not None:
        weight *= observation.sample_count / (observation.sample_count + config.sample_prior)
    return weight, quality


def percentiles(scores: dict[str, float]) -> dict[str, float]:
    """Mid-rank percentile in [0, 100]; ties share the midpoint; a lone vendor is neutral."""
    if len(scores) == 1:
        return dict.fromkeys(scores, 50.0)
    ordered = sorted(scores.values())
    span = len(ordered) - 1
    result = {}
    for key, value in scores.items():
        below = bisect_left(ordered, value)
        equal = bisect_right(ordered, value) - below
        result[key] = 100 * (below + (equal - 1) / 2) / span
    return result


def aggregate(observations: list[Observation], sources: dict[str, Source], config: Config,
              min_sources: int | None = None, excluded: Mapping[str, str] | None = None) -> Ranking:
    """``excluded`` maps vendor -> reason for hard-rule removals (e.g. expired domain).

    Excluded vendors leave the cohort but stay in every source's percentile basis, so removing
    one never changes another vendor's score.
    """
    excluded = excluded or {}
    min_sources = config.minimum_sources if min_sources is None else min_sources
    if min_sources < 1:
        raise ValueError("min_sources must be positive")
    by_vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for obs in observations:
        if obs.source not in sources:
            raise ValueError(f"unknown source: {obs.source}")
        if (obs.source, obs.vendor) in seen:
            raise ValueError(f"duplicate observation: {obs.source}/{obs.vendor}")
        seen.add((obs.source, obs.vendor))
        source = sources[obs.source]
        weight, quality = observation_weight(obs, source, config)
        by_vendor[obs.vendor].append({
            "source": obs.source, "group": source.group, "raw_score": obs.score, "weight": weight,
            "quality": quality, "percentile": None, "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            "sample_count": obs.sample_count, "state": obs.state, "domain": obs.domain,
            "website_url": obs.website_url, "source_url": obs.source_url,
            "issues": list(obs.issues) + ([quality] if quality in {"unknown_date", "future_date", "stale"} else []),
            "raw_evidence": obs.raw_evidence,
        })

    # Too few scorable vendors make percentiles meaningless (two vendors -> 0 and 100).
    counts = defaultdict(int)
    for entries in by_vendor.values():
        for e in entries:
            if e["weight"] > 0:
                counts[e["source"]] += 1
    scoring = {name for name in sources if counts[name] >= config.minimum_peers}
    thin_sources = tuple(sorted(name for name in sources if name not in scoring))
    for entries in by_vendor.values():
        for e in entries:
            if e["weight"] > 0 and e["source"] not in scoring:
                e["weight"], e["quality"] = 0.0, "insufficient_peers"
                e["issues"].append("insufficient_peers")

    for name in scoring:
        source_scores = {v: e["raw_score"] for v, entries in by_vendor.items() for e in entries
                         if e["source"] == name and e["weight"] > 0}
        for vendor, value in percentiles(source_scores).items():
            next(e for e in by_vendor[vendor] if e["source"] == name)["percentile"] = value

    def covering(entries: list[dict[str, Any]]) -> set[str]:
        """Groups whose evidence is strong enough to count toward the entry threshold."""
        totals: dict[str, float] = defaultdict(float)
        for e in entries:
            totals[e["group"]] = max(totals[e["group"]], e["weight"])
        return {g for g, w in totals.items() if w >= config.coverage_weight}

    coverage = {v: covering(entries) for v, entries in by_vendor.items()}
    cohort = {v for v, groups in coverage.items() if len(groups) >= min_sources and v not in excluded}

    # Sources in one lineage group share at most one source's weight per vendor.
    for vendor in cohort:
        groups = defaultdict(list)
        for e in by_vendor[vendor]:
            if e["weight"] > 0:
                groups[e["group"]].append(e)
        for entries in groups.values():
            scale = max(e["weight"] for e in entries) / sum(e["weight"] for e in entries)
            for e in entries:
                e["weight"] *= scale

    def calculate(vendor: str, dropped_group: str | None = None) -> tuple[float, float, float]:
        entries = [e for e in by_vendor[vendor] if e["weight"] > 0 and e["group"] != dropped_group]
        weight = sum(e["weight"] for e in entries)
        total = sum(e["percentile"] * e["weight"] for e in entries)
        denominator = weight + config.prior_strength
        score = (total + config.prior_score * config.prior_strength) / denominator if denominator else config.prior_score
        mean = total / weight if weight else 0.0
        sigma = math.sqrt(sum(e["weight"] * (e["percentile"] - mean) ** 2 for e in entries) / weight) if weight else 0.0
        return score, weight, sigma

    scores = {v: calculate(v) for v in cohort}
    ordered = sorted(cohort, key=lambda v: (-scores[v][0], v.casefold()))
    ranges = {v: [i] for i, v in enumerate(ordered, 1)}
    groups_present = sorted({e["group"] for v in cohort for e in by_vendor[v] if e["weight"] > 0})
    if len(groups_present) > 1:
        # Remove one whole lineage group, keep the same cohort, and re-rank.
        for dropped_group in groups_present:
            subset = sorted(cohort, key=lambda v: (-calculate(v, dropped_group)[0], v.casefold()))
            for rank, vendor in enumerate(subset, 1):
                ranges[vendor].append(rank)

    source_order = {name: i for i, name in enumerate(sources)}
    results = []
    for rank, vendor in enumerate(ordered, 1):
        score, weight, sigma = scores[vendor]
        details = sorted(by_vendor[vendor], key=lambda e: source_order[e["source"]])
        groups = coverage[vendor]
        results.append(RankedVendor(
            rank=rank, vendor=vendor, score=score, source_count=len(groups), effective_weight=weight,
            score_stddev=sigma, rank_best=min(ranges[vendor]), rank_worst=max(ranges[vendor]),
            contributions=tuple(details),
            website_url=next((str(e["website_url"]) for e in details if e["website_url"]), ""),
            domains=tuple(sorted({e["domain"] for e in details if e["domain"]})),
            issues=tuple(sorted({str(issue) for e in details for issue in e["issues"]})),
            coverage_loss_groups=tuple(sorted(groups)) if len(groups) - 1 < min_sources else (),
        ))

    evaluations = {}
    for vendor, entries in by_vendor.items():
        status = "ranked" if vendor in cohort else ("excluded" if vendor in excluded else "insufficient_sources")
        for e in entries:
            evaluations[(e["source"], vendor)] = {
                "weight": e["weight"], "quality": e["quality"], "percentile": e["percentile"],
                "group": e["group"], "vendor_status": status, "excluded_reason": excluded.get(vendor),
            }
    return Ranking(results, evaluations, len(cohort), tuple(sorted(scoring)), thin_sources,
                   {v: r for v, r in excluded.items() if v in by_vendor})
