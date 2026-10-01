from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATES = ("valid", "missing", "invalid")


def finite_number(value: object, label: str, lower: float = 0, upper: float = 100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")
    if not lower <= value <= upper:
        raise ValueError(f"{label}: must be in [{lower}, {upper}]")
    return float(value)


@dataclass(frozen=True)
class Source:
    """A ranking site. Sources sharing a lineage `group` count as one vote."""

    name: str
    reliability: float = 1.0
    group: str = ""
    # Only Veridrop uses this: reports older than the window are ignored.
    window_days: int | None = None

    def __post_init__(self) -> None:
        finite_number(self.reliability, f"source {self.name}: reliability", 0, 1)
        if not self.group:
            object.__setattr__(self, "group", self.name)
        if self.window_days is not None and (type(self.window_days) is not int or self.window_days < 1):
            raise ValueError(f"source {self.name}: window_days must be a positive integer")


@dataclass(frozen=True)
class Observation:
    """One source's verdict on one vendor, on that source's own 0-100 scale."""

    source: str
    # The source's display name until identity resolution, then the canonical vendor.
    vendor: str
    domain: str | None = None
    score: float | None = None
    observed_at: date | None = None
    sample_count: int | None = None
    state: str = "valid"
    website_url: str | None = None
    source_url: str | None = None
    issues: tuple[str, ...] = ()
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        json.dumps(self.raw_evidence, allow_nan=False)
        if not self.source.strip() or not self.vendor.strip():
            raise ValueError("source and vendor must not be empty")
        if self.score is not None:
            finite_number(self.score, f"{self.source}/{self.vendor}/score")
        if self.sample_count is not None and (type(self.sample_count) is not int or self.sample_count < 0):
            raise ValueError(f"{self.source}/{self.vendor}: sample_count must be a non-negative integer")
        if self.state not in STATES:
            raise ValueError(f"unknown observation state: {self.state}")
        if self.state == "valid" and self.score is None:
            raise ValueError(f"{self.source}/{self.vendor}: a valid observation needs a score")


@dataclass(frozen=True)
class Config:
    as_of: date
    half_life_days: float = 30.0
    max_age_days: int = 90
    unknown_date_weight: float = 0.25
    prior_score: float = 50.0
    prior_strength: float = 0.8
    minimum_sources: int = 2
    # A source needs this many scorable vendors before its percentiles mean anything.
    minimum_peers: int = 5
    # Weight factor n / (n + sample_prior) for sources that report sample counts.
    sample_prior: float = 3.0
    # A source group counts toward minimum_sources only with at least this much weight;
    # weaker evidence still contributes to the score but cannot qualify a vendor alone.
    coverage_weight: float = 0.25

    def __post_init__(self) -> None:
        finite_number(self.coverage_weight, "coverage_weight", 0, 1)
        finite_number(self.half_life_days, "half_life_days", 0.001, 10000)
        finite_number(self.unknown_date_weight, "unknown_date_weight", 0, 1)
        finite_number(self.prior_score, "prior_score")
        finite_number(self.prior_strength, "prior_strength", 0, 100)
        finite_number(self.sample_prior, "sample_prior", 0, 1000)
        for key in ("max_age_days", "minimum_sources", "minimum_peers"):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")


@dataclass(frozen=True)
class RankedVendor:
    rank: int
    vendor: str
    score: float
    source_count: int
    effective_weight: float
    score_stddev: float
    rank_best: int
    rank_worst: int
    contributions: tuple[dict[str, Any], ...] = ()
    website_url: str = ""
    domains: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    coverage_loss_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ranking:
    results: list[RankedVendor]
    # (source, vendor) -> weight, quality, percentile and vendor eligibility.
    evaluations: dict[tuple[str, str], dict[str, Any]]
    cohort_size: int
    scoring_sources: tuple[str, ...]
    thin_sources: tuple[str, ...]
    # vendor -> hard-rule reason, for vendors removed from the cohort despite having observations.
    excluded: dict[str, str] = field(default_factory=dict)
