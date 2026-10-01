from __future__ import annotations

from datetime import datetime, timezone

from ..models import Observation, finite_number
from .common import flight_records, next_flight

SOURCE = "okkmax"
LIST_URL = "https://www.okkmax.com/list"
AVAILABILITY_URL = "https://www.okkmax.com/availability"
# Availability point status: 1 ok, 2 degraded, 0 down, -1 no data in the bucket.
NO_DATA = -1


def parse_okkmax(list_body: bytes, availability_body: bytes) -> list[Observation]:
    """Composite `score` from /list; measurement time from the /availability probe series.

    `/api/search` also has a `score`, but it is the mean channel detection score, not the composite.
    """
    rows = flight_records(next_flight(list_body.decode("utf-8", errors="strict")), "rows", "score")
    cards = flight_records(next_flight(availability_body.decode("utf-8", errors="strict")), "cards", "channels")
    if len(rows) < 10 or len({r.get("slug") for r in rows}) != len(rows):
        raise ValueError("OkkMax incomplete or duplicate station rows")
    probes = {}
    for card in cards:
        points = [p for channel in card.get("channels") or [] for p in channel.get("points") or []
                  if p.get("st") != NO_DATA and isinstance(p.get("t"), int)]
        probes[card.get("slug")] = {
            "latest_bucket": max((p["t"] for p in points), default=None),
            "avg_uptime": card.get("avgUptimeNum"), "avg_latency_ms": card.get("avgLatencyNum"),
            "channels": len(card.get("channels") or []),
        }
    observations = []
    for row in rows:
        name, host, slug = str(row.get("name") or "").strip(), str(row.get("host") or "").strip(), row.get("slug")
        if not name or not host or not slug:
            raise ValueError("OkkMax row without name, host or slug")
        score = row.get("score")
        if score is not None:
            finite_number(score, f"OkkMax/{name}/score")
        probe = probes.get(slug)
        stamp = probe["latest_bucket"] if probe else None
        issues = [] if score is not None else ["missing_composite"]
        if stamp is None:
            issues.append("no_availability_series")
        observations.append(Observation(
            SOURCE, name, domain=host, score=score, state="valid" if score is not None else "missing",
            observed_at=datetime.fromtimestamp(stamp, timezone.utc).date() if stamp else None,
            website_url=f"https://{host}/", source_url=f"https://www.okkmax.com/station/{slug}",
            issues=tuple(issues),
            raw_evidence={key: row.get(key) for key in ("slug", "uptime", "latency", "modelCount", "families", "recommend", "tags")}
            | {"availability_24h": probe},
        ))
    return observations
