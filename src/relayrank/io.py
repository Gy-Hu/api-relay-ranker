from __future__ import annotations

import csv
import json
import tomllib
from datetime import date
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Config, Observation, RankedVendor, Source


def _optional_number(value: str | None, kind: type = float) -> Any:
    if value is None or not value.strip():
        return None
    return kind(value)


def load_config(path: str | Path) -> tuple[Config, dict[str, Source], dict[str, str]]:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)

    aggregation = raw.get("aggregation", {})
    config = Config(
        as_of=date.fromisoformat(aggregation.get("as_of", date.today().isoformat())),
        half_life_days=float(aggregation.get("half_life_days", 45)),
        prior_score=float(aggregation.get("prior_score", 50)),
        prior_strength=float(aggregation.get("prior_strength", 0.8)),
        minimum_sources=int(aggregation.get("minimum_sources", 2)),
        variance_penalty=float(aggregation.get("variance_penalty", 0)),
        low_outlier_gap=float(aggregation.get("low_outlier_gap", 0)),
        three_source_bonus=float(aggregation.get("three_source_bonus", 0)),
        four_source_bonus=float(aggregation.get("four_source_bonus", 0)),
        unknown_date_weight=float(aggregation.get("unknown_date_weight", 0.25)),
        max_age_days=int(aggregation.get("max_age_days", 90)),
        metric_weights={
            key: float(value)
            for key, value in raw.get("metric_weights", Config(date.today()).metric_weights).items()
        },
    )
    sources = {
        item["name"]: Source(
            name=item["name"],
            published_at=date.fromisoformat(item["published_at"]) if item.get("published_at") else None,
            reliability=float(item.get("reliability", 1)),
            independence_group=item.get("independence_group", item["name"]),
        )
        for item in raw.get("sources", [])
    }
    aliases = {}
    for canonical, names in raw.get("aliases", {}).items():
        for alias in [canonical, *names]:
            key = alias.strip().casefold()
            if key in aliases and aliases[key] != canonical.strip():
                raise ValueError(f"ambiguous vendor alias: {alias}")
            aliases[key] = canonical.strip()
    return config, sources, aliases


def load_observations(path: str | Path, aliases: dict[str, str]) -> list[Observation]:
    observations: list[Observation] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_vendor = row["vendor"].strip()
            vendor = aliases.get(raw_vendor.casefold(), raw_vendor)
            observations.append(
                Observation(
                    source=row["source"].strip(),
                    vendor=vendor,
                    rank=_optional_number(row.get("rank"), int),
                    total_vendors=_optional_number(row.get("total_vendors"), int),
                    score=_optional_number(row.get("score")),
                    evidence_kind="composite" if row.get("score", "").strip() else "metrics",
                    uptime=_optional_number(row.get("uptime")),
                    cache_rate=_optional_number(row.get("cache_rate")),
                    price_value=_optional_number(row.get("price_value")),
                )
            )
    return observations


def write_csv(path: str | Path, results: list[RankedVendor]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank",
                "vendor",
                "website_url",
                "score",
                "raw_score_stddev",
                "score_stddev",
                "disagreement_penalty",
                "coverage_bonus",
                "low_outlier_sources",
                "evidence_strength",
                "sources",
                "effective_weight",
                "rank_best",
                "rank_worst",
            ]
        )
        for item in results:
            writer.writerow(
                [
                    item.rank,
                    item.vendor,
                    item.website_url,
                    f"{item.score:.2f}",
                    f"{item.raw_score_stddev:.2f}",
                    f"{item.score_stddev:.2f}",
                    f"{item.disagreement_penalty:.2f}",
                    f"{item.coverage_bonus:.2f}",
                    ";".join(item.low_outlier_sources),
                    f"{item.confidence:.3f}",
                    item.source_count,
                    f"{item.effective_weight:.3f}",
                    item.rank_best,
                    item.rank_worst,
                ]
            )


def write_json(path: str | Path, results: list[RankedVendor]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "algorithm_version": "2.0",
            "issues": list(item.issues),
            "coverage_loss_groups": list(item.coverage_loss_groups),
            "evidence_strength": round(item.confidence, 4),
            "rank": item.rank,
            "vendor": item.vendor,
            "website_url": item.website_url,
            "score": round(item.score, 3),
            "raw_score_stddev": round(item.raw_score_stddev, 3),
            "score_stddev": round(item.score_stddev, 3),
            "disagreement_penalty": round(item.disagreement_penalty, 3),
            "coverage_bonus": round(item.coverage_bonus, 3),
            "low_outlier_sources": list(item.low_outlier_sources),
            "evidence_strength_is_probability": False,
            "source_count": item.source_count,
            "effective_weight": round(item.effective_weight, 4),
            "rank_range_leave_one_out": [item.rank_best, item.rank_worst],
            "contributions": list(item.contributions),
        }
        for item in results
    ]
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_observations(path: str | Path, observations: list[Observation], sources=None, config=None, min_sources=1) -> None:
    from .engine import observation_weight, _observation_score
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    active_groups = {}
    inactive_vendors = {o.vendor for o in observations if o.state == "inactive"}
    for observation in observations:
        row = asdict(observation)
        row["observed_at"] = observation.observed_at.isoformat() if observation.observed_at else None
        if sources is not None and config is not None:
            source = sources[observation.source]
            weight, reason = observation_weight(observation, source, config)
            if weight > 0:
                try:
                    _observation_score(observation, config)
                except ValueError:
                    weight, reason = 0, "missing_metrics"
            row["evaluation"] = {"eligible_for_scoring": weight > 0, "reason": reason,
                                 "weight_before_correlation": weight,
                                 "group": source.independence_group or source.name}
            if weight > 0:
                active_groups.setdefault(observation.vendor, set()).add(source.independence_group or source.name)
        payload.append(row)
    for row in payload:
        if "evaluation" in row:
            count = len(active_groups.get(row["vendor"], set()))
            row["evaluation"]["vendor_scoring_groups"] = count
            row["evaluation"]["vendor_eligibility"] = (
                "inactive_signal" if row["vendor"] in inactive_vendors else
                "eligible" if count >= min_sources else "insufficient_scoring_groups")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
