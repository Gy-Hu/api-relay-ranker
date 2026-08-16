from __future__ import annotations

import html
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import RankedVendor


SOURCE_LABELS = {
    "helpaio": "HelpAIO",
    "zhaotutu": "zhaotutu",
    "apiranking": "APIRanking",
    "tokhub": "TokHub",
}
SOURCE_URLS = {
    "helpaio": "https://www.helpaio.com/transit",
    "zhaotutu": "https://zhaotutu.ai/",
    "apiranking": "https://apiranking.com/",
    "tokhub": "https://www.tokhub.me/",
}
SOURCE_ORDER = tuple(SOURCE_LABELS)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _timestamp(value: str) -> str:
    if not value:
        return "未知"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        hong_kong = parsed.astimezone(timezone(timedelta(hours=8)))
        return hong_kong.strftime("%Y-%m-%d %H:%M HKT")
    except ValueError:
        return value


def _report_dict(report: Any) -> dict[str, Any]:
    if is_dataclass(report):
        return asdict(report)
    return dict(report)


def _source_link(source: str, css_class: str = "source-link") -> str:
    label = SOURCE_LABELS[source]
    return (
        f'<a class="{css_class}" href="{SOURCE_URLS[source]}" target="_blank" '
        f'rel="noopener noreferrer external" aria-label="打开 {label} 榜单">'
        f'{label}<span aria-hidden="true">↗</span></a>'
    )


def _source_details(contributions: Iterable[dict[str, object]]) -> str:
    indexed = {str(item["source"]): item for item in contributions}
    cards: list[str] = []
    for source in SOURCE_ORDER:
        item = indexed.get(source)
        if item is None:
            cards.append(
                f'<div class="source-card source-card--missing">{_source_link(source)}<strong>未收录</strong></div>'
            )
            continue
        rank = item.get("rank")
        total = item.get("total_vendors")
        rank_text = f"#{rank} / {total}" if rank is not None and total is not None else "有数据"
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        metric_bits = []
        for key, label in (("score", "站点分"), ("uptime", "可用率"), ("cache_rate", "缓存")):
            value = metrics.get(key) if isinstance(metrics, dict) else None
            if value is not None:
                metric_bits.append(f"{label} {float(value):.2f}")
        if item.get("low_outlier_guarded"):
            metric_bits.append(
                f"异常低分保护 {float(item['raw_score']):.2f}→{float(item['adjusted_score']):.2f}"
            )
        metric_text = " · ".join(metric_bits) or f"贡献分 {float(item['raw_score']):.2f}"
        cards.append(
            '<div class="source-card">'
            f'{_source_link(source)}<strong>{_escape(rank_text)}</strong>'
            f'<small>{_escape(metric_text)}</small></div>'
        )
    return "".join(cards)


def _ranking_rows(results: list[RankedVendor]) -> str:
    rows: list[str] = []
    for item in results:
        high_confidence = item.source_count >= 3
        label = "高置信" if high_confidence else "两榜候选"
        badge_class = "badge--high" if high_confidence else "badge--candidate"
        vendor_name = _escape(item.vendor)
        vendor_markup = (
            f'<a class="vendor-link" href="{_escape(item.website_url)}" target="_blank" rel="noopener noreferrer external" '
            f'aria-label="访问 {vendor_name} 官网">{vendor_name}<span aria-hidden="true">↗</span></a>'
            if item.website_url
            else f"<strong>{vendor_name}</strong>"
        )
        if item.low_outlier_sources:
            disagreement_markup = (
                f'<span>原始分歧 σ <strong>{item.raw_score_stddev:.2f}</strong>，异常保护后 '
                f'<strong>{item.score_stddev:.2f}</strong>，保守扣分 <strong>{item.disagreement_penalty:.2f}</strong></span>'
            )
        else:
            disagreement_markup = (
                f'<span>来源评分分歧 σ <strong>{item.score_stddev:.2f}</strong>，'
                f'保守扣分 <strong>{item.disagreement_penalty:.2f}</strong></span>'
            )
        bonus_markup = (
            f'<span>多榜覆盖加分 <strong>+{item.coverage_bonus:.2f}</strong></span>'
            if item.coverage_bonus > 0
            else ""
        )
        rows.append(
            '<article class="rank-card">'
            '<details>'
            '<summary>'
            f'<span class="position">{item.rank:02d}</span>'
            f'<span class="vendor">{vendor_markup}<small>{item.source_count}/4 榜覆盖</small></span>'
            f'<span class="badge {badge_class}">{label}</span>'
            f'<span class="score"><strong>{item.score:.2f}</strong><small>综合分</small></span>'
            '<span class="chevron" aria-hidden="true">＋</span>'
            '</summary>'
            '<div class="detail-body">'
            '<div class="detail-stats">'
            f'<span>置信度 <strong>{item.confidence * 100:.1f}%</strong></span>'
            f'{disagreement_markup}'
            f'{bonus_markup}'
            f'<span>移除单榜后的名次区间 <strong>{item.rank_best}–{item.rank_worst}</strong></span>'
            '</div>'
            f'<div class="source-grid">{_source_details(item.contributions)}</div>'
            '</div>'
            '</details>'
            '</article>'
        )
    return "".join(rows)


