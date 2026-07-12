from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean, median

from ..models import Observation


SOURCE = "tokhub"


def parse_tokhub(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    payload = json.loads(body)
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for channel in payload.get("items", []):
        provider = str(channel.get("provider") or "").strip()
        if provider and channel.get("score") is not None:
            canonical = aliases.get(provider.casefold(), provider)
            grouped[canonical].append(channel)

    providers: list[tuple[str, float, float | None]] = []
    for vendor, channels in grouped.items():
        score = float(median(float(channel["score"]) for channel in channels))
        uptimes = [float(channel["uptime24h"]) for channel in channels if channel.get("uptime24h") is not None]
        providers.append((vendor, score, mean(uptimes) if uptimes else None))

    providers.sort(key=lambda item: (-item[1], item[0].casefold()))
    total = len(providers)
    observations = [
        Observation(
            source=SOURCE,
            vendor=vendor,
            rank=1 + sum(1 for _, other_score, _ in providers if other_score > score),
            total_vendors=total,
            score=score,
            uptime=uptime,
        )
        for vendor, score, uptime in providers
    ]
    if len(observations) < 5:
        raise ValueError(f"TokHub parser returned only {len(observations)} providers")
    return observations
