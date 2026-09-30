from __future__ import annotations

import math
from collections import defaultdict
from .models import Config, Observation, RankedVendor, Source


METRICS = ("rank", "score", "uptime", "cache_rate", "price_value")


def _rank_score(observation: Observation) -> float | None:
    if observation.rank is None:
        return None
    return 100.0 if observation.total_vendors == 1 else 100 * (observation.total_vendors - observation.rank) / (observation.total_vendors - 1)


def _observation_score(observation: Observation, config: Config) -> tuple[float, dict[str, float]]:
    if observation.score is not None and config.metric_weights.get("score", 0) > 0:
        return observation.score, {"score": observation.score}
    if observation.evidence_kind == "composite":
        raise ValueError(f"{observation.source}/{observation.vendor}: composite score missing or disabled")
    values = {key: getattr(observation, key) for key in METRICS if key != "rank"}
    values["rank"] = _rank_score(observation)
    present = {k: v for k, v in values.items() if v is not None and config.metric_weights.get(k, 0) > 0}
    denominator = sum(config.metric_weights[k] for k in present)
    if denominator <= 0:
        raise ValueError(f"{observation.source}/{observation.vendor}: no weighted metric is present")
    return sum(v * config.metric_weights[k] for k, v in present.items()) / denominator, present


def observation_weight(observation: Observation, source: Source, config: Config) -> tuple[float, str]:
    if observation.state != "valid" or observation.evidence_kind in {"ordering", "status"}:
        return 0.0, observation.state if observation.state != "valid" else "reference"
    stamp = observation.observed_at or source.published_at
    if stamp is None:
        return source.reliability * config.unknown_date_weight, "unknown_date"
    age = (config.as_of - stamp).days
    if age < 0:
        return 0.0, "future_date"
    if age > config.max_age_days:
        return 0.0, "stale"
    return source.reliability * 0.5 ** (age / config.half_life_days), "dated"


def aggregate(observations: list[Observation], sources: dict[str, Source], config: Config,
              min_sources: int = 1) -> list[RankedVendor]:
    if min_sources < 1:
        raise ValueError("min_sources must be positive")
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for obs in observations:
        if obs.source not in sources:
            raise ValueError(f"unknown source in CSV: {obs.source}")
        key = (obs.source, obs.vendor)
        if key in seen:
            raise ValueError(f"duplicate observation: {obs.source}/{obs.vendor}")
        seen.add(key)
        source = sources[obs.source]
        weight, quality = observation_weight(obs, source, config)
        value, metrics = None, {}
        if weight > 0:
            try:
                value, metrics = _observation_score(obs, config)
            except ValueError:
                weight, quality = 0.0, "missing_metrics"
        issues = list(obs.issues)
        if quality in {"unknown_date", "future_date", "stale", "missing_metrics"}:
            issues.append(quality)
        by_vendor[obs.vendor].append({
            "source": obs.source, "group": source.independence_group or source.name,
            "rank": obs.rank, "total_vendors": obs.total_vendors,
            "website_url": obs.website_url, "raw_score": value,
            "weight": weight, "metrics": metrics, "quality": quality,
            "state": obs.state, "evidence_kind": obs.evidence_kind,
            "observed_at": (obs.observed_at or source.published_at).isoformat() if (obs.observed_at or source.published_at) else None,
            "date_basis": obs.date_basis, "issues": issues, "raw_evidence": obs.raw_evidence,
        })

    # Correlated observations share a maximum of one source's weight per vendor.
    groups_by_vendor = {}
    for vendor, details in by_vendor.items():
        groups = defaultdict(list)
        for d in details:
            if d["weight"] > 0:
                groups[d["group"]].append(d)
        for entries in groups.values():
            total = sum(d["weight"] for d in entries)
            scale = max(d["weight"] for d in entries) / total
            for d in entries:
                d["weight"] *= scale
        groups_by_vendor[vendor] = groups

    cohort = [v for v, groups in groups_by_vendor.items() if len(groups) >= min_sources
              and not any(d["state"] == "inactive" for d in by_vendor[v])]

    def calculate(vendor: str, excluded: str | None = None):
        entries = [d for d in by_vendor[vendor] if d["weight"] > 0 and d["group"] != excluded]
        weight = sum(d["weight"] for d in entries)
        denominator = weight + config.prior_strength
        score = ((sum(d["raw_score"] * d["weight"] for d in entries) + config.prior_score * config.prior_strength)
                 / denominator) if denominator else config.prior_score
        if weight:
            mean = sum(d["raw_score"] * d["weight"] for d in entries) / weight
            sigma = math.sqrt(sum(d["weight"] * (d["raw_score"] - mean) ** 2 for d in entries) / weight)
        else:
            sigma = 0.0
        return score, weight, sigma

    scores = {v: calculate(v) for v in cohort}
    ordered = sorted(cohort, key=lambda v: (-scores[v][0], v.casefold()))
    ranges = {v: [i] for i, v in enumerate(ordered, 1)}
    # Keep the same eligible cohort, remove an independent group (including clones).
    all_groups = sorted({g for v in cohort for g in groups_by_vendor[v]})
    if len(all_groups) > 1:
        for excluded in all_groups:
            subset = sorted(cohort, key=lambda v: (-calculate(v, excluded)[0], v.casefold()))
            for rank, vendor in enumerate(subset, 1):
                ranges[vendor].append(rank)
    results = []
    for rank, vendor in enumerate(ordered, 1):
        score, weight, sigma = scores[vendor]
        details = by_vendor[vendor]
        count = len(groups_by_vendor[vendor])
        issues = tuple(sorted({str(issue) for d in details for issue in d["issues"]}))
        loss = tuple(sorted(g for g in groups_by_vendor[vendor] if count - 1 < min_sources))
        results.append(RankedVendor(
            rank, vendor, score, 1 - math.exp(-weight), count, weight, sigma, 0.0,
            min(ranges[vendor]), max(ranges[vendor]), tuple(details),
            next((str(d["website_url"]) for d in details if d["website_url"]), ""),
            raw_score_stddev=sigma, issues=issues, coverage_loss_groups=loss,
        ))
    return results
