"""Extract public, model-scoped evidence without manufacturing a quality score."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .adapters.common import Document, Node

BENCHMARK_URL = "https://apiranking.com/benchmark"


def _text(node: Node) -> str:
    return " ".join(node.text().split())


def _cells(row: Node) -> list[Node]:
    return [n for n in row.children if isinstance(n, Node) and n.tag in {"td", "th"}]


def _number(text: str, integer: bool = False):
    text = text.strip().replace(",", "")
    if text in {"", "—", "--", "-"}:
        return None
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        raise ValueError(f"unexpected numeric benchmark value: {text!r}")
    value = float(text)
    if not math.isfinite(value) or (integer and not value.is_integer()):
        raise ValueError("invalid benchmark number")
    return int(value) if integer else value


def _seconds(text: str):
    if text.strip() in {"", "—", "--", "-"}:
        return None
    match = re.fullmatch(r'([\d.]+)\s*(秒|ms|s)', text.strip())
    if not match:
        raise ValueError(f"unexpected benchmark duration: {text!r}")
    value = _number(match[1])
    return value / 1000 if match[2] == "ms" else value


def _time(text: str) -> dict:
    # These pages currently omit the year/timezone. Do not invent either from fetch time.
    return {"display": text, "timestamp": None, "reason": "year_or_timezone_not_provided"}


def _table(section: Node, headers: list[str]) -> list[list[str]]:
    tables = [n for n in section.walk() if n.tag == "table"]
    if len(tables) != 1:
        raise ValueError("missing or ambiguous benchmark detail table")
    rows = [n for n in tables[0].walk() if n.tag == "tr"]
    if not rows or [_text(c) for c in _cells(rows[0])] != headers:
        raise ValueError("benchmark detail table schema changed")
    return [[_text(c) for c in _cells(r)] for r in rows[1:]]


def parse_benchmarks(body: bytes, aliases: dict[str, str]) -> dict:
    root = Document(body.decode("utf-8", errors="strict")).root
    rows = [n for n in root.walk() if n.tag == "tr" and n.has_class("bx-row")]
    panels = [n for n in root.walk() if n.tag == "tr" and n.has_class("bx-detail")]
    by_id = {n.attrs.get("id"): n for n in panels}
    if not rows or len(by_id) != len(panels):
        raise ValueError("benchmark rows unavailable or duplicate detail IDs")
    used = set()
    batches = []
    for row in rows:
        cells = _cells(row)
        if len(cells) != 7:
            raise ValueError("benchmark summary columns changed")
        targets = [n.attrs.get("data-target") for n in row.walk() if n.has_class("bx-expand")]
        if len(targets) != 1 or targets[0] not in by_id or targets[0] in used:
            raise ValueError("missing or duplicate benchmark detail association")
        batch_id = targets[0]
        used.add(batch_id)
        panel = by_id[batch_id]
        if panel.attrs.get("data-family") != row.attrs.get("data-family"):
            raise ValueError("benchmark model family mismatch")
        anonymous = row.has_class("bx-anon-row") or "■■" in _text(cells[0])
        links = [n for n in cells[0].walk() if n.tag == "a" and urlsplit(n.attrs.get("href", "")).path.startswith("/p/")]
        if not anonymous and len(links) != 1:
            raise ValueError("benchmark vendor identity missing")
        raw_name = _text(links[0]) if not anonymous else None
        vendor = aliases.get(raw_name.casefold(), raw_name) if raw_name else None
        subs = [_text(n) for n in cells[0].walk() if n.has_class("bx-sub") and not n.has_class("bx-rate")]
        if len(subs) != 2:
            raise ValueError("benchmark model/channel schema changed")
        group, model = subs
        money = re.search(r'([¥$])\s*([\d.]+)\s*/\s*([¥$])\s*([\d.]+)', _text(cells[2]))
        if money and money[1] != money[3]:
            raise ValueError("benchmark currencies mismatched")
        if not money and _text(cells[2]) not in {"—", "-", ""}:
            raise ValueError("benchmark charge format changed")
        cache_text = _text(cells[4])
        denominator = re.search(r'(\d+)\s*轮', cache_text)
        hits = None if row.attrs.get("data-cache") == "-1" else _number(row.attrs.get("data-cache", ""), integer=True)
        trials = int(denominator[1]) if denominator else None
        if hits is not None and (trials is None or not 0 <= hits <= trials):
            raise ValueError("benchmark cache numerator/denominator mismatch")
        batch = {
            "batch_id": batch_id, "source_url": BENCHMARK_URL + "#" + batch_id,
            "vendor": vendor, "vendor_display": raw_name, "anonymous": anonymous,
            "listing_slug": urlsplit(links[0].attrs["href"]).path.removeprefix("/p/") if not anonymous else None,
            "model_family": row.attrs.get("data-family"), "client": row.attrs.get("data-mode"),
            "channel": None if anonymous else group, "model": model,
            "test_time": _time(_text(cells[1])), "currency": ("CNY" if money[1] == "¥" else "USD") if money else None,
            "expected_charge_display": _number(money[2]) if money else None, "actual_charge_display": _number(money[4]) if money else None,
            "billing_assessment": _text(cells[3]), "cache_hits": hits, "cache_trials": trials,
            "cache_assessment": cache_text, "average_wait_seconds": _seconds(_text(cells[5])),
            "rounds": [], "billing_records": [], "history": [],
            "issues": ["selection_bias_anonymized_negative_results", "test_year_or_timezone_unknown",
                       "sample_only_not_service_uptime"],
        }
        for section in [n for n in panel.walk() if n.has_class("bxd-sec")]:
            title = next((_text(n) for n in section.walk() if n.tag == "h4"), "")
            if title == "逐轮记录":
                data = _table(section, ["轮", "结果", "首字", "输入", "缓存写", "缓存读", "输出"])
                for values in data:
                    if len(values) != 7 or not re.fullmatch(r'R\d+', values[0]):
                        raise ValueError("malformed benchmark round")
                    batch["rounds"].append(dict(zip(
                        ["round", "result", "first_token_seconds", "input_tokens", "cache_write_tokens", "cache_read_tokens", "output_tokens"],
                        [values[0], values[1], _seconds(values[2]), *[_number(v, integer=True) for v in values[3:]]])))
            elif title == "应扣怎么算":
                batch["charge_calculation"] = _text(section)
                exact = re.search(r'应扣\s*([¥$])([\d.]+)', _text(section))
                if exact:
                    if money and exact[1] != money[1]:
                        raise ValueError("benchmark charge calculation currency mismatch")
                    batch["expected_charge_exact"] = _number(exact[2])
            elif title.startswith("站方面板扣费记录"):
                data = _table(section, ["时间", "模型", "输入", "输出", "缓存写", "缓存读", "扣费"])
                for values in data:
                    if values and values[0] == "合计":
                        if len(values) != 2:
                            raise ValueError("benchmark billing total schema changed")
                        batch["actual_charge_total"] = _number(values[1])
                    else:
                        if len(values) != 7:
                            raise ValueError("malformed benchmark billing row")
                        batch["billing_records"].append({"time": _time(values[0]), "model": values[1],
                            **dict(zip(["input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"], [_number(v, True) for v in values[2:6]])),
                            "charge": _number(values[6])})
            elif title == "历史批次":
                for values in _table(section, ["时间", "应扣", "实扣", "缓存命中", "平均等待"]):
                    if len(values) != 5:
                        raise ValueError("malformed benchmark history")
                    batch["history"].append({"time": _time(values[0]), "expected_charge": _number(values[1]),
                        "actual_charge": _number(values[2]), "cache_display": values[3],
                        "average_wait_seconds": _seconds(values[4])})
        rounds = batch["rounds"]
        if not rounds or len({r["round"] for r in rounds}) != len(rounds):
            raise ValueError("benchmark rounds missing or duplicated")
        batch["round_count"] = len(rounds)
        batch["successful_round_count"] = sum(r["result"] == "成功" for r in rounds)
        if not batch["billing_records"]:
            batch["issues"].append("billing_records_missing")
        # Precision/currency assumptions are not silently repaired.
        elif "actual_charge_total" in batch:
            charges = [r["charge"] for r in batch["billing_records"]]
            if all(c is not None for c in charges) and abs(sum(charges) - batch["actual_charge_total"]) > max(0.0001, len(charges)*0.00005):
                batch["issues"].append("billing_total_mismatch")
        batches.append(batch)
    if set(by_id) != used:
        raise ValueError("unmatched benchmark detail panels")
    return {"source_url": BENCHMARK_URL, "batch_count": len(batches),
            "anonymous_count": sum(b["anonymous"] for b in batches),
            "selection_bias": "Some negative results are anonymized by the source. Missing named evidence is not positive evidence.",
            "batches": batches}


def parse_channel_detail(payload: dict, expected: dict) -> dict:
    channel = payload.get("channel")
    if not isinstance(channel, dict) or any(channel.get(k) != expected.get(k) for k in ("id", "provider", "model")):
        raise ValueError("TokHub detail channel identity mismatch")
    records = payload.get("recentRecords")
    l3 = payload.get("l3")
    if not isinstance(records, list) or not isinstance(l3, dict):
        raise ValueError("TokHub detail records/summary missing")
    issues = ["recent_records_are_bounded_sample", "summary_rates_not_used_as_measurements"]
    normalized = []
    stamps = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not all(k in record for k in ("time", "layer", "type", "result")):
            raise ValueError("malformed TokHub probe record")
        stamp = datetime.fromisoformat(record["time"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("TokHub probe timestamp has no timezone")
        key = (stamp, record["layer"], record["type"])
        if key in seen:
            raise ValueError("duplicate TokHub probe record")
        seen.add(key)
        stamps.append(stamp.astimezone(timezone.utc))
        latency = record.get("latencyMs")
        if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0):
            raise ValueError("invalid TokHub probe latency")
        normalized.append({k: record.get(k) for k in ("time", "layer", "type", "httpCode", "latencyMs", "result")})
    l3_records = [r for r in normalized if r["layer"] == "L3"]
    if l3.get("quotaClass") == "not_run":
        issues.append("l3_not_run")
    if not l3_records:
        issues.append("no_l3_records_in_returned_sample")
    return {"channel_id": channel["id"], "public_slug": channel.get("publicSlug"),
            "provider": channel["provider"], "model": channel["model"],
            "requested_range": "24h", "record_count": len(records), "l3_record_count": len(l3_records),
            "record_window_start": min(stamps).isoformat() if stamps else None,
            "record_window_end": max(stamps).isoformat() if stamps else None,
            "recent_records": normalized, "l3_summary": l3, "layers": payload.get("layers"),
            "errors": payload.get("errors"), "costs": payload.get("costs"),
            "issues": issues, "measured_uptime": None}
