# 云端交付包 · 总览

> 交付对象：云端 WorkBuddy + 轻量云（Lighthouse）。目标：复盘看板构建链脱离本地电脑运行。
> 交付日期：2026-09-28｜配套文档：项目根《云端迁移指南.md》（分阶段实施与风险）

## 目录地图（按流水线步骤归档）

```
云端交付包/
├─ README.md                  本文件（目录地图 + 部署顺序 + 环境变量）
├─ requirements.txt           依赖钉版（akshare==1.18.81，⛔ 不得随意升级）
├─ crontab.example            云端定时表（全A两行已注释=后置项）
│
├─ 01_数据抓取/               【步骤一：行情与池类数据采集】
│   ├─ collect_short_term_data.py   池包采集（zt/zb/dt/反馈/首板 + snapshot.json），原样直迁
│   ├─ fetch_snapshot.py            行情快照（指数/期指贴水仓差/宽度），已参数化，纯 HTTP
│   └─ README.md                    迁移要点（钉版/watchlist 处理/回补/探活等价物）
│
├─ 02_打分链路/               【步骤二：评分体系（⛔ 严格串行）】
│   ├─ build_sector_full.py         板块全量打分 v2（强度30/资金30/宽度20/量能20）
│   ├─ build_zt_quality.py          涨停质量五维（封板时间25/牢固度25/大单20/共振20/量能10）
│   ├─ build_auction_baseline.py    竞价预期基准（历史校准三档）
│   ├─ sector_taxonomy.py           46 个 L1 方向归口（方向景气分唯一来源）
│   ├─ collect_a_details.py         【后置】全A明细采集器（已实测 5561 行，启用见 README）
│   ├─ run_scoring.sh               串行编排（并行=方向景气分归零，禁改）
│   └─ README.md                    执行顺序/依赖/参数/后置项说明
│
├─ 03_视觉渲染/               【步骤三：看板视觉层（与现网逐字节一致）】
│   ├─ build_dashboard.py           详细看板（6 段完整产物）
│   ├─ build_entry.py               主入口（覆盖式）
│   ├─ md_to_replay.py              05md → replay.html
│   ├─ _fix_block_order.js          段序搬移（渲染链固定步骤）
│   ├─ 数据契约-渲染器输入.md        渲染器全部输入的契约
│   └─ README.md                    视觉零改动声明 + diff=0 验收法
│
└─ 04_AI复盘链路/             【步骤四：AI 复盘与思考链路交付要求】
    └─ AI复盘链路-交付要求.md        六步思考链路/输入输出契约/五道质量门/automation prompt
```

## 部署顺序（对应《云端迁移指南》P0–P4）

1. **仓库化**：本包 + `scripts/`（权威源）进 git → 云端 clone 到 `/srv/kanban`；
   ⚠️ 部署时所有 `.py` **平铺进同一 `scripts/` 目录**（`build_sector_full` import `build_dashboard`/`sector_taxonomy`，编号目录是交付视图不是运行结构）；
2. **依赖**：`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`；
3. **抓取上云**：按 `crontab.example` 启用 16:10 / 16:35 两任务 → 连续 3 交易日与本地产物 diff=0；
4. **打分+渲染**：16:40 `run_scoring.sh` + 渲染复现某历史日看板，与本地产物 diff=0；
5. **AI 环节**：按 04 文档配置 WorkBuddy automation（16:55 触发）。

## 环境变量（云端唯一配置面）

| 变量 | 默认（=本地现网） | 云端建议 |
|---|---|---|
| `KANBAN_ROOT` | `D:\Work buddy project\每日盯盘` | `/srv/kanban`（git 仓库根） |
| `SHORT_TERM_DATA_DIR` | `D:\OneDrive\Stock\短线数据采集` | `/srv/data/shortterm` |
| `DETAILS_DATA_DIR` | `D:\OneDrive\Stock\details` | `/srv/data/details` |
| `WATCHLIST_FILE` | 本地 watchlist.json | **不配置**（跳过持仓段，⛔ 成本不上云） |

## 全局铁律（任何环节不得违背）

- ⛔ 打分三连必须串行：sector_full → zt_quality → auction_baseline；
- ⛔ 缺数标「数据不可用」禁推算，禁 WebSearch 补数；
- ⛔ 产物禁含 `持仓|成本|浮盈|浮亏|安全垫`；watchlist/成本类文件不上云；
- ✅ 跑后三检：hero/body/style 非空 + 热力图无同向两行 + 净化 grep；
- ⚠️ Lighthouse 2026-10-17 到期，动手前先定续费/换商。
