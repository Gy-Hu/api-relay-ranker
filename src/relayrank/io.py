from __future__ import annotations

import csv
import json
import tomllib
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path
from typing import Any

from .identity import Directory, VendorSpec, host_of, registrable_domain
from .longevity import LongevityConfig, VendorAge
from .models import Config, Observation, Ranking, Source

ALGORITHM_VERSION = "3.1"
_AGGREGATION_KEYS = {f.name for f in fields(Config)} - {"as_of"}


def load_exclusions(path: str | Path, domains: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Apply explicit owner exclusions after identity resolution, without changing peers."""
    with Path(path).open("rb") as handle:
        items = tomllib.load(handle).get("exclusions", [])
    rules = {}
    for item in items:
        host = host_of(item.get("domain"))
        reason = item.get("reason", "").strip()
        if not host or not reason:
            raise ValueError("each exclusion requires a domain and reason")
        domain = registrable_domain(host)
        if domain in rules:
            raise ValueError(f"duplicate exclusion: {domain}")
        rules[domain] = reason
    return {vendor: "; ".join(dict.fromkeys(rules[registrable_domain(d)] for d in ds
                                            if registrable_domain(d) in rules))
            for vendor, ds in domains.items()
            if any(registrable_domain(d) in rules for d in ds)}


def load_config(path: str | Path, as_of: date) -> tuple[Config, dict[str, Source], Directory, LongevityConfig]:
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
    return config, sources, Directory(specs), LongevityConfig.from_toml(raw.get("longevity", {}))


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: str | Path, ranking: Ranking, source_names: list[str], ages: dict[str, VendorAge]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "vendor", "score", "sources", "effective_weight", "percentile_stddev",
                         "rank_best", "rank_worst", "operating_days", "service_start", "age_basis", "domains",
                         *[f"{name}_percentile" for name in source_names]])
        for item in ranking.results:
            by_source = {c["source"]: c for c in item.contributions if c["weight"] > 0}
            age = ages.get(item.vendor)
            writer.writerow([
                item.rank, item.vendor, f"{item.score:.2f}", item.source_count, f"{item.effective_weight:.3f}",
                f"{item.score_stddev:.2f}", item.rank_best, item.rank_worst,
                age.operating_days if age and age.operating_days is not None else "",
                age.service_start.isoformat() if age and age.service_start else "", (age.basis or "") if age else "",
                ";".join(item.domains),
                *[f"{by_source[name]['percentile']:.1f}" if name in by_source else "" for name in source_names],
            ])


def write_audit(path: str | Path, ranking: Ranking, config: Config, sources: dict[str, Source],
                ages: dict[str, VendorAge], longevity: LongevityConfig, refresh_stats: dict[str, int]) -> None:
    _dump(Path(path), {
        "algorithm_version": ALGORITHM_VERSION,
        "as_of": config.as_of.isoformat(),
        "config": {key: getattr(config, key) for key in sorted(_AGGREGATION_KEYS)},
        "longevity": {"min_operating_days": longevity.min_operating_days, "lookups_this_run": refresh_stats},
        "sources": {name: asdict(source) for name, source in sources.items()},
        "scoring_sources": list(ranking.scoring_sources),
        "thin_sources": list(ranking.thin_sources),
        "cohort_size": ranking.cohort_size,
        "excluded": [{"vendor": v, "reason": r, "longevity": ages[v].to_json() if v in ages else None}
                     for v, r in sorted(ranking.excluded.items())],
        "results": [{
            "rank": item.rank, "vendor": item.vendor, "score": round(item.score, 3),
            "source_count": item.source_count, "effective_weight": round(item.effective_weight, 4),
            "percentile_stddev": round(item.score_stddev, 3),
            "rank_range_leave_one_group_out": [item.rank_best, item.rank_worst],
            "coverage_loss_groups": list(item.coverage_loss_groups),
            "website_url": item.website_url, "domains": list(item.domains), "issues": list(item.issues),
            "longevity": ages[item.vendor].to_json() if item.vendor in ages else None,
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
