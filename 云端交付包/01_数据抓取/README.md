# 数据抓取脚本交付（D2）—— 可直接迁移云端

> 范围：**只含复盘看板所需的行情类抓取**。全市场全A明细采集（collect_a_details.py）本轮后置，见文末。

## 文件清单

| 文件 | 数据源 | 产出 | 云端适配状态 |
|---|---|---|---|
| `collect_short_term_data.py` | 东财池接口（akshare 1.18.81）+ 新浪交易日历 | `{D}/zt_pool.csv、zb_pool.csv、dt_pool.csv、previous_zt_feedback.csv、first_boards.csv、snapshot.json、短线数据_{D}.xlsx` + 根目录 `DB_Short_Term_Summary.csv` | ✅ 原样直迁：输出目录 `--output-dir` 参数化、日期自动选择（15:30 闸门）、`--trade-date` 手动回补、原子写、内建校验失败非零退出、summary 按交易日幂等 upsert |
| `fetch_snapshot.py` | 腾讯 qt.gtimg.cn + 东财 push2 clist + 新浪 CFF（同花顺 dataapi 兜底） | `daily/{D}/行情快照.json`（指数/期指贴水仓差/宽度/池类/板块资金） | ✅ 已参数化：`KANBAN_ROOT`、`SHORT_TERM_DATA_DIR`、`WATCHLIST_FILE` 三个 env；纯 HTTP 无本地客户端依赖 |

## 迁移要点

1. **依赖钉版**：`pip install akshare==1.18.81 pandas openpyxl requests`（池接口列名绑定 akshare 版本，升级须重跑校验）；
2. **watchlist 处理**：fetch_snapshot 会读 `WATCHLIST_FILE` 抓持仓/自选行情——云端**不配置该变量**即自动跳过该段（warnings 提示属预期）；⛔ 成本类文件不上云；
3. **时区**：两脚本均自带 Asia/Shanghai 逻辑（采集器）/ 无时区敏感输出（快照），cron 按示例表排 CST；
4. **探活**：本地"TDX 探活铁律"在云端等价物 = fetch_snapshot 首个腾讯请求失败即整体告警（脚本内已含 retry+warning，无需额外探活）；
5. **回补**：池包断日 `--trade-date YYYYMMDD` 逐日补（接口支持历史日期）；行情快照跨日字段（昨贴水/仓差）依赖历史快照连续，缺日自动置 null 并告警，**勿手工填**。

## 本轮后置（不属于本交付）

- `scripts/collect_a_details.py`（全A明细采集器，已写好并实测 5561 行落盘、下游兼容）—— 待 P2/P3 再启用，届时 crontab 恢复 09:26/16:20 两行即可；
- 龙虎榜链路（已在云端 17:10 运行，不动）。
