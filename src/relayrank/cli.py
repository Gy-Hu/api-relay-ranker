from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import longevity
from .engine import aggregate
from .io import load_config, load_exclusions, write_audit, write_csv, write_observations, write_reports
from .live import SOURCES, LiveCollectionError, collect_live
from .site import write_site


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relayrank", description="Aggregate measured API relay rankings.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    live = subparsers.add_parser("live", help="fetch every source and publish the reference ranking")
    live.add_argument("--config", required=True, help="TOML sources, weighting and vendor domains")
    live.add_argument("--top", type=int, default=20, help="number of vendors meeting the operating-age bar to publish")
    live.add_argument("--min-sources", type=int, default=None, help="minimum scoring source groups per vendor")
    live.add_argument("--snapshot-dir", default="snapshots", help="raw response archive")
    live.add_argument("--output-dir", default="outputs", help="ranking.csv, audit.json, observations.json, sources.json")
    live.add_argument("--evidence-cache", default="data/domain_evidence.json",
                      help="persistent per-domain age/expiry evidence (committed to the repository)")
    live.add_argument("--skip-lookups", action="store_true", help="use the evidence cache without RDAP/CT/Wayback queries")
    live.add_argument("--site-dir", help="also generate the static site here (data copied to <site>/data)")
    live.add_argument("--site-url", default="", help="public site URL used in social metadata")
    live.add_argument("--og-image", help="optional social preview image copied into the site")
    live.add_argument("--allow-partial", action="store_true", help="publish even if a source fails")
    live.add_argument("--timeout", type=float, default=30)
    return parser


def _print_table(ranking, ages, limit: int) -> None:
    print(f"{'#':>3}  {'vendor':<22} {'score':>6} {'src':>4} {'weight':>7} {'days':>5} {'stable range':>12}")
    print("-" * 66)
    for item in ranking.results[:limit]:
        days = ages[item.vendor].operating_days if item.vendor in ages else None
        print(f"{item.rank:>3}  {item.vendor:<22.22} {item.score:>6.1f} {item.source_count:>4} "
              f"{item.effective_weight:>7.2f} {days if days is not None else '?':>5} {item.rank_best:>5}-{item.rank_worst:<5}")
    for vendor, reason in sorted(ranking.excluded.items()):
        print(f"  excluded: {vendor} ({reason})")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    config, sources, directory, longevity_config = load_config(args.config, now.date())
    min_sources = config.minimum_sources if args.min_sources is None else args.min_sources
    if args.top < 1 or min_sources < 1 or args.timeout <= 0:
        raise ValueError("top, min-sources and timeout must be positive")
    output = Path(args.output_dir)
    try:
        raw, reports = collect_live(sources, directory, config.as_of, args.snapshot_dir,
                                    allow_partial=args.allow_partial, timeout=args.timeout)
    except LiveCollectionError as error:
        write_reports(output / "sources.json", error.reports)
        raise
    cache = longevity.load_cache(args.evidence_cache)
    longevity.record_observations(cache, raw, config.as_of)
    observations, domains = directory.resolve(raw)
    # Look up likely-ranked vendors first so the per-run budget goes where it matters.
    preliminary = aggregate(observations, sources, config, min_sources=min_sources)
    ordered = [v.vendor for v in preliminary.results] + sorted(set(domains) - {v.vendor for v in preliminary.results})
    priority = list(dict.fromkeys(d for vendor in ordered for d in domains.get(vendor, ())))
    stats = {} if args.skip_lookups else longevity.refresh(cache, priority, config.as_of, longevity.HttpLookups())
    longevity.save_cache(args.evidence_cache, cache)
    ages = longevity.vendor_ages(domains, cache, config.as_of)
    excluded = {v: a.excluded for v, a in ages.items() if a.excluded}
    excluded.update(load_exclusions(args.config, domains))
    ranking = aggregate(observations, sources, config, min_sources=min_sources, excluded=excluded)
    for report in reports:
        print(f"{report.name}: {'ok' if report.ok else 'FAILED'} {report.valid_count}/{report.vendor_count} scored"
              + (f" ({report.error})" if report.error else ""))
    if stats:
        print(f"longevity lookups: {stats}")
    _print_table(ranking, ages, args.top)
    names = [info.name for info in SOURCES]
    write_csv(output / "ranking.csv", ranking, names, ages)
    write_audit(output / "audit.json", ranking, config, sources, ages, longevity_config, stats)
    write_observations(output / "observations.json", observations, ranking, domains)
    write_reports(output / "sources.json", reports)
    if args.site_dir:
        data_dir = Path(args.site_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("ranking.csv", "audit.json", "observations.json", "sources.json"):
            shutil.copyfile(output / name, data_dir / name)
        shutil.copyfile(args.evidence_cache, data_dir / "domain_evidence.json")
        write_site(args.site_dir, ranking, reports, sources, config, generated_at=now.isoformat(),
                   ages=ages, min_operating_days=longevity_config.min_operating_days,
                   top=args.top, min_sources=min_sources, site_url=args.site_url, og_image=args.og_image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
