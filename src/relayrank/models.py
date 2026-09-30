from __future__ import annotations

import math
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


def finite_number(value: object, label: str, lower: float = 0, upper: float = 100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")
    if not lower <= value <= upper:
        raise ValueError(f"{label}: must be in [{lower}, {upper}]")
    return float(value)


@dataclass(frozen=True)
class Source:
    name: str
    published_at: date | None
    reliability: float = 1.0
    independence_group: str = ""

    def __post_init__(self) -> None:
        finite_number(self.reliability, f"source {self.name}: reliability", 0, 1)


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
    # A source composite is used once; its ingredients and ordering are audit only.
    evidence_kind: str = "composite"
    state: str = "valid"
    observed_at: date | None = None
    date_basis: str = "unknown"
    issues: tuple[str, ...] = ()
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        json.dumps(self.raw_evidence, allow_nan=False)
        if not self.source.strip() or not self.vendor.strip():
            raise ValueError("source and vendor must not be empty")
        for key in ("score", "uptime", "cache_rate", "price_value"):
            value = getattr(self, key)
            if value is not None:
                finite_number(value, f"{self.source}/{self.vendor}/{key}")
        if (self.rank is None) != (self.total_vendors is None):
            raise ValueError("rank and total_vendors must be provided together")
        if self.rank is not None:
            if type(self.rank) is not int or type(self.total_vendors) is not int or not 1 <= self.rank <= self.total_vendors:
                raise ValueError("rank must be an integer in [1, total_vendors]")
        if self.state not in {"valid", "missing", "reference", "inactive", "invalid"}:
            raise ValueError(f"unknown observation state: {self.state}")
        if self.evidence_kind not in {"composite", "metrics", "ordering", "status"}:
            raise ValueError(f"unknown evidence kind: {self.evidence_kind}")


@dataclass(frozen=True)
class Config:
    as_of: date
    half_life_days: float = 45.0
    prior_score: float = 50.0
    prior_strength: float = 0.8
    minimum_sources: int = 2
    # Retained in output/config for compatibility; unsafe v1 adjustments are disabled.
    variance_penalty: float = 0.0
    low_outlier_gap: float = 0.0
    three_source_bonus: float = 0.0
    four_source_bonus: float = 0.0
    unknown_date_weight: float = 0.25
    max_age_days: int = 90
    metric_weights: dict[str, float] = field(default_factory=lambda: {
        "rank": 0.0, "score": 1.0, "uptime": 0.2, "cache_rate": 0.1, "price_value": 0.1,
    })

    def __post_init__(self) -> None:
        finite_number(self.prior_score, "prior_score")
        finite_number(self.prior_strength, "prior_strength", 0, 100)
        finite_number(self.half_life_days, "half_life_days", 0.001, 10000)
        finite_number(self.unknown_date_weight, "unknown_date_weight", 0, 1)
        if type(self.minimum_sources) is not int or self.minimum_sources < 1:
            raise ValueError("minimum_sources must be a positive integer")
        if type(self.max_age_days) is not int or self.max_age_days < 1:
            raise ValueError("max_age_days must be a positive integer")
        for key in ("variance_penalty", "low_outlier_gap", "three_source_bonus", "four_source_bonus"):
            if getattr(self, key) != 0:
                raise ValueError(f"{key} is retired; set to 0 (v2 preserves negative evidence and monotonicity)")
        allowed = {"rank", "score", "uptime", "cache_rate", "price_value"}
        if set(self.metric_weights) - allowed:
            raise ValueError("unknown metric weight")
        for key, value in self.metric_weights.items():
            finite_number(value, f"metric weight {key}", 0, 100)
        if not any(self.metric_weights.values()):
            raise ValueError("at least one metric weight must be positive")


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
    issues: tuple[str, ...] = ()
    coverage_loss_groups: tuple[str, ...] = ()
