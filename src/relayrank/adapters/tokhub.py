from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from ..models import Observation, finite_number
from .common import safe_http_url

SOURCE = "tokhub"


def parse_tokhub(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    payload = json.loads(body)
    items = payload.get("items", [])
    if len(items) != payload.get("total"):
        raise ValueError("TokHub pagination incomplete")
    if len({c.get("id") for c in items}) != len(items):
        raise ValueError("TokHub duplicate channel IDs")
    grouped = defaultdict(list)
    for channel in items:
        provider = str(channel.get("provider") or "").strip()
        if not provider or not channel.get("id"):
            raise ValueError("TokHub missing channel identity")
        for key in ("score", "uptime24h", "successRate"):
            if channel.get(key) is not None:
                finite_number(channel[key], f"TokHub/{provider}/{key}")
        grouped[aliases.get(provider.casefold(), provider)].append(channel)
    observations = []
    for vendor, channels in grouped.items():
        issues = {"status_mapping_not_measured_rate"}
        for c in channels:
            if c.get("status") != "healthy":
                issues.add(f"channel_status: {c.get('model', 'unknown')} / {c.get('status', 'unknown')} / {c.get('errorType') or 'unspecified'}")
        stamps = []
        for c in channels:
            if c.get("lastProbeAt"):
                stamps.append(datetime.fromisoformat(c["lastProbeAt"].replace("Z", "+00:00")).date())
        url = next((safe_http_url(c.get("officialSiteUrl"), origin_only=True) for c in channels if safe_http_url(c.get("officialSiteUrl"))), None)
        observations.append(Observation(
            SOURCE, vendor, website_url=url, evidence_kind="status", state="reference",
            observed_at=min(stamps) if len(stamps) == len(channels) else None,
            date_basis="oldest_channel_probe", issues=tuple(sorted(issues)),
            raw_evidence={"channels": [{key: c.get(key) for key in (
                "id", "model", "endpoint", "status", "diagnosis", "score", "uptime24h",
                "successRate", "lastProbeAt", "l1Status", "l2Status", "l3Status", "errorType",
            )} for c in channels]},
        ))
    if len(observations) < 5:
        raise ValueError(f"TokHub parser returned only {len(observations)} providers")
    return observations
