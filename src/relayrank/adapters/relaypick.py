from __future__ import annotations

import re
from datetime import datetime

from ..models import Observation, finite_number
from .common import Document, flight_records, next_flight

SOURCE = "relaypick"
URL = "https://relaypick.com/ranking"


def _meta(flight: str, key: str) -> str | None:
    match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', flight)
    return match[1] if match else None


def parse_relaypick(body: bytes) -> list[Observation]:
    """Read the eligible ranking (30-day observation period passed) from the SSR flight data.

    `/api/export/ranking.csv` carries the same rows but sits under a robots-disallowed path.
    """
    html = body.decode("utf-8", errors="strict")
    flight = next_flight(html)
    rows = flight_records(flight, "rows", "final_score")
    # The server-rendered table is what visitors see; refuse to publish if it disagrees.
    table_rows = sum(1 for n in Document(html).root.walk() if n.tag == "tr") - 1
    if table_rows != len(rows):
        raise ValueError(f"RelayPick table/data mismatch: {table_rows}/{len(rows)}")
    if len(rows) < 10 or len({r.get("slug") for r in rows}) != len(rows):
        raise ValueError("RelayPick incomplete or duplicate ranking rows")
    window = {"from": _meta(flight, "windowFrom"), "to": _meta(flight, "windowTo")}
    rule = _meta(flight, "ruleVersion")
    observations = []
    for row in rows:
        name, domain, slug = str(row.get("name") or "").strip(), str(row.get("domain") or "").strip(), row.get("slug")
        if not name or not domain or not slug:
            raise ValueError("RelayPick row without name, domain or slug")
        raw_score = row.get("final_score")
        score = finite_number(float(raw_score), f"RelayPick/{name}/final_score") if raw_score is not None else None
        computed = row.get("computed_at")
        issues = []
        state = "valid" if score is not None else "missing"
        if row.get("status") != "listed":
            state = "invalid"
            issues.append(f"source_status: {row.get('status')}")
        if row.get("verdict") == "unsampled":
            # 25% of RelayPick's formula is authenticity; unsampled stations get 0 there.
            issues.append("authenticity_unsampled")
        observations.append(Observation(
            SOURCE, name, domain=domain, score=score, state=state,
            observed_at=datetime.fromisoformat(computed.replace("Z", "+00:00")).date() if computed else None,
            website_url=f"https://{domain}/", source_url=f"https://relaypick.com/station/{slug}",
            issues=tuple(issues),
            raw_evidence={key: row.get(key) for key in (
                "rank", "slug", "status", "verdict", "supply_type", "uptime", "p50_ms", "computed_at", "online_since")}
            | {"rule_version": rule, "window": window},
        ))
    return observations
