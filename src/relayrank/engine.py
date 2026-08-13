from __future__ import annotations

import math
from collections import Counter, defaultdict
from .models import Config, Observation, RankedVendor, Source


METRICS = ("rank", "score", "uptime", "cache_rate", "price_value")


def _clamp(value: float) -> float:
    return min(100.0, max(0.0, value))


def _rank_score(observation: Observation) -> float | None:
    if observation.rank is None or observation.total_vendors is None:
        return None
    if observation.total_vendors <= 1:
        return 100.0
    return _clamp(100 * (observation.total_vendors - observation.rank) / (observation.total_vendors - 1))


def _observation_score(observation: Observation, config: Config) -> tuple[float, dict[str, float]]:
    values = {
        "rank": _rank_score(observation),
        "score": observation.score,
        "uptime": observation.uptime,
        "cache_rate": observation.cache_rate,
        "price_value": observation.price_value,
    }
    present = {key: _clamp(float(value)) for key, value in values.items() if value is not None}
    denominator = sum(config.metric_weights.get(key, 0) for key in present)
    if denominator <= 0:
        raise ValueError(f"{observation.source}/{observation.vendor}: no weighted metric is present")
    normalized_weights = {key: config.metric_weights[key] / denominator for key in present}
    return sum(present[key] * normalized_weights[key] for key in present), present


def _source_weights(sources: dict[str, Source], config: Config) -> dict[str, float]:
    groups = Counter(source.independence_group or source.name for source in sources.values())
    weights: dict[str, float] = {}
    for name, source in sources.items():
        age = max(0, (config.as_of - source.published_at).days)
        freshness = 0.5 ** (age / config.half_life_days) if config.half_life_days > 0 else 1.0
        # Fully correlated sources share one vote instead of multiplying influence.
        correlation_discount = groups[source.independence_group or source.name]
        weights[name] = source.reliability * freshness / correlation_discount
    return weights


def _scores(
    observations: list[Observation],
    sources: dict[str, Source],
    config: Config,
    excluded_source: str | None = None,
) -> tuple[
    dict[str, float],
    dict[str, list[dict[str, object]]],
    dict[str, float],
    dict[str, float],
]:
    source_weights = _source_weights(sources, config)
    weighted_sum: defaultdict[str, float] = defaultdict(float)
    weighted_square_sum: defaultdict[str, float] = defaultdict(float)
    weight_sum: defaultdict[str, float] = defaultdict(float)
    details: defaultdict[str, list[dict[str, object]]] = defaultdict(list)

    seen: set[tuple[str, str]] = set()
    for observation in observations:
        if observation.source == excluded_source:
            continue
        if observation.source not in sources:
            raise ValueError(f"unknown source in CSV: {observation.source}")
        key = (observation.source, observation.vendor)
        if key in seen:
            raise ValueError(f"duplicate observation: {observation.source}/{observation.vendor}")
        seen.add(key)
        raw_score, metrics = _observation_score(observation, config)
        weight = source_weights[observation.source]
        weighted_sum[observation.vendor] += raw_score * weight
        weighted_square_sum[observation.vendor] += raw_score * raw_score * weight
        weight_sum[observation.vendor] += weight
        details[observation.vendor].append(
            {
                "source": observation.source,
                "rank": observation.rank,
                "total_vendors": observation.total_vendors,
                "website_url": observation.website_url,
                "raw_score": round(raw_score, 3),
                "weight": round(weight, 4),
                "metrics": metrics,
            }
        )

    score_stddevs = {
        vendor: math.sqrt(
            max(
                0.0,
                weighted_square_sum[vendor] / weight_sum[vendor]
                - (weighted_sum[vendor] / weight_sum[vendor]) ** 2,
            )
        )
        if weight_sum[vendor] > 0
        else 0.0
        for vendor in weighted_sum
    }
    scores = {
        vendor: _clamp(
            (weighted_sum[vendor] + config.prior_score * config.prior_strength)
            / (weight_sum[vendor] + config.prior_strength)
            - config.variance_penalty * score_stddevs[vendor]
        )
        for vendor in weighted_sum
    }
    return scores, details, dict(weight_sum), score_stddevs


def aggregate(observations: list[Observation], sources: dict[str, Source], config: Config) -> list[RankedVendor]:
    if not observations:
        return []
    scores, details, evidence, score_stddevs = _scores(observations, sources, config)
    ordered = sorted(scores, key=lambda vendor: (-scores[vendor], vendor.casefold()))

    leave_one_out_ranks: defaultdict[str, list[int]] = defaultdict(list)
    used_sources = sorted({observation.source for observation in observations})
    if len(used_sources) > 1:
        for excluded in used_sources:
            subset_scores, _, _, _ = _scores(observations, sources, config, excluded)
            subset_order = sorted(subset_scores, key=lambda vendor: (-subset_scores[vendor], vendor.casefold()))
            for rank, vendor in enumerate(subset_order, 1):
                leave_one_out_ranks[vendor].append(rank)

    results: list[RankedVendor] = []
    for rank, vendor in enumerate(ordered, 1):
        source_count = len(details[vendor])
        evidence_confidence = 1 - math.exp(-evidence[vendor])
        coverage_confidence = min(1.0, source_count / max(1, config.minimum_sources))
        confidence = evidence_confidence * coverage_confidence
        ranges = leave_one_out_ranks[vendor] or [rank]
        website_url = next(
            (str(item["website_url"]) for item in details[vendor] if item.get("website_url")),
            "",
        )
        results.append(
            RankedVendor(
                rank=rank,
                vendor=vendor,
                score=scores[vendor],
                confidence=confidence,
                source_count=source_count,
                effective_weight=evidence[vendor],
                score_stddev=score_stddevs[vendor],
                disagreement_penalty=config.variance_penalty * score_stddevs[vendor],
                rank_best=min(ranges),
                rank_worst=max(ranges),
                contributions=tuple(details[vendor]),
                website_url=website_url,
            )
        )
    return results
