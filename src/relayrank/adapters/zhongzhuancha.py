"""中转查: published current /100 on cards backed by real 72-hour model calls."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..models import Observation, finite_number
from .common import Document, next_flight, safe_http_url

SOURCE = "zhongzhuancha"
URL = "https://zhongzhuancha.com/transit"


def _catalog(html: str) -> dict:
    flight = next_flight(html)
    keys = list(re.finditer(r'"catalog"\s*:\s*', flight))
    if len(keys) != 1:
        raise ValueError("中转查: expected one measured catalog")
    catalog, _ = json.JSONDecoder().raw_decode(flight, keys[0].end())
    sites = catalog["sites"]
    if catalog.get("error") or not sites or len(sites) != catalog["total"]:
        raise ValueError("中转查: incomplete measured catalog")
    return catalog


def _model_samples(body: bytes, detail_url: str) -> list[dict]:
    root = Document(body.decode()).root
    if not any(n.tag == "link" and n.attrs.get("rel") == "canonical" and n.attrs.get("href") == detail_url
               for n in root.walk()):
        raise ValueError(f"中转查: unexpected detail page for {detail_url}")
    sections = [n.parent for n in root.walk() if n.tag == "h2" and n.text().strip() == "近 72 小时样本"]
    if len(sections) != 1:
        raise ValueError(f"中转查: missing 72-hour sample window on {detail_url}")
    table_rows = [n for n in sections[0].walk() if n.tag == "tr"]
    headers = [n.text().strip() for n in table_rows[0].walk() if n.tag == "th"] if table_rows else []
    if headers != ["模型", "协议", "通过 / 总数", "首字延迟", "最后检测"]:
        raise ValueError(f"中转查: unexpected model sample columns on {detail_url}")
    models = []
    for tr in table_rows[1:]:
        model, protocol, counts, latency, last = (n.text().strip() for n in tr.walk() if n.tag == "td")
        passed, total = (int(x) for x in counts.split("/"))
        if not model or not protocol or not 0 <= passed <= total:
            raise ValueError(f"中转查: invalid model sample on {detail_url}")
        models.append({"model": model, "protocol": protocol, "passed": passed, "total": total,
                       "first_token_latency": latency if latency != "未记录" else None,
                       "last_probe_local": last})
    if not models:
        raise ValueError(f"中转查: missing model sample rows on {detail_url}")
    return models


def collect(get) -> list[Observation]:
    html = get(SOURCE, URL).decode()
    catalog = _catalog(html)
    cards = {n.attrs["id"]: n for n in Document(html).root.walk() if n.tag == "article" and "id" in n.attrs}
    observations = []
    seen = set()
    for site in catalog["sites"]:
        slug = site["slug"]
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", slug) or slug in seen:
            raise ValueError(f"中转查: unsafe or duplicate site slug: {slug!r}")
        seen.add(slug)
        total = site["completionSamples"]
        if total is None or (type(total) is int and total == 0):
            continue  # A directory/profile entry is not a measurement, even if it has a base score.
        if type(total) is not int or total < 1:
            raise ValueError(f"中转查: invalid sample count for {slug}")
        card = cards.get("site-" + slug)
        scores = [] if card is None else [m for n in card.walk() if n.tag == "strong"
                                          if (m := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*/100\s*", n.text()))]
        if len(scores) != 1:
            raise ValueError(f"中转查: missing or ambiguous /100 card score for {slug}")
        score = float(scores[0][1])
        if abs(score - finite_number(site["score"], f"{slug}/score")) > 0.005001:
            raise ValueError(f"中转查: card/embedded score mismatch for {slug}")
        checked_at = datetime.fromisoformat(site["checkedAt"].replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            raise ValueError(f"中转查: missing probe timezone for {slug}")
        families = [{k: grp[k] for k in ("vendor", "passed", "total")} for grp in site["modelHealth"]]
        if any(type(v) is not int for g in families for v in (g["passed"], g["total"])) or any(
            not 0 <= g["passed"] <= g["total"] for g in families
        ) or sum(g["total"] for g in families) != total:
            raise ValueError(f"中转查: inconsistent family probe counts for {slug}")
        passed = sum(g["passed"] for g in families)
        # Profiles and the index can refresh independently. Keep the detail's own
        # per-model counts/times; do not replace the score's denominator or date.
        detail_url = f"{URL}/info/{slug}"
        models = _model_samples(get(f"{SOURCE}-{slug}", detail_url), detail_url)
        service_details = site.get("serviceDetails")
        channels = [d["value"] for d in service_details if d["label"] in ("本站监测分组", "监测分组", "本站监测范围")
                    ] if isinstance(service_details, list) else []
        observations.append(Observation(
            SOURCE, site["name"], domain=site["domain"], website_url=safe_http_url(site["url"], origin_only=True),
            score=score, sample_count=total, observed_at=checked_at.astimezone(timezone.utc).date(),
            source_url=f"{URL}#site-{slug}",
            issues=("source_zero_availability_requires_verification",) if passed == 0 else (),
            raw_evidence={"window_hours": 72, "last_probe_at": site["checkedAt"], "rank": site["rank"],
                          "ranked_sites": catalog["total"], "directory_sites": catalog["directoryTotal"],
                          "passed": passed, "total": total, "families": families, "monitor_channels": channels,
                          "detail_url": detail_url, "detail_model_samples": models},
        ))
    if not observations:
        raise ValueError("中转查: no measured sites")
    return observations
