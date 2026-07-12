from __future__ import annotations

from ..models import Observation
from .common import item_list


SOURCE = "apiranking"


def parse_apiranking(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    html = body.decode("utf-8", errors="replace")
    rankings = item_list(html)
    total = len(rankings)
    observations = [
        Observation(
            source=SOURCE,
            vendor=aliases.get(name.casefold(), name),
            rank=rank,
            total_vendors=total,
        )
        for rank, name in rankings
    ]
    if len(observations) < 10:
        raise ValueError(f"APIRanking parser returned only {len(observations)} vendors")
    return observations

