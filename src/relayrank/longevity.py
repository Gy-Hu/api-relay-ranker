"""Operating-age evidence per registrable domain, and the expired-domain hard rule.

Evidence is a date by which the relay demonstrably existed. Two families:

* Observed by third parties watching the relay itself. Kept even when older than the current
  domain's registration, because vendors move domains (HelpAIO tracked Micu before micuapi.ai):
  ``helpaio_listed`` (card "已收 N 天"), ``veridrop_report`` (earliest report seen),
  ``first_seen`` (first run in which any of our sources listed the domain).
* Derived from the domain itself, or claimed by the vendor. Dropped when older than the
  registration date, because it may belong to a previous owner of a second-hand domain or be
  unverifiable: ``first_cert`` (crt.sh), ``wayback`` (homepage capture), ``relaypick_online``
  (RelayPick "上线", floored by the vendor's earliest registration).

Registration alone is never evidence of operation. Dates are cached in the repository so each
daily run only queries new domains, failed lookups and domains near expiry.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import quote

from .fetch import USER_AGENT
from .identity import host_of, registrable_domain
from .models import Observation

CACHE_VERSION = 1
RETRY_DAYS = 1               # transient failures (429, timeouts) are retried on the next run
NO_RDAP_RETRY_DAYS = 30      # TLDs without RDAP (.cn) rarely change
RDAP_REFRESH_DAYS = 90       # registration can change on re-registration
EXPIRY_WATCH_DAYS = 30       # re-check RDAP daily inside this window, and show a warning
REUSE_TOLERANCE_DAYS = 30    # older evidence than this before registration => previous owner

EVIDENCE_LABELS = {
    "helpaio_listed": "HelpAIO 收录",
    "veridrop_report": "Veridrop 首份报告",
    "first_seen": "本站首次收录",
    "first_cert": "首张证书",
    "wayback": "Wayback 首次存档",
    "relaypick_online": "RelayPick 上线日期",
}
OBSERVED_KINDS = ("helpaio_listed", "veridrop_report", "first_seen")


@dataclass(frozen=True)
class LongevityConfig:
    # Display only: vendors without evidence of this many operating days are hidden by default.
    min_operating_days: int = 90

    def __post_init__(self) -> None:
        if type(self.min_operating_days) is not int or self.min_operating_days < 0:
            raise ValueError("min_operating_days must be a non-negative integer")

    @classmethod
    def from_toml(cls, raw: dict[str, Any]) -> "LongevityConfig":
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown [longevity] keys: {', '.join(sorted(unknown))}")
        return cls(**raw)


@dataclass(frozen=True)
class Budget:
    """Lookups per run; leftovers are picked up by the next run."""
    rdap: int = 80
    cert: int = 10
    wayback: int = 20


class Lookups(Protocol):
    def rdap(self, domain: str) -> dict[str, Any]:
        """``{"registered", "expires", "status"}``; raises LookupError("no_rdap") when unsupported."""

    def certificates(self, domain: str) -> list[date]:
        """not_before dates of CT-logged certificates for the domain and its subdomains."""

    def wayback(self, domain: str, since: date | None) -> date | None:
        """First homepage capture on or after ``since`` (any time when None)."""


def _day(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date() if "T" in text else date.fromisoformat(text[:10])


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


class HttpLookups:
    """RDAP from each TLD's registry (IANA bootstrap), certificates via crt.sh, captures via Wayback CDX.

    The rdap.org redirector rate-limits bulk use, so registry servers are queried directly.
    """

    BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"

    def __init__(self, timeout: float = 25, cert_timeout: float = 45, cert_pause: float = 3) -> None:
        self.timeout, self.cert_timeout, self.cert_pause = timeout, cert_timeout, cert_pause
        self._cert_lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._servers: dict[str, str] | None = None

    def _get_json(self, url: str, timeout: float, accept: str = "application/json") -> Any:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read(40_000_000))
        except urllib.error.HTTPError as error:
            raise LookupError(f"HTTP {error.code}") from error

    def _rdap_server(self, domain: str) -> str:
        with self._bootstrap_lock:
            if self._servers is None:
                services = self._get_json(self.BOOTSTRAP_URL, self.timeout).get("services", [])
                self._servers = {tld.lower(): urls[0].rstrip("/") + "/"
                                 for tlds, urls in services for tld in tlds if urls}
        labels = domain.lower().split(".")
        for i in range(len(labels)):  # longest matching suffix
            server = self._servers.get(".".join(labels[i:]))
            if server:
                return server
        raise LookupError("no_rdap")

    def rdap(self, domain: str) -> dict[str, Any]:
        try:
            data = self._get_json(self._rdap_server(domain) + "domain/" + quote(domain), self.timeout, "application/rdap+json")
        except LookupError as error:
            if str(error) == "HTTP 404":
                raise LookupError("no_rdap") from error
            raise
        events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", []) if isinstance(e, dict)}
        return {"registered": _iso(_day(events.get("registration"))), "expires": _iso(_day(events.get("expiration"))),
                "status": sorted(str(s) for s in data.get("status", []))}

    def certificates(self, domain: str) -> list[date]:
        with self._cert_lock:  # crt.sh throttles bursts; keep requests sequential and spaced
            try:
                rows = self._get_json(f"https://crt.sh/?q=%25.{quote(domain)}&output=json&deduplicate=Y", self.cert_timeout)
            finally:
                time.sleep(self.cert_pause)
        return [_day(r["not_before"]) for r in rows if isinstance(r, dict) and r.get("not_before")]

    def wayback(self, domain: str, since: date | None) -> date | None:
        url = f"https://web.archive.org/cdx/search/cdx?url={quote(domain)}&output=json&limit=1&fl=timestamp"
        if since:
            url += "&from=" + since.strftime("%Y%m%d")
        rows = self._get_json(url, self.timeout)
        return _day(rows[1][0]) if isinstance(rows, list) and len(rows) > 1 else None


def load_cache(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"schema_version": CACHE_VERSION, "domains": {}}
    cache = json.loads(path.read_text(encoding="utf-8"))
    if cache.get("schema_version") != CACHE_VERSION:
        raise ValueError(f"{path}: unsupported domain evidence schema {cache.get('schema_version')}")
    return cache


def save_cache(path: str | Path, cache: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {"schema_version": CACHE_VERSION, "domains": dict(sorted(cache["domains"].items()))}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def _entry(cache: dict[str, Any], domain: str, as_of: date) -> dict[str, Any]:
    return cache["domains"].setdefault(domain, {"first_seen": as_of.isoformat()})


def _keep_earliest(entry: dict[str, Any], key: str, value: date | None) -> None:
    if value and (key not in entry or value < date.fromisoformat(entry[key])):
        entry[key] = value.isoformat()


def record_observations(cache: dict[str, Any], observations: Iterable[Observation], as_of: date) -> None:
    """Store source-provided evidence per registrable domain (call with unresolved observations)."""
    for o in observations:
        host = host_of(o.domain)
        if not host:
            continue
        entry = _entry(cache, registrable_domain(host), as_of)
        raw = o.raw_evidence
        if o.source == "helpaio" and raw.get("listed_days") is not None and o.observed_at:
            _keep_earliest(entry, "helpaio_listed", o.observed_at - timedelta(days=raw["listed_days"]))
        elif o.source == "veridrop":
            _keep_earliest(entry, "veridrop_report", _day(raw.get("earliest_report_seen")))
        elif o.source == "relaypick" and raw.get("online_since"):
            entry["relaypick_online"] = _day(raw["online_since"]).isoformat()  # a claim: latest value wins


def _stale(record: dict[str, Any] | None, as_of: date, every_days: int | None) -> bool:
    if not record:
        return True
    age = (as_of - date.fromisoformat(record["checked"])).days
    if "error" in record:
        return age >= (NO_RDAP_RETRY_DAYS if record["error"] == "no_rdap" else RETRY_DAYS)
    return every_days is not None and age >= every_days


def _rdap_due(entry: dict[str, Any], as_of: date) -> bool:
    record = entry.get("rdap")
    if _stale(record, as_of, RDAP_REFRESH_DAYS):
        return True
    expires = _day(record.get("expires"))
    return bool(expires and expires - as_of <= timedelta(days=EXPIRY_WATCH_DAYS) and record["checked"] < as_of.isoformat())


def _registered(entry: dict[str, Any]) -> date | None:
    return _day((entry.get("rdap") or {}).get("registered"))


def _derived_due(entry: dict[str, Any], key: str, as_of: date) -> bool:
    record = entry.get(key)
    if _stale(record, as_of, None):
        return True
    # A changed registration date means a new owner: earlier results may belong to the old one.
    return "error" not in record and record.get("basis") != _iso(_registered(entry))


def _cert_record(lookups: Lookups, domain: str, basis: date | None) -> dict[str, Any]:
    dates = sorted(d for d in lookups.certificates(domain) if d)
    after = [d for d in dates if basis is None or d >= basis]
    return {"basis": _iso(basis), "first": _iso(after[0] if after else None), "first_any": _iso(dates[0] if dates else None)}


def _wayback_record(lookups: Lookups, domain: str, basis: date | None) -> dict[str, Any]:
    first_any = lookups.wayback(domain, None)
    first = first_any if basis is None or first_any is None or first_any >= basis else lookups.wayback(domain, basis)
    return {"basis": _iso(basis), "first": _iso(first), "first_any": _iso(first_any)}


def _attempt(function, *args) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return function(*args), None
    except Exception as error:  # noqa: BLE001 - lookups are best effort; the error is cached and retried
        return None, str(error)[:200] or type(error).__name__


def refresh(cache: dict[str, Any], domains: list[str], as_of: date, lookups: Lookups,
            budget: Budget = Budget()) -> dict[str, int]:
    """Query due lookups for ``domains`` (highest priority first) within the per-run budget."""
    for domain in domains:
        _entry(cache, domain, as_of)
    entries = cache["domains"]
    checked = as_of.isoformat()
    stats = {"rdap": 0, "cert": 0, "wayback": 0, "errors": 0}

    def store(domain: str, key: str, result: dict[str, Any] | None, error: str | None) -> None:
        entries[domain][key] = {"checked": checked, **(result or {})} if error is None else {"checked": checked, "error": error}
        stats[key] += 1
        stats["errors"] += error is not None

    rdap_jobs = [d for d in domains if _rdap_due(entries[d], as_of)][:budget.rdap]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for domain, (result, error) in zip(rdap_jobs, executor.map(lambda d: _attempt(lookups.rdap, d), rdap_jobs)):
            store(domain, "rdap", result, error)

    cert_jobs = [d for d in domains if _derived_due(entries[d], "cert", as_of)][:budget.cert]
    wayback_jobs = [d for d in domains if _derived_due(entries[d], "wayback", as_of)][:budget.wayback]
    with ThreadPoolExecutor(max_workers=3) as executor:
        certs = [executor.submit(_attempt, _cert_record, lookups, d, _registered(entries[d])) for d in cert_jobs]
        waybacks = [executor.submit(_attempt, _wayback_record, lookups, d, _registered(entries[d])) for d in wayback_jobs]
        for domain, future in zip(cert_jobs, certs):
            store(domain, "cert", *future.result())
        for domain, future in zip(wayback_jobs, waybacks):
            store(domain, "wayback", *future.result())
    return stats


@dataclass(frozen=True)
class VendorAge:
    service_start: date | None
    basis: str | None
    basis_domain: str | None
    operating_days: int | None
    evidence: tuple[dict[str, Any], ...] = ()
    expired_domains: tuple[str, ...] = ()
    expiring: tuple[tuple[str, str], ...] = ()
    reused_domains: tuple[str, ...] = ()
    excluded: str | None = None

    def mature(self, min_days: int) -> bool:
        return self.operating_days is not None and self.operating_days >= min_days

    def to_json(self) -> dict[str, Any]:
        return {"service_start": _iso(self.service_start), "basis": self.basis, "basis_domain": self.basis_domain,
                "operating_days": self.operating_days, "evidence": list(self.evidence),
                "expired_domains": list(self.expired_domains),
                "expiring_domains": [{"domain": d, "expires": e} for d, e in self.expiring],
                "reused_domains": list(self.reused_domains), "excluded": self.excluded}


def vendor_age(domains: Iterable[str], cache: dict[str, Any], as_of: date) -> VendorAge:
    entries = {d: cache["domains"][d] for d in domains if d in cache["domains"]}
    registrations = {d: r for d, e in entries.items() if (r := _registered(e))}
    vendor_floor = min(registrations.values(), default=None)
    evidence = []

    def add(kind: str, domain: str, day: date | None, floor: date | None, reused_domain: bool = False) -> None:
        if not day:
            return
        reason = None
        if day > as_of:
            reason = "future_date"
        elif kind not in OBSERVED_KINDS and floor and day < floor:
            reason = "before_registration"
        elif reused_domain:
            # The domain changed hands; transfers keep the old registration date, so none of
            # its own history can be attributed to the current operator.
            reason = "domain_reused"
        evidence.append({"kind": kind, "domain": domain, "date": day.isoformat(), "used": reason is None, "reason": reason})

    reused, expired, expiring, known = [], [], [], 0
    for domain, entry in sorted(entries.items()):
        floor = registrations.get(domain)
        for kind in OBSERVED_KINDS:
            add(kind, domain, _day(entry.get(kind)), floor)
        records = {kind: entry.get(key) or {} for kind, key in (("first_cert", "cert"), ("wayback", "wayback"))}
        was_reused = any(floor and (first_any := _day(r.get("first_any"))) and first_any < floor - timedelta(days=REUSE_TOLERANCE_DAYS)
                         for r in records.values() if "error" not in r)
        if was_reused:
            reused.append(domain)
        for kind, record in records.items():
            if "error" not in record:
                add(kind, domain, _day(record.get("first")), floor, was_reused)
        add("relaypick_online", domain, _day(entry.get("relaypick_online")), vendor_floor)
        rdap = entry.get("rdap") or {}
        expires = _day(rdap.get("expires"))
        if expires and "error" not in rdap:
            known += 1
            if expires < as_of and _day(rdap["checked"]) > expires:  # confirmed by a check after expiry
                expired.append(domain)
            elif expires - as_of <= timedelta(days=EXPIRY_WATCH_DAYS):
                expiring.append((domain, expires.isoformat()))

    used = [e for e in evidence if e["used"]]
    first = min(used, key=lambda e: (e["date"], e["kind"]), default=None)
    start = date.fromisoformat(first["date"]) if first else None
    return VendorAge(
        service_start=start, basis=first["kind"] if first else None, basis_domain=first["domain"] if first else None,
        operating_days=(as_of - start).days if start else None,
        evidence=tuple(sorted(evidence, key=lambda e: (e["date"], e["kind"]))),
        expired_domains=tuple(expired), expiring=tuple(expiring), reused_domains=tuple(sorted(set(reused))),
        excluded=("domain_expired: " + ", ".join(expired)) if expired and len(expired) == known else None,
    )


def vendor_ages(vendor_domains: dict[str, tuple[str, ...]], cache: dict[str, Any], as_of: date) -> dict[str, VendorAge]:
    return {vendor: vendor_age(domains, cache, as_of) for vendor, domains in vendor_domains.items()}
