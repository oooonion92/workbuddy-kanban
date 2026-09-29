# WorkBuddy 接入：竞价取数 → 滚动跟踪表（机械执行）

> 本页是 `tdx_1j2.py plan` 与 `board_tracker.py snapshot/auction` 的配套说明。
> **目标：整条链路可复现、可审计，且不依赖语言模型判断。**

## 1. 为什么需要一条「机械通道」

WorkBuddy 环境里竞价数据来自 **TDX MCP（`tdx_quotes`，一次一只）**。若放任模型自由发挥，
就会出现「挑标的、排序、凭感觉判强弱」——这正是要消除的。

因此拆成四段，**每段都有唯一确定的输入输出**：

| 段 | 执行者 | 动作 | 产物 |
|---|---|---|---|
| ① 选谁去取 | **脚本** `tdx_1j2.py plan` | 读冻结的 `eod_state.csv`，**默认全量**导出待取代码 | `fetch_list.csv` + `fetch_protocol.md` |
| ② 取数 | **语言模型（纯机械）** | 逐行调 `tdx_quotes(hasHQInfo=1)`，**原样**抄 `HQInfo` 五个字段 | `quotes.json` |
| ③ 转录 | **脚本** `board_tracker.py snapshot` | `quotes.json` → 标准竞价快照 CSV（不做任何打分） | `竞价快照.csv` |
| ④ 评价 | **脚本** `board_tracker.py auction` | 在**滚动跟踪表**上填竞价块并判三档 | `连板跟踪_{T}.csv` |

模型在②里**没有任何决策权**：名单由①给定，三档由④判定。

## 2. `quotes.json` 格式（二选一）

```json
// A) 精简（推荐）
{"600105": {"open": 47.17, "prev_close": 46.72, "amount": 12345678,
            "date": "20260924", "time": "092500"}}
// B) 原样粘贴 tdx_quotes 返回的 HQInfo（脚本自适应）
{"600105": {"HQInfo": {"Open": 47.17, "Close": 46.72, "Amount": 12345678,
                       "HQDate": "20260924", "HQTime": "92500"}}}
```

- 金额单位 **元**；时间 `HHMMSS` 或 `HH:MM:SS` 皆可（脚本补零）
- `open` 必须是**正式开盘价**（09:25 之后），不是虚拟撮合价、不是盘中现价
- ⛔ **全量取数（30+ 只）只取 `HQInfo`**：调用时 `hasHQInfo=1`，**不要**开 `hasCalcInfo`
  —— 后者会带上盘口/财务/统计，返回体积会把 09:25 窗口拖垮
- 缺行或字段无效 → 该票**竞价列留空**，**不按不及预期处理**，也不进三档统计

## 3. 时间窗（最容易出错的一点）

| 字段 | 09:25–09:30 | 09:30 之后 |
|---|---|---|
| `HQInfo.Open` | 正式开盘价 ✅ | 不变 ✅ |
| `HQInfo.Close` | 昨收 ✅ | 不变 ✅ |
| **`HQInfo.Amount`** | **竞价成交额** ✅ | ❌ **全天累计成交额** |

→ `Amount` 决定了「竞昨比」与量能标签，**过了 09:30 再取就是错数**。
→ 若确实错过窗口：**不要**用盘中 `Amount` 顶替；改走历史回放
（`score.py auction --kind details-replay --replay`，读 `details\全部Ａ股{date}` 的 `今开`/`开盘%`/`开盘金额`），
并在结论中**明确标注「历史回放」**。

## 4. 日常时序

```
T   16:30  OneDrive 短线数据采集/{T} 同步完成
T   盘后   score.py eod --date {T} --evaluation-date {T+1} --out daily/{T}/first_board/{T}_eod
           board_tracker.py init --date {T} --eod … --prev 连板跟踪_{T-1}.csv
           tdx_1j2.py plan --eod … --out daily/{T}/first_board/fetch_{T+1}
T+1 09:25  逐票 tdx_quotes(hasHQInfo=1) → quotes.json                      ← 纯机械，30s 内完成
T+1 09:26  board_tracker.py snapshot → board_tracker.py auction             ← 在 连板跟踪_{T}.csv 上评价
T+1 盘后   board_tracker.py eod --track 连板跟踪_{T}.csv --date {T+1}       ← 填表现 + 判晋级
           然后重复 T 日三步 → 连板跟踪_{T+1}.csv
```

## 5. 禁止事项清单

1. 禁止用盘中现价、昨收推算值或虚拟撮合价替代 `open`
2. 禁止在 09:30 之后取 `Amount` 当竞价额
3. 禁止由模型调整阈值、增删标的、按体感排序、补缺失值
4. 禁止修改 `assets/models.json` 或 `eod_state.csv`（manifest 哈希会直接拒绝）
5. 禁止把「2 板及以上」的三档与「首板」的三档混为一谈 —— 两套基准不同（表内 `竞价基准口径` 列已标注）
6. 禁止拍阈值：`竞价预期基准.json` 缺失时三档**留空**，不得用体感补
7. 禁止把结论表述为买入指令 —— 全部为**研究排序**
8. 禁止产出 HTML；交付只有 CSV
