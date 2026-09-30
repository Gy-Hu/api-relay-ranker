from __future__ import annotations

import html
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Config, RankedVendor
from datetime import date


SOURCE_LABELS = {
    "helpaio": "HelpAIO",
    "zhaotutu": "zhaotutu",
    "apiranking": "APIRanking",
    "tokhub": "TokHub",
}
SOURCE_URLS = {
    "helpaio": "https://www.helpaio.com/transit",
    "zhaotutu": "https://api.zhaotutu.ai/",
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


ISSUE_LABELS = {
    "source_zero_availability_requires_verification": "原站可用率为零，需核验探测与样本",
    "channel_detail_unavailable": "本通道详情抓取失败，未补造记录",
    "l3_not_run": "真实生成摘要标记未执行",
    "no_l3_records_in_returned_sample": "返回样本没有真实生成记录",
    "recent_records_are_bounded_sample": "最近记录是有限样本，不代表完整24小时",
    "summary_rates_not_used_as_measurements": "摘要百分比不当作实测比率",
    "unknown_date": "计分日期未知（权重折减）",
    "metric_measurement_date_unknown": "指标测量时间未确认",
    "source_composite_only": "仅采用原站综合分",
    "missing_ranking_score": "缺少有效排名分",
    "ordering_only": "页面顺序仅作收录旁证",
    "status_mapping_not_measured_rate": "状态映射值，不是实测百分比",
    "source_reports_website_unavailable": "原站标记官网不可用",
    "source_reports_inactive": "原站标记已停运",
    "source_reports_degraded": "原站标记服务降级",
    "unknown_provider_status": "未知服务状态",
    "missing_composite": "综合分缺失",
    "future_date": "时间异常，未计分",
    "stale": "数据过期，未计分",
    "missing_metrics": "缺少有效指标，未计分",
}


def _issue_text(issue: str) -> str:
    return ISSUE_LABELS.get(issue, issue.replace("channel_status:", "通道状态：").replace("source_status:", "原站状态：").replace("source_annotation:", "原站提示："))


def _source_details(contributions: Iterable[dict[str, object]]) -> str:
    indexed = {str(item["source"]): item for item in contributions}
    cards = []
    for source in SOURCE_ORDER:
        item = indexed.get(source)
        if item is None:
            cards.append(f'<div class="source-card source-card--missing">{_source_link(source)}<strong>无观测</strong><small>不按零分处理</small></div>')
            continue
        scored = float(item.get("weight", 0)) > 0
        value = item.get("raw_score")
        title = f"{float(value):.2f} 分" if scored and value is not None else "未计入评分"
        bits = []
        if item.get("rank") is not None:
            bits.append(f"原站位置 #{item['rank']} / {item['total_vendors']}（不参与计分）")
        bits.append(f"证据日期：{item.get('observed_at') or '未知'}")
        if scored:
            bits.append(f"有效权重 {float(item['weight']):.3f}")
        evidence = item.get("raw_evidence") or {}
        for batch in evidence.get("benchmark_batches", []):
            cache = f"{batch['cache_hits']}/{batch['cache_trials']}" if batch["cache_hits"] is not None else "未提供"
            wait = f"{batch['average_wait_seconds']} 秒" if batch["average_wait_seconds"] is not None else "未提供"
            bits.append(f"实测 {batch['model']} / {batch['channel']}：{batch['test_time']['display']}（年份/时区未知），{batch['round_count']}轮，缓存 {cache}，平均等待 {wait}")
        for channel in evidence.get("channels", []):
            detail = channel.get("detail")
            if detail:
                bits.append(f"{channel.get('model')}: 返回 {detail['record_count']} 条探测，其中 L3 {detail['l3_record_count']} 条")
        bits.extend(_issue_text(str(x)) for x in item.get("issues", []))
        cards.append(f'<div class="source-card">{_source_link(source)}<strong>{_escape(title)}</strong><small>{"<br>".join(_escape(x) for x in bits)}</small></div>')
    return "".join(cards)


def _ranking_rows(results: list[RankedVendor]) -> str:
    rows = []
    for item in results:
        risk = any(x.startswith(("channel_status:", "source_status:", "source_annotation:")) for x in item.issues)
        label = "有风险信号" if risk else ("日期待核验" if "unknown_date" in item.issues else "交叉参考")
        vendor_name = _escape(item.vendor)
        vendor_markup = (f'<a class="vendor-link" href="{_escape(item.website_url)}" target="_blank" rel="noopener noreferrer external">{vendor_name}<span aria-hidden="true">↗</span></a>' if item.website_url else f"<strong>{vendor_name}</strong>")
        coverage = f'<span>移除 {len(item.coverage_loss_groups)} 个组中的任一组后，证据将不足入榜门槛</span>' if item.coverage_loss_groups else ""
        rows.append(
            '<article class="rank-card"><details><summary>'
            f'<span class="position">{item.rank:02d}</span>'
            f'<span class="vendor">{vendor_markup}<small>{item.source_count} 个计分来源组 · {label}</small></span>'
            f'<span class="badge badge--candidate">{label}</span>'
            f'<span class="score"><strong>{item.score:.2f}</strong><small>参考综合分</small></span>'
            '<span class="chevron" aria-hidden="true">＋</span></summary><div class="detail-body"><div class="detail-stats">'
            f'<span>证据强度指数 <strong>{item.confidence:.2f}</strong>（非概率）</span>'
            f'<span>来源分歧 σ <strong>{item.score_stddev:.2f}</strong>（仅展示）</span>'
            f'<span>固定候选集移除独立组后的名次 <strong>{item.rank_best}–{item.rank_worst}</strong></span>'
            f'{coverage}</div><div class="source-grid">{_source_details(item.contributions)}</div></div></details></article>'
        )
    return "".join(rows) or '<p class="warning">当前没有足够独立评分证据支持排序。请查看来源状态与原始观测；本次不沿用旧榜单。</p>'


def _source_rows(reports: list[dict[str, Any]]) -> str:
    indexed = {str(report["name"]): report for report in reports}
    rows = []
    labels = {"limited": "数据受限", "reference_only": "仅作旁证", "usable": "可计分", "unavailable": "不可用"}
    for name in SOURCE_ORDER:
        report = indexed.get(name, {"ok": False})
        status = labels.get(str(report.get("quality")), "已解析") if report.get("ok") else "抓取/解析失败"
        counts = f"{int(report.get('vendor_count', 0))} 家 / {int(report.get('scoring_count', 0))} 家计分"
        error = f"<br>{_escape(report['error'])}" if report.get("error") else ""
        if report.get("detail_count") or report.get("detail_error_count"):
            counts += f"<br>详情 {int(report.get('detail_count', 0))} 份；失败 {int(report.get('detail_error_count', 0))} 份"
        rows.append(f'<tr><th>{_source_link(name, "source-table-link")}</th><td>{status}{error}</td><td>{counts}</td><td>{_escape(_timestamp(str(report.get("fetched_at") or "")))}</td></tr>')
    return "".join(rows)


def write_site(
    output_dir: str | Path,
    results: list[RankedVendor],
    reports: Iterable[Any],
    generated_at: str,
    site_url: str = "",
    og_image: str | Path | None = None,
    min_sources: int = 2,
    cohort_size: int | None = None,
    config: Config | None = None,
) -> Path:
    config = config or Config(date.today())
    cohort_size = len(results) if cohort_size is None else cohort_size
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
  <meta property="og:title" content="RelayRank · API 中转站证据参考榜">
  <meta property="og:description" content="区分有效评分、缺测与风险旁证，不将收录数量当作可靠概率。">
  <meta property="og:type" content="website">
  {f'<meta property="og:url" content="{_escape(canonical_url)}">' if canonical_url else ''}
  {og_markup}
  <title>RelayRank · API 中转站证据参考榜</title>
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
      <div class="eyebrow">Four-source evidence review · v2</div>
      <h1>API 中转站<br>证据参考榜</h1>
      <p class="lede">核查 HelpAIO、zhaotutu、APIRanking 与 TokHub。仅有效综合分参与排序；页面位置与通道状态作为旁证。未知日期降权，缺测不记零，故障信号保留。</p>
      <div class="meta-strip"><span>抓取时间 <strong>{_escape(_timestamp(generated_at))}</strong></span><span>来源读取 <strong>{healthy_sources}/4 已解析</strong></span><span>发布规则 <strong>至少 {min_sources} 个计分来源组</strong></span></div>
    </div>
  </header>
  <main class="shell">
    <div class="section-head"><h2>当前 Top {len(results)}</h2><p>排序依据各站综合评估，未承诺特定模型或渠道可用。展开查看有效权重、未知日期与风险信号；收录旁证不增加计分覆盖。</p></div>
    <section class="rank-list" aria-label="中转站综合排名">{_ranking_rows(results)}</section>
    <div class="lower-grid">
      <section class="panel"><h2>来源数据质量</h2><table><thead><tr><th>来源</th><th>状态</th><th>样本</th><th>抓取时间</th></tr></thead><tbody>{_source_rows(report_dicts)}</tbody></table></section>
      <section class="panel"><h2>计算原则</h2><ol class="method"><li>每来源综合分只计一次，再向 {config.prior_score:g} 分先验收缩（先验权重 {config.prior_strength:g}）。</li><li>日期未知权重乘 {config.unknown_date_weight:g}；已知日期按 {config.half_life_days:g} 天半衰期衰减，超过 {config.max_age_days} 天退出计分。</li><li>不抬高低分，不因多榜收录额外加分；同组相关来源合计至多一票。</li><li>在固定的 {cohort_size} 家候选中移除整个来源组，报告名次变化及覆盖不足。分组是建模假设，不证明跨站独立。</li></ol></section>
    </div>
    <aside class="warning"><strong>风险提示：</strong>参考综合分不是可用性承诺或可靠概率。日期不明、来源冲突及探测异常仍需核验，未提供模型维度证据时不生成模型专属排名。</aside>
  </main>
  <footer><div class="shell"><span>RelayRank v2 · <a href="data/audit.json">计分审计</a> · <a href="data/observations.json">全部原始观测与排除原因</a> · <a href="data/sources.json">来源报告</a> · <a href="data/details.json">实测与探测详情</a></span><a href="https://github.com/Gy-Hu/api-relay-ranker">查看方法与源码</a></div></footer>
</body>
</html>
'''
    target = destination / "index.html"
    target.write_text(document, encoding="utf-8")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    return target
