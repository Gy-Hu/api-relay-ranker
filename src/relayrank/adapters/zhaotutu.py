from __future__ import annotations

import re
from statistics import mean

from ..models import Observation
from .common import escaped_number


SOURCE = "zhaotutu"
START = re.compile(r'\\"id\\":\\"([^"\\]+)\\",\\"name\\":\\"([^"\\]+)\\",\\"nameCn\\":')


def parse_zhaotutu(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    html = body.decode("utf-8", errors="replace")
    starts = list(START.finditer(html))
    providers: dict[str, tuple[str, float, float | None, float | None]] = {}
    for index, match in enumerate(starts):
        provider_id, name = match.group(1), match.group(2).strip()
        if provider_id in providers:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else min(len(html), match.start() + 200_000)
        block = html[match.start():end]
        score = escaped_number(block, "overallScore")
        status_match = re.search(r'\\"status\\":\\"([^"\\]+)\\"', block)
        status = status_match.group(1) if status_match else None
        if score is None or status not in {"active", "normal", "ascending"}:
            continue
        uptime_match = re.search(
            r'\\"uptimeSummary\\":\{[^}]*\\"uptime3d\\":([0-9]+(?:\.[0-9]+)?)', block
        )
        uptime = float(uptime_match.group(1)) if uptime_match else None
        cache_values = [
            float(value)
            for value in re.findall(r'\\"cacheHitRate\\":([0-9]+(?:\.[0-9]+)?)', block)
            if float(value) > 0
        ]
        cache_rate = mean(cache_values) if cache_values else None
        providers[provider_id] = (name, score, uptime, cache_rate)

    ordered = sorted(providers.values(), key=lambda item: (-item[1], item[0].casefold()))
    total = len(ordered)
    observations = [
        Observation(
            source=SOURCE,
            vendor=aliases.get(name.casefold(), name),
            rank=1 + sum(1 for _, other_score, _, _ in ordered if other_score > score),
            total_vendors=total,
            score=score,
            uptime=uptime,
            cache_rate=cache_rate,
        )
        for name, score, uptime, cache_rate in ordered
    ]
    if len(observations) < 10:
        raise ValueError(f"zhaotutu parser returned only {len(observations)} vendors")
    return observations
