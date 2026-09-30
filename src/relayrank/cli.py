from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from .engine import aggregate
from .io import load_config, load_observations, write_csv, write_json, write_observations
from .live import LiveCollectionError, collect_live
from .site import write_site


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relayrank", description="Aggregate API relay rankings without letting one source dominate.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rank = subparsers.add_parser("rank", help="calculate a composite ranking")
    rank.add_argument("--config", required=True, help="TOML source and weighting configuration")
    rank.add_argument("--input", required=True, help="CSV observations")
    rank.add_argument("--output", help="write concise CSV output")
    rank.add_argument("--json", help="write detailed JSON audit trail")
    rank.add_argument("--limit", type=int, default=20)
    live = subparsers.add_parser("live", help="fetch all four live rankings and calculate Top N")
    live.add_argument("--config", required=True, help="TOML weighting and vendor alias configuration")
    live.add_argument("--top", type=int, default=10, help="number of ranked vendors to print")
    live.add_argument("--min-sources", type=int, default=None, help="minimum independent source coverage")
    live.add_argument("--snapshot-dir", default="snapshots", help="immutable raw response archive")
    live.add_argument("--output", default="outputs/live-ranking.csv")
    live.add_argument("--json", default="outputs/live-audit.json")
    live.add_argument("--source-report", default="outputs/live-sources.json")
    live.add_argument("--site-dir", help="generate a static ranking site in this directory")
    live.add_argument("--site-url", default="", help="public site URL used in social metadata")
    live.add_argument("--og-image", help="optional social preview image copied into the site")
    live.add_argument("--allow-partial", action="store_true", help="publish even if one of four sources fails")
    live.add_argument("--timeout", type=float, default=30)
    return parser


def _print_table(results: list, limit: int) -> None:
    print(f"{'#':>3}  {'vendor':<20} {'score':>7} {'evid':>6} {'src':>4} {'stable range':>12}")
    print("-" * 61)
    for item in results[:limit]:
        print(f"{item.rank:>3}  {item.vendor:<20.20} {item.score:>7.2f} {item.confidence:>6.2f} {item.source_count:>4} {item.rank_best:>4}-{item.rank_worst:<4}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config, sources, aliases = load_config(args.config)
    if args.command == "live":
        if args.min_sources is None:
            args.min_sources = config.minimum_sources
        if args.top < 1 or args.min_sources < 1 or args.timeout <= 0:
            raise ValueError("top, min-sources and timeout must be positive")
        config = replace(config, as_of=datetime.now(timezone.utc).date())
        collection_error = None
        try:
            observations, sources, reports = collect_live(
                aliases=aliases,
                snapshot_dir=args.snapshot_dir,
                source_config=sources,
                allow_partial=args.allow_partial,
                timeout=args.timeout,
                config=config,
            )
        except LiveCollectionError as error:
            reports = error.reports
            collection_error = error
        report_path = Path(args.source_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps([report.__dict__ for report in reports], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if collection_error is not None:
            raise collection_error
        all_results = aggregate(observations, sources, config, min_sources=args.min_sources)
        results = all_results[:args.top]
        write_observations(Path(args.json).with_name("observations.json"), observations, sources, config, args.min_sources)
        _print_table(results, args.top)
        write_csv(Path(args.output), results)
        write_json(Path(args.json), results)
        if args.site_dir:
            data_dir = Path(args.site_dir) / "data"
            write_csv(data_dir / "ranking.csv", results)
            write_json(data_dir / "audit.json", results)
            write_observations(data_dir / "observations.json", observations, sources, config, args.min_sources)
            (data_dir / "sources.json").write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
            generated_at = max((report.fetched_at for report in reports if report.fetched_at), default="")
            write_site(
                args.site_dir,
                results,
                reports,
                generated_at=generated_at,
                site_url=args.site_url,
                og_image=args.og_image,
                min_sources=args.min_sources,
                cohort_size=len(all_results),
                config=config,
            )
        return 0

    observations = load_observations(args.input, aliases)
    results = aggregate(observations, sources, config)
    _print_table(results, args.limit)
    if args.output:
        write_csv(Path(args.output), results)
    if args.json:
        write_json(Path(args.json), results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
