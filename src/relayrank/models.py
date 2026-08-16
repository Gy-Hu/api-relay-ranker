from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Source:
    name: str
    published_at: date
    reliability: float = 1.0
    independence_group: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.reliability <= 1:
            raise ValueError(f"source {self.name}: reliability must be in [0, 1]")


@dataclass(frozen=True)
class Observation:
    source: str
    vendor: str
    rank: int | None = None
    total_vendors: int | None = None
    score: float | None = None
    uptime: float | None = None
    cache_rate: float | None = None
    price_value: float | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class Config:
    as_of: date
    half_life_days: float = 45.0
    prior_score: float = 50.0
    prior_strength: float = 0.8
    minimum_sources: int = 3
    variance_penalty: float = 0.25
    low_outlier_gap: float = 25.0
    three_source_bonus: float = 2.0
    four_source_bonus: float = 4.0
    metric_weights: dict[str, float] = field(
        default_factory=lambda: {
            "rank": 0.40,
            "score": 0.20,
            "uptime": 0.20,
            "cache_rate": 0.10,
            "price_value": 0.10,
        }
    )

    def __post_init__(self) -> None:
        if self.variance_penalty < 0:
            raise ValueError("variance_penalty must be non-negative")
        if self.low_outlier_gap < 0:
            raise ValueError("low_outlier_gap must be non-negative")
        if self.three_source_bonus < 0 or self.four_source_bonus < 0:
            raise ValueError("coverage bonuses must be non-negative")
        if self.four_source_bonus < self.three_source_bonus:
            raise ValueError("four_source_bonus must be at least three_source_bonus")


@dataclass(frozen=True)
class RankedVendor:
    rank: int
    vendor: str
    score: float
    confidence: float
    source_count: int
    effective_weight: float
    score_stddev: float
    disagreement_penalty: float
    rank_best: int
    rank_worst: int
    contributions: tuple[dict[str, object], ...] = ()
    website_url: str = ""
    raw_score_stddev: float = 0.0
    coverage_bonus: float = 0.0
    low_outlier_sources: tuple[str, ...] = ()
