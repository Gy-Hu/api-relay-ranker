from __future__ import annotations

import html
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .live import SOURCES
from .models import Config, Ranking, Source

INFO = {info.name: info for info in SOURCES}

ISSUE_LABELS = {
    "source_zero_availability_requires_verification": "原站可用率为零，需核验",
    "authenticity_unsampled": "原站真伪项未抽样（该项按 0 计入原站分）",
    "missing_composite": "原站综合分缺失",
    "no_availability_series": "无可用率探测序列，日期未知",
    "no_valid_reports_in_window": "窗口内没有有效检测报告",
    "multiple_source_entries_averaged": "同一来源多个域名条目已取平均",
    "insufficient_peers": "该来源可计分商家太少，未计分",
    "unknown_date": "日期未知（权重折减）",
    "future_date": "日期异常，未计分",
    "stale": "数据过期，未计分",
}
ISSUE_PREFIXES = {
    "source_status:": "原站状态：",
    "veridrop_detail_unavailable:": "Veridrop 详情暂不可用：",
    "name_matches_configured_vendor:": "名称与已配置商家相同但域名不同，未合并：",
}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _timestamp(value: str | None) -> str:
    if not value:
        return "未知"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")


def _issue_text(issue: str) -> str:
    if issue in ISSUE_LABELS:
        return ISSUE_LABELS[issue]
    for prefix, label in ISSUE_PREFIXES.items():
        if issue.startswith(prefix):
            return label + issue[len(prefix):].strip()
    return issue


def _link(url: str | None, label: str, css_class: str) -> str:
    if not url:
        return f'<span class="{css_class}">{_escape(label)}</span>'
    return (f'<a class="{css_class}" href="{_escape(url)}" target="_blank" rel="noopener noreferrer external">'
            f'{_escape(label)}<span aria-hidden="true">↗</span></a>')


def _source_cards(contributions: Iterable[dict[str, Any]]) -> str:
    indexed = {str(c["source"]): c for c in contributions}
    cards = []
    for info in SOURCES:
        item = indexed.get(info.name)
        if item is None:
            cards.append(f'<div class="source-card source-card--missing">{_link(info.homepage, info.label, "source-link")}'
                         '<strong>无观测</strong><small>不按零分处理</small></div>')
            continue
        scored = item["weight"] > 0
        title = f'P{item["percentile"]:.0f}' if scored else "未计分"
        bits = []
        if item.get("raw_score") is not None:
            bits.append(f'原站分 {item["raw_score"]:g}')
        bits.append(f'日期 {item.get("observed_at") or "未知"}')
        if item.get("sample_count") is not None:
            bits.append(f'有效样本 {item["sample_count"]}')
        if scored:
            bits.append(f'有效权重 {item["weight"]:.2f}')
        if item.get("domain"):
            bits.append(str(item["domain"]))
        bits.extend(_issue_text(str(x)) for x in item.get("issues", []))
        cards.append(f'<div class="source-card">{_link(item.get("source_url") or info.homepage, info.label, "source-link")}'
                     f'<strong>{_escape(title)}</strong><small>{"<br>".join(_escape(b) for b in bits)}</small></div>')
    return "".join(cards)


def _ranking_rows(ranking: Ranking, top: int) -> str:
    rows = []
    for item in ranking.results[:top]:
        risk = any(i.startswith("source_status:") or i == "source_zero_availability_requires_verification" for i in item.issues)
        label = "有风险信号" if risk else f"{item.source_count} 源交叉"
        vendor = _link(item.website_url, item.vendor, "vendor-link") if item.website_url else f"<strong>{_escape(item.vendor)}</strong>"
        coverage = ('<span>移除任一来源组后将不足入榜门槛</span>' if item.coverage_loss_groups else "")
        rows.append(
            '<article class="rank-card"><details><summary>'
            f'<span class="position">{item.rank:02d}</span>'
            f'<span class="vendor">{vendor}<small>{_escape(" · ".join(item.domains))}</small></span>'
            f'<span class="badge badge--{"candidate" if risk else "high"}">{label}</span>'
            f'<span class="score"><strong>{item.score:.1f}</strong><small>综合分</small></span>'
            '<span class="chevron" aria-hidden="true">＋</span></summary><div class="detail-body"><div class="detail-stats">'
            f'<span>有效权重 <strong>{item.effective_weight:.2f}</strong></span>'
            f'<span>来源分歧 σ <strong>{item.score_stddev:.1f}</strong> 百分位</span>'
            f'<span>移除一个来源组后的名次 <strong>{item.rank_best}–{item.rank_worst}</strong></span>'
            f'{coverage}</div><div class="source-grid">{_source_cards(item.contributions)}</div></div></details></article>'
        )
    return "".join(rows) or '<p class="warning">当前没有商家满足入榜所需的独立来源数量。请查看来源状态与原始观测；本次不沿用旧榜单。</p>'


def _source_rows(reports: list[dict[str, Any]], sources: dict[str, Source], ranking: Ranking) -> str:
    indexed = {str(r["name"]): r for r in reports}
    rows = []
    for info in SOURCES:
        report = indexed.get(info.name, {"ok": False})
        if not report.get("ok"):
            status = f'<span class="status status--error">失败</span><br>{_escape(report.get("error") or "")}'
        elif info.name in ranking.thin_sources:
            status = '<span class="status status--error">可计分商家不足</span>'
        else:
            status = '<span class="status status--ok">计分</span>'
        source = sources[info.name]
        rows.append(
            f'<tr><th>{_link(info.homepage, info.label, "source-table-link")}<br><small>{_escape(info.measures)}</small></th>'
            f'<td>{status}</td><td>{int(report.get("valid_count", 0))} / {int(report.get("vendor_count", 0))}</td>'
            f'<td>{source.reliability:g} · {_escape(source.group)}</td><td>{_escape(_timestamp(report.get("fetched_at")))}</td></tr>')
    return "".join(rows)


