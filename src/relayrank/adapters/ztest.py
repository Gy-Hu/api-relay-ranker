"""真测: the published mean for ranked model/group combinations, not the directory order."""
from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit

from ..models import Observation, finite_number
from .common import Document, Node, safe_http_url

SOURCE = "ztest"
ORIGIN = "https://ztest.ai"
URL = ORIGIN + "/sites"


@dataclass(frozen=True)
class _Site:
    slug: str
    name: str
    host: str

    @property
    def url(self) -> str:
        return f"{ORIGIN}/site/{self.slug}"


def _one(root: Node, *, tag: str | None = None, cls: str | None = None) -> Node:
    nodes = [n for n in root.walk() if (tag is None or n.tag == tag) and (cls is None or n.has_class(cls))]
    if len(nodes) != 1:
        raise ValueError(f"ztest: expected one {tag or ''}.{cls or ''}, found {len(nodes)}")
    return nodes[0]


def _directory(body: bytes) -> list[_Site]:
    root = Document(body.decode()).root
    total = re.fullmatch(r"共\s*(\d+)\s*个", _one(root, cls="tb-count").text().strip())
    sites = []
    for card in (n for n in root.walk() if n.has_class("sx-card")):
        title = _one(card, cls="sx-title")
        link = _one(title, tag="a", cls="sx-link")
        slug = re.fullmatch(r"/site/([a-z0-9][a-z0-9_-]{0,127})", link.attrs.get("href") or "")
        name = link.text().strip()
        host = _one(title, tag="small").text().partition("·")[0].strip().lower()
        if not slug or not name or not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z0-9-]+", host):
            raise ValueError(f"ztest: invalid directory identity for {name!r}")
        sites.append(_Site(slug[1], name, host))
    if not sites or total is None or int(total[1]) != len(sites) or len({s.slug for s in sites}) != len(sites):
        raise ValueError("ztest: incomplete or duplicate directory")
    return sites


def _availability(monitor: Node) -> dict[str, int | None]:
    """有效样本 are periodic availability checks, not grading reports or ranked combinations."""
    if any(n.tag == "strong" and n.text().strip() == "还没有监测数据" for n in monitor.walk()):
        return {"availability_checks_24h": None, "availability_checks_7d": None}
    nums = _one(monitor, cls="nums")
    checks = {}
    for period, label in (("24h", "近 24 小时可用率"), ("7d", "近 7 天可用率")):
        cells = [n for n in nums.walk() if n.tag == "span" and n.text().strip() in {label, label + "（合并）"}]
        if len(cells) != 1:
            raise ValueError(f"ztest: missing or ambiguous {period} availability")
        note = _one(cells[0].parent, tag="small").text().strip()
        count = re.fullmatch(r"有效样本 ([0-9,]+)(?:\s*·.*)?", note)
        if count is None and note != "暂无有效样本":
            raise ValueError(f"ztest: unknown {period} availability sample count {note!r}")
        checks[f"availability_checks_{period}"] = int(count[1].replace(",", "")) if count else 0
    return checks


def _detail(site: _Site, body: bytes) -> Observation:
    root = Document(body.decode()).root
    canonicals = [n.attrs.get("href") for n in root.walk() if n.tag == "link" and n.attrs.get("rel") == "canonical"]
    if canonicals != [site.url]:
        raise ValueError(f"ztest: wrong detail page for {site.slug}")
    info = _one(root, cls="site-info")
    official = [n for n in info.walk() if n.tag == "span" and n.text().strip() == "官网"]
    if len(official) != 1:
        raise ValueError(f"ztest/{site.slug}: missing or ambiguous official website")
    website = safe_http_url(_one(official[0].parent, tag="a").attrs.get("href"), origin_only=True)
    if not website or not (host := urlsplit(website).hostname):
        raise ValueError(f"ztest/{site.slug}: invalid official website")

    box = _one(root, cls="score-box")
    ring = _one(box, cls="big-ring")
    value = _one(ring, tag="b").text().strip()
    label = _one(ring, tag="small").text().strip()
    status = _one(box, cls="pill").text().strip()
    count = re.fullmatch(r"([1-9]\d*) 个组合进入排名", status)
    if label == "平均综合评分" and count and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        ranked = int(count[1])
        score = finite_number(float(value), f"ztest/{site.slug}/score")
    elif (value, label, status) == ("—", "暂无评分", "暂无进入排名的组合"):
        ranked, score = 0, None
    else:
        raise ValueError(f"ztest/{site.slug}: unrecognized site score {value!r} / {label!r} / {status!r}")

    timing = _one(box, tag="p").text()
    stamp = re.search(r"最近检查\s+(—|\d{4}-\d\d-\d\d \d\d:\d\d)\s*·", timing)
    if stamp is None or (score is not None and stamp[1] == "—"):
        raise ValueError(f"ztest/{site.slug}: missing latest check for score")
    checked = None if stamp[1] == "—" else datetime.fromisoformat(stamp[1])
    return Observation(
        SOURCE, site.name, domain=host, website_url=website, source_url=site.url,
        score=score, observed_at=checked.date() if checked else None,
        state="valid" if score is not None else "missing",
        issues=() if score is not None else ("no_ranked_combinations",),
        raw_evidence={"ranked_combinations": ranked, "latest_check": stamp[1] if checked else None,
                      **_availability(_one(root, cls="monitor"))},
    )


def collect(get: Callable[[str, str], bytes]) -> list[Observation]:
    sites = _directory(get("ztest-sites", URL))

    def visit(site: _Site) -> Observation:
        try:
            body = get(f"ztest-site-{site.slug}", site.url)
        except HTTPError as exc:
            if exc.code not in {404, 410}:
                raise
            # A listing may outlive its detail page. Preserve membership but not a vote.
            return Observation(SOURCE, site.name, domain=site.host, state="missing", source_url=URL,
                               issues=("missing_detail",), raw_evidence={"detail_url": site.url, "http_status": exc.code})
        return _detail(site, body)

    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(visit, sites))
