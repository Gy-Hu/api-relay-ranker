from __future__ import annotations

import re
from ..identity import host_of
from ..models import Observation, finite_number
from .common import Document, item_list, safe_http_url, source_date

SOURCE = "helpaio"
URL = "https://www.helpaio.com/transit"


def parse_helpaio(body: bytes) -> list[Observation]:
    html = body.decode("utf-8", errors="strict")
    rankings = item_list(html, "排行榜")
    doc = Document(html).root
    stamp = source_date(doc.text())
    cards = [n for n in doc.walk() if n.tag == "article" and "data-station-index" in n.attrs]
    if len(cards) != len(rankings):
        raise ValueError(f"HelpAIO card/list mismatch: {len(cards)}/{len(rankings)}")
    by_index = {int(n.attrs["data-station-index"]): n for n in cards}
    if len(by_index) != len(cards):
        raise ValueError("duplicate HelpAIO station index")
    observations = []
    for rank, name in rankings:
        card = by_index.get(rank - 1)
        if card is None:
            raise ValueError(f"missing HelpAIO card for {name}")
        nodes = list(card.walk())
        heading = next((n.text().strip() for n in nodes if n.tag == "h2"), "")
        if heading != name:
            raise ValueError(f"HelpAIO card identity mismatch: {name}/{heading}")
        # Only the displayed ranking formula qualifies, never a standalone base score.
        formulas = [n for n in nodes if n.tag == "div" and n.has_class("items-baseline")]
        parsed = None
        for node in formulas:
            match = re.fullmatch(r'\s*([\d.]+)\s*分\s*=\s*([\d.]+)\s*×\s*([\d.]+)%\s*(?:×\s*([\d.]+))?\s*', node.text())
            if match:
                parsed = tuple(float(v) if v is not None else 1.0 for v in match.groups())
                break
        link = next((safe_http_url(n.attrs.get("href")) for n in nodes if n.tag == "a" and n.has_class("transit-station-link")), None)
        detail = next((n.attrs.get("href") for n in nodes if n.tag == "a" and str(n.attrs.get("href", "")).startswith("/transit/info/")), None)
        issues = []
        score = None
        raw = {"rank": rank, "total": len(rankings)}
        if parsed:
            score, base, uptime, penalty = parsed
            finite_number(base, f"HelpAIO/{name}/base_score")
            finite_number(uptime, f"HelpAIO/{name}/uptime3d")
            finite_number(penalty, f"HelpAIO/{name}/penalty", 0, 1)
            if abs(score - base * uptime / 100 * penalty) > 0.15:
                raise ValueError(f"HelpAIO formula mismatch for {name}")
            raw.update(base_score=base, uptime3d=uptime, penalty_factor=penalty)
            if uptime == 0:
                issues.append("source_zero_availability_requires_verification")
        else:
            issues.append("missing_composite")
        observations.append(Observation(
            SOURCE, name, domain=host_of(link), score=score, observed_at=stamp,
            state="valid" if parsed else "missing",
            website_url=safe_http_url(link, origin_only=True),
            source_url=f"https://www.helpaio.com{detail}" if detail else URL,
            issues=tuple(issues), raw_evidence=raw,
        ))
    if len(observations) < 10 or not any(o.score is not None for o in observations):
        raise ValueError("HelpAIO ranking formulas unavailable; refusing rank-only fallback")
    return observations