def write_site(output_dir: str | Path, ranking: Ranking, reports: Iterable[Any], sources: dict[str, Source],
               config: Config, generated_at: str, top: int = 20, min_sources: int = 2,
               site_url: str = "", og_image: str | Path | None = None) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_dicts = [asdict(r) if is_dataclass(r) else dict(r) for r in reports]
    healthy = sum(bool(r.get("ok")) for r in report_dicts)
    labels = "、".join(info.label for info in SOURCES)
    canonical_url = site_url.rstrip("/") + "/" if site_url else ""
    og_markup = ""
    if og_image and Path(og_image).is_file():
        shutil.copyfile(og_image, destination / "og.png")
        image_url = canonical_url + "og.png" if canonical_url else "og.png"
        og_markup = (f'<meta property="og:image" content="{_escape(image_url)}">'
                     '<meta name="twitter:card" content="summary_large_image">')
    shown = min(top, len(ranking.results))
    document = f'''<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="每日聚合 {labels} 的实测数据，按来源内百分位合成的 API 中转站参考榜。">
  <meta property="og:title" content="RelayRank · API 中转站综合参考榜">
  <meta property="og:description" content="四个实测来源，按域名识别商家，来源内百分位归一后加权合成。">
  <meta property="og:type" content="website">
  {f'<meta property="og:url" content="{_escape(canonical_url)}">' if canonical_url else ''}
  {og_markup}
  <title>RelayRank · API 中转站综合参考榜</title>
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
    .source-link,.source-table-link {{ width:max-content; color:var(--muted); font-size:12px; text-decoration:none; }} .source-link:hover,.source-table-link:hover {{ color:var(--acid); text-decoration:underline; text-underline-offset:3px; }} .source-link span,.source-table-link span {{ margin-left:4px; }} .source-card strong {{ margin-top:5px; font:700 17px/1.2 ui-monospace,SFMono-Regular,monospace; }} .source-card small {{ margin-top:auto; font-size:11px; overflow-wrap:anywhere; }}
    .source-card--missing {{ opacity:.48; }}
    .lower-grid {{ display:grid; grid-template-columns:1.25fr .75fr; gap:18px; margin-top:54px; }}
    .panel {{ padding:22px; background:var(--panel); border:1px solid var(--line); border-radius:16px; }} .panel h2 {{ margin-bottom:16px; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:11px 8px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; }} th {{ color:var(--ink); }} td {{ color:var(--muted); }}
    .status {{ display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }} .status::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }} .status--ok {{ color:var(--mint); }} .status--error {{ color:#ff7474; }}
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
      <div class="eyebrow">Measured-source composite · v3</div>
      <h1>API 中转站<br>综合参考榜</h1>
      <p class="lede">汇总 {labels} 四个自有实测的榜单。商家按域名对齐；每个来源的分数先换算成该来源内的百分位，再按可靠性、新鲜度和样本量加权合成。缺测不记零，风险信号保留。</p>
      <div class="meta-strip"><span>生成时间 <strong>{_escape(_timestamp(generated_at))}</strong></span><span>来源 <strong>{healthy}/{len(SOURCES)} 已解析</strong></span><span>入榜 <strong>{ranking.cohort_size} 家（至少 {min_sources} 个来源组）</strong></span></div>
    </div>
  </header>
  <main class="shell">
    <div class="section-head"><h2>Top {shown}</h2><p>综合分 0–100：50 为各来源中位水平。展开查看每个来源的原站分、百分位 P、日期和样本。</p></div>
    <section class="rank-list" aria-label="中转站综合排名">{_ranking_rows(ranking, top)}</section>
    <div class="lower-grid">
      <section class="panel"><h2>来源</h2><table><thead><tr><th>来源</th><th>状态</th><th>有分/条目</th><th>可靠性 · 组</th><th>抓取时间</th></tr></thead><tbody>{_source_rows(report_dicts, sources, ranking)}</tbody></table></section>
      <section class="panel"><h2>计算方法</h2><ol class="method"><li>各来源只取其综合分一次，换算为该来源全部可计分商家内的百分位。</li><li>权重 = 来源可靠性 × 0.5^(天数/{config.half_life_days:g})；超过 {config.max_age_days} 天退出；日期未知乘 {config.unknown_date_weight:g}；有样本数的来源再乘 n/(n+{config.sample_prior:g})。</li><li>综合分 = (Σ权重×百分位 + {config.prior_strength:g}×{config.prior_score:g}) / (Σ权重 + {config.prior_strength:g})，证据少的商家向中位收缩。</li><li>同一数据血缘的来源合计至多一票；移除整个来源组重排得到名次区间。</li></ol></section>
    </div>
    <aside class="warning"><strong>说明：</strong>各来源测量对象不同（可用率、价格、真伪报告），综合分是相对排序参考，不是可用性承诺。Veridrop 报告由社区触发，样本分布不均匀；RelayPick 多数站点未做真伪抽样。</aside>
  </main>
  <footer><div class="shell"><span>RelayRank v3 · <a href="data/ranking.csv">ranking.csv</a> · <a href="data/audit.json">计分审计</a> · <a href="data/observations.json">全部观测与排除原因</a> · <a href="data/sources.json">来源报告</a></span><a href="https://github.com/Gy-Hu/api-relay-ranker">方法与源码</a></div></footer>
</body>
</html>
'''
    target = destination / "index.html"
    target.write_text(document, encoding="utf-8")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    return target
