from __future__ import annotations

import csv
import json
import tomllib
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path
from typing import Any

from .identity import Directory, VendorSpec
from .models import Config, Observation, Ranking, Source

ALGORITHM_VERSION = "3.0"
_AGGREGATION_KEYS = {f.name for f in fields(Config)} - {"as_of"}


def load_config(path: str | Path, as_of: date) -> tuple[Config, dict[str, Source], Directory]:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    aggregation = raw.get("aggregation", {})
    unknown = set(aggregation) - _AGGREGATION_KEYS
    if unknown:
        raise ValueError(f"unknown [aggregation] keys: {', '.join(sorted(unknown))}")
    config = Config(as_of=as_of, **aggregation)
    sources = {}
    for item in raw.get("sources", []):
        source = Source(name=item["name"], reliability=float(item.get("reliability", 1)),
                        group=item.get("group", ""), window_days=item.get("window_days"))
        if source.name in sources:
            raise ValueError(f"duplicate source: {source.name}")
        sources[source.name] = source
    specs = tuple(VendorSpec(item["name"].strip(), tuple(item.get("domains", ())), tuple(item.get("aliases", ())))
                  for item in raw.get("vendors", []))
    return config, sources, Directory(specs)


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: str | Path, ranking: Ranking, source_names: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "vendor", "score", "sources", "effective_weight", "percentile_stddev",
                         "rank_best", "rank_worst", "domains", *[f"{name}_percentile" for name in source_names]])
        for item in ranking.results:
            by_source = {c["source"]: c for c in item.contributions if c["weight"] > 0}
            writer.writerow([
                item.rank, item.vendor, f"{item.score:.2f}", item.source_count, f"{item.effective_weight:.3f}",
                f"{item.score_stddev:.2f}", item.rank_best, item.rank_worst, ";".join(item.domains),
                *[f"{by_source[name]['percentile']:.1f}" if name in by_source else "" for name in source_names],
            ])


def write_audit(path: str | Path, ranking: Ranking, config: Config, sources: dict[str, Source]) -> None:
    _dump(Path(path), {
        "algorithm_version": ALGORITHM_VERSION,
        "as_of": config.as_of.isoformat(),
        "config": {key: getattr(config, key) for key in sorted(_AGGREGATION_KEYS)},
        "sources": {name: asdict(source) for name, source in sources.items()},
        "scoring_sources": list(ranking.scoring_sources),
        "thin_sources": list(ranking.thin_sources),
        "cohort_size": ranking.cohort_size,
        "results": [{
            "rank": item.rank, "vendor": item.vendor, "score": round(item.score, 3),
            "source_count": item.source_count, "effective_weight": round(item.effective_weight, 4),
            "percentile_stddev": round(item.score_stddev, 3),
            "rank_range_leave_one_group_out": [item.rank_best, item.rank_worst],
            "coverage_loss_groups": list(item.coverage_loss_groups),
            "website_url": item.website_url, "domains": list(item.domains), "issues": list(item.issues),
            "contributions": list(item.contributions),
        } for item in ranking.results],
    })


def write_observations(path: str | Path, observations: list[Observation], ranking: Ranking,
                       domains: dict[str, tuple[str, ...]]) -> None:
    payload = []
    for observation in observations:
        row = asdict(observation)
        row["observed_at"] = observation.observed_at.isoformat() if observation.observed_at else None
        row["vendor_domains"] = list(domains.get(observation.vendor, ()))
        row["evaluation"] = ranking.evaluations.get((observation.source, observation.vendor))
        payload.append(row)
    _dump(Path(path), payload)


def write_reports(path: str | Path, reports: list[Any]) -> None:
    _dump(Path(path), [asdict(report) for report in reports])
