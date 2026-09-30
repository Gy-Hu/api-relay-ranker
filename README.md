# API Relay Ranker

[![Update ranking and deploy Pages](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml/badge.svg)](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml)

**[查看每日更新的证据参考榜](https://gy-hu.github.io/api-relay-ranker/)**

抓取 HelpAIO、zhaotutu、APIRanking 和 TokHub，保留原始响应、字段缺失、来源状态及计分依据。仅依赖 Python 3.11+ 标准库。

## v2 的计分与限制

2026-09-30 的审计发现：APIRanking 的 JSON-LD 只包含前 30 家，HelpAIO 已更换评分结构，TokHub 的部分百分比是状态映射值，旧版低分保护还会产生“来源分提高、总分下降”的现象。v2 更换了数据契约和计分规则，分数不应与 v1 直接比较。

### 来源如何使用

| 来源 | 获取内容 | 用途与限制 |
|---|---|---|
| [HelpAIO](https://www.helpaio.com/transit) | 逐卡读取页面排名分，核对名称、位置及基础分 × 可用率 × 降权系数公式 | 只使用排名综合分一次；基础分和可用率仅供审计。缺排名分保留缺失，不用列表位置替代。时间是原站页面声明的更新日，并非逐次测量时间。 |
| [zhaotutu](https://api.zhaotutu.ai/) | 解码完整 Next.js 数据对象，保留正常、降级、停运及缺分记录 | 使用原站 `overallScore`；不再平均模型级/厂商级缓存，不重新叠加可用率。无法确认综合分测量日期，按未知日期降权。原站综合分本身可能包含基线或缺测数据，不能视作经过本项目复测。 |
| [APIRanking](https://apiranking.com/) | 完整可见列表与状态，核对 `numberOfItems` 和连续位置 | 页面顺序仅作收录旁证，**不折算质量分，不增加计分覆盖**。SEO 预览截断时不拿预览长度当总数；缺行、重复行或结构改变时拒绝该次发布。 |
| [TokHub](https://www.tokhub.me/) | 公开 API 全部分页、通道模型、探测日期和错误状态 | `score`、`uptime24h`、`successRate` 在已核验公开代码中含固定状态映射，**只作通道状态旁证**，不当作实测百分比或综合质量分。状态不代表其他模型/通道也异常。 |

四站读取成功不代表四站都可计分。页面分别显示已解析数量、可计分商家数、数据受限及仅作旁证。缺失、旁证、非法数值、过期、未来日期及停运信号都保留原因。

### 公式

对于有有效分数的观测：

- 有原站综合分时，只使用该分数，名次和组成指标不再重复贡献。
- 离线 CSV 明确提供独立指标而没有综合分时，才按配置的正权重归一；未提供的指标保持缺失，实测 0 保留为 0。
- 来源权重 = 配置可靠性 × 新鲜度。已知时间按 `0.5 ** (age_days / half_life_days)` 衰减；日期未知乘 `unknown_date_weight`（默认 0.25）；超过 `max_age_days`（默认 90 天）或未来日期退出计分。
- 每个商家的相关来源组内，各观测按原权重占比分摊，组总权重不超过该组最大单源权重。缺席或不可计分的来源不占票。
- 参考综合分 = `(Σ有效权重 × 原站分数 + prior_strength × prior_score) / (Σ有效权重 + prior_strength)`。
- 默认先验分 50、先验权重 0.8。**不抬高低分、不增加覆盖奖励、不扣除分歧罚分**。分歧标准差单独显示。固定证据权重和资格时，任一输入分提高不会降低总分。

可靠性、新鲜度折扣和先验都是透明的建模选择，并非经过校准的概率。原站评分口径不同，参考综合分不承诺服务安全、模型真实性或某条线路的可用率。日期未知的数据仍可能过时，因此页面明确警示。

计分覆盖只数有正有效权重的来源组；旁证、零权重、缺分和过期观测不增加覆盖。配置的分组不证明来源独立，发现共同数据血缘时应合组。本项目不按域名或模糊名称自动合并商家：只有配置中明确的别名才合并，冲突别名和同源重复商家会报错。

### 排序稳定性

先按 `--min-sources` 形成候选集合，再排序。移除一个完整来源组（含其所有关联来源）时保持同一候选集合，用剩余证据和先验重算；没有剩余证据时使用先验。区间包含基线排名。移除后低于入榜门槛会单独标记，不能把该情景解释为仍有足够证据的排名。

Top N 是候选集合的截取，因此区间可以超过 N；页面同时显示完整候选数。分数相同按商家名称稳定排序，不伪装成有意义的质量差异。

当前上游没有统一的模型/渠道/时间窗口契约，本项目不生成 Claude、Codex、Gemini 专属质量排名。TokHub 每条状态保留模型名称，避免把某个通道的异常推广到整个商家。

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
relayrank live --config examples/config.toml --top 10 --min-sources 2 --site-dir build/site
```

不安装也可运行：

```bash
PYTHONPATH=src python3 -m relayrank live --config examples/config.toml --site-dir build/site
PYTHONPATH=src python3 -m relayrank rank --config examples/config.toml --input examples/rankings.csv
```

CSV 示例是历史观测，不是当前推荐。

默认要求四站抓取和解析成功；失败时保存报告与快照，返回非零退出码，GitHub Pages 保留上一版。显式 `--allow-partial` 可接受缺站，但仍要求至少两个配置上不同的来源组成功读取。**即使全部读取成功，若没有商家满足有效计分覆盖，也发布明确的“证据不足”页面，不伪造排名或默默沿用旧数据。**

GitHub Actions 每天按 cron `17 1 * * *`（香港时间计划 09:17，实际可能延迟）更新；推送 main 和手动触发也会运行。测试、抓取与构建通过后才部署。原始响应与审计 artifacts 保存 30 天。

## 审计输出

- `ranking.csv`：参考分、有效覆盖、证据强度指数、分歧及名次区间。
- `audit.json`：本次入榜商家的全部来源贡献，包含未计分旁证、日期、权重、异常和移除组后覆盖不足信息，`algorithm_version=2.0`。
- `observations.json`：全部商家和通道原始证据，包括未入榜记录、计分资格及排除原因。
- `sources.json`：来源读取结果、可计分/旁证/排除数量、未知日期数量及每页响应哈希。
- `snapshots/<UTC运行时间>/`：原始响应与元数据。TokHub 每页和合并后的解析输入都保存，不覆盖第一页。

CLI 默认写入 `outputs/`；有 `--site-dir` 时，还复制一套到网页的 `data/` 目录，并提供下载链接。

证据强度指数 `1-exp(-有效权重)` 仅是权重规模摘要，**不是可靠概率**。v2 JSON/CSV 使用 `evidence_strength`，不再输出 `confidence` 百分比；Python 结果对象暂保留同名内部兼容属性。旧审计中的保护、覆盖奖励和分歧罚分字段保留为零，方便读取迁移。

## 配置迁移

v1 中 `variance_penalty`、`low_outlier_gap`、`three_source_bonus`、`four_source_bonus` 必须改为 0，否则明确报错，避免旧配置悄悄恢复有问题的策略。默认关闭名次分权重。

可调参数包括 `prior_strength`、`prior_score`、`half_life_days`、`max_age_days`、`unknown_date_weight` 和来源分组。它们都需记录理由，不能以让特定商家排名更高为目的调参。

## 验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试包括 2026-09-30 的四站公共快照（gzip 压缩，哈希与来源见 `tests/fixtures/provenance.json`），以及合成的字段缺失、零值、非法值、结构变化、分页漂移、相关来源、单调性、时间衰减、固定候选区间和空榜发布场景。公共快照不代表之后的页面永远兼容；解析契约失效时停止发布并保留诊断。
