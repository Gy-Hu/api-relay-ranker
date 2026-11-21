from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .adapters import helpaio, okkmax, zhongzhuancha, ztest
from .fetch import FetchResult, fetch, save_snapshot
from .models import Observation, Source

Fetcher = Callable[..., FetchResult]


@dataclass(frozen=True)
class SourceInfo:
    name: str
    label: str
    homepage: str
    measures: str
    collect: Callable[[Callable[[str, str], bytes]], list[Observation]]


SOURCES = (
    SourceInfo("helpaio", "HelpAIO", helpaio.URL, "自费实测基础分 × 3 日可用率 × 降权系数", helpaio.collect),
    SourceInfo("okkmax", "OkkMax", okkmax.LIST_URL, "分组真实请求可用率、速度与 Claude 纯度", okkmax.collect),
    SourceInfo("ztest", "真测", ztest.URL, "进入排名的模型与分组综合分的站点均值", ztest.collect),
    SourceInfo("zhongzhuancha", "中转查", zhongzhuancha.URL, "近 72 小时有真实模型调用样本的站点当前分", zhongzhuancha.collect),
)


@dataclass
class SourceReport:
    name: str
    ok: bool
    vendor_count: int = 0
    valid_count: int = 0
    fetched_at: str | None = None
    error: str | None = None
    pages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class LiveCollectionError(RuntimeError):
    def __init__(self, message: str, reports: list[SourceReport]) -> None:
        super().__init__(message)
        self.reports = reports


def collect_live(sources: dict[str, Source], snapshot_dir: str | Path, *, allow_partial: bool = False, timeout: float = 30,
                 fetcher: Fetcher = fetch) -> tuple[list[Observation], list[SourceReport]]:
    missing = [info.name for info in SOURCES if info.name not in sources]
    if missing:
        raise ValueError(f"config lacks [[sources]] entries for: {', '.join(missing)}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")

    def collect(info: SourceInfo) -> tuple[list[Observation], SourceReport]:
        report = SourceReport(info.name, False)

        def get(label: str, url: str) -> bytes:
            result = fetcher(label, url, timeout)
            save_snapshot(result, snapshot_dir, run_id)
            report.pages.append({"url": result.url, "sha256": result.sha256, "fetched_at": result.fetched_at.isoformat()})
            return result.body

        try:
            rows = info.collect(get)
        except Exception as error:  # Keep the other sources' results for the audit artifact.
            report.error = str(error)
            return [], report
        report.vendor_count = len(rows)
        report.valid_count = sum(o.state == "valid" for o in rows)
        report.fetched_at = max(p["fetched_at"] for p in report.pages)
        report.warnings = sorted({issue.split(":")[0] for o in rows for issue in o.issues})
        report.ok = report.valid_count > 0
        if not report.ok:
            report.error = "no valid observations"
        return rows, report

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        results = list(executor.map(collect, SOURCES))
    ordered = [report for _, report in results]
    missing = [r for r in ordered if not r.ok]
    if missing and not allow_partial:
        reasons = "; ".join(f"{r.name}: {r.error}" for r in missing)
        raise LiveCollectionError(f"required sources missing valid observations: {reasons}", ordered)
    if len({sources[r.name].group for r in ordered if r.valid_count}) < 2:
        raise LiveCollectionError("need valid observations from at least two source groups", ordered)
    return [o for rows, _ in results for o in rows], ordered
