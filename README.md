# API Relay Ranker

[![Update ranking and deploy Pages](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml/badge.svg)](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml)

**[查看每日自动更新的 Top 10 榜单](https://gy-hu.github.io/api-relay-ranker/)**

一个可审计、抗偏差的 API 中转商实时榜单聚合器。它同时抓取 HelpAIO、zhaotutu、APIRanking 和 TokHub，不把不同网站的名次直接平均，而是把“榜单质量”和“商家表现”分开建模。

> `rank` 子命令用于离线 CSV；`live` 子命令会实时抓取四站。每次响应原文、时间和 SHA-256 都会保存到 `snapshots/`，便于复核。榜单只能降低盲选风险，不能保证服务商不会停服、泄露数据或改变线路。

## 为什么更中立

- **来源权重透明**：每个榜单都有可信度，不允许隐藏的硬编码偏爱。
- **时间衰减**：旧数据按半衰期自动减权，避免历史口碑永久主导结果。
- **相关来源折扣**：转载、同一社区或同一数据集衍生的多个榜单共享一票。
- **缺失值公平**：榜单未提供缓存率时，已有指标重新归一，而不是给缓存率记零分。
- **小样本收缩**：只上过一个榜单的商家会向 50 分中性先验收缩，减少偶然第一。
- **稳定性审计**：逐一移除来源后重算，输出每家名次可能落入的区间。
- **全链路解释**：JSON 结果保留每个来源的原始指标、单源得分和实际权重。

这套方法减少偏差，但不能证明任何中转商安全。中转服务有停服、跑路、日志留存和密钥泄露风险；应小额充值、避免提交敏感数据。

## 快速开始

项目仅依赖 Python 3.11+ 标准库。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
relayrank rank \
  --config examples/config.toml \
  --input examples/rankings.csv \
  --output outputs/ranking.csv \
  --json outputs/audit.json
```

### 实时四榜 Top N

```bash
PYTHONPATH=src python3 -m relayrank live \
  --config examples/config.toml \
  --top 10
```

默认规则：

- 四站必须全部抓取和解析成功，否则拒绝发布结果；
- Top N 商家至少要被 3 个独立榜单覆盖；
- 原始响应写入 `snapshots/<UTC运行时间>/`；
- 精简排名写入 `outputs/live-ranking.csv`；
- 每个来源的贡献写入 `outputs/live-audit.json`；
- 抓取数量、时间与哈希写入 `outputs/live-sources.json`。

生成与 GitHub Pages 相同的静态页面：

```bash
PYTHONPATH=src python3 -m relayrank live \
  --config examples/config.toml \
  --top 10 \
  --min-sources 2 \
  --site-dir build/site
```

线上榜单每天香港时间 09:17 由 GitHub Actions 自动更新，也可以从 Actions 页面手动触发。四站必须全部成功，测试和聚合也必须通过，才会部署新页面；失败时保留上一版有效页面。原始快照和审计数据作为工作流 artifact 保存 30 天。

调查范围较宽时可以加入 `--min-sources 2`。只有明确接受缺站风险时才使用 `--allow-partial`，生成的结果不应称为完整四榜排名。

四个适配器的口径：

- HelpAIO：读取 JSON-LD 名次及页面内结构化站点评分；
- zhaotutu：读取结构化综合分、3日可用率和缓存率；
- APIRanking：读取 JSON-LD 页面顺序；“未检测”保持缺失，不记零分；
- TokHub：读取公开通道 API，同一商家的多个模型通道先取中位分并聚合可用率，只计一票。

不安装也可以直接运行：

```bash
PYTHONPATH=src python3 -m relayrank rank \
  --config examples/config.toml \
  --input examples/rankings.csv
```

## 输入格式

`examples/rankings.csv` 每行代表一个来源对一个商家的观测。默认文件禁止使用虚构榜单：

| 字段 | 含义 |
|---|---|
| `source` | 必须与 TOML 中的来源名一致 |
| `vendor` | 商家名称；别名会自动映射到标准名称 |
| `rank` / `total_vendors` | 该来源的名次和榜单总数，成对填写 |
| `score` | 来源给出的综合分，0–100 |
| `uptime` | 可用率，0–100 |
| `cache_rate` | 缓存命中率，0–100 |
| `price_value` | 性价比评分，0–100，越高越好 |

指标可以留空。至少要有一个在 `[metric_weights]` 中权重大于零的指标。

## 配置原则

`reliability` 建议依据可复核方法打分，而不是依据榜单结论：

- 0.9–1.0：持续自动探测、公开方法、可复核原始数据；
- 0.7–0.9：自费实测且披露样本和更新时间；
- 0.4–0.7：社区问卷或公开口碑汇总；
- 0.0–0.4：匿名推荐、返佣导向或无法复核的榜单。

来自相同数据、作者、商业组织或大量互相转载的来源应填写同一个 `independence_group`。它们的总影响力会被限制为一组一票。

`prior_strength` 越大，孤立证据越难制造高排名；`half_life_days` 越小，系统越看重近期数据。所有调整都应提交配置变更并记录理由。

## 测试

测试只用 Python 标准库：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

标准库自检和示例运行无需额外依赖：

```bash
PYTHONPATH=src python3 -m relayrank rank --config examples/config.toml --input examples/rankings.csv
```

## 下一步

当前版本已经具备四站实时抓取与可解释聚合。后续适合增加：定时任务、供应商身份人工审核、异常变动告警，以及按 Claude/Codex/Gemini 使用场景分别排名。不要在抓取器中绕过网站服务条款或访问控制。
