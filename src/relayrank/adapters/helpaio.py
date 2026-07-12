from __future__ import annotations

import re

from ..models import Observation
from .common import escaped_number, item_list


SOURCE = "helpaio"


def _scores(html: str) -> dict[str, float]:
    starts = list(re.finditer(r'\\"siteName\\":\\"([^"\\]+)\\"', html))
    scores: dict[str, float] = {}
    for index, match in enumerate(starts):
        name = match.group(1).strip()
        end = starts[index + 1].start() if index + 1 < len(starts) else min(len(html), match.start() + 300_000)
        block = html[match.start():end]
        score = escaped_number(block, "siteScore")
        if score is not None and name not in scores:
            scores[name] = score
    return scores


def parse_helpaio(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    html = body.decode("utf-8", errors="replace")
    rankings = item_list(html, "排行榜")
    scores = _scores(html)
    total = len(rankings)
    observations = [
        Observation(
            source=SOURCE,
            vendor=aliases.get(name.casefold(), name),
            rank=rank,
            total_vendors=total,
            score=scores.get(name),
        )
        for rank, name in rankings
    ]
    if len(observations) < 10:
        raise ValueError(f"HelpAIO parser returned only {len(observations)} vendors")
    return observations

