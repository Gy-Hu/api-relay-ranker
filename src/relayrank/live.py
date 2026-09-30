from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from .adapters import parse_apiranking, parse_helpaio, parse_tokhub, parse_zhaotutu
from .engine import observation_weight, _observation_score
from .fetch import fetch, save_snapshot
from .models import Config, Observation, Source

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
    scoring_count: int = 0
    reference_count: int = 0
    excluded_count: int = 0
    unknown_date_count: int = 0
    quality: str = "unavailable"
    warnings: tuple[str, ...] = ()
    pages: tuple[dict, ...] = ()


class LiveCollectionError(RuntimeError):
    def __init__(self, message: str, reports: list[SourceReport]) -> None:
        super().__init__(message)
        self.reports = reports


LIVE_SOURCES = (
    LiveSource("helpaio", "https://www.helpaio.com/transit", 0.90, parse_helpaio),
    LiveSource("zhaotutu", "https://api.zhaotutu.ai/", 0.85, parse_zhaotutu),
    LiveSource("apiranking", "https://apiranking.com/", 0.70, parse_apiranking),
    LiveSource("tokhub", "https://www.tokhub.me/api/public/channels", 0.90, parse_tokhub),
)


def _fetch_source(spec: LiveSource, timeout: float, snapshot_dir, run_id):
    result = fetch(spec.name, spec.url, timeout)
    save_snapshot(result, snapshot_dir, run_id)
    pages = [{"url": result.url, "sha256": result.sha256, "fetched_at": result.fetched_at.isoformat()}]
    if spec.name != "tokhub":
        return result, tuple(pages)
    payload = json.loads(result.body)
    items = payload.get("items", [])
    total = payload.get("total")
    page_size = payload.get("pageSize")
    if type(total) is not int or total < 0 or type(page_size) is not int or page_size < 1 or payload.get("page") != 1:
        raise ValueError("TokHub invalid pagination metadata")
    if total > 10000:
        raise ValueError("TokHub pagination exceeds safety bound")
    ids = {c.get("id") for c in items}
    if None in ids or len(ids) != len(items):
        raise ValueError("TokHub duplicate or missing channel IDs")
    page = 1
    while len(items) < total:
        page += 1
        extra = fetch(f"tokhub-page-{page}", spec.url + "?" + urlencode({"page": page, "pageSize": page_size}), timeout)
        save_snapshot(extra, snapshot_dir, run_id)
        pages.append({"url": extra.url, "sha256": extra.sha256, "fetched_at": extra.fetched_at.isoformat()})
        data = json.loads(extra.body)
        batch = data.get("items", [])
        batch_ids = {c.get("id") for c in batch}
        if (data.get("total") != total or data.get("page") != page or data.get("pageSize") != page_size
                or not batch or None in batch_ids or len(batch_ids) != len(batch) or ids & batch_ids):
            raise ValueError("TokHub pagination changed, repeated or incomplete; retry next run")
        ids.update(batch_ids)
        items.extend(batch)
    if len(items) != total:
        raise ValueError("TokHub pagination count mismatch")
    merged = json.dumps({**payload, "items": items}, ensure_ascii=False, allow_nan=False).encode()
    # sha256 describes the exact assembled parser input; each original page hash is retained.
    assembled = replace(result, source="tokhub-assembled", body=merged, sha256=hashlib.sha256(merged).hexdigest())
    save_snapshot(assembled, snapshot_dir, run_id)
    return assembled, tuple(pages)


def collect_live(aliases: dict[str, str], snapshot_dir: str | Path,
                 source_config: dict[str, Source] | None = None, allow_partial: bool = False,
                 timeout: float = 30, config: Config | None = None):
    now = datetime.now(timezone.utc)
    config = config or Config(now.date())
    run_id = now.strftime("%Y%m%dT%H%M%S.%fZ")
    fetched, reports = {}, {}
    with ThreadPoolExecutor(max_workers=len(LIVE_SOURCES)) as executor:
        futures = {executor.submit(_fetch_source, spec, timeout, snapshot_dir, run_id): spec for spec in LIVE_SOURCES}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                fetched[spec.name] = future.result()
            except Exception as error:
                reports[spec.name] = SourceReport(spec.name, False, error=str(error))
    observations, sources = [], {}
    for spec in LIVE_SOURCES:
        if spec.name not in fetched:
            continue
        result, pages = fetched[spec.name]
        try:
            parsed = spec.parser(result.body, aliases)
            if not parsed or len({o.vendor for o in parsed}) != len(parsed):
                raise ValueError("empty source or duplicate canonical vendors")
            if any(o.source != spec.name for o in parsed):
                raise ValueError("parser returned wrong source")
            configured = (source_config or {}).get(spec.name)
            # Fetch time is not evidence time. Adapters must provide source observation dates.
            source = Source(spec.name, None, configured.reliability if configured else spec.reliability,
                            configured.independence_group if configured else spec.name)
            qualities = [observation_weight(o, source, config) for o in parsed]
            for i, (o, (w, _)) in enumerate(zip(parsed, qualities)):
                if w > 0:
                    try:
                        _observation_score(o, config)
                    except ValueError:
                        qualities[i] = (0.0, "missing_metrics")
            scored = sum(w > 0 for w, _ in qualities)
            refs = sum(o.state == "reference" for o in parsed)
            unknown = sum(q == "unknown_date" for _, q in qualities)
            warnings = tuple(sorted({issue for o in parsed for issue in o.issues} | {q for _, q in qualities if q in {"stale", "future_date", "unknown_date"}}))
            quality = "reference_only" if not scored and refs else ("limited" if unknown or scored + refs < len(parsed) else "usable")
            reports[spec.name] = SourceReport(spec.name, True, len(parsed), result.sha256,
                result.fetched_at.isoformat(), scoring_count=scored, reference_count=refs,
                excluded_count=len(parsed)-scored-refs, unknown_date_count=unknown,
                quality=quality, warnings=warnings, pages=pages)
            observations.extend(parsed)
            sources[spec.name] = source
        except Exception as error:
            reports[spec.name] = SourceReport(spec.name, False, sha256=result.sha256,
                fetched_at=result.fetched_at.isoformat(), error=f"parse error: {error}", pages=pages)
    ordered = [reports[s.name] for s in LIVE_SOURCES]
    failures = [r for r in ordered if not r.ok]
    if failures and not allow_partial:
        raise LiveCollectionError("live ranking aborted because all four sources are required: " + "; ".join(f"{r.name}: {r.error}" for r in failures), ordered)
    if len({s.independence_group or s.name for s in sources.values()}) < 2:
        raise LiveCollectionError("live ranking needs at least two successfully parsed independent sources", ordered)
    return observations, sources, ordered