def _source_rows(reports: list[dict[str, Any]]) -> str:
    indexed = {str(report["name"]): report for report in reports}
    rows: list[str] = []
    for name in SOURCE_ORDER:
        report = indexed.get(name, {"ok": False, "vendor_count": 0, "fetched_at": ""})
        status = "正常" if report.get("ok") else "异常"
        status_class = "status--ok" if report.get("ok") else "status--error"
        rows.append(
            "<tr>"
            f"<th>{_source_link(name, 'source-table-link')}</th>"
            f'<td><span class="status {status_class}">{status}</span></td>'
            f"<td>{int(report.get('vendor_count') or 0)} 家</td>"
            f"<td>{_escape(_timestamp(str(report.get('fetched_at') or '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def write_site(
    output_dir: str | Path,
    results: list[RankedVendor],
    reports: Iterable[Any],
    generated_at: str,
    site_url: str = "",
    og_image: str | Path | None = None,
) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_dicts = [_report_dict(report) for report in reports]
    healthy_sources = sum(bool(report.get("ok")) for report in report_dicts)
    canonical_url = site_url.rstrip("/") + "/" if site_url else ""
    og_markup = ""
    if og_image:
        source_image = Path(og_image)
        if source_image.is_file():
            shutil.copyfile(source_image, destination / "og.png")
            image_url = canonical_url + "og.png" if canonical_url else "og.png"
            og_markup = (
                f'<meta property="og:image" content="{_escape(image_url)}">'
                f'<meta name="twitter:card" content="summary_large_image">'
            )

    document = f'''<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="每日聚合 HelpAIO、zhaotutu、APIRanking 与 TokHub 的可审计 API 中转站排名。">
  <meta property="og:title" content="RelayRank · API 中转站实时榜">
  <meta property="og:description" content="四榜交叉验证，区分高置信商家与两榜候选。">
  <meta property="og:type" content="website">
  {f'<meta property="og:url" content="{_escape(canonical_url)}">' if canonical_url else ''}
  {og_markup}
  <title>RelayRank · API 中转站实时榜</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0b0d0c; --panel:#121513; --line:#29302b; --ink:#f3f6f2; --muted:#9ba79e; --acid:#b7f34a; --mint:#59dba0; --amber:#f1bb54; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:radial-gradient(circle at 80% -10%,#24341d 0,transparent 34rem),var(--bg); color:var(--ink); font:15px/1.55 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    a {{ color:inherit; }}
    .shell {{ width:min(1100px,calc(100% - 32px)); margin:auto; }}
    header {{ padding:72px 0 44px; border-bottom:1px solid var(--line); }}
    .eyebrow {{ display:flex; align-items:center; gap:10px; color:var(--acid); font:700 12px/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.15em; text-transform:uppercase; }}
    .eyebrow::before {{ content:""; width:8px; height:8px; border-radius:50%; background:var(--acid); box-shadow:0 0 18px var(--acid); }}
    h1 {{ max-width:850px; margin:22px 0 18px; font-size:clamp(42px,7vw,82px); line-height:.98; letter-spacing:-.055em; }}
    .lede {{ max-width:700px; margin:0; color:var(--muted); font-size:18px; }}
    .meta-strip {{ display:flex; flex-wrap:wrap; gap:12px 28px; margin-top:34px; color:var(--muted); font-family:ui-monospace,SFMono-Regular,monospace; font-size:12px; }}
    .meta-strip strong {{ color:var(--ink); }}
    main {{ padding:34px 0 80px; }}
    .section-head {{ display:flex; justify-content:space-between; align-items:end; gap:24px; margin:0 0 18px; }}
    h2 {{ margin:0; font-size:24px; letter-spacing:-.025em; }}
    .section-head p {{ max-width:500px; margin:0; color:var(--muted); text-align:right; }}
    .rank-list {{ display:grid; gap:8px; }}
    .rank-card {{ background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line); border-radius:14px; overflow:hidden; }}
    details[open] {{ background:#151a16; }}
    summary {{ min-height:76px; padding:12px 18px; display:grid; grid-template-columns:54px minmax(170px,1fr) auto 90px 24px; gap:16px; align-items:center; cursor:pointer; list-style:none; }}
    summary::-webkit-details-marker {{ display:none; }}
    .position {{ color:var(--muted); font:600 18px/1 ui-monospace,SFMono-Regular,monospace; }}
    .vendor {{ display:flex; flex-direction:column; }} .vendor strong,.vendor a {{ width:max-content; max-width:100%; color:var(--ink); font-size:17px; font-weight:700; text-decoration:none; }} .vendor a:hover {{ color:var(--acid); text-decoration:underline; text-underline-offset:4px; }} .vendor a span {{ margin-left:6px; color:var(--muted); font-size:12px; }}
    small {{ color:var(--muted); }}
    .badge {{ padding:5px 9px; border-radius:999px; font-size:11px; font-weight:700; white-space:nowrap; }}
    .badge--high {{ color:#07150d; background:var(--mint); }} .badge--candidate {{ color:#241704; background:var(--amber); }}
    .score {{ display:flex; flex-direction:column; text-align:right; }} .score strong {{ font:700 21px/1 ui-monospace,SFMono-Regular,monospace; }}
    .chevron {{ color:var(--muted); font-size:20px; transition:transform .2s ease; }} details[open] .chevron {{ transform:rotate(45deg); }}
    .detail-body {{ padding:0 18px 18px 88px; border-top:1px solid var(--line); }}
    .detail-stats {{ display:flex; flex-wrap:wrap; gap:12px 28px; padding:15px 0; color:var(--muted); font-size:13px; }} .detail-stats strong {{ color:var(--ink); }}
    .source-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }}
    .source-card {{ min-height:102px; padding:12px; border:1px solid var(--line); border-radius:10px; display:flex; flex-direction:column; }}
    .source-link,.source-table-link {{ width:max-content; color:var(--muted); font-size:12px; text-decoration:none; }} .source-link:hover,.source-table-link:hover {{ color:var(--acid); text-decoration:underline; text-underline-offset:3px; }} .source-link span,.source-table-link span {{ margin-left:4px; }} .source-card strong {{ margin-top:5px; font:700 17px/1.2 ui-monospace,SFMono-Regular,monospace; }} .source-card small {{ margin-top:auto; font-size:11px; }}
    .source-card--missing {{ opacity:.48; }}
    .lower-grid {{ display:grid; grid-template-columns:1.25fr .75fr; gap:18px; margin-top:54px; }}
    .panel {{ padding:22px; background:var(--panel); border:1px solid var(--line); border-radius:16px; }} .panel h2 {{ margin-bottom:16px; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:11px 8px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; }} th {{ color:var(--ink); }} td {{ color:var(--muted); }}
    .status {{ display:inline-flex; align-items:center; gap:6px; }} .status::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }} .status--ok {{ color:var(--mint); }} .status--error {{ color:#ff7474; }}
    .method {{ margin:0; padding-left:18px; color:var(--muted); }} .method li+li {{ margin-top:10px; }}
    .warning {{ margin-top:18px; padding:18px 22px; border:1px solid #4c3d20; border-radius:14px; background:#17140e; color:#d9c79f; }}
    footer {{ padding:24px 0 44px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; }} footer .shell {{ display:flex; justify-content:space-between; gap:20px; }}
    @media (max-width:760px) {{ header {{ padding-top:48px; }} .section-head {{ display:block; }} .section-head p {{ margin-top:8px; text-align:left; }} summary {{ grid-template-columns:36px 1fr 70px 20px; gap:10px; }} .badge {{ display:none; }} .detail-body {{ padding-left:14px; }} .source-grid {{ grid-template-columns:repeat(2,1fr); }} .lower-grid {{ grid-template-columns:1fr; }} .panel {{ overflow-x:auto; }} }}
    @media (max-width:440px) {{ .shell {{ width:min(100% - 20px,1100px); }} h1 {{ font-size:42px; }} summary {{ padding-inline:12px; }} .source-grid {{ grid-template-columns:1fr; }} .meta-strip {{ display:grid; gap:8px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="shell">
      <div class="eyebrow">Four-source live index</div>
      <h1>API 中转站<br>实时综合榜</h1>
      <p class="lede">同时交叉验证 HelpAIO、zhaotutu、APIRanking 与 TokHub。综合分衡量当前表现，覆盖榜数与置信度衡量证据厚度。</p>
      <div class="meta-strip"><span>数据时间 <strong>{_escape(_timestamp(generated_at))}</strong></span><span>来源状态 <strong>{healthy_sources}/4 正常</strong></span><span>发布规则 <strong>至少覆盖 2 榜</strong></span></div>
    </div>
  </header>
  <main class="shell">
    <div class="section-head"><h2>当前 Top {len(results)}</h2><p>点击任一商家，查看四个来源的名次与指标。三榜以上标为高置信，两榜仅作为候选。</p></div>
    <section class="rank-list" aria-label="中转站综合排名">{_ranking_rows(results)}</section>
    <div class="lower-grid">
      <section class="panel"><h2>来源健康状态</h2><table><thead><tr><th>来源</th><th>状态</th><th>样本</th><th>抓取时间</th></tr></thead><tbody>{_source_rows(report_dicts)}</tbody></table></section>
      <section class="panel"><h2>计算原则</h2><ol class="method"><li>不同榜单按可靠性与新鲜度加权。</li><li>至少三榜时限制孤立异常低分的影响，并保留原值审计。</li><li>三榜与四榜覆盖获得递增加分。</li><li>移除任一来源重算，检查名次稳定性。</li></ol></section>
    </div>
    <aside class="warning"><strong>风险提示：</strong>榜单只能降低盲选风险，不能保证商家不会停服、泄露数据或改变线路。请小额充值，不要提交敏感数据。</aside>
  </main>
  <footer><div class="shell"><span>RelayRank · 自动生成，可审计</span><a href="https://github.com/Gy-Hu/api-relay-ranker">查看方法与源码</a></div></footer>
</body>
</html>
'''
    target = destination / "index.html"
    target.write_text(document, encoding="utf-8")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    return target
