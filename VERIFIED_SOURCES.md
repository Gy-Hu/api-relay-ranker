# 来源核验记录

所有判断均来自只读 HTTP GET：没有注册，没有提交 key，也没有对中转站发起探测。"重合"指和 HelpAIO 收录商家对得上的数量。

**来源更换（2026-11-21）：** [旧配置的 Actions 运行](https://github.com/Gy-Hu/api-relay-ranker/actions/runs/44849390877) 取得 HelpAIO 20 条、OkkMax 83 条有效观测；RelayPick 返回 522，Veridrop 返回 403。后两者已退出当前计分，历史运营证据保留。该次运行不是新四源配置的发布验证。

## 计分来源

| 来源 | 数据位置 | 新鲜度证据 | 血缘 |
|---|---|---|---|
| HelpAIO | `/transit` 卡片公式 + JSON-LD 排行 | 页面"数据更新于"日期 | 自有实测 |
| [真测](https://ztest.ai/sites) | 从目录进入商家详情，取合格模型/分组的站点平均分；目录受赞助影响，不取展示顺序 | 详情页实际观测日期 | 原站实测；公开数据不足以完整复算综合分，可用性检查次数不作评分样本数 |
| OkkMax | `/list` Flight `rows[].score`；`/availability` `cards[].channels[].points[].t` | 每小时一个探测桶 | 自有探测；fanbidog/ai-relay-rank 是其镜像 |
| [中转查](https://zhongzhuancha.com/transit) | 从目录中选有实测的商家，取已公布的当前 /100 综合分；未测不等于零 | 72 小时窗口，取实际最近探测日期；链接商家详情供核对模型、渠道 | 原站实测；公开数据不足以完整复算综合分 |

## 排除的来源

| 来源 | 排除原因 |
|---|---|
| zhaotutu | 进榜商家的 `lastChecked` 停在 2026-04-30 至 05-15；431/468 条模型记录的 24h 与 3d 可用率完全相同 |
| APIRanking | 页面顺序不是质量分；benchmark 页的实测年份/时区未知 |
| TokHub | `score`/`uptime24h` 是状态映射值；只覆盖 9 家 |
| ChunduAI | 报告 JSON 与 Veridrop 逐字段一致（同一时间戳、端点、耗时和错误文本） |
| fanbidog/ai-relay-rank | OkkMax 镜像，数据只在 2026-07-08 提交过 |
| aiapirank.github.io | Hvoy 镜像；877 家中只有 111 家有可用率数据；没有综合分 |
| CheckFakeAPI | 近 7 天 0 条报告；68 家中 51 家只有单样本；JSON 接口只返回 35 家 |
| BaiPiao | 主分 = 站长分 + 投票；收录收费；与 HelpAIO 收录商家无重合 |
| GrokCode | 没有样本的商家用兜底评分；与 HelpAIO 收录商家无重合 |
| ai-transfer | 可用率是写死在 JS 里的推广文案 |
| Chenking api-rank | 人工打分，自动检测已关闭，链接大量带返利参数 |
| RouterHubs | 有新鲜的每小时可达探测，但没有综合分；与 HelpAIO 收录商家只重合 1 家 |
| apirank.ttop5.cc | 数据来自作者真实开发日志，质量不错，但快照停在 2026-08-19 |
| APIMaket | 多次尝试均无法抓取 |

## 身份陷阱

- `duckcode.cn` ≠ `duckcoding.ai`（HelpAIO 链接的是后者）
- `88api.ai` ≠ 88 Code（`88code.org`）
- NekoAPI（`nekoapi.com`）≠ Neko Code（`nekocode.ai`）
- `ikuncode.com` 和 `78code.top` 未确认与对应商家同属一家
- RelayPick 把 `packyapi.com` 和 `packyapi.ai` 列为两个条目；本项目在配置中把两者合并为 Packy Code
