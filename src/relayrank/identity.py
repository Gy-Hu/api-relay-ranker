"""Vendor identity: sources are joined by registrable domain, never by fuzzy names."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from statistics import fmean
from urllib.parse import urlsplit

from .models import Observation

# Public suffixes with two labels that relay domains actually use.
_MULTI_LABEL_SUFFIXES = frozenset({
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.hk", "com.tw", "co.uk", "co.jp", "com.au", "com.sg",
})
# Shared hosting: every subdomain belongs to a different owner.
_SHARED_HOSTS = frozenset({
    "github.io", "vercel.app", "pages.dev", "workers.dev", "netlify.app", "herokuapp.com",
    "onrender.com", "zeabur.app", "railway.app", "fly.dev", "deno.dev",
})


def host_of(value: str | None) -> str | None:
    """Lower-case host of a URL or bare host, or None if there is none."""
    if not value:
        return None
    text = value.strip()
    parsed = urlsplit(text if "//" in text else "//" + text)
    host = (parsed.hostname or "").strip(".").lower()
    return host or None


def registrable_domain(host: str) -> str:
    host = host.strip(".").lower()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    for suffix in _SHARED_HOSTS | _MULTI_LABEL_SUFFIXES:
        if host.endswith("." + suffix):
            return ".".join(labels[-(suffix.count(".") + 2):])
    return ".".join(labels[-2:])


@dataclass(frozen=True)
class VendorSpec:
    name: str
    domains: tuple[str, ...] = ()
    # Only used for observations that carry no domain at all.
    aliases: tuple[str, ...] = ()


class Directory:
    def __init__(self, specs: tuple[VendorSpec, ...] = ()) -> None:
        self.specs = specs
        self._by_domain: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}
        names = set()
        for spec in specs:
            if not spec.name.strip() or spec.name.casefold() in names:
                raise ValueError(f"empty or duplicate vendor name: {spec.name!r}")
            names.add(spec.name.casefold())
            for domain in spec.domains:
                host = host_of(domain)
                if host is None:
                    raise ValueError(f"vendor {spec.name}: invalid domain {domain!r}")
                key = registrable_domain(host)
                if self._by_domain.setdefault(key, spec.name) != spec.name:
                    raise ValueError(f"domain {key} configured for both {self._by_domain[key]} and {spec.name}")
            for alias in (spec.name, *spec.aliases):
                key = alias.strip().casefold()
                if self._by_alias.setdefault(key, spec.name) != spec.name:
                    raise ValueError(f"ambiguous vendor alias: {alias}")

    def resolve(self, observations: list[Observation]) -> tuple[list[Observation], dict[str, tuple[str, ...]]]:
        """Rename observations to canonical vendors and merge same-source duplicates.

        Returns the observations plus each vendor's registrable domains (configured and observed).
        """
        keys = []
        for o in observations:
            host = host_of(o.domain)
            if host:
                domain = registrable_domain(host)
                name = self._by_domain.get(domain)
                keys.append(("vendor", name) if name else ("domain", domain))
            else:
                name = self._by_alias.get(o.vendor.strip().casefold())
                keys.append(("vendor", name) if name else ("name", o.vendor.strip().casefold()))

        taken = {spec.name.casefold() for spec in self.specs}
        display: dict[tuple[str, str], str] = {}
        for key, o in zip(keys, observations):
            if key in display:
                continue
            if key[0] == "vendor":
                display[key] = key[1]
                continue
            label = o.vendor.strip()
            if label.casefold() in taken:
                label = f"{label} ({key[1]})" if key[0] == "domain" else f"{label} (no domain)"
            taken.add(label.casefold())
            display[key] = label

        domains: dict[str, set[str]] = {}
        for spec in self.specs:
            domains[spec.name] = {registrable_domain(host_of(d)) for d in spec.domains}
        grouped: dict[tuple[str, str], list[Observation]] = {}
        for key, o in zip(keys, observations):
            vendor = display[key]
            issues = o.issues
            alias = self._by_alias.get(o.vendor.strip().casefold())
            if key[0] == "domain" and alias:
                # Same name as a configured vendor on an unconfigured domain: keep apart, flag it.
                issues = (*issues, f"name_matches_configured_vendor: {alias}")
            renamed = replace(o, vendor=vendor, issues=issues, raw_evidence={"source_name": o.vendor, **o.raw_evidence})
            grouped.setdefault((o.source, vendor), []).append(renamed)
            if key[0] == "domain":
                domains.setdefault(vendor, set()).add(key[1])
            elif host_of(o.domain):
                domains.setdefault(vendor, set()).add(registrable_domain(host_of(o.domain)))
        merged = [entries[0] if len(entries) == 1 else _merge(entries) for entries in grouped.values()]
        return merged, {vendor: tuple(sorted(values)) for vendor, values in domains.items()}


def _merge(entries: list[Observation]) -> Observation:
    """One source listing several domains of one vendor: average the valid entries."""
    first = entries[0]
    valid = [e for e in entries if e.state == "valid"]
    chosen = valid or entries
    dates = [e.observed_at for e in chosen if e.observed_at]
    counts = [e.sample_count for e in chosen]
    issues = sorted({issue for e in entries for issue in e.issues} | {"multiple_source_entries_averaged"})
    return replace(
        first,
        score=fmean(e.score for e in valid) if valid else first.score,
        state="valid" if valid else first.state,
        observed_at=max(dates) if dates and len(dates) == len(chosen) else None,
        sample_count=sum(counts) if None not in counts else None,
        website_url=next((e.website_url for e in chosen if e.website_url), None),
        source_url=next((e.source_url for e in chosen if e.source_url), None),
        issues=tuple(issues),
        raw_evidence={"entries": [
            {"domain": e.domain, "score": e.score, "state": e.state, **e.raw_evidence} for e in entries
        ]},
    )
