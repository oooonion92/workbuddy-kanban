# 02 打分链路 —— 执行说明

## 运行顺序（⛔ 串行铁律，2026-09-23 事故固化）

```
build_sector_full → build_zt_quality → build_auction_baseline
```

并行后果：涨停质量的「方向景气分 8 分」在板块全量.json 落盘前读取 → 全池归零、质量分被系统性压低约 6 分。已内置两道护栏：缺文件显式失败 + dir_score 覆盖率 0 提取告警（回溯自检：dir_score 非 None 比例应 ≈100%）。

一键编排：`bash run_scoring.sh YYYYMMDD`（内部即上述顺序）。

## 每脚本说明

| 脚本 | 输入 | 输出 | CLI |
|---|---|---|---|
| `build_sector_full.py` | `全部Ａ股{D}.xlsx`（DETAILS_DATA_DIR） | `daily/{D}/板块全量.json` | `--dates 2026-09-28` |
| `build_zt_quality.py` | 涨停池（SHORT_TERM_DATA_DIR）+ 全A明细 + 板块全量.json | `daily/{D}/涨停质量.json` | `--dates 2026-09-28` |
| `build_auction_baseline.py` | 涨停质量.json + 历史 全A明细（校准） | `daily/{D}/竞价预期基准.json/.md` | `--date 2026-09-28` |
| `sector_taxonomy.py` | （被 import，不单独跑） | 46 L1 方向归口 | — |

## 后置项：collect_a_details.py（全A明细采集器）

- 状态：**已写好并实测**（2026-09-28 全市场 5561 行落盘、下游 load_stocks 读通）；按本轮范围**暂不启用**；
- 启用方式：crontab.example 取消注释 09:26（auction 模式，抓竞价金额，收盘后不可回补）与 16:20（full 模式）两行；
- 产出 `全部Ａ股{D}.xlsx` 同名同列序（金额万元、流通市值「xx亿」文本——与现网手动导出格式一致，chg_w 等权回退行为逐字节复现）；
- 待办：①`竞价昨比` 口径双跑反证 ②东财 f100 行业（129 类，335 只 `-`）→ sector_taxonomy 东财映射表 ③`--mode compare` 逐列对比验收。

## 依赖

- 打分三连依赖 全A明细 `全部Ａ股{D}.xlsx`（历史校准）与池包 csv —— 在 01_数据抓取 + 后置采集器就位后才能跑当日；
- `build_sector_full.py` import `build_dashboard` / `sector_taxonomy` → **部署时全部 .py 平铺同一目录**（git 仓库 scripts/ 下，编号目录仅为交付视图）；
- 环境变量三件套：`KANBAN_ROOT` / `SHORT_TERM_DATA_DIR` / `DETAILS_DATA_DIR`（见根 README）。
