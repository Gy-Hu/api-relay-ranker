from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .engine import aggregate
from .io import load_config, write_audit, write_csv, write_observations, write_reports
from .live import SOURCES, LiveCollectionError, collect_live
from .site import write_site


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relayrank", description="Aggregate measured API relay rankings.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    live = subparsers.add_parser("live", help="fetch every source and publish the reference ranking")
    live.add_argument("--config", required=True, help="TOML sources, weighting and vendor domains")
    live.add_argument("--top", type=int, default=20, help="number of ranked vendors to print and publish")
    live.add_argument("--min-sources", type=int, default=None, help="minimum scoring source groups per vendor")
    live.add_argument("--snapshot-dir", default="snapshots", help="raw response archive")
    live.add_argument("--output-dir", default="outputs", help="ranking.csv, audit.json, observations.json, sources.json")
    live.add_argument("--site-dir", help="also generate the static site here (data copied to <site>/data)")
    live.add_argument("--site-url", default="", help="public site URL used in social metadata")
    live.add_argument("--og-image", help="optional social preview image copied into the site")
    live.add_argument("--allow-partial", action="store_true", help="publish even if a source fails")
    live.add_argument("--timeout", type=float, default=30)
    return parser


def _print_table(ranking, limit: int) -> None:
    print(f"{'#':>3}  {'vendor':<22} {'score':>6} {'src':>4} {'weight':>7} {'stable range':>12}")
    print("-" * 60)
    for item in ranking.results[:limit]:
        print(f"{item.rank:>3}  {item.vendor:<22.22} {item.score:>6.1f} {item.source_count:>4} "
              f"{item.effective_weight:>7.2f} {item.rank_best:>5}-{item.rank_worst:<5}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    config, sources, directory = load_config(args.config, now.date())
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
    observations, domains = directory.resolve(raw)
    ranking = aggregate(observations, sources, config, min_sources=min_sources)
    for report in reports:
        print(f"{report.name}: {'ok' if report.ok else 'FAILED'} {report.valid_count}/{report.vendor_count} scored"
              + (f" ({report.error})" if report.error else ""))
    _print_table(ranking, args.top)
    names = [info.name for info in SOURCES]
    write_csv(output / "ranking.csv", ranking, names)
    write_audit(output / "audit.json", ranking, config, sources)
    write_observations(output / "observations.json", observations, ranking, domains)
    write_reports(output / "sources.json", reports)
    if args.site_dir:
        data_dir = Path(args.site_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("ranking.csv", "audit.json", "observations.json", "sources.json"):
            shutil.copyfile(output / name, data_dir / name)
        write_site(args.site_dir, ranking, reports, sources, config, generated_at=now.isoformat(),
                   top=args.top, min_sources=min_sources, site_url=args.site_url, og_image=args.og_image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
