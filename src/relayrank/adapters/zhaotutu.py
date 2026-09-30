from __future__ import annotations

import json
import re
from ..models import Observation
from .common import next_flight, safe_http_url

SOURCE = "zhaotutu"


def parse_zhaotutu(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    flight = next_flight(body.decode("utf-8", errors="strict"))
    decoder = json.JSONDecoder()
    candidates = []
    for match in re.finditer(r'"providers"\s*:\s*', flight):
        try:
            value, _ = decoder.raw_decode(flight, match.end())
            if isinstance(value, list) and all(isinstance(o, dict) for o in value):
                candidates.append(value)
        except ValueError:
            continue
    if len(candidates) != 1:
        raise ValueError("zhaotutu provider payload missing or ambiguous")
    providers = candidates[0]
    if len(providers) < 10 or len({o.get("id") for o in providers}) != len(providers):
        raise ValueError("zhaotutu incomplete or duplicate providers")
    rows = []
    for p in providers:
        name = str(p.get("name") or "").strip()
        status = p.get("status")
        score = p.get("overallScore")
        issues = ["metric_measurement_date_unknown", "source_composite_only"]
        state = "valid"
        if status == "defunct":
            state = "inactive"
            issues.append("source_reports_inactive")
        elif status not in {"active", "normal", "ascending", "degraded"}:
            state = "invalid"
            issues.append("unknown_provider_status")
        elif score is None:
            state = "missing"
            issues.append("missing_composite")
        if status == "degraded":
            issues.append("source_reports_degraded")
        annotation = p.get("annotation") if isinstance(p.get("annotation"), dict) else {}
        if annotation.get("type"):
            issues.append("source_annotation: " + str(annotation.get("reason") or annotation["type"]))
        # Do not average nested model and vendor summaries, or guess the meaning of zero.
        # Store every value, including zero, null, sample counts and timestamps for audit.
        raw = {key: p.get(key) for key in ("id", "nameCn", "status", "overallScore", "uptimeSummary", "models", "modelMonitorByVendor", "lastUpdated", "dataSources", "annotation")}
        rows.append(Observation(SOURCE, aliases.get(name.casefold(), name), score=score,
                                website_url=safe_http_url(p.get("url")), state=state,
                                issues=tuple(issues), raw_evidence=raw))
    return rows
