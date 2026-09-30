from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .adapters import parse_apiranking, parse_helpaio, parse_tokhub, parse_zhaotutu
from .fetch import fetch, save_snapshot
from .models import Observation, Source


Parser = Callable[[bytes, dict[str, str]], list[Observation]]


@dataclass(frozen=True)
class LiveSource:
    name: str
    url: str
    reliability: float
    parser: Parser


@dataclass(frozen=True)
class SourceReport:
    name: str
    ok: bool
    vendor_count: int = 0
    sha256: str = ""
    fetched_at: str = ""
    error: str = ""


class LiveCollectionError(RuntimeError):
    """Collection failed, with source diagnostics available for archiving."""

    def __init__(self, message: str, reports: list[SourceReport]) -> None:
        super().__init__(message)
        self.reports = reports


LIVE_SOURCES = (
    LiveSource("helpaio", "https://www.helpaio.com/transit", 0.90, parse_helpaio),
    LiveSource("zhaotutu", "https://api.zhaotutu.ai/", 0.85, parse_zhaotutu),
    LiveSource("apiranking", "https://apiranking.com/", 0.70, parse_apiranking),
    LiveSource("tokhub", "https://www.tokhub.me/api/public/channels", 0.90, parse_tokhub),
)


def collect_live(
    aliases: dict[str, str],
    snapshot_dir: str | Path,
    source_config: dict[str, Source] | None = None,
    allow_partial: bool = False,
    timeout: float = 30,
) -> tuple[list[Observation], dict[str, Source], list[SourceReport]]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fetched = {}
    reports: dict[str, SourceReport] = {}
    with ThreadPoolExecutor(max_workers=len(LIVE_SOURCES)) as executor:
        futures = {executor.submit(fetch, source.name, source.url, timeout): source for source in LIVE_SOURCES}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
                save_snapshot(result, snapshot_dir, run_id)
                fetched[source.name] = (source, result)
            except Exception as error:
                reports[source.name] = SourceReport(source.name, False, error=str(error))

    observations: list[Observation] = []
    sources: dict[str, Source] = {}
    for spec in LIVE_SOURCES:
        if spec.name not in fetched:
            continue
        _, result = fetched[spec.name]
        try:
            parsed = spec.parser(result.body, aliases)
            observations.extend(parsed)
            configured = (source_config or {}).get(spec.name)
            sources[spec.name] = Source(
                name=spec.name,
                published_at=result.fetched_at.date(),
                reliability=configured.reliability if configured else spec.reliability,
                independence_group=configured.independence_group if configured else spec.name,
            )
            reports[spec.name] = SourceReport(
                name=spec.name,
                ok=True,
                vendor_count=len(parsed),
                sha256=result.sha256,
                fetched_at=result.fetched_at.isoformat(),
            )
        except Exception as error:
            reports[spec.name] = SourceReport(spec.name, False, error=f"parse error: {error}")

    ordered_reports = [reports[source.name] for source in LIVE_SOURCES]
    failures = [report for report in ordered_reports if not report.ok]
    if failures and not allow_partial:
        summary = "; ".join(f"{report.name}: {report.error}" for report in failures)
        raise LiveCollectionError(f"live ranking aborted because all four sources are required: {summary}", ordered_reports)
    if len(sources) < 2:
        raise LiveCollectionError("live ranking needs at least two successfully parsed independent sources", ordered_reports)
    return observations, sources, ordered_reports
