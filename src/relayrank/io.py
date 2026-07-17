from __future__ import annotations

import csv
import json
import tomllib
from datetime import date
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
        minimum_sources=int(aggregation.get("minimum_sources", 3)),
        metric_weights={
            key: float(value)
            for key, value in raw.get("metric_weights", Config(date.today()).metric_weights).items()
        },
    )
    sources = {
        item["name"]: Source(
            name=item["name"],
            published_at=date.fromisoformat(item["published_at"]),
            reliability=float(item.get("reliability", 1)),
            independence_group=item.get("independence_group", item["name"]),
        )
        for item in raw.get("sources", [])
    }
    aliases = {
        alias.strip().casefold(): canonical.strip()
        for canonical, names in raw.get("aliases", {}).items()
        for alias in [canonical, *names]
    }
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
        writer.writerow(["rank", "vendor", "website_url", "score", "confidence", "sources", "effective_weight", "rank_best", "rank_worst"])
        for item in results:
            writer.writerow([item.rank, item.vendor, item.website_url, f"{item.score:.2f}", f"{item.confidence:.3f}", item.source_count, f"{item.effective_weight:.3f}", item.rank_best, item.rank_worst])


def write_json(path: str | Path, results: list[RankedVendor]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "rank": item.rank,
            "vendor": item.vendor,
            "website_url": item.website_url,
            "score": round(item.score, 3),
            "confidence": round(item.confidence, 4),
            "source_count": item.source_count,
            "effective_weight": round(item.effective_weight, 4),
            "rank_range_leave_one_out": [item.rank_best, item.rank_worst],
            "contributions": list(item.contributions),
        }
        for item in results
    ]
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
