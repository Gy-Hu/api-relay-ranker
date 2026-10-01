# API Relay Ranker

[![Update ranking and deploy Pages](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml/badge.svg)](https://github.com/Gy-Hu/api-relay-ranker/actions/workflows/pages.yml)

**[查看每日更新的综合参考榜](https://gy-hu.github.io/api-relay-ranker/)**

每天抓取四个**自己做测量**的中转站榜单，按域名对齐商家，把每个来源的分数换算成该来源内的百分位，再加权合成一个综合分。只依赖 Python 3.11+ 标准库，不需要浏览器、账号或 API Key。

## 来源

| 来源 | 读取内容 | 测的是什么 | 已知局限 |
|---|---|---|---|
| [HelpAIO](https://www.helpaio.com/transit) | 卡片上的排名分公式（基础分 × 3 日可用率 × 降权系数），逐卡校验 | 站长自费实测 | 只收录约 20 家精选站；3 日可用率波动大 |
| [RelayPick](https://relaypick.com/ranking) | SSR 页面内嵌的完整合格榜（`final_score`、`computed_at`） | 价格 35%、稳定性 25%、真伪 25%、透明度 15%；美国单点每 10 分钟探测 | 非 5xx 即算可达；多数站点真伪"未抽样"，该项按 0 计入原站分；CSV 导出在 robots 禁止的 `/api/` 下，因此读页面 |
| [OkkMax](https://www.okkmax.com/list) | `/list` 内嵌的综合分 `score`；`/availability` 探测序列的最新时间作为观测日期 | 分组真实请求的可用率、速度与 Claude 纯度 | 生产环境权重未公开；`/api/search` 的 `score` 不是综合分，不使用 |
| [Veridrop](https://veridrop.org/leaderboard) | 按域名搜索所有端点，读取各端点详情页的报告历史 | 社区用户触发的真实请求检测（协议、真伪签名、计费） | 谁提交、测哪个协议都不均匀；付费认证只影响置顶展示，因此不读榜单页 |

OkkMax 的服务条款禁止"抓取数据用于商业目的"。本项目仅供自用。

**已移除的来源：**
- zhaotutu：综合分停在 2026-04/05，没有可靠的测量日期
- APIRanking：页面顺序不是质量分
- TokHub：百分比是状态映射值，覆盖 9 家

**核验后排除的来源：**
- ChunduAI：复制 Veridrop 的报告
- fanbidog/ai-relay-rank：OkkMax 镜像，停在 07-08
- aiapirank：Hvoy 镜像，多数商家没有测量数据
- CheckFakeAPI：近 7 天没有报告，多数商家只有单样本
- BaiPiao：站长打分 + 投票，收录收费
- GrokCode：没有样本的商家用兜底评分
- ai-transfer：硬编码的推广文案
- Chenking api-rank：人工打分，自动检测已关闭
- RouterHubs：没有综合分，重合 1 家
- apirank.ttop5.cc：快照停在 08-19

## 商家识别

各来源按**可注册域名**对齐商家（`www.packyapi.ai`、`api-slb.packyapi.com` 等都归到所属域名）。`examples/config.toml` 的 `[[vendors]]` 把同一商家的多个域名合并，例如 Packy 的 `.com` 和 `.ai`。

- 未配置的域名在各来源之间按域名自然合并，但不会按名字相似合并。
- 与已配置商家同名、但域名不同的条目单独入榜，并标记 `name_matches_configured_vendor`。
- 同一来源列出同一商家的多个域名时，取平均，只计一次。
- 同一域名在配置中分给两个商家会直接报错。

## 计分

1. **原站分数只用一次。** 各来源取自己的综合分。Veridrop 没有可用的综合分，用 60 天窗口内有效报告计算：先按协议分别取中位数，再按 n/(n+3) 加权平均。这样不会被社区提交的协议比例左右。例如 Packy 的 Claude 中位 79、OpenAI 中位 0，混在一起取中位会是 0。
2. **来源内百分位。** 每个来源把其全部可计分商家换算成 0–100 的中位秩百分位。各站尺度差别很大（RelayPick 头名 63，Veridrop 中位数多在 90 以上），只有排序信息可以比较。可计分商家少于 `minimum_peers`（5）的来源当次不计分。
3. **权重** = 来源可靠性 × 0.5^(天数 / 30)：
   - 超过 90 天或日期在未来，退出计分；日期未知乘 0.25。
   - 提供样本数的来源（Veridrop）再乘 n/(n+3)。
   - 同一血缘组的来源合计至多一票。
4. **综合分** = (Σ权重 × 百分位 + 0.8 × 50) / (Σ权重 + 0.8)。50 代表各来源的中位水平，证据少的商家向 50 收缩。
5. **入榜门槛：** 至少 2 个来源组，且每个组的权重 ≥ `coverage_weight`（0.25）。权重更低的旧数据或稀疏数据仍参与计分，但不能单独把商家带进榜。
6. **名次区间：** 固定候选集，逐个移除整个来源组后重新排名，得到名次范围。

这些规则保证：任一来源的原站分提高，不会让该商家的综合分下降。

可靠性、半衰期和先验都是建模选择，不是经过校准的概率，调整时需在配置注释中写明理由。百分位只表达"在该来源里排第几"，而各来源的收录范围不同：HelpAIO 只收录精选站，RelayPick 包含停运站。所以同一个百分位在不同来源里含义并不完全相同。综合分是相对排序参考，不是可用性承诺。

## 运营时间与硬规则

运营天数**只用于展示和默认筛选，不计入综合分**：现在没有跑路数据能标定"多活一个月风险降多少"。

- **硬规则：** 商家的全部已知域名都已过期，且过期日之后的 RDAP 复查仍显示过期，直接不进榜。被排除的商家仍留在各来源的百分位基数里，所以不会改变其他商家的分数。目前四个来源都没有结构化的"停运/跑路"字段（HelpAIO 只在评论文字里提到，且无法可靠对应到具体商家），因此暂无来源标记触发的排除。
- **最早运营证据：** 取以下证据中最早的一个，按商家所有域名合并：
  - 第三方对中转本身的观测，即使早于当前域名注册日也保留（商家会换域名）：HelpAIO 卡片的"已收 N 天"、Veridrop 最早的报告、本站首次收录该域名的日期。
  - 来自域名本身或商家自述的证据，早于注册日即视为前任域名主人或无法核实而丢弃：首张证书（ctlogs.dev 证书透明日志索引；早期缓存来自 crt.sh）、Wayback 首页首次存档、RelayPick 上线日期（以商家最早的域名注册日为下限）。如果证书或存档早于注册日（域名曾属他人），这个域名自己的证书和存档全部不用：域名转让会保留原注册日，注册日之后的记录也可能仍属前任。
  - 单独的域名注册日期不算运营证据：二手老域名（hao.ai 2017 年注册，2026 年才上线）会让它虚高。
- **默认筛选：** 页面默认只显示运营满 `min_operating_days`（90）天的站，可勾选显示其余；名次仍是全部候选中的名次。域名 30 天内到期、或证书/存档早于注册日（域名曾属他人）会在卡片上提示。
- **缓存：** 这些日期写在仓库的 `data/domain_evidence.json`，每天只查询新域名、上次失败的查询、临近到期的域名（每日复查）和满 90 天的 RDAP 记录；不支持 RDAP 的后缀（如 .cn）30 天后再试。每次运行有查询上限（RDAP 80、证书 80、Wayback 20）。RDAP 通过 IANA 引导表直接查各注册局服务器（rdap.org 中转会限流）。GitHub Actions 在内容变化时把它提交回 main。
- **证书来源：** ctlogs.dev 的 `/v1/hosts/{域名}`，取每个主机名的首次证书日期。匿名调用每小时 100 次、一次一个请求；额度用完时本次停止查询、不记错误，剩下的域名下次再查。免费档只索引近 90 天内有证书的主机名，首次日期来自它 2025 年起的索引，所以老域名只能看到 2025 年以后的证书，这正好对应当前运营者。可选：在仓库 Actions secrets 里设置 `CTLOGS_API_KEY`（account.ctlogs.dev 免费申请，每月 1 万次），脱离匿名共享池。crt.sh 的接口限流严重、几乎每次失败，已不再使用。

## 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
relayrank live --config examples/config.toml --top 30 --site-dir build/site
```

不安装也可以运行：`PYTHONPATH=src python3 -m relayrank live --config examples/config.toml`。

- HelpAIO、RelayPick、OkkMax 任一抓取或解析失败，就不发布，返回非零退出码，GitHub Pages 保留上一版。
- Veridrop 单个域名查询失败只记录警告，全部失败才算该来源失败。
- `--allow-partial` 可以接受缺站，但仍要求至少两个来源组成功。
- 没有商家满足门槛时，发布明确的"证据不足"页面。

GitHub Actions 每天按 cron `17 1 * * *` 运行，推送 main 和手动触发也会运行。测试与构建通过后才部署；原始响应和审计数据作为 artifact 保存 30 天。工作流需要 `contents: write`，只用于提交 `data/domain_evidence.json`。

`--skip-lookups` 只使用缓存、不做 RDAP/证书/Wayback 查询；`--evidence-cache` 可指定缓存路径。

## 输出

- `ranking.csv`：名次、综合分、来源组数、有效权重、各来源百分位、名次区间、运营天数、最早运营证据及其类型、域名。
- `audit.json`：配置、来源、入榜商家的逐来源贡献（原站分、百分位、权重、日期、样本数、原始证据）、每家的运营证据（含被丢弃的及原因）、硬规则排除名单和本次查询次数，`algorithm_version=3.1`。
- `domain_evidence.json`：每个可注册域名的注册/到期日、证书、存档和来源观测日期。
- `observations.json`：全部来源的全部条目（包括未入榜的），附计分资格和排除原因。
- `sources.json`：每个来源的读取结果、条目数，以及每个原始响应的 URL 和哈希。
- `snapshots/<UTC 运行时间>/`：全部原始响应。

## 验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用 2026-10-01 的公共页面快照（gzip 压缩，来源与哈希见 `tests/fixtures/provenance.json`），外加合成数据，覆盖：
- 尺度不变性和单调性
- 血缘去重和弱证据入榜门槛
- 缺测不记零
- 域名对齐与同名不合并
- Veridrop 协议混合和占位页处理
- 结构变化时拒绝发布
- 运营证据：注册日前证据丢弃、换域名后第三方观测保留、过期须经过期后复查才排除、续费后恢复、查询预算与失败重试
