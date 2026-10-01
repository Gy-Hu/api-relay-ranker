"""Veridrop: community-triggered real-request detection reports (authenticity, protocol, billing).

The site's all-time median mixes months-old reports, so only valid reports inside a recent
window count. Community users choose which protocol to test, so a pooled median would follow
the submission mix (Packy: Claude 79, OpenAI 0, pooled 0). Instead each protocol gets its own
median, and protocols are averaged with weight n / (n + PROTOCOL_SAMPLE_PRIOR).
Paid "certified" placement only affects which rows are promoted; per-host report history is
the same for every domain, so only search and detail pages are read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from urllib.parse import quote

from ..models import Observation
from .common import Document, Node

SOURCE = "veridrop"
BASE = "https://veridrop.org"
INVALID_VERDICT = "检测无效"
PROTOCOL_SAMPLE_PRIOR = 3


def search_url(domain: str) -> str:
    return f"{BASE}/search?q={quote(domain, safe='')}"


def detail_url(host: str, page: int = 1) -> str:
    url = f"{BASE}/leaderboard/{quote(host, safe='')}"
    return url if page == 1 else f"{url}?page={page}"


@dataclass(frozen=True)
class Report:
    day: date
    protocol: str
    model: str
    score: int | None
    verdict: str
    report_url: str | None


@dataclass(frozen=True)
class DetailPage:
    page: int
    pages: int
    reports: tuple[Report, ...]


def parse_search(body: bytes) -> list[tuple[str, int]]:
    """Hosts returned for a domain query with their valid-report counts (sponsored rows excluded)."""
    root = Document(body.decode("utf-8", errors="strict")).root
    if not any(n.has_class("domain-search-panel") for n in root.walk()):
        raise ValueError("Veridrop search page structure changed")
    hits = []
    for node in root.walk():
        if node.tag != "article" or node.attrs.get("data-impression-surface") != "domain_search_result":
            continue
        host = str(node.attrs.get("data-impression-domain") or "").strip().lower()
        match = re.search(r"(\d+)\s*次有效检测", node.text())
        if not host or not match:
            raise ValueError("Veridrop search row without host or count")
        hits.append((host, int(match[1])))
    return hits


def _cells(row: Node) -> list[Node]:
    return [c for c in row.children if isinstance(c, Node) and c.tag == "td"]


def parse_detail(body: bytes) -> DetailPage | None:
    """Report history page; None when Veridrop serves its cold "数据正在整理" placeholder."""
    root = Document(body.decode("utf-8", errors="strict")).root
    section = next((n for n in root.walk() if n.tag == "section" and n.attrs.get("id") == "history"), None)
    if section is None:
        if "数据正在整理" in root.text():
            return None
        raise ValueError("Veridrop detail page has no report history section")
    pager = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*页", section.text())
    table = next((n for n in section.walk() if n.tag == "table"), None)
    if table is None:
        return DetailPage(1, 1, ())
    header = [re.sub(r"\s+", "", n.text()) for n in table.walk() if n.tag == "th"]
    if header[:5] != ["日期", "协议", "测试模型", "分数", "判定"]:
        raise ValueError(f"Veridrop history columns changed: {header}")
    reports = []
    for row in (n for n in table.walk() if n.tag == "tr"):
        cells = _cells(row)
        if not cells:
            continue
        text = [re.sub(r"\s+", " ", c.text()).strip() for c in cells]
        score = int(text[3]) if text[3].isdigit() else None
        if score is not None and not 0 <= score <= 100:
            raise ValueError(f"Veridrop score out of range: {score}")
        link = next((n.attrs.get("href") for n in row.walk() if n.tag == "a" and str(n.attrs.get("href", "")).startswith("/r/")), None)
        reports.append(Report(date.fromisoformat(text[0]), text[1], text[2], score, text[4],
                              BASE + link if link else None))
    page, pages = (int(pager[1]), int(pager[2])) if pager else (1, 1)
    return DetailPage(page, pages, tuple(reports))


def build_observation(domain: str, reports: dict[str, list[Report]], unavailable: list[str],
                      as_of: date, window_days: int) -> Observation:
    start = as_of - timedelta(days=window_days)
    recent = [(host, r) for host, rows in reports.items() for r in rows if start <= r.day <= as_of]
    valid = [r for _, r in recent if r.score is not None and r.verdict != INVALID_VERDICT]
    issues = [f"veridrop_detail_unavailable: {host}" for host in unavailable]
    by_protocol: dict[str, list[int]] = {}
    for r in valid:
        by_protocol.setdefault(r.protocol, []).append(r.score)
    medians = {p: float(median(v)) for p, v in sorted(by_protocol.items())}
    weights = {p: len(v) / (len(v) + PROTOCOL_SAMPLE_PRIOR) for p, v in by_protocol.items()}
    score = sum(medians[p] * weights[p] for p in medians) / sum(weights.values()) if valid else None
    if medians and max(medians.values()) - min(medians.values()) >= 50:
        issues.append("veridrop_protocols_disagree")
    verdicts: dict[str, int] = {}
    for _, r in recent:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
    hosts = sorted(reports)
    if not valid:
        issues.append("no_valid_reports_in_window")
    return Observation(
        SOURCE, domain, domain=domain, score=score,
        state="valid" if valid else "missing",
        observed_at=max(r.day for r in valid) if valid else None,
        sample_count=len(valid),
        source_url=detail_url(max(hosts, key=lambda h: len(reports[h]))) if hosts else None,
        issues=tuple(issues),
        raw_evidence={
            "window": {"from": start.isoformat(), "to": as_of.isoformat()},
            "hosts": hosts, "verdict_counts": verdicts,
            "protocol_medians": medians,
            "protocol_counts": {p: len(v) for p, v in sorted(by_protocol.items())},
            "reports": [{"host": host, "date": r.day.isoformat(), "protocol": r.protocol, "model": r.model,
                         "score": r.score, "verdict": r.verdict, "report_url": r.report_url}
                        for host, r in sorted(recent, key=lambda x: x[1].day, reverse=True)],
        },
    )
