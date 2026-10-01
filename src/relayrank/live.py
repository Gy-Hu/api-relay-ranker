from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .adapters import parse_helpaio, parse_okkmax, parse_relaypick
from .adapters import helpaio, okkmax, relaypick, veridrop
from .fetch import FetchResult, fetch, save_snapshot
from .identity import Directory, host_of, registrable_domain
from .models import Observation, Source

Fetcher = Callable[..., FetchResult]


@dataclass(frozen=True)
class SourceInfo:
    name: str
    label: str
    homepage: str
    measures: str


SOURCES = (
    SourceInfo("helpaio", "HelpAIO", helpaio.URL, "自费实测基础分 × 3 日可用率 × 降权系数"),
    SourceInfo("relaypick", "RelayPick", relaypick.URL, "价格、10 分钟级可达探测、透明度（真伪多数未抽样）"),
    SourceInfo("okkmax", "OkkMax", okkmax.LIST_URL, "分组真实请求可用率、速度与 Claude 纯度"),
    SourceInfo("veridrop", "Veridrop", veridrop.BASE + "/leaderboard", "社区触发的真伪、协议与计费检测报告"),
)
REQUIRED = ("helpaio", "relaypick", "okkmax")
VERIDROP_WORKERS = 4
VERIDROP_MAX_PAGES = 10


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


class _Session:
    """Fetches, archives every raw response, and records its hash for the source report."""

    def __init__(self, fetcher: Fetcher, snapshot_dir: str | Path, run_id: str, timeout: float) -> None:
        self.fetcher, self.snapshot_dir, self.run_id, self.timeout = fetcher, snapshot_dir, run_id, timeout

    def get(self, label: str, url: str, pages: list[dict]) -> FetchResult:
        result = self.fetcher(label, url, self.timeout)
        save_snapshot(result, self.snapshot_dir, self.run_id)
        pages.append({"url": result.url, "sha256": result.sha256, "fetched_at": result.fetched_at.isoformat()})
        return result


def _primary(session: _Session, name: str) -> tuple[list[Observation], SourceReport]:
    report = SourceReport(name, False)
    try:
        if name == "helpaio":
            result = session.get(name, helpaio.URL, report.pages)
            rows = parse_helpaio(result.body)
        elif name == "relaypick":
            result = session.get(name, relaypick.URL, report.pages)
            rows = parse_relaypick(result.body)
        else:
            listing = session.get("okkmax-list", okkmax.LIST_URL, report.pages)
            result = session.get("okkmax-availability", okkmax.AVAILABILITY_URL, report.pages)
            rows = parse_okkmax(listing.body, result.body)
    except Exception as error:  # noqa: BLE001 - recorded in the published source report
        report.error = str(error)
        return [], report
    _finish(report, rows, result.fetched_at)
    return rows, report


def _finish(report: SourceReport, rows: list[Observation], fetched_at: datetime) -> None:
    report.ok = True
    report.vendor_count = len(rows)
    report.valid_count = sum(o.state == "valid" for o in rows)
    report.fetched_at = fetched_at.isoformat()
    report.warnings = sorted({issue.split(":")[0] for o in rows for issue in o.issues})


def _veridrop_domain(session: _Session, domain: str, as_of: date, window_days: int, pages: list[dict]):
    """All endpoint hosts of one registrable domain; pages until reports leave the window."""
    hits = veridrop.parse_search(session.get(f"veridrop-search-{domain}", veridrop.search_url(domain), pages).body)
    hosts = [host for host, valid in hits if valid > 0 and (host == domain or host.endswith("." + domain))]
    start = date.fromordinal(as_of.toordinal() - window_days)
    reports, unavailable = {}, []
    for host in hosts:
        collected, page = [], 1
        while True:
            body = session.get(f"veridrop-{host}-p{page}", veridrop.detail_url(host, page), pages).body
            parsed = veridrop.parse_detail(body)
            if parsed is None:
                unavailable.append(host)
                break
            collected.extend(parsed.reports)
            if (page >= parsed.pages or page >= VERIDROP_MAX_PAGES or not parsed.reports
                    or min(r.day for r in parsed.reports) < start):
                break
            page += 1
        if collected:
            reports[host] = collected
    if not reports and not unavailable:
        return None
    return veridrop.build_observation(domain, reports, unavailable, as_of, window_days)


def _veridrop(session: _Session, domains: list[str], as_of: date, window_days: int):
    report = SourceReport("veridrop", False)
    rows, failures = [], []

    def lookup(domain: str):
        pages: list[dict] = []
        try:
            return domain, _veridrop_domain(session, domain, as_of, window_days, pages), pages, None
        except Exception as error:  # noqa: BLE001 - per-domain failure is reported, not fatal
            return domain, None, pages, str(error)

    with ThreadPoolExecutor(max_workers=VERIDROP_WORKERS) as executor:
        for domain, observation, pages, error in executor.map(lookup, domains):
            report.pages.extend(pages)
            if error:
                failures.append(f"{domain}: {error}")
            elif observation is not None:
                rows.append(observation)
    if failures and len(failures) == len(domains):
        report.error = "every Veridrop lookup failed: " + "; ".join(failures[:3])
        return [], report
    _finish(report, rows, datetime.now(timezone.utc))
    report.warnings += [f"lookup_failed: {f}" for f in failures]
    return rows, report


def collect_live(sources: dict[str, Source], directory: Directory, as_of: date, snapshot_dir: str | Path,
                 allow_partial: bool = False, timeout: float = 30,
                 fetcher: Fetcher = fetch) -> tuple[list[Observation], list[SourceReport]]:
    missing = [info.name for info in SOURCES if info.name not in sources]
    if missing:
        raise ValueError(f"config lacks [[sources]] entries for: {', '.join(missing)}")
    session = _Session(fetcher, snapshot_dir, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"), timeout)
    with ThreadPoolExecutor(max_workers=len(REQUIRED)) as executor:
        primary = dict(zip(REQUIRED, executor.map(lambda name: _primary(session, name), REQUIRED)))
    observations = [o for name in REQUIRED for o in primary[name][0]]
    reports = {name: primary[name][1] for name in REQUIRED}

    domains = {registrable_domain(host_of(o.domain)) for o in observations if host_of(o.domain)}
    domains |= {registrable_domain(host_of(d)) for spec in directory.specs for d in spec.domains}
    window = sources["veridrop"].window_days or 60
    rows, reports["veridrop"] = _veridrop(session, sorted(domains), as_of, window)
    observations += rows

    ordered = [reports[info.name] for info in SOURCES]
    failed = [r for r in ordered if not r.ok]
    if failed and not allow_partial:
        raise LiveCollectionError("aborted, every source is required: " + "; ".join(f"{r.name}: {r.error}" for r in failed), ordered)
    if len({sources[r.name].group for r in ordered if r.ok}) < 2:
        raise LiveCollectionError("need at least two successfully parsed source groups", ordered)
    return observations, ordered
